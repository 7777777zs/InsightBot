"""Static safety checks for LLM-generated SQL.

The database role is already read-only; this is the second layer of defence.
We parse the SQL into an AST (sqlglot) instead of using regexes, so tricks like
comments, casing or data-modifying CTEs cannot slip through.
"""

import sqlglot
from sqlglot import exp


class UnsafeQueryError(ValueError):
    """Raised when a query is not a single, read-only SELECT."""


FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create,
    exp.Alter, exp.TruncateTable, exp.Grant, exp.Copy, exp.Command,
    exp.Into,  # SELECT ... INTO new_table
    exp.Lock,  # SELECT ... FOR UPDATE
)

# Functions that can sleep, touch the filesystem or affect other sessions.
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "dblink", "dblink_exec",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "set_config",
    "pg_advisory_lock", "pg_advisory_lock_shared", "pg_try_advisory_lock",
    "pg_try_advisory_lock_shared", "pg_advisory_xact_lock", "pg_advisory_xact_lock_shared",
    "nextval", "setval", "pg_notify",
}


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def enforce_readonly(sql: str, dialect: str = "postgres", max_rows: int = 200) -> str:
    """Validate `sql` and return a normalized version with a LIMIT of at most `max_rows`."""
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeQueryError(f"Could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise UnsafeQueryError("Exactly one SQL statement is allowed.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise UnsafeQueryError("Only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeQueryError(f"Forbidden SQL construct: {type(node).__name__}.")
        if isinstance(node, exp.Func) and _function_name(node) in FORBIDDEN_FUNCTIONS:
            raise UnsafeQueryError(f"Forbidden function: {_function_name(node)}.")

    return _cap_limit(stmt, max_rows).sql(dialect=dialect)


def _cap_limit(stmt: exp.Query, max_rows: int) -> exp.Query:
    limit = stmt.args.get("limit")
    if limit is None:
        return stmt.limit(max_rows)
    value = limit.expression
    if isinstance(limit, exp.Limit) and not limit.args.get("limit_options") and isinstance(value, exp.Literal) and value.is_int and 0 <= int(value.this) <= max_rows:
        return stmt
    return stmt.limit(max_rows)
