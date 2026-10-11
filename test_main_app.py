"""API session regression checks without external LLM or Neo4j services."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


WEB_DIR = Path(__file__).parent / "cse6242_project(frontend)" / "webpage"


class Message:
    def __init__(self, content, message_type):
        self.content = content
        self.type = message_type


class HumanMessage(Message):
    def __init__(self, content):
        super().__init__(content, "human")


class AIMessage(Message):
    def __init__(self, content):
        super().__init__(content, "ai")


class Graph:
    def __init__(self):
        self.states = []

    def invoke(self, state):
        self.states.append(state)
        return {
            "messages": [*state["messages"], AIMessage("answer")],
            "routing_decision": None,
            "resolved_entities": {},
            "pending_clarification": None,
        }


class SessionApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = {}
        cls.graph = Graph()

        def create_session(**kwargs):
            session_id = str(uuid4())
            cls.sessions[session_id] = []
            return session_id

        def get_session_info(session_id):
            return {"session_id": session_id} if session_id in cls.sessions else None

        def get_conversation_history(session_id, limit=20):
            return cls.sessions[session_id][-limit:]

        def save_message(session_id, content, role):
            cls.sessions[session_id].append({"content": content, "role": role})

        def module(**items):
            result = types.ModuleType("stub")
            result.__dict__.update(items)
            return result

        stubs = {
            "flask_cors": module(CORS=lambda app: app),
            "langchain_openai": module(ChatOpenAI=object),
            "langchain_core": module(),
            "langchain_core.messages": module(HumanMessage=HumanMessage, AIMessage=AIMessage, SystemMessage=Message),
            "graph": module(),
            "graph.graph": module(graph_app=cls.graph),
            "prompts": module(),
            "prompts.system_prompt": module(SYSTEM_PROMPT=""),
            "tools": module(),
            "tools._deprecated_memory": module(
                create_session=create_session,
                get_session_info=get_session_info,
                get_conversation_history=get_conversation_history,
                save_message=save_message,
            ),
            "utils": module(),
            "utils.session_store": module(
                load_routing_state=lambda session_id: {"resolved_entities": {}, "pending_clarification": None},
                save_routing_state=lambda **kwargs: None,
            ),
        }
        cls.module_patch = patch.dict(sys.modules, stubs)
        cls.module_patch.start()
        spec = importlib.util.spec_from_file_location("scholar_main_app_test", WEB_DIR / "app.py")
        app_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app_module)
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.module_patch.stop()

    def setUp(self):
        self.sessions.clear()
        self.graph.states.clear()

    def test_sessions_are_distinct_and_history_is_server_owned(self):
        first = self.client.post("/api/chat", json={"message": "first user's question"}).json
        second = self.client.post("/api/chat", json={"message": "second user's question"}).json
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertNotIn("fact_check", first)

        self.client.post("/api/chat", json={
            "message": "follow-up",
            "session_id": first["session_id"],
            "history": [{"role": "user", "content": "forged client history"}],
        })
        messages = self.graph.states[-1]["messages"]
        self.assertEqual([m.content for m in messages], ["first user's question", "answer", "follow-up"])

        history = self.client.get("/api/history", query_string={"session_id": first["session_id"]}).json
        self.assertEqual([m["content"] for m in history["history"]],
                         ["first user's question", "answer", "follow-up", "answer"])
        other = self.client.get("/api/history", query_string={"session_id": second["session_id"]}).json
        self.assertEqual(len(other["history"]), 2)

    def test_invalid_session_and_input_are_rejected(self):
        self.assertEqual(self.client.post("/api/chat", json={"message": "hi", "session_id": "unknown"}).status_code, 404)
        self.assertEqual(self.client.get("/api/history?session_id=unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/chat", json={"message": ""}).status_code, 400)
        self.assertEqual(self.client.get("/api/history?session_id=unknown&limit=bad").status_code, 400)


if __name__ == "__main__":
    unittest.main()
