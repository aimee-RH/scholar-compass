"""
scripts/generate_embeddings.py

用 BGE-M3 为 Paper 节点生成 embedding，存回 Neo4j。

运行方式:
    python scripts/generate_embeddings.py                # 全量
    python scripts/generate_embeddings.py --limit 100    # 测试
    python scripts/generate_embeddings.py --resume       # 断点续传

策略:
    - 有 abstract: embedding_text = title + ". " + abstract
    - 无 abstract: embedding_text = title + ". Topic: " + subfield + ". Published in " + source
    - 两种都存 embedding_quality 字段标识质量
"""

import os
import sys
import re
import json
import time
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional, Set
from dotenv import load_dotenv
from neo4j import GraphDatabase
from tqdm import tqdm
import torch
from sentence_transformers import SentenceTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ========== 配置 ==========
MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIM = 1024
BATCH_SIZE = 64  # M4 Air 16GB 内存建议 64
MAX_SEQ_LENGTH = 512  # BGE-M3 支持最长 8192，但长文本慢，512 足够
PROGRESS_FILE = ".embedding_progress.json"


# ===========================


def get_best_device() -> str:
    """自动选最好的可用 device"""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"




# 在文件顶部加这个函数
def clean_math_tags(text: str) -> str:
    """移除 MathML、HTML 标签和 LaTeX 公式，保留纯文本"""
    if not text:
        return ""
    # 移除所有 XML/HTML 标签
    cleaned = re.sub(r'<[^>]+>', ' ', text)
    # 移除 LaTeX 数学公式（$...$ 或 $$...$$）
    cleaned = re.sub(r'\$+[^$]*?\$+', ' ', cleaned)
    # 移除 \ensuremath{} \mathrm{} 等残留 LaTeX 命令
    cleaned = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', ' ', cleaned)
    # 折叠多空格
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


class EmbeddingGenerator:
    def __init__(self, uri: str, user: str, password: str, database: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_papers_to_embed(self, limit: Optional[int] = None,
                            subfield: Optional[str] = None,
                            from_year: Optional[int] = None,
                            to_year: Optional[int] = None) -> List[Dict]:
        """
        查出还没生成 embedding 的 paper，带上 title / abstract / subfield / source
        """
        cypher = """
        MATCH (p:Paper)
        WHERE p.embedding IS NULL
          AND ($from_year IS NULL OR p.publication_year >= $from_year)
          AND ($to_year IS NULL OR p.publication_year <= $to_year)
        OPTIONAL MATCH (p)-[:IN_SUBFIELD]->(sf:Subfield)
        OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(src:Source)
        WITH p, sf, src
        WHERE $subfield IS NULL OR sf.display_name = $subfield
        RETURN p.id AS id,
               p.title AS title,
               p.abstract AS abstract,
               sf.display_name AS subfield,
               src.display_name AS source
        """
        cypher += " ORDER BY p.id"
        if limit is not None:
            cypher += " LIMIT $limit"

        with self.driver.session(database=self.database) as sess:
            result = sess.run(cypher, limit=limit, subfield=subfield,
                              from_year=from_year, to_year=to_year)
            return [dict(r) for r in result]

    def update_embeddings(self, updates: List[Dict]):
        """批量回写 embedding"""
        cypher = """
        UNWIND $batch AS row
        MATCH (p:Paper {id: row.id})
        SET p.embedding = row.embedding,
            p.embedding_quality = row.quality
        """
        with self.driver.session(database=self.database) as sess:
            sess.run(cypher, batch=updates)

    def count_embedded(self) -> int:
        with self.driver.session(database=self.database) as sess:
            return sess.run(
                "MATCH (p:Paper) WHERE p.embedding IS NOT NULL RETURN count(p) AS c"
            ).single()["c"]

    def count_total(self) -> int:
        with self.driver.session(database=self.database) as sess:
            return sess.run("MATCH (p:Paper) RETURN count(p) AS c").single()["c"]





# 替换原来的 build_embedding_text 函数
def build_embedding_text(paper: Dict) -> tuple[str, str]:
    """
    构造 embedding 输入文本和质量标识。
    返回 (text, quality_label)
    """
    title = clean_math_tags(paper.get("title") or "")
    abstract = clean_math_tags(paper.get("abstract") or "")
    subfield = (paper.get("subfield") or "").strip()
    source = (paper.get("source") or "").strip()

    if not title:
        return "Untitled paper", "minimal"

    if abstract and len(abstract) > 20:  # 防止 abstract 只有一两个字符的坏数据
        text = f"{title}. {abstract}"
        return text, "full"

    # 降级方案
    parts = [title]
    if subfield:
        parts.append(f"Research area: {subfield}")
    if source:
        parts.append(f"Published in: {source}")
    return ". ".join(parts), "title_only"


def load_progress() -> Set[str]:
    if not Path(PROGRESS_FILE).exists():
        return set()
    try:
        with open(PROGRESS_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_progress(processed: Set[str]):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(list(processed), f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Delete progress file")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--subfield", help="Exact Neo4j subfield name for a focused pilot")
    parser.add_argument("--from-year", type=int, help="Earliest publication year to include")
    parser.add_argument("--to-year", type=int, help="Latest publication year to include")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.from_year and args.to_year and args.from_year > args.to_year:
        parser.error("--from-year must be no later than --to-year")

    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    if args.reset and Path(PROGRESS_FILE).exists():
        Path(PROGRESS_FILE).unlink()
        logger.info("Progress file deleted.")

    # 1. 加载模型
    device = get_best_device()
    logger.info(f"🚀 Using device: {device}")
    logger.info(f"📦 Loading model: {MODEL_NAME}")

    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=device)
    model.max_seq_length = MAX_SEQ_LENGTH
    logger.info(f"✅ Model loaded in {time.time() - t0:.1f}s")
    logger.info(f"   Actual device: {model.device}")

    # 2. 连数据库
    gen = EmbeddingGenerator(uri, user, password, database)

    try:
        total_in_db = gen.count_total()
        already_done = gen.count_embedded()
        logger.info(f"📊 Database status: {already_done}/{total_in_db} papers have embeddings")

        papers = gen.get_papers_to_embed(limit=args.limit, subfield=args.subfield,
                                         from_year=args.from_year, to_year=args.to_year)
        logger.info(f"📋 Papers to process: {len(papers)}")

        if not papers:
            logger.info("✅ All papers already have embeddings. Nothing to do.")
            return

        # 断点续传
        processed: Set[str] = load_progress() if args.resume else set()
        if processed:
            papers = [p for p in papers if p["id"] not in processed]
            logger.info(f"Resuming: {len(processed)} done, {len(papers)} remaining")

        # 3. 批量生成 embedding
        stats = {"full": 0, "title_only": 0, "minimal": 0}
        pbar = tqdm(total=len(papers), desc="Generating embeddings")

        for i in range(0, len(papers), args.batch_size):
            batch = papers[i: i + args.batch_size]

            # 构造文本
            texts = []
            qualities = []
            for paper in batch:
                text, quality = build_embedding_text(paper)
                texts.append(text)
                qualities.append(quality)
                stats[quality] += 1

            # 生成 embedding
            try:
                embeddings = model.encode(
                    texts,
                    batch_size=args.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True,  # ⚠️ 关键：归一化后可用余弦相似度
                )
            except Exception as e:
                logger.error(f"Embedding error at batch {i}: {e}")
                pbar.update(len(batch))
                continue

            # 构造 Neo4j 更新数据
            updates = []
            for paper, emb, quality in zip(batch, embeddings, qualities):
                updates.append({
                    "id": paper["id"],
                    "embedding": emb.tolist(),  # numpy array → list for Neo4j
                    "quality": quality,
                })

            # 写入 Neo4j
            try:
                gen.update_embeddings(updates)
            except Exception as e:
                logger.error(f"Neo4j write error at batch {i}: {e}")
                pbar.update(len(batch))
                continue

            # 更新进度
            processed.update(p["id"] for p in batch)
            if i % (args.batch_size * 5) == 0:  # 每 5 批保存一次
                save_progress(processed)

            pbar.update(len(batch))

        pbar.close()
        save_progress(processed)

        # 4. 统计
        logger.info("\n=== Final Statistics ===")
        logger.info(f"  ✅ Full quality (title+abstract): {stats['full']}")
        logger.info(f"  ⚪ Title-only quality:            {stats['title_only']}")
        logger.info(f"  ⚠️  Minimal quality:              {stats['minimal']}")

        final_embedded = gen.count_embedded()
        logger.info(f"\n📊 Database: {final_embedded}/{total_in_db} papers embedded")

    finally:
        gen.close()


if __name__ == "__main__":
    main()
