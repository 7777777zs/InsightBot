import pytest

from app.sql_guard import UnsafeQueryError, enforce_readonly


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders VALUES (1)",
    "SELECT 1; DROP TABLE orders",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "SELECT * INTO backup FROM orders",
    "SELECT * FROM orders FOR UPDATE",
    "SELECT pg_sleep(100)",
    "SELECT pg_read_file('/etc/passwd')",
    "GRANT ALL ON orders TO public",
    "not sql at all (",
])
def test_rejects_unsafe_queries(sql):
    with pytest.raises(UnsafeQueryError):
        enforce_readonly(sql)


def test_adds_limit_when_missing():
    assert enforce_readonly("SELECT * FROM orders", max_rows=100).endswith("LIMIT 100")


def test_caps_large_limit():
    assert enforce_readonly("SELECT * FROM orders LIMIT 100000", max_rows=100).endswith("LIMIT 100")


def test_keeps_small_limit():
    assert enforce_readonly("SELECT * FROM orders LIMIT 5", max_rows=100).endswith("LIMIT 5")


def test_allows_joins_ctes_and_unions():
    sql = """WITH t AS (SELECT customer_id, SUM(amount) AS s FROM orders GROUP BY 1)
             SELECT c.name, t.s FROM t JOIN customers c ON c.id = t.customer_id
             UNION ALL SELECT 'x', 1"""
    assert "LIMIT" in enforce_readonly(sql)


def test_percent_and_colons_survive():
    out = enforce_readonly("SELECT '10:30' AS t, name FROM customers WHERE name LIKE 'A%'")
    assert "'A%'" in out and "'10:30'" in out
