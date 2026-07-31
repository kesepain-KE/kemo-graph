"""直接操作 graph.db 的图谱构建工具。"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence
from uuid import uuid4


def add_entity(
    conn: sqlite3.Connection,
    keyword: str,
    summary: str,
    aliases: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    source_id: str = "",
    content_hash: str = "",
) -> str:
    """创建独立节点；即使存在同名节点也绝不自动合并。"""

    keyword = _required_text(keyword, "keyword", min_length=2, max_length=20)
    summary = _required_text(summary, "summary")
    source_id = _required_text(source_id, "source_id")
    content_hash = _required_text(content_hash, "content_hash")
    aliases_json = _encode_string_list(aliases, "aliases")
    tags_json = _encode_string_list(tags, "tags")
    node_id = str(uuid4())
    now = _now_iso()

    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO nodes (
                node_id, keyword, summary, aliases, tags, ref_count,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (node_id, keyword, summary, aliases_json, tags_json, now, now),
        )
        conn.execute(
            """
            INSERT INTO node_sources (node_id, source_id, content_hash)
            VALUES (?, ?, ?)
            """,
            (node_id, source_id, content_hash),
        )
    return node_id


def add_relation(
    conn: sqlite3.Connection,
    source_node_id: str,
    relation: str,
    target_node_id: str,
    evidence_weight: float,
    source_id: str,
    content_hash: str,
) -> str:
    """添加关系证据，并按全部来源证据的平均值重算边权重。"""

    source_node_id = _required_text(source_node_id, "source_node_id")
    target_node_id = _required_text(target_node_id, "target_node_id")
    relation = _required_text(relation, "relation", max_length=100)
    source_id = _required_text(source_id, "source_id")
    content_hash = _required_text(content_hash, "content_hash")
    if isinstance(evidence_weight, bool) or not isinstance(evidence_weight, (int, float)):
        raise TypeError("evidence_weight 必须是数值")
    weight = float(evidence_weight)
    if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
        raise ValueError("evidence_weight 必须是 0 到 1 之间的有限数值")

    with _transaction(conn):
        _require_nodes(conn, (source_node_id, target_node_id))
        row = _fetch_one(
            conn,
            """
            SELECT edge_id FROM edges
            WHERE source_node_id = ? AND relation = ? AND target_node_id = ?
            """,
            (source_node_id, relation, target_node_id),
        )
        if row is None:
            edge_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO edges (
                    edge_id, source_node_id, relation, target_node_id,
                    weight, support_count, created_at
                ) VALUES (?, ?, ?, ?, 0, 0, ?)
                """,
                (edge_id, source_node_id, relation, target_node_id, _now_iso()),
            )
        else:
            edge_id = str(row["edge_id"])

        conn.execute(
            """
            INSERT INTO edge_sources (
                edge_id, source_id, content_hash, evidence_weight
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(edge_id, source_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                evidence_weight = excluded.evidence_weight
            """,
            (edge_id, source_id, content_hash, weight),
        )
        _recalculate_edge(conn, edge_id)
    return edge_id


def search_entities(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """精确关键词优先，再按 keyword 和 aliases 做模糊搜索。"""

    query = _required_text(query, "query")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit 必须是整数")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    contains = f"%{_escape_like(query)}%"
    rows = _fetch_all(
        conn,
        """
        SELECT node_id, keyword, summary, aliases, tags, ref_count
        FROM nodes
        WHERE keyword = ? COLLATE NOCASE
           OR keyword LIKE ? ESCAPE '\\' COLLATE NOCASE
           OR COALESCE(aliases, '[]') LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY
            CASE
                WHEN keyword = ? COLLATE NOCASE THEN 0
                WHEN keyword LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 1
                ELSE 2
            END,
            ref_count DESC,
            keyword,
            node_id
        LIMIT ?
        """,
        (query, contains, contains, query, contains, limit),
    )
    return [_node_payload(row) for row in rows]


def get_entity(conn: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    """返回节点详情以及 node_sources 中的普通字典列表。"""

    node_id = _required_text(node_id, "node_id")
    row = _fetch_one(
        conn,
        """
        SELECT node_id, keyword, summary, aliases, tags, ref_count,
               created_at, updated_at
        FROM nodes WHERE node_id = ?
        """,
        (node_id,),
    )
    if row is None:
        raise LookupError(f"节点不存在：{node_id}")
    sources = _fetch_all(
        conn,
        """
        SELECT source_id, content_hash
        FROM node_sources WHERE node_id = ? ORDER BY source_id
        """,
        (node_id,),
    )
    return {**_node_payload(row), "created_at": row["created_at"], "updated_at": row["updated_at"], "sources": sources}


def list_entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """列出全部节点的摘要字段。"""

    return _fetch_all(
        conn,
        """
        SELECT node_id, keyword, summary, ref_count
        FROM nodes ORDER BY keyword, node_id
        """,
    )


def update_entity(
    conn: sqlite3.Connection,
    node_id: str,
    keyword: str | None = None,
    summary: str | None = None,
    aliases: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
) -> dict[str, Any]:
    """仅更新明确传入的节点字段，并返回更新后的完整节点。"""

    node_id = _required_text(node_id, "node_id")
    assignments: list[str] = []
    values: list[Any] = []
    if keyword is not None:
        assignments.append("keyword = ?")
        values.append(_required_text(keyword, "keyword", min_length=2, max_length=20))
    if summary is not None:
        assignments.append("summary = ?")
        values.append(_required_text(summary, "summary"))
    if aliases is not None:
        assignments.append("aliases = ?")
        values.append(_encode_string_list(aliases, "aliases"))
    if tags is not None:
        assignments.append("tags = ?")
        values.append(_encode_string_list(tags, "tags"))
    if not assignments:
        raise ValueError("至少需要提供一个待更新字段")

    assignments.append("updated_at = ?")
    values.append(_now_iso())
    values.append(node_id)
    with _transaction(conn):
        cursor = conn.execute(
            f"UPDATE nodes SET {', '.join(assignments)} WHERE node_id = ?",
            tuple(values),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"节点不存在：{node_id}")
    return get_entity(conn, node_id)


def delete_entity(
    conn: sqlite3.Connection,
    node_id: str,
    source_id: str,
) -> dict[str, Any]:
    """解除一个来源绑定；节点失去全部来源时删除节点和关联边。"""

    node_id = _required_text(node_id, "node_id")
    source_id = _required_text(source_id, "source_id")
    with _transaction(conn):
        if _fetch_one(conn, "SELECT node_id FROM nodes WHERE node_id = ?", (node_id,)) is None:
            raise LookupError(f"节点不存在：{node_id}")
        cursor = conn.execute(
            "DELETE FROM node_sources WHERE node_id = ? AND source_id = ?",
            (node_id, source_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"节点 {node_id} 未绑定来源 {source_id}")

        edge_rows = _fetch_all(
            conn,
            """
            SELECT edge_id FROM edges
            WHERE source_node_id = ? OR target_node_id = ?
            """,
            (node_id, node_id),
        )
        for edge_row in edge_rows:
            edge_id = str(edge_row["edge_id"])
            conn.execute(
                "DELETE FROM edge_sources WHERE edge_id = ? AND source_id = ?",
                (edge_id, source_id),
            )
            _recalculate_edge(conn, edge_id)

        remaining = int(
            conn.execute(
                "SELECT COUNT(*) FROM node_sources WHERE node_id = ?", (node_id,)
            ).fetchone()[0]
        )
        deleted = remaining == 0
        if deleted:
            conn.execute(
                "DELETE FROM edge_sources WHERE edge_id IN ("
                "SELECT edge_id FROM edges WHERE source_node_id = ? OR target_node_id = ?)",
                (node_id, node_id),
            )
            conn.execute(
                "DELETE FROM edges WHERE source_node_id = ? OR target_node_id = ?",
                (node_id, node_id),
            )
            conn.execute("DELETE FROM group_nodes WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        else:
            conn.execute(
                "UPDATE nodes SET ref_count = ?, updated_at = ? WHERE node_id = ?",
                (remaining, _now_iso(), node_id),
            )
        conn.execute("DELETE FROM group_nodes")
        conn.execute("DELETE FROM groups")
    return {"deleted": deleted, "recycled": False}


def _recalculate_edge(conn: sqlite3.Connection, edge_id: str) -> None:
    aggregate = conn.execute(
        """
        SELECT COUNT(*) AS support_count, AVG(evidence_weight) AS weight
        FROM edge_sources WHERE edge_id = ?
        """,
        (edge_id,),
    ).fetchone()
    support_count = int(aggregate[0])
    if support_count == 0:
        conn.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
        return
    conn.execute(
        "UPDATE edges SET weight = ?, support_count = ? WHERE edge_id = ?",
        (float(aggregate[1]), support_count, edge_id),
    )


def _require_nodes(conn: sqlite3.Connection, node_ids: Sequence[str]) -> None:
    placeholders = ",".join("?" for _ in node_ids)
    found = {
        str(row[0])
        for row in conn.execute(
            f"SELECT node_id FROM nodes WHERE node_id IN ({placeholders})",
            tuple(node_ids),
        ).fetchall()
    }
    missing = sorted(set(node_ids) - found)
    if missing:
        raise LookupError(f"关系端点节点不存在：{', '.join(missing)}")


def _node_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": row["node_id"],
        "keyword": row["keyword"],
        "summary": row["summary"],
        "aliases": _decode_string_list(row.get("aliases")),
        "tags": _decode_string_list(row.get("tags")),
        "ref_count": int(row.get("ref_count") or 0),
    }


def _encode_string_list(value: Sequence[str] | None, name: str) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} 必须是字符串数组")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _required_text(item, f"{name} 项")
        if text not in seen:
            seen.add(text)
            result.append(text)
    return json.dumps(result, ensure_ascii=False)


def _decode_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _required_text(
    value: Any,
    name: str,
    *,
    min_length: int = 1,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    text = value.strip()
    if len(text) < min_length:
        raise ValueError(f"{name} 长度不能小于 {min_length}")
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"{name} 长度不能超过 {max_length}")
    return text


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, tuple(parameters))
    row = cursor.fetchone()
    return None if row is None else _row_dict(cursor, row)


def _fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(parameters))
    return [_row_dict(cursor, row) for row in cursor.fetchall()]


def _row_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    names = [str(column[0]) for column in cursor.description or ()]
    return dict(zip(names, row, strict=True))


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "add_entity",
    "add_relation",
    "search_entities",
    "get_entity",
    "list_entities",
    "update_entity",
    "delete_entity",
]
