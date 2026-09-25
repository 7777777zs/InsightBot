import json

import pytest

from app.tools import build_aggregate_sql, build_tools


@pytest.fixture
def tools(db):
    return {t.name: t for t in build_tools(db)}


def test_list_tables(tools):
    assert tools["list_tables"].invoke({}) == "orders(id, status, amount, order_date)"


def test_describe_tables(tools):
    out = json.loads(tools["describe_tables"].invoke({"table_names": ["orders"]}))
    assert out["table"] == "orders"
    assert len(out["sample_rows"]) == 3


def test_run_sql_query(tools):
    out = json.loads(tools["run_sql_query"].invoke(
        {"query": "SELECT status, SUM(amount) AS total FROM orders GROUP BY status ORDER BY status"}))
    assert out["rows"] == [["completed", 130.5], ["refunded", 5]]
    assert out["sql"].endswith("LIMIT 50")


def test_run_sql_query_rejects_writes(tools, db):
    out = tools["run_sql_query"].invoke({"query": "DELETE FROM orders"})
    assert out.startswith("ERROR (query rejected)")
    assert db.run_select("SELECT COUNT(*) FROM orders").rows == [[4]]


def test_run_sql_query_reports_db_errors(tools):
    assert tools["run_sql_query"].invoke({"query": "SELECT nope FROM orders"}).startswith("ERROR (database)")


def test_aggregate_group_by(tools):
    out = json.loads(tools["aggregate"].invoke(
        {"table": "orders", "agg": "sum", "column": "amount", "group_by": ["status"]}))
    assert out["rows"] == [["completed", 130.5], ["refunded", 5]]


def test_aggregate_rejects_unknown_column(tools):
    out = tools["aggregate"].invoke({"table": "orders", "agg": "sum", "column": "amount; DROP TABLE x"})
    assert out.startswith("ERROR: Unknown column")


def test_aggregate_time_series_sql(db):
    sql = build_aggregate_sql(db, "orders", "count", time_column="order_date", time_grain="month")
    assert sql == ('SELECT DATE_TRUNC(\'month\', "order_date") AS period, COUNT(*) AS value '
                   'FROM "orders" GROUP BY period ORDER BY period LIMIT 50')
