# 1,000-paper embedding pilot

On November 4, 2026, a local **copy** of the saved Neo4j graph was used for a focused BGE-M3 retrieval check. The original iCloud graph was not changed.

## Scope and result

- Selected 1,000 papers in the `Artificial Intelligence` subfield from 2019–2024, ordered by paper ID. Every selected paper had a title.
- The existing generator produced 1,000 normalized 1,024-dimensional BGE-M3 vectors in about 24 seconds on this machine's MPS device. All were `title_only`: title plus subfield and publication source, with no abstract.
- A Neo4j 5.15 cosine vector index named `paper_embedding_idx` reached `ONLINE` state. The graph readiness check reported 1,000 embedded papers out of 130,121 in the copied graph.
- Vector search returned real OpenAlex paper IDs, titles, years, similarity scores, and linked authors. For “reinforcement learning for robotics,” the top result was [“Sequencing of multi-robot behaviors using reinforcement learning”](https://openalex.org/W4200389578) at score 0.83. Other broad queries produced mixed results: the small sample and title-only text limit what the scores mean.

This verifies the embedding → vector index → paper → author path. It does **not** establish advisor recommendation quality, faculty identity, or a useful score threshold. The project owner confirms the source dataset targets Georgia Tech faculty, but `Author` nodes in this graph have only `id` and `display_name`; coauthors are not marked separately. A faculty card should therefore use a curated target list or a verified institution field before publication.

## Reproduce on a graph copy

From `cse6242_project(frontend)/webpage`, point `NEO4J_*` environment variables at an isolated Neo4j 5.15 copy and run from a clean working directory so an old `.embedding_progress.json` is not reused:

```bash
python scripts/generate_embeddings.py --limit 1000 --subfield 'Artificial Intelligence' --from-year 2019 --to-year 2026 --batch-size 32
```

Then create the vector index in Neo4j:

```cypher
CREATE VECTOR INDEX paper_embedding_idx IF NOT EXISTS
FOR (p:Paper) ON p.embedding
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};
```

Run `python scripts/graph_readiness.py --topic 'reinforcement learning'`. Before expanding to all papers, evaluate a small set of student outreach questions against expected publications and faculty, and inspect research trajectory and collaboration evidence for each candidate.
