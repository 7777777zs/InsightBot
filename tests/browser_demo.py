"""Deterministic browser smoke fixture, never used in production.
Run: python -m tests.browser_demo
"""
import itertools
import uvicorn
from langchain_core.messages import AIMessage
from app.agent import InsightAgent
from app.db import Database
from app.main import create_app
from app.memory import InMemoryChatHistory
from app.tools import build_tools
from tests.conftest import ScriptedChatModel

if __name__ == '__main__':
    db = Database('sqlite://')
    history = InMemoryChatHistory()
    messages = itertools.cycle([
        AIMessage(content='', tool_calls=[{'name': 'run_sql_query', 'args': {'query': 'SELECT 42 AS answer'}, 'id': 'browser'}]),
        AIMessage(content='The answer is 42. <script>unsafe()</script>')])
    agent = InsightAgent(ScriptedChatModel(messages=messages), build_tools(db), history)
    uvicorn.run(create_app((db, history, agent)), host='127.0.0.1', port=8765)
