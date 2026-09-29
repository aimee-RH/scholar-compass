# Scholar Compass

**Agentic academic advisor discovery powered by Claude, LangGraph, BGE-M3, and Neo4j GraphRAG.**

Scholar Compass helps students discover, compare, and analyze research advisors through natural-language conversations. It combines multi-turn intent routing, entity disambiguation, semantic retrieval, graph queries, and persistent session context.

## Highlights

- Multi-turn AI agent with intent routing and entity resolution
- GraphRAG using Neo4j and BGE-M3 vector retrieval
- Claude-powered structured routing and Text2Cypher fallback
- Fast-path disambiguation without an LLM call
- Advisor comparison and recommendation tools
- Evaluation scripts, latency benchmarks, and LangSmith tracing

## Engineering decisions

| Challenge | Approach | Reported result |
| --- | --- | --- |
| Repeated LLM calls during routing | Combine intent classification, reference resolution, and entity extraction in one structured call | 3 calls reduced to 1 |
| Slow follow-up disambiguation | Resolve numbered candidate selections directly from session state | 5.4 ms average fast path |
| Weak semantic retrieval | Add the BGE-M3 query instruction before encoding | Top-1 similarity improved from 0.33 to 0.82 in the documented CV query |
| Ambiguous scholars | Present candidates before answering with the wrong author's data | Interactive entity disambiguation |

The documented graph contains **45,009 papers**, **115,627 authors**, and **288,046 authorship relationships**, sourced from [OpenAlex](https://openalex.org/). These figures and performance results are from the project's existing benchmark notes; see the [detailed implementation and evaluation](cse6242_project%28frontend%29/webpage/README.md).

## How it works

```text
User question
  → Claude structured router (intent, references, entities)
  → LangGraph workflow
  → Neo4j factual queries / BGE-M3 vector search / advisor analysis tools
  → Session-aware answer
```

The active application is in [`cse6242_project(frontend)/webpage`](cse6242_project%28frontend%29/webpage/). The separate [`backend/rag.py`](backend/rag.py) is an earlier Flask RAG service, not the LangGraph application described above.

## Public preview on Replit

The [interactive preview](demo_app.py) runs with four clearly labeled **fictional** advisor profiles. It demonstrates interest matching, advisor comparison, and follow-up questions without requiring API keys, a graph database, or the large BGE-M3 model. It does **not** claim to return real advisor recommendations or the full GraphRAG results described above.

Import this repository at [Replit's GitHub import page](https://replit.com/import). The root [`requirements.txt`](requirements.txt) installs only the preview dependencies, and [`.replit`](.replit) runs the preview on port 5001. Click **Run**, then check `/api/health` and try the prompt buttons. Replit's Publishing tool can publish the same app using the configured deployment command.

To run the preview locally: `python -m pip install -r requirements.txt && python demo_app.py`.

For the full GraphRAG app, use the separate [application requirements](cse6242_project%28frontend%29/webpage/requirements.txt), populate a Neo4j instance with the graph data and embeddings, and configure `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, and `ANTHROPIC_API_KEY` as secrets. The full app defaults to CPU embedding (`USE_MPS=false`) and reads `PORT` or `FLASK_PORT` for its HTTP port. Replit's SQL database does not replace Neo4j without a data and query rewrite.

## Run locally

The main application needs Python, a Neo4j database populated with the project data and embeddings, and an Anthropic API key. The database is not bundled in this repository.

```bash
git clone https://github.com/aimee-RH/scholar-compass.git
cd 'scholar-compass/cse6242_project(frontend)/webpage'
python -m pip install -r requirements.txt
cp .env.example .env
# Add your Anthropic key and Neo4j connection settings to .env.
python app.py
```

For the data import scripts and evaluation details, see the [application README](cse6242_project%28frontend%29/webpage/README.md). If you run the earlier `backend/rag.py` service, set its variables from [`backend/.env.example`](backend/.env.example) in your environment. Never commit a populated `.env` file.

## Repository map

- [`cse6242_project(frontend)/webpage`](cse6242_project%28frontend%29/webpage/) — LangGraph app, retrieval and analysis tools, evaluation scripts
- [`backend`](backend/) — earlier Flask RAG service and graph setup files
- [`datapreprocess`](datapreprocess/) — source data filtering and cleaning
- [`final_report.pdf`](final_report.pdf) — project report
