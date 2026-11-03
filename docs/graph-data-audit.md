# Local graph data audit

The original local project under `Documents/2025fall-semester/6242-proj` contains a 29.9 MB cleaned CSV, a 988 MB Neo4j data directory, and an `.embedding_progress.json` file recording 45,009 paper IDs. These files are not in this repository.

On November 3, 2026, the Neo4j directory was copied and opened with Neo4j 5.15.0 for a read-only audit of its contents. The copy contains 130,121 `Paper` nodes and 206,585 `Author` nodes, but **zero `Paper.embedding` values**, zero paper abstracts, and no `paper_embedding_idx` vector index. The copied graph files were last modified on April 17, while the embedding progress file was modified on April 23. The progress file therefore does not prove that this particular saved graph contains the later embeddings. The original data directory was not changed.

Before running the full GraphRAG app, locate the Neo4j store that was active when embeddings were generated, or regenerate embeddings from the cleaned 45,009-paper dataset into an isolated graph copy. Verify with:

```cypher
MATCH (p:Paper)
RETURN count(p) AS papers, count(p.embedding) AS embedded;

SHOW INDEXES YIELD name, type, state
WHERE name = 'paper_embedding_idx'
RETURN name, type, state;
```

The read-only [`scripts/graph_readiness.py`](../cse6242_project%28frontend%29/webpage/scripts/graph_readiness.py) runs these checks and samples real paper-to-author evidence. For example, from the application directory, set the Neo4j environment variables for a **copy** of the graph and run `python scripts/graph_readiness.py --topic "machine learning"`. It exits with status 1 until embeddings and the vector index are present.

The first real-data demo should retrieve a small set of papers for one research topic, show each paper's title, year, and matched authors, and link those authors to advisor cards. Keep the fictional preview explicitly labeled until this path is verified end to end. Do not commit raw graph data, embeddings, API keys, or local `.env` files.
