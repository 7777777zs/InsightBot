import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.agent import InsightAgent, _sql_from_tool_message
from app.config import Settings
from app.main import create_app, build_components
from app.memory import InMemoryChatHistory
from app.sql_guard import enforce_readonly, UnsafeQueryError
from app.tools import build_tools
from tests.conftest import ScriptedChatModel


def test_bounded_result_preserves_artifact(db):
    tool = {t.name: t for t in build_tools(db)}['run_sql_query']
    sql = "SELECT '" + 'x' * 10000 + "' AS value"
    result = tool.invoke({'name': 'run_sql_query', 'args': {'query': sql}, 'id': 'large', 'type': 'tool_call'})
    assert len(result.content) <= 8000
    assert json.loads(result.content)['truncated']
    assert _sql_from_tool_message(result) == sql + ' LIMIT 50'


def test_validation_and_schema_failure_are_recoverable(db, monkeypatch):
    tools = {t.name: t for t in build_tools(db)}
    assert tools['aggregate'].invoke({'table': 'orders', 'agg': 'drop'}).startswith('ERROR')
    assert tools['aggregate'].invoke({'table': 'orders', 'agg': 'count', 'limit': -1}).startswith('ERROR')
    def broken():
        raise OperationalError('query', {}, Exception('private credentials'))
    monkeypatch.setattr(db, 'table_names', broken)
    assert tools['list_tables'].invoke({}).startswith('ERROR')


@pytest.mark.parametrize('limit', ['LIMIT ALL', 'LIMIT -1', 'FETCH FIRST 2 ROWS WITH TIES', 'LIMIT 9999'])
def test_limit_variants(limit):
    assert enforce_readonly('SELECT * FROM orders ' + limit, max_rows=10).endswith('LIMIT 10')


@pytest.mark.parametrize('sql', ['SELECT pg_catalog.pg_sleep(1)', 'SELECT nextval(\'s\')', 'SELECT pg_advisory_lock(1)'])
def test_side_effect_functions(sql):
    with pytest.raises(UnsafeQueryError):
        enforce_readonly(sql)


def test_settings_and_pair_window():
    for config in [{'max_rows': 0}, {'agent_max_steps': -1}, {'history_max_messages': 3}]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **config)
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        build_components(Settings(_env_file=None, openai_api_key=' '))
    with pytest.raises(ValueError):
        InMemoryChatHistory(3)
    async def scenario():
        history = InMemoryChatHistory(2)
        with pytest.raises(ValueError):
            await history.append('a', [HumanMessage('orphan')])
        for value in ['first', 'second']:
            await history.append('a', [HumanMessage(value), AIMessage(value)])
        assert [m.content for m in await history.get('a')] == ['second', 'second']
        assert await history.get('b') == []
    asyncio.run(scenario())


def test_retry_and_context(db):
    history = InMemoryChatHistory()
    async def scenario():
        await history.append('a', [HumanMessage('Edmonton revenue?'), AIMessage('CAD 100')])
        model = ScriptedChatModel(messages=iter([
            AIMessage(content='', tool_calls=[{'name': 'aggregate', 'args': {'table': 'orders', 'agg': 'invalid'}, 'id': 'bad'}]),
            AIMessage(content='', tool_calls=[{'name': 'run_sql_query', 'args': {'query': 'SELECT COUNT(*) FROM orders'}, 'id': 'good'}]),
            AIMessage(content='There are 4 orders.')]))
        agent = InsightAgent(model, build_tools(db), history)
        inputs = await agent._inputs('a', 'And Calgary?')
        assert [m.content for m in inputs['messages']] == ['Edmonton revenue?', 'CAD 100', 'And Calgary?']
        result = await agent.ask('a', 'And Calgary?')
        assert result['sql'] == ['SELECT COUNT(*) FROM orders LIMIT 50']
        assert len(await history.get('a')) == 4
    asyncio.run(scenario())


def test_api_failures_and_readiness(db):
    class BrokenAgent:
        async def ask(self, *args):
            raise GraphRecursionError('private internals')
        async def stream(self, *args):
            yield {'type': 'token', 'content': 'Partial'}
            raise GraphRecursionError('private internals')
    with TestClient(create_app((db, InMemoryChatHistory(), BrokenAgent()))) as client:
        assert client.get('/').status_code == 200
        assert client.get('/ready').status_code == 200
        assert client.post('/chat', json={'question': '  '}).status_code == 422
        response = client.post('/chat', json={'question': 'hello'})
        assert response.status_code == 422 and 'private' not in response.text
        events = [json.loads(line[6:]) for line in client.post('/chat/stream', json={'question': 'hello'}).text.splitlines() if line.startswith('data: ')]
        assert [e['type'] for e in events] == ['session', 'token', 'error']
        assert client.get('/sessions/' + events[0]['session_id'] + '/history').status_code == 404


def test_shutdown_and_unavailable_dependency(db):
    class History(InMemoryChatHistory):
        closed = False
        async def ping(self):
            raise ConnectionError('secret')
        async def close(self):
            self.closed = True
    history = History()
    with TestClient(create_app((db, history, None))) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 503
    assert history.closed


def test_cancellation_does_not_save_history(db):
    async def scenario():
        history = InMemoryChatHistory()
        model = ScriptedChatModel(messages=iter([AIMessage(content='An incomplete answer')]))
        agent = InsightAgent(model, build_tools(db), history)
        stream = agent.stream('cancelled', 'question')
        await anext(stream)
        await stream.aclose()
        assert await history.get('cancelled') == []
    asyncio.run(scenario())


def test_agent_step_limit_keeps_history_empty(db):
    async def scenario():
        history = InMemoryChatHistory()
        agent = InsightAgent(ScriptedChatModel(messages=iter([AIMessage(content='', tool_calls=[{'name': 'run_sql_query', 'args': {'query': 'SELECT 1'}, 'id': f'loop-{i}'}]) for i in range(10)])), build_tools(db), history, max_steps=1)
        with pytest.raises(GraphRecursionError):
            await agent.ask('loop', 'question')
        assert await history.get('loop') == []
    asyncio.run(scenario())


def test_model_receives_previous_conversation(db):
    from pydantic import Field
    class RecordingModel(ScriptedChatModel):
        observed: list = Field(default_factory=list)
        def _generate(self, messages, *args, **kwargs):
            self.observed.append(messages)
            return super()._generate(messages, *args, **kwargs)
    async def scenario():
        history = InMemoryChatHistory()
        await history.append('cities', [HumanMessage('Edmonton?'), AIMessage('CAD 100')])
        model = RecordingModel(messages=iter([AIMessage(content='Calgary is CAD 200.')]))
        await InsightAgent(model, build_tools(db), history).ask('cities', 'And Calgary?')
        assert [m.content for m in model.observed[0]][-3:] == ['Edmonton?', 'CAD 100', 'And Calgary?']
    asyncio.run(scenario())


def test_evaluation_cannot_accept_unreviewed_or_failed_followups():
    from evaluation.run import summary
    rows = [{'language': lang, 'id': i, 'pass': None, 'request_accepted_seconds': .1}
            for lang in ('en', 'zh') for i in range(1, 11)]
    assert not summary(rows)['en']['accepted']
    for row in rows:
        row['pass'] = True
    assert summary(rows)['en']['accepted']
    rows[8]['pass'] = False
    assert not summary(rows)['en']['accepted']


def test_schema_output_is_bounded_json(db):
    describe = {tool.name: tool for tool in build_tools(db)}['describe_tables']
    result = describe.invoke({'table_names': ['orders'] * 100})
    assert len(result) <= 8000
    records = [json.loads(line) for line in result.splitlines()]
    assert records[-1]['truncated']


def test_provider_and_redis_errors_are_controlled(db):
    import httpx
    from openai import APIConnectionError
    from redis.exceptions import ConnectionError as RedisConnectionError

    class BrokenAgent:
        error = None

        async def ask(self, *args):
            raise self.error

    agent = BrokenAgent()
    with TestClient(create_app((db, InMemoryChatHistory(), agent))) as client:
        for error, status in [
            (APIConnectionError(request=httpx.Request('POST', 'https://example.invalid')), 502),
            (RedisConnectionError('private connection information'), 503),
        ]:
            agent.error = error
            response = client.post('/chat', json={'question': 'hello'})
            assert response.status_code == status
            assert 'private' not in response.text
