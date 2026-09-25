"""Database access: schema introspection and guarded query execution."""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, inspect, text

from app.sql_guard import enforce_readonly


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "truncated": self.truncated,
        }


@dataclass
class TableInfo:
    name: str
    columns: list[dict]
    comment: str | None = None
    sample_rows: list[dict] = field(default_factory=list)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return value


class Database:
    def __init__(self, url: str, max_rows: int = 200, statement_timeout_ms: int = 5000):
        connect_args = {"connect_timeout": 5} if url.startswith("postgresql") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms
        name = self.engine.dialect.name
        self.dialect = "postgres" if name == "postgresql" else name  # sqlglot naming

    def table_names(self) -> list[str]:
        return sorted(inspect(self.engine).get_table_names())

    def columns(self, table: str) -> list[str]:
        return [c["name"] for c in inspect(self.engine).get_columns(table)]

    def describe(self, table: str, sample_size: int = 3) -> TableInfo:
        insp = inspect(self.engine)
        if table not in insp.get_table_names():
            raise ValueError(f"Unknown table: {table}")
        columns = [
            {"name": c["name"], "type": str(c["type"]), "comment": c.get("comment")}
            for c in insp.get_columns(table)
        ]
        try:
            comment = insp.get_table_comment(table).get("text")
        except NotImplementedError:  # SQLite
            comment = None
        quoted = self.engine.dialect.identifier_preparer.quote(table)
        sample = self.run_select(f"SELECT * FROM {quoted} LIMIT {sample_size}")
        rows = [dict(zip(sample.columns, r)) for r in sample.rows]
        return TableInfo(table, columns, comment, rows)

    def run_select(self, sql: str) -> QueryResult:
        safe_sql = enforce_readonly(sql, dialect=self.dialect, max_rows=self.max_rows)
        with self.engine.connect() as conn:
            if self.dialect == "postgres":
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"))
            # no_parameters: pass SQL through untouched so '%' / ':' in literals are safe.
            result = conn.execution_options(no_parameters=True).exec_driver_sql(safe_sql)
            columns = list(result.keys())
            rows = [[_jsonable(v) for v in row] for row in result.fetchall()]
            conn.rollback()
        return QueryResult(safe_sql, columns, rows, truncated=len(rows) >= self.max_rows)
