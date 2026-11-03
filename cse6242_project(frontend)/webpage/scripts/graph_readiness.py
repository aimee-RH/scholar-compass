"""Check whether a Neo4j graph can support a real Scholar Compass demo.

Run with the main app's Neo4j environment variables. This script only reads data.
"""

import argparse
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default="machine learning", help="Paper title phrase for an evidence sample")
    args = parser.parse_args()
    load_dotenv()

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not password:
        parser.error("Set NEO4J_PASSWORD before running the audit")

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            counts = session.run(
                "MATCH (p:Paper) RETURN count(p) AS papers, "
                "sum(CASE WHEN 'embedding' IN keys(p) THEN 1 ELSE 0 END) AS embedded, "
                "sum(CASE WHEN 'abstract' IN keys(p) THEN 1 ELSE 0 END) AS abstracts"
            ).single()
            index = session.run(
                "SHOW INDEXES YIELD name, type, state "
                "WHERE name = 'paper_embedding_idx' RETURN type, state"
            ).single()
            samples = session.run(
                "MATCH (a:Author)-[:AUTHORED]->(p:Paper) "
                "WHERE toLower(p.title) CONTAINS toLower($topic) "
                "RETURN p.title AS title, p.publication_year AS year, "
                "collect(DISTINCT a.display_name)[..3] AS authors "
                "LIMIT 3",
                topic=args.topic,
            )
            print(f"Papers: {counts['papers']:,}; embedded: {counts['embedded']:,}; abstracts: {counts['abstracts']:,}")
            print(f"Vector index: {index['state'] if index else 'missing'}")
            print(f"Paper → author evidence for {args.topic!r}:")
            found = False
            for row in samples:
                found = True
                print(f"- {row['title']} ({row['year']}); authors: {', '.join(row['authors'])}")
            if not found:
                print("- No matching paper with an author")
            if not counts["embedded"] or not index or index["state"] != "ONLINE":
                print("Real vector retrieval is not ready in this graph.")
                return 1
    print("Graph has embeddings and an online vector index; test retrieval quality next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
