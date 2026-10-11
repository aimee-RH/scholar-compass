"""
评估脚本 - 跑 benchmark 并输出报告

用法:
    python eval/run_eval.py                  # 跑全部
    python eval/run_eval.py --limit 5        # 快速测试前 5 个
    python eval/run_eval.py --category factual_simple  # 只跑某类
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.metrics import (
    compute_task_classification_metrics,
    compute_entity_resolution_metrics,
    compute_semantic_retrieval_metrics,
    compute_latency_metrics,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def build_state_for_case(case: Dict) -> Dict:
    """根据 test case 构造 LangGraph 初始 state"""
    messages = []

    # 加载对话历史
    for h in case.get("conversation_history", []):
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        else:
            messages.append(AIMessage(content=h["content"]))

    # 当前 query
    messages.append(HumanMessage(content=case["query"]))

    state = {
        "messages": messages,
        "routing_decision": None,
        "resolved_entities": {},
        "pending_clarification": None,
    }

    # 如果 case 指定了 pending_clarification，构造一个
    pc = case.get("pending_clarification")
    if pc:
        # 构造一个 mock PendingClarification
        # 注意：这里需要用到你项目里的 schema
        from graph.schemas import PendingClarification, AuthorCandidate
        candidates = [
            AuthorCandidate(
                author_id=f"https://openalex.org/A500000000{i}",
                name=pc["entity_name"],
                paper_count=10 + i,
                total_citations=100 + i * 50,
                sample_titles=[f"Mock paper {i}.1", f"Mock paper {i}.2"],
            )
            for i in range(pc.get("candidates_count", 2))
        ]
        state["pending_clarification"] = PendingClarification(
            entity_name=pc["entity_name"],
            candidates=candidates,
            created_at=datetime.utcnow().isoformat(),
        )

    return state


def run_single_case(case: Dict, graph_app) -> Dict:
    """跑单个 test case"""
    state = build_state_for_case(case)

    t0 = time.time()
    try:
        result = graph_app.invoke(state)
        elapsed_ms = (time.time() - t0) * 1000
        error = None
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000
        error = str(e)
        result = {}
        logger.error(f"Case {case['id']} failed: {e}")

    decision = result.get("routing_decision")

    # 抽取关键字段
    predicted = {
        "id": case["id"],
        "query": case["query"],
        "category": case.get("category"),
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error,
    }

    # Expected 字段
    for k in [
        "expected_task_type",
        "expected_query_shape",
        "expected_ambiguity_reason",
        "expected_candidates_count",
        "expected_has_coreference",
        "expected_has_time_range",
        "expected_fast_path",
        "expected_max_elapsed_ms",
        "expected_relevant_subfields",
        "expected_min_top1_score",
    ]:
        if k in case:
            predicted[k] = case[k]

    # Predicted 字段
    if decision:
        predicted["predicted_task_type"] = decision.task_type
        predicted["predicted_query_shape"] = decision.query_shape
        predicted["predicted_ambiguity_reason"] = decision.ambiguity_reason
        predicted["predicted_has_coreference"] = decision.has_coreference
        predicted["predicted_reasoning"] = decision.reasoning

        if decision.entities and decision.entities.authors:
            first_author = decision.entities.authors[0]
            predicted["actual_candidates_count"] = len(first_author.candidates)

        if decision.entities and decision.entities.time_range:
            predicted["predicted_has_time_range"] = True

    # 对 SEMANTIC_SEARCH 结果，提取 top-1 信息（从 AI message 里解析）
    if decision and decision.task_type == "SEMANTIC_SEARCH":
        ai_messages = [m for m in result.get("messages", []) if getattr(m, "type", None) == "ai"]
        if ai_messages:
            reply = ai_messages[-1].content
            predicted["reply_preview"] = reply[:500]

            # 简单 regex 抽取 top 1 score（你的 semantic_search.py 输出格式如果是 [0.82] ... 就能抽）
            import re
            score_match = re.search(r"\[(\d+\.\d+)\]", reply)
            if score_match:
                predicted["predicted_top1_score"] = float(score_match.group(1))

    # 判断是否走了快速路径（elapsed < 100ms 视为快速路径）
    predicted["actual_fast_path"] = predicted["elapsed_ms"] < 100

    checks = {}
    for expected_key, actual_key in (
        ("expected_task_type", "predicted_task_type"),
        ("expected_ambiguity_reason", "predicted_ambiguity_reason"),
        ("expected_fast_path", "actual_fast_path"),
    ):
        if expected_key in predicted:
            checks[expected_key] = predicted[expected_key] == predicted.get(actual_key)
    if "expected_max_elapsed_ms" in predicted:
        checks["expected_max_elapsed_ms"] = predicted["elapsed_ms"] <= predicted["expected_max_elapsed_ms"]
    predicted["checks"] = checks
    predicted["passed"] = error is None and all(checks.values())

    return predicted


def print_report(results: List[Dict], report: Dict):
    """打印可读的评估报告"""
    print("\n" + "=" * 70)
    print("📊 EVALUATION REPORT")
    print("=" * 70)

    print(f"\n📝 Total Cases: {len(results)}")
    errors = [r for r in results if r.get("error")]
    print(f"❌ Errors: {len(errors)}")
    print(f"✅ Regression cases passed: {sum(r['passed'] for r in results)}/{len(results)}")

    # 1. Task Classification
    tc = report["task_classification"]
    print(f"\n━━━ Task Classification Accuracy ━━━")
    print(f"Overall: {tc['overall_accuracy']:.1%} ({tc['correct']}/{tc['total']})")
    print("\nPer-type breakdown:")
    for t, v in tc["per_type_accuracy"].items():
        print(f"  {t:25s}: {v['accuracy']:.1%}  ({v['correct']}/{v['total']})")

    # Confusion matrix
    print("\nConfusion Matrix (expected → predicted):")
    for expected, predictions in tc["confusion_matrix"].items():
        for predicted, count in predictions.items():
            marker = "✅" if expected == predicted else "❌"
            print(f"  {marker} {expected} → {predicted}: {count}")

    # 2. Entity Resolution
    er = report["entity_resolution"]
    print(f"\n━━━ Entity Resolution ━━━")
    if "note" in er:
        print(f"  {er['note']}")
    else:
        print(f"Total ambiguity cases: {er['total']}")
        print(f"Ambiguity reason accuracy: {er['ambiguity_reason_accuracy']:.1%}")
        if er.get("candidates_count_accuracy") is not None:
            print(f"Candidates count accuracy: {er['candidates_count_accuracy']:.1%}")

    # 3. Semantic Retrieval
    sr = report["semantic_retrieval"]
    print(f"\n━━━ Semantic Retrieval ━━━")
    if "note" in sr:
        print(f"  {sr['note']}")
    else:
        print(f"Total semantic queries: {sr['total_semantic_queries']}")
        if sr.get("top1_subfield_match_rate") is not None:
            print(
                f"Top-1 subfield match rate: {sr['top1_subfield_match_rate']:.1%}  ({sr.get('subfield_match_total')} scored)")
        if sr.get("average_top1_score") is not None:
            print(
                f"Top-1 score avg: {sr['average_top1_score']:.3f}  (min={sr['min_top1_score']:.3f}, max={sr['max_top1_score']:.3f})")
        print(f"Low-confidence (<0.75): {sr['low_confidence_count']}")

    # 4. Latency
    lt = report["latency"]
    print(f"\n━━━ Latency ━━━")
    print(
        f"Overall: avg {lt['overall']['avg_ms']:.0f}ms, p50 {lt['overall']['p50_ms']:.0f}ms, p95 {lt['overall']['p95_ms']:.0f}ms")
    print("\nBy category:")
    for cat, v in sorted(lt["by_category"].items(), key=lambda x: x[1]["avg_ms"]):
        print(f"  {cat:40s}: avg {v['avg_ms']:6.0f}ms  p95 {v['p95_ms']:6.0f}ms  (n={v['count']})")

    # 5. Failed cases
    failures = []
    for r in results:
        if r.get("expected_task_type") and r.get("predicted_task_type") != r.get("expected_task_type"):
            failures.append(r)

    if failures:
        print(f"\n━━━ Failed Task Classification Cases ━━━")
        for r in failures:
            print(f"\n  [{r['id']}] {r['query']}")
            print(f"    Expected: {r['expected_task_type']}")
            print(f"    Got:      {r['predicted_task_type']}")
            print(f"    Reason:   {r.get('predicted_reasoning', '')[:100]}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default=str(Path(__file__).with_name("benchmark.json")))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--output", default=None, help="Output JSON path (default: eval/results/eval_<timestamp>.json)")
    args = parser.parse_args()

    load_dotenv()

    # 导入 graph
    from graph.graph import create_graph
    graph_app = create_graph()
    logger.info("✅ Graph loaded")

    # 加载 benchmark
    with open(args.benchmark, "r", encoding="utf-8") as f:
        benchmark = json.load(f)

    cases = benchmark["test_cases"]

    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]

    if args.limit:
        cases = cases[:args.limit]

    logger.info(f"Running {len(cases)} test cases...")

    # 跑 eval
    results = []
    for i, case in enumerate(cases, 1):
        logger.info(f"[{i}/{len(cases)}] {case['id']}: {case['query'][:60]}")
        result = run_single_case(case, graph_app)
        results.append(result)

        # 简要状态
        if result.get("error"):
            print(f"   ❌ ERROR: {result['error'][:80]}")
        else:
            tc = "✅" if result.get("expected_task_type") == result.get("predicted_task_type") else "❌"
            print(f"   {tc} Task: {result.get('predicted_task_type', '?')} | Elapsed: {result['elapsed_ms']:.0f}ms")

    # 计算指标
    report = {
        "task_classification": compute_task_classification_metrics(results),
        "entity_resolution": compute_entity_resolution_metrics(results),
        "semantic_retrieval": compute_semantic_retrieval_metrics(results),
        "latency": compute_latency_metrics(results),
    }

    # 打印报告
    print_report(results, report)

    # 保存结果
    if args.output:
        output_path = Path(args.output)
    else:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(exist_ok=True, parents=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = results_dir / f"eval_{ts}.json"

    output_data = {
        "metadata": {
            "total_cases": len(results),
            "timestamp": datetime.now().isoformat(),
            "benchmark_version": benchmark.get("metadata", {}).get("version", "unknown"),
        },
        "results": results,
        "report": report,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"\n📄 Results saved to: {output_path}")


if __name__ == "__main__":
    main()
