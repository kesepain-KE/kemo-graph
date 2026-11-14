from __future__ import annotations
import json
import sqlite3
from typing import Any
from core.storage.fulltext import compile_fulltext

_SCHEMA = """
BEGIN IMMEDIATE;
CREATE VIRTUAL TABLE IF NOT EXISTS node_text_fts USING fts5(
    keyword, summary, aliases, tags, content='nodes', content_rowid='rowid',
    tokenize='simple 0'
);
CREATE TRIGGER IF NOT EXISTS node_text_fts_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO node_text_fts(rowid,keyword,summary,aliases,tags)
    VALUES(new.rowid,new.keyword,new.summary,new.aliases,new.tags);
END;
CREATE TRIGGER IF NOT EXISTS node_text_fts_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO node_text_fts(node_text_fts,rowid,keyword,summary,aliases,tags)
    VALUES('delete',old.rowid,old.keyword,old.summary,old.aliases,old.tags);
END;
CREATE TRIGGER IF NOT EXISTS node_text_fts_update
AFTER UPDATE OF keyword,summary,aliases,tags ON nodes BEGIN
    INSERT INTO node_text_fts(node_text_fts,rowid,keyword,summary,aliases,tags)
    VALUES('delete',old.rowid,old.keyword,old.summary,old.aliases,old.tags);
    INSERT INTO node_text_fts(rowid,keyword,summary,aliases,tags)
    VALUES(new.rowid,new.keyword,new.summary,new.aliases,new.tags);
END;
INSERT INTO node_text_fts(node_text_fts) VALUES('rebuild');
COMMIT;
"""

def prepare_node_search(connection: sqlite3.Connection) -> None:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'node_text_fts'"
    ).fetchone() is None:
        # Also backfill existing databases. The write lock keeps the backfill
        # and triggers atomic with respect to ongoing graph mutations.
        connection.executescript(_SCHEMA)

def search_nodes(
    connection: sqlite3.Connection, query: str, *, limit: int = 20, offset: int = 0,
) -> dict[str, Any]:
    query = query.strip()
    expression = compile_fulltext(connection, query)
    result: dict[str, Any] = {
        "query": query, "engine": "sqlite-fts5/simple", "total": 0,
        "limit": limit, "offset": offset, "nodes": [],
    }
    if not expression:
        return result
    connection.execute("BEGIN")
    selection = (
        "FROM node_text_fts JOIN nodes n ON n.rowid = node_text_fts.rowid "
        "WHERE node_text_fts MATCH ?"
    )
    result["total"] = int(connection.execute(
        "SELECT count(*) " + selection, (expression,),
    ).fetchone()[0])
    rows = connection.execute(
        "SELECT n.*,bm25(node_text_fts,4.0,1.0,2.0,1.0) AS rank,"
        "snippet(node_text_fts,-1,'','','…',28) AS snippet "
        + selection + " ORDER BY rank,n.keyword COLLATE NOCASE,n.node_id LIMIT ? OFFSET ?",
        (expression, limit, offset),
    ).fetchall()
    result["nodes"] = [{
        "node_id": row["node_id"], "keyword": row["keyword"],
        "summary": row["summary"], "aliases": json.loads(row["aliases"] or "[]"),
        "tags": json.loads(row["tags"] or "[]"), "ref_count": int(row["ref_count"]),
        "rank": float(row["rank"]), "snippet": row["snippet"],
    } for row in rows]
    return result
