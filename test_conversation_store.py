"""Checks for the Neo4j-backed session implementation without a live database."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MEMORY_FILE = Path(__file__).parent / "cse6242_project(frontend)" / "webpage" / "tools" / "_deprecated_memory.py"


class Connector:
    def __init__(self):
        self.queries = []
        self.fail = False

    def execute_query(self, query, params=None):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.queries.append((query, params))
        if "session_exists" in query:
            return [{"session_exists": 1}]
        return [{"is_new": True}]


class ConversationStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connector = Connector()
        stub = types.ModuleType("tools.neo4j_connector")
        stub.neo4j_connector = cls.connector
        cls.module_patch = patch.dict(sys.modules, {"tools.neo4j_connector": stub})
        cls.module_patch.start()
        spec = importlib.util.spec_from_file_location("conversation_store_test", MEMORY_FILE)
        cls.memory = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.memory
        spec.loader.exec_module(cls.memory)

    @classmethod
    def tearDownClass(cls):
        cls.module_patch.stop()
        sys.modules.pop("conversation_store_test", None)

    def setUp(self):
        self.connector.queries.clear()
        self.connector.fail = False

    def test_each_session_has_a_unique_id(self):
        first = self.memory.create_session()
        second = self.memory.create_session()
        self.assertNotEqual(first, second)
        created = [params["session_id"] for query, params in self.connector.queries
                   if "CREATE (s:Session" in query]
        self.assertEqual(created, [first, second])

    def test_database_failure_is_not_treated_as_empty_history(self):
        self.connector.fail = True
        with patch.object(self.memory.logger, "exception"), self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.memory.get_conversation_history("session-id")


if __name__ == "__main__":
    unittest.main()
