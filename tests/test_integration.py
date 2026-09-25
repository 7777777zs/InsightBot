"""Opt-in integration checks; use dedicated acceptance services only."""
import asyncio
import os
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import Database
from app.memory import RedisChatHistory
from app.tools import build_tools

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to the seeded insight_reader database')
    db = Database(url, statement_timeout_ms=50)
    yield db
    db.engine.dispose()


def test_postgres_seed_and_queries(postgres):
    assert postgres.table_names() == ['customers', 'order_items', 'orders', 'products']
    assert postgres.run_select('SELECT COUNT(*) FROM customers').rows == [[500]]
    assert postgres.run_select('SELECT COUNT(*) FROM orders').rows == [[3000]]
    assert postgres.describe('orders').comment
    result = postgres.run_select("SELECT SUM(i.quantity*i.unit_price) FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.status='completed'")
    assert result.rows[0][0] > 0
    tool = {t.name: t for t in build_tools(postgres)}['aggregate']
    assert 'ERROR' not in tool.invoke({'table': 'orders', 'agg': 'count', 'time_column': 'order_date', 'time_grain': 'month'})


@pytest.mark.parametrize('statement', [
    'DELETE FROM orders WHERE false', 'UPDATE orders SET status=status WHERE false',
    'CREATE TABLE forbidden_probe (id int)', 'SELECT * FROM orders FOR UPDATE',
    'WITH d AS (DELETE FROM orders WHERE false RETURNING *) SELECT * FROM d'])
def test_reader_blocks_writes_without_guard(postgres, statement):
    # Always roll back, even if a misconfigured role unexpectedly permits a write.
    with postgres.engine.connect() as conn:
        try:
            with pytest.raises(DBAPIError):
                conn.exec_driver_sql(statement)
        finally:
            conn.rollback()


def test_reader_privileges_and_timeout(postgres):
    with postgres.engine.connect() as conn:
        assert conn.execute(text('SELECT current_user')).scalar() == 'insight_reader'
        assert conn.execute(text("SELECT has_table_privilege(current_user, 'orders', 'INSERT,UPDATE,DELETE')")).scalar() is False
    with pytest.raises(DBAPIError):
        postgres.run_select('SELECT SUM(g) FROM generate_series(1, 1000000000) AS g')
    assert postgres.run_select('SELECT 1').rows == [[1]]


def test_redis_lifecycle():
    url = os.getenv('TEST_REDIS_URL')
    if not url:
        pytest.skip('Set TEST_REDIS_URL for Redis integration')
    import redis.asyncio as redis
    async def scenario():
        prefix = 'insightbot:test:' + uuid.uuid4().hex + ':'
        client = redis.from_url(url, decode_responses=True)
        history = RedisChatHistory(client, max_messages=2, ttl_seconds=2, prefix=prefix)
        try:
            pair = [HumanMessage('question'), AIMessage('answer')]
            await history.append('a', pair)
            await asyncio.sleep(1.1)
            await history.append('a', pair)
            assert await client.ttl(prefix+'a') > 0
            assert len(await history.get('a')) == 2
            assert await history.get('b') == []
            await history.close()
            client = redis.from_url(url, decode_responses=True)
            history = RedisChatHistory(client, 2, 2, prefix)
            assert len(await history.get('a')) == 2
            await history.clear('a')
            assert await history.get('a') == []
            await history.append('a', pair)
            await asyncio.sleep(2.2)
            assert await history.get('a') == []
        finally:
            await client.delete(prefix+'a')
            await history.close()
    asyncio.run(scenario())
