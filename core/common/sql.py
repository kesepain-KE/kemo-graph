from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Sequence
from uuid import uuid4


def fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, tuple(parameters))
    row = cursor.fetchone()
    return None if row is None else row_dict(cursor, row)


def fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(parameters))
    return [row_dict(cursor, row) for row in cursor.fetchall()]


def row_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    names = [str(column[0]) for column in cursor.description or ()]
    return dict(zip(names, row, strict=True))


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    savepoint = f"provider_tool_{uuid4().hex}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE {savepoint}")
