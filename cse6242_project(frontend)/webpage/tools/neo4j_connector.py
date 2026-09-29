from neo4j import GraphDatabase
from typing import List, Dict, Any
import os

class Neo4jConnector:
    """Neo4j database connection manager"""

    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver = None

    def connect(self):
        """Establish database connection"""
        if not self.uri or not self.password:
            raise RuntimeError("Set NEO4J_URI and NEO4J_PASSWORD in the environment")
        if not self.driver:
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password)
            )
        return self.driver

    def execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict]:
        """Execute Cypher query and return results"""
        driver = self.connect()
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            result = session.run(query, params or {})
            return [record.data() for record in result]

    def close(self):
        """Close connection"""
        if self.driver:
            self.driver.close()

# Global singleton
neo4j_connector = Neo4jConnector()


def get_neo4j_driver():
    """
    获取 Neo4j driver 实例（用于 semantic_search 等需要直接使用 session 的场景）
    """
    return neo4j_connector.connect()
