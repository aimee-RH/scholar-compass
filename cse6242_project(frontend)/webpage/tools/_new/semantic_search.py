"""
Semantic search tool for finding papers and authors via vector similarity.

Uses BGE-M3 embedding model with proper query instruction prefix.
Returns papers with optional filtering by subfield (hybrid search).
"""

import os
import logging
from typing import List, Dict, Optional
from langchain_core.tools import tool
from sentence_transformers import SentenceTransformer
from tools.neo4j_connector import get_neo4j_driver

logger = logging.getLogger(__name__)

# ========== 常量 ==========
# BGE-M3 官方推荐的 retrieval instruction
BGE_QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query: "

# 召回分数阈值（低于这个视为低置信度）
LOW_CONFIDENCE_THRESHOLD = 0.75

# 向量索引名（在 Stage 3 创建的）
VECTOR_INDEX_NAME = "paper_embedding_idx"

# ==========================


# 模块级单例，避免每次调用都加载模型（BGE-M3 加载需要 3 秒）
_embedding_model = None


def _get_embedding_model() -> SentenceTransformer:
    """懒加载 embedding 模型"""
    global _embedding_model
    if _embedding_model is None:
        logger.info("[semantic_search] Loading BGE-M3 model (first call, ~3s)")
        device = "mps" if os.getenv("USE_MPS", "false").lower() == "true" else "cpu"
        _embedding_model = SentenceTransformer("BAAI/bge-m3", device=device)
        logger.info(f"[semantic_search] Model loaded on device: {_embedding_model.device}")
    return _embedding_model


def _embed_query(query_text: str) -> List[float]:
    """
    把 query 文本转成 embedding 向量。
    关键：必须加 BGE_QUERY_INSTRUCTION prefix，否则检索质量崩溃。
    """
    model = _get_embedding_model()
    query_with_instruction = BGE_QUERY_INSTRUCTION + query_text
    embedding = model.encode(
        query_with_instruction,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embedding.tolist()


@tool
def semantic_search(
        query_text: str,
        top_k: int = 10,
        subfield_filter: Optional[List[str]] = None,
        min_score: float = LOW_CONFIDENCE_THRESHOLD,
) -> str:
    """
    基于向量相似度的语义论文搜索。适用于：
    - 按研究方向找论文（如 "federated learning privacy"）
    - 按主题找相关领域老师（如 "neural network for computer vision"）
    - 探索性查询（没有具体作者名时使用）

    工作机制：
    1. 用 BGE-M3 把 query 转向量（加 retrieval instruction prefix）
    2. Neo4j 向量索引检索 top_k * 2 候选
    3. 可选按 subfield 过滤（hybrid search）
    4. 低置信度（top score < min_score）时警告用户

    Args:
        query_text: 用户自然语言查询，如 "federated learning privacy"
        top_k: 返回多少条（默认 10）
        subfield_filter: 可选的 subfield 名字列表（从 routing_decision 获取）
        min_score: 最低可接受分数（默认 0.75）

    Returns:
        格式化后的结果字符串，包含论文列表 + 作者 + 置信度提示
    """
    logger.info(f"[semantic_search] Query: '{query_text}', top_k={top_k}, "
                f"filter={subfield_filter}")

    try:
        query_emb = _embed_query(query_text)
    except Exception as e:
        logger.error(f"[semantic_search] Embedding error: {e}", exc_info=True)
        return f"❌ Embedding 生成失败: {e}"

    # 构造 Cypher 查询
    if subfield_filter:
        # Hybrid search：向量召回 + subfield 过滤
        cypher = """
        CALL db.index.vector.queryNodes($index_name, $candidates, $emb)
        YIELD node, score
        MATCH (node)-[:IN_SUBFIELD]->(s:Subfield)
        WHERE s.display_name IN $subfields
        OPTIONAL MATCH (a:Author)-[:AUTHORED]->(node)
        WITH node, score, s.display_name AS subfield, 
             collect(a.display_name)[..5] AS authors
        RETURN node.id AS paper_id,
               node.title AS title,
               node.publication_year AS year,
               node.cited_by_count AS citations,
               score,
               subfield,
               authors
        ORDER BY score DESC
        LIMIT $top_k
        """
        params = {
            "index_name": VECTOR_INDEX_NAME,
            "candidates": top_k * 3,  # 多捞一些，因为要过滤
            "emb": query_emb,
            "subfields": subfield_filter,
            "top_k": top_k,
        }
    else:
        # 纯向量检索
        cypher = """
        CALL db.index.vector.queryNodes($index_name, $top_k, $emb)
        YIELD node, score
        OPTIONAL MATCH (node)-[:IN_SUBFIELD]->(s:Subfield)
        OPTIONAL MATCH (a:Author)-[:AUTHORED]->(node)
        WITH node, score, s.display_name AS subfield,
             collect(a.display_name)[..5] AS authors
        RETURN node.id AS paper_id,
               node.title AS title,
               node.publication_year AS year,
               node.cited_by_count AS citations,
               score,
               subfield,
               authors
        ORDER BY score DESC
        """
        params = {
            "index_name": VECTOR_INDEX_NAME,
            "top_k": top_k,
            "emb": query_emb,
        }

    # 执行查询
    driver = get_neo4j_driver()
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            result = session.run(cypher, **params)
            rows = [dict(r) for r in result]
    except Exception as e:
        logger.error(f"[semantic_search] Neo4j error: {e}", exc_info=True)
        return f"❌ 数据库查询失败: {e}"

    if not rows:
        if subfield_filter:
            return (f"⚠️ 在指定领域 ({', '.join(subfield_filter)}) 中未找到与 "
                    f"「{query_text}」相关的论文。\n"
                    f"建议：尝试去掉领域过滤，或换用更宽泛的查询。")
        return f"⚠️ 未找到与「{query_text}」相关的论文。"

    # 检查置信度
    top_score = rows[0]["score"]
    low_confidence = top_score < min_score

    # 格式化输出
    lines = []

    if low_confidence:
        lines.append(
            f"⚠️ **低置信度提示**: 查询「{query_text}」的最高相关度仅为 {top_score:.2f}"
            f"（低于阈值 {min_score}），说明数据库中可能没有高度相关的论文。\n"
            f"以下是相对最相关的结果，请谨慎参考：\n"
        )
    else:
        lines.append(f"✅ 为「{query_text}」找到 {len(rows)} 篇相关论文："
                     f"（最高相关度 {top_score:.2f}）\n")

    for i, row in enumerate(rows, 1):
        authors_str = ", ".join(row["authors"][:3])
        if len(row["authors"]) > 3:
            authors_str += f" et al. (共 {len(row['authors'])} 人)"

        lines.append(
            f"{i}. [{row['score']:.2f}] 《{row['title']}》\n"
            f"   📅 {row['year']} | 📊 被引 {row['citations']} | "
            f"🏷️ {row['subfield'] or '未分类'}\n"
            f"   👥 {authors_str}\n"
        )

    result_text = "\n".join(lines)
    logger.info(f"[semantic_search] Returned {len(rows)} results, "
                f"top_score={top_score:.3f}, low_conf={low_confidence}")
    return result_text
