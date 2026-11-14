from __future__ import annotations

import sqlite3
from pathlib import Path


from .paths import DatabasePaths

def connect_sources(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.sources_db)


def connect_search_cache(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.search_cache_db)


def connect_graph(paths: DatabasePaths) -> sqlite3.Connection:
    from core.fulltext import load_simple

    connection = _connect(paths.graph_db)
    try:
        load_simple(connection)
    except Exception:
        connection.close()
        raise
    return connection


def connect_rag(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.rag_db)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
