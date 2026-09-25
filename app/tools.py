"""Custom LangChain tools the agent uses to explore and query the database.

Every tool returns a string (what the LLM sees). Errors are returned as text
rather than raised, so the agent can read the message and correct itself.
"""

import json
from typing import Literal, get_args

from langchain_core.tools import BaseTool, tool
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp

from app.db import Database
from app.sql_guard import UnsafeQueryError

MAX_TOOL_OUTPUT_CHARS = 8000

AggFunc = Literal["count", "count_distinct", "sum", "avg", "min", "max"]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]


def _clip(s: str) -> str:
    if len(s) <= MAX_TOOL_OUTPUT_CHARS:
        return s
    return s[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated, refine the query to return fewer rows]"


def _query_output(result) -> tuple[str, dict]:
    payload = result.to_dict()
    payload["returned_row_count"] = len(payload["rows"])
    payload["rows"] = list(payload["rows"])
    def encoded():
        return json.dumps(payload, default=str)
    while len(encoded()) > MAX_TOOL_OUTPUT_CHARS and payload["rows"]:
        payload["rows"].pop()
        payload["truncated"] = True
    payload["row_count"] = len(payload["rows"])
    if len(encoded()) > MAX_TOOL_OUTPUT_CHARS:
        payload = {"rows": [], "row_count": 0, "truncated": True,
                   "notice": "Query metadata exceeds output budget; simplify the query."}
    return json.dumps(payload, default=str), {"sql": result.sql}


def _ident(name: str, dialect: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect=dialect)


def build_aggregate_sql(
    db: Database,
    table: str,
    agg: AggFunc,
    column: str | None = None,
    group_by: list[str] | None = None,
    time_column: str | None = None,
    time_grain: TimeGrain | None = None,
    limit: int = 50,
) -> str:
    """Build a single-table GROUP BY query from structured arguments.

    All identifiers are checked against the live schema, so the LLM cannot
    inject arbitrary SQL through this tool.
    """
    if agg not in get_args(AggFunc) or (time_grain is not None and time_grain not in get_args(TimeGrain)):
        raise ValueError("Unsupported aggregate or time grain")
    if limit < 1:
        raise ValueError("limit must be positive")
    if table not in db.table_names():
        raise ValueError(f"Unknown table '{table}'. Available: {', '.join(db.table_names())}")
    valid = set(db.columns(table))
    group_by = group_by or []
    for col in [column, time_column, *group_by]:
        if col is not None and col not in valid:
            raise ValueError(f"Unknown column '{col}' in table '{table}'. Columns: {', '.join(sorted(valid))}")
    if agg != "count" and column is None:
        raise ValueError(f"'{agg}' needs a column.")
    if (time_column is None) != (time_grain is None):
        raise ValueError("time_column and time_grain must be given together.")

    q = lambda name: _ident(name, db.dialect)  # noqa: E731
    if agg == "count":
        metric = f"COUNT({q(column)})" if column else "COUNT(*)"
    elif agg == "count_distinct":
        metric = f"COUNT(DISTINCT {q(column)})"
    else:
        metric = f"{agg.upper()}({q(column)})"

    select, keys = [], []
    if time_column:
        select.append(f"DATE_TRUNC('{time_grain}', {q(time_column)}) AS period")
        keys.append("period")
    for col in group_by:
        select.append(q(col))
        keys.append(q(col))
    select.append(f"{metric} AS value")

    sql = f"SELECT {', '.join(select)} FROM {q(table)}"
    if keys:
        sql += f" GROUP BY {', '.join(keys)}"
        sql += " ORDER BY period" if time_column else " ORDER BY value DESC"
    return sql + f" LIMIT {int(limit)}"


def build_tools(db: Database) -> list[BaseTool]:
    @tool
    def list_tables() -> str:
        """List every table in the database with its columns. Call this first to see what data exists."""
        try:
            return _clip("\n".join(f"{t}({', '.join(db.columns(t))})" for t in db.table_names()))
        except SQLAlchemyError:
            return "ERROR (database): Schema unavailable; retry later."

    @tool
    def describe_tables(table_names: list[str]) -> str:
        """Show column types, descriptions and a few sample rows for the given tables.
        Use it before writing SQL so you know exact column names and value formats."""
        out = []
        notice = json.dumps({"truncated": True, "notice": "Request remaining tables separately."})
        for name in table_names:
            try:
                info = db.describe(name)
                data = {"table": info.name, "comment": info.comment,
                        "columns": info.columns, "sample_rows": info.sample_rows}
                encoded = json.dumps(data, default=str)
                if len(encoded) > MAX_TOOL_OUTPUT_CHARS - len(notice) - 2:
                    data.update(sample_rows=[], truncated=True)
                    encoded = json.dumps(data, default=str)
                if len(encoded) > MAX_TOOL_OUTPUT_CHARS - len(notice) - 2:
                    encoded = json.dumps({"error": "ERROR: Schema exceeds output budget; use SQL to inspect fewer columns."})
            except ValueError:
                encoded = json.dumps({"error": "ERROR: Unknown table; call list_tables for valid names."})
            except SQLAlchemyError:
                encoded = json.dumps({"error": "ERROR: Schema unavailable; retry later."})
            if sum(len(item) + 1 for item in out) + len(encoded) + len(notice) + 1 > MAX_TOOL_OUTPUT_CHARS:
                out.append(notice)
                break
            out.append(encoded)
        return "\n".join(out) or "ERROR: Provide at least one table name."

    @tool(response_format="content_and_artifact")
    def run_sql_query(query: str) -> tuple[str, dict]:
        """Execute ONE read-only PostgreSQL SELECT statement and return the result as JSON.
        Use it for joins, filters and anything `aggregate` cannot express.
        If it returns an ERROR, read the message, fix the SQL and try again."""
        try:
            return _query_output(db.run_select(query))
        except UnsafeQueryError as e:
            return f"ERROR (query rejected): {e}", {}
        except SQLAlchemyError as e:
            return f"ERROR (database): {str(getattr(e, 'orig', e))[:1000]}", {}

    @tool(response_format="content_and_artifact")
    def aggregate(
        table: str,
        agg: AggFunc,
        column: str | None = None,
        group_by: list[str] | None = None,
        time_column: str | None = None,
        time_grain: TimeGrain | None = None,
        limit: int = 50,
    ) -> tuple[str, dict]:
        """Quick aggregation on a SINGLE table without writing SQL, e.g. count of rows per status,
        or monthly totals (time_column='order_date', time_grain='month').
        For anything spanning several tables use run_sql_query instead."""
        try:
            sql = build_aggregate_sql(db, table, agg, column, group_by, time_column, time_grain, limit)
            return _query_output(db.run_select(sql))
        except (ValueError, UnsafeQueryError) as e:
            return f"ERROR: {e}", {}
        except SQLAlchemyError as e:
            return f"ERROR (database): {str(getattr(e, 'orig', e))[:1000]}", {}

    tools = [list_tables, describe_tables, run_sql_query, aggregate]
    for item in tools:
        item.handle_validation_error = "ERROR: Invalid tool arguments. Check the tool schema and retry."
    return tools
