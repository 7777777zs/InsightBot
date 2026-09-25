"""End-to-end: HTTP request -> FastAPI -> LangChain agent -> tools -> SQLite, with a scripted LLM."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent import InsightAgent
from app.main import create_app
from app.memory import InMemoryChatHistory
from app.tools import build_tools
from tests.conftest import ScriptedChatModel

SQL = "SELECT COUNT(*) AS n FROM orders WHERE status = 'completed'"


def scripted_turn():
    """The LLM first calls run_sql_query, then answers using the result."""
    return [
        AIMessage(content="", tool_calls=[
            {"name": "run_sql_query", "args": {"query": SQL}, "id": "call_1", "type": "tool_call"}]),
        AIMessage(content="There are 3 completed orders."),
    ]


@pytest.fixture
def client(db):
    history = InMemoryChatHistory()
    model = ScriptedChatModel(messages=iter(scripted_turn() * 2))
    agent = InsightAgent(model, build_tools(db), history)
    with TestClient(create_app((db, history, agent))) as c:
        yield c


def test_chat_runs_tool_and_remembers(client):
    r = client.post("/chat", json={"question": "How many completed orders?", "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "There are 3 completed orders."
    assert body["sql"] == [SQL + " LIMIT 50"]

    history = client.get("/sessions/s1/history").json()
    assert [m["role"] for m in history] == ["human", "ai"]

    client.post("/chat", json={"question": "And refunded?", "session_id": "s1"})
    assert len(client.get("/sessions/s1/history").json()) == 4

    assert client.delete("/sessions/s1").status_code == 204
    assert client.get("/sessions/s1/history").status_code == 404


def test_chat_stream_emits_events(client):
    with client.stream("POST", "/chat/stream", json={"question": "How many completed orders?"}) as r:
        events = [json.loads(line[len("data: "):]) for line in r.iter_lines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "session"
    assert "tool_call" in types and "tool_result" in types and "token" in types
    assert events[-1] == {"type": "done", "answer": "There are 3 completed orders.", "sql": [SQL + " LIMIT 50"]}
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == "There are 3 completed orders."


def test_schema_endpoint(client):
    assert client.get("/schema").json() == {"orders": ["id", "status", "amount", "order_date"]}
