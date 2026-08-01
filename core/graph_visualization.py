"""面向 GPU 图谱前端的稳定快照、分页和局部子图读取。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator, Literal, Sequence

from .db import DatabasePaths, connect_graph


GRAPH_VISUALIZATION_FORMAT_VERSION = "1"
_SQLITE_PARAMETER_CHUNK = 400

GraphDirection = Literal["forward", "backward", "both"]


class GraphNodeNotFoundError(LookupError):
    """局部图谱的锚点节点不存在。"""


class GraphRevisionChangedError(RuntimeError):
    """客户端请求的图谱 revision 已经过期。"""

    def __init__(self, expected: str, current: str) -> None:
        self.expected = expected
        self.current = current
        super().__init__(
            f"图谱已发生变化，请重新加载：expected={expected}, current={current}"
        )


_REVISION_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "nodes",
        """
        SELECT node_id, keyword, summary, aliases, tags, weight, ref_count,
               created_at, updated_at
        FROM nodes ORDER BY node_id
        """,
    ),
    (
        "node_sources",
        """
        SELECT node_id, source_id, content_hash, evidence_weight, evidence
        FROM node_sources ORDER BY node_id, source_id
        """,
    ),
    (
        "edges",
        """
        SELECT edge_id, source_node_id, relation, target_node_id,
               weight, support_count, created_at
        FROM edges ORDER BY edge_id
        """,
    ),
    (
        "edge_sources",
        """
        SELECT edge_id, source_id, content_hash, evidence_weight
        FROM edge_sources ORDER BY edge_id, source_id
        """,
    ),
    (
        "groups",
        """
        SELECT group_id, summary, node_count, edge_count, created_at, updated_at
        FROM groups ORDER BY group_id
        """,
    ),
    (
        "group_nodes",
        """
        SELECT group_id, node_id FROM group_nodes ORDER BY group_id, node_id
        """,
    ),
)


def compute_graph_revision(connection: sqlite3.Connection) -> str:
    """为所有会影响可视化结果的 Graph 权威数据生成内容指纹。"""

    digest = hashlib.sha256()
    _feed_digest(digest, f"graph-visualization:{GRAPH_VISUALIZATION_FORMAT_VERSION}")
    for label, query in _REVISION_QUERIES:
        _feed_digest(digest, label)
        cursor = connection.execute(query)
        for row in cursor:
            _feed_digest(
                digest,
                json.dumps(
                    list(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
    return digest.hexdigest()


def get_visualization_meta(paths: DatabasePaths) -> dict[str, Any]:
    """返回加载可视化分页前所需的 revision、数量和群组摘要。"""

    with _graph_snapshot(paths) as connection:
        revision = compute_graph_revision(connection)
        counts = connection.execute(
            """
            SELECT (SELECT COUNT(*) FROM nodes) AS node_count,
                   (SELECT COUNT(*) FROM edges) AS edge_count,
                   (SELECT COUNT(*) FROM groups) AS group_count
            """
        ).fetchone()
        groups = _group_meta(connection)
    return {
        "revision": revision,
        "node_count": int(counts["node_count"]),
        "edge_count": int(counts["edge_count"]),
        "group_count": int(counts["group_count"]),
        "groups": groups,
    }


def list_visualization_nodes(
    paths: DatabasePaths,
    *,
    page: int = 1,
    page_size: int = 1000,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """稳定分页读取节点，附带来源 ID 和真实群组 ID。"""

    page, page_size = _validate_pagination(page, page_size, max_page_size=5000)
    with _graph_snapshot(paths) as connection:
        revision = compute_graph_revision(connection)
        _require_revision(expected_revision, revision)
        total = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        rows = connection.execute(
            """
            SELECT node_id, keyword, summary, aliases, tags, weight, ref_count,
                   created_at, updated_at
            FROM nodes
            ORDER BY keyword COLLATE NOCASE, node_id
            LIMIT ? OFFSET ?
            """,
            (page_size, (page - 1) * page_size),
        ).fetchall()
        nodes = _node_payloads(connection, rows)
    return {
        "revision": revision,
        "nodes": nodes,
        "pagination": _pagination(page, page_size, total),
    }


def list_visualization_edges(
    paths: DatabasePaths,
    *,
    page: int = 1,
    page_size: int = 2000,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """独立分页读取关系，避免节点翻页时重复发送全部关系。"""

    page, page_size = _validate_pagination(page, page_size, max_page_size=10000)
    with _graph_snapshot(paths) as connection:
        revision = compute_graph_revision(connection)
        _require_revision(expected_revision, revision)
        total = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
        rows = connection.execute(
            """
            SELECT edge_id, source_node_id, relation, target_node_id,
                   weight, support_count, created_at
            FROM edges ORDER BY edge_id
            LIMIT ? OFFSET ?
            """,
            (page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "revision": revision,
        "edges": [_edge_payload(row) for row in rows],
        "pagination": _pagination(page, page_size, total),
    }


def get_neighborhood(
    paths: DatabasePaths,
    node_id: str,
    *,
    depth: int = 2,
    direction: GraphDirection = "both",
    limit: int = 2000,
    edge_limit: int = 10000,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """按节点 ID 执行确定性 BFS；不调用实体抽取或任何模型。"""

    node_id = _required_text(node_id, "node_id")
    depth = _validate_integer(depth, "depth", minimum=1, maximum=10)
    limit = _validate_integer(limit, "limit", minimum=1, maximum=10000)
    edge_limit = _validate_integer(
        edge_limit,
        "edge_limit",
        minimum=1,
        maximum=50000,
    )
    if direction not in {"forward", "backward", "both"}:
        raise ValueError("direction 必须是 forward、backward 或 both")

    with _graph_snapshot(paths) as connection:
        revision = compute_graph_revision(connection)
        _require_revision(expected_revision, revision)
        if connection.execute(
            "SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone() is None:
            raise GraphNodeNotFoundError(f"节点不存在：{node_id}")

        edge_rows = connection.execute(
            """
            SELECT edge_id, source_node_id, relation, target_node_id,
                   weight, support_count, created_at
            FROM edges ORDER BY edge_id
            """
        ).fetchall()
        adjacency = _build_adjacency(edge_rows, direction)
        depth_by_id, truncated = _bounded_bfs(
            adjacency,
            node_id,
            depth=depth,
            limit=limit,
        )
        ordered_ids = sorted(depth_by_id, key=lambda item: (depth_by_id[item], item))
        node_rows = _node_rows_by_ids(connection, ordered_ids)
        nodes = _node_payloads(connection, node_rows, depth_by_id=depth_by_id)

        visible = set(depth_by_id)
        selected_edges: list[dict[str, Any]] = []
        edges_truncated = False
        for row in edge_rows:
            if (
                row["source_node_id"] not in visible
                or row["target_node_id"] not in visible
            ):
                continue
            if len(selected_edges) >= edge_limit:
                edges_truncated = True
                break
            selected_edges.append(_edge_payload(row))
        groups = _groups_for_nodes(connection, nodes)

    return {
        "revision": revision,
        "anchor_node_id": node_id,
        "depth": depth,
        "direction": direction,
        "node_limit": limit,
        "edge_limit": edge_limit,
        "truncated": truncated,
        "edges_truncated": edges_truncated,
        "nodes": nodes,
        "edges": selected_edges,
        "groups": groups,
    }


@contextmanager
def _graph_snapshot(paths: DatabasePaths) -> Iterator[sqlite3.Connection]:
    connection = connect_graph(paths)
    connection.execute("BEGIN")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _feed_digest(digest: Any, value: str | bytes) -> None:
    encoded = value if isinstance(value, bytes) else value.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _validate_pagination(
    page: int,
    page_size: int,
    *,
    max_page_size: int,
) -> tuple[int, int]:
    return (
        _validate_integer(page, "page", minimum=1),
        _validate_integer(
            page_size,
            "page_size",
            minimum=1,
            maximum=max_page_size,
        ),
    )


def _validate_integer(
    value: int,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{name} 必须大于等于 {minimum}")
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    return text


def _require_revision(expected: str | None, current: str) -> None:
    if expected is None:
        return
    normalized = _required_text(expected, "expected_revision").casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("expected_revision 必须是 64 位十六进制字符串")
    if normalized != current:
        raise GraphRevisionChangedError(normalized, current)


def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def _node_payloads(
    connection: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    depth_by_id: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    node_ids = [str(row["node_id"]) for row in rows]
    source_ids = _related_values(
        connection,
        "node_sources",
        "node_id",
        "source_id",
        node_ids,
    )
    group_ids = _related_values(
        connection,
        "group_nodes",
        "node_id",
        "group_id",
        node_ids,
    )
    nodes: list[dict[str, Any]] = []
    for row in rows:
        node_id = str(row["node_id"])
        payload = {
            "node_id": node_id,
            "keyword": row["keyword"],
            "summary": row["summary"],
            "aliases": _decode_string_list(row["aliases"], "aliases"),
            "tags": _decode_string_list(row["tags"], "tags"),
            "weight": float(row["weight"] or 0.0),
            "ref_count": int(row["ref_count"] or 0),
            "source_ids": source_ids.get(node_id, []),
            "group_ids": group_ids.get(node_id, []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if depth_by_id is not None:
            payload["depth"] = depth_by_id[node_id]
        nodes.append(payload)
    return nodes


def _related_values(
    connection: sqlite3.Connection,
    table: str,
    key_column: str,
    value_column: str,
    keys: Sequence[str],
) -> dict[str, list[str]]:
    result = {key: [] for key in keys}
    for chunk in _chunks(keys, _SQLITE_PARAMETER_CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT {key_column}, {value_column} FROM {table}
            WHERE {key_column} IN ({placeholders})
            ORDER BY {key_column}, {value_column}
            """,
            tuple(chunk),
        ).fetchall()
        for row in rows:
            result[str(row[key_column])].append(str(row[value_column]))
    return result


def _decode_string_list(value: Any, name: str) -> list[str]:
    if value in (None, ""):
        return []
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"图谱节点的 {name} 不是合法 JSON 数组") from exc
    if not isinstance(decoded, list) or any(
        not isinstance(item, str) for item in decoded
    ):
        raise ValueError(f"图谱节点的 {name} 不是字符串数组")
    return decoded


def _edge_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "edge_id": row["edge_id"],
        "source_node_id": row["source_node_id"],
        "relation": row["relation"],
        "target_node_id": row["target_node_id"],
        "weight": float(row["weight"] or 0.0),
        "support_count": int(row["support_count"] or 0),
        "created_at": row["created_at"],
    }


def _group_meta(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "group_id": row["group_id"],
            "summary": row["summary"],
            "node_count": int(row["node_count"] or 0),
            "edge_count": int(row["edge_count"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in connection.execute(
            """
            SELECT group_id, summary, node_count, edge_count, created_at, updated_at
            FROM groups ORDER BY group_id
            """
        ).fetchall()
    ]


def _build_adjacency(
    edge_rows: Sequence[sqlite3.Row],
    direction: GraphDirection,
) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for row in edge_rows:
        source = str(row["source_node_id"])
        target = str(row["target_node_id"])
        if direction in {"forward", "both"}:
            adjacency.setdefault(source, set()).add(target)
        if direction in {"backward", "both"}:
            adjacency.setdefault(target, set()).add(source)
    return adjacency


def _bounded_bfs(
    adjacency: dict[str, set[str]],
    anchor_id: str,
    *,
    depth: int,
    limit: int,
) -> tuple[dict[str, int], bool]:
    depth_by_id = {anchor_id: 0}
    pending = deque([anchor_id])
    truncated = False
    while pending:
        current = pending.popleft()
        current_depth = depth_by_id[current]
        if current_depth >= depth:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor in depth_by_id:
                continue
            if len(depth_by_id) >= limit:
                truncated = True
                continue
            depth_by_id[neighbor] = current_depth + 1
            pending.append(neighbor)
    return depth_by_id, truncated


def _node_rows_by_ids(
    connection: sqlite3.Connection,
    node_ids: Sequence[str],
) -> list[sqlite3.Row]:
    rows_by_id: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(node_ids, _SQLITE_PARAMETER_CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT node_id, keyword, summary, aliases, tags, weight, ref_count,
                   created_at, updated_at
            FROM nodes WHERE node_id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        rows_by_id.update((str(row["node_id"]), row) for row in rows)
    missing = [node_id for node_id in node_ids if node_id not in rows_by_id]
    if missing:
        raise ValueError(f"图谱关系引用了不存在的节点：{', '.join(missing[:5])}")
    return [rows_by_id[node_id] for node_id in node_ids]


def _groups_for_nodes(
    connection: sqlite3.Connection,
    nodes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    members: dict[str, list[str]] = {}
    for node in nodes:
        for group_id in node["group_ids"]:
            members.setdefault(group_id, []).append(str(node["node_id"]))
    if not members:
        return []

    rows_by_id: dict[str, sqlite3.Row] = {}
    group_ids = sorted(members)
    for chunk in _chunks(group_ids, _SQLITE_PARAMETER_CHUNK):
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT group_id, summary, node_count, edge_count, created_at, updated_at
            FROM groups WHERE group_id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        rows_by_id.update((str(row["group_id"]), row) for row in rows)

    result: list[dict[str, Any]] = []
    for group_id in group_ids:
        row = rows_by_id.get(group_id)
        if row is None:
            raise ValueError(f"群组成员引用了不存在的群组：{group_id}")
        result.append(
            {
                "group_id": group_id,
                "summary": row["summary"],
                "node_count": int(row["node_count"] or 0),
                "edge_count": int(row["edge_count"] or 0),
                "node_ids": sorted(members[group_id]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return result


def _chunks(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


__all__ = [
    "GRAPH_VISUALIZATION_FORMAT_VERSION",
    "GraphDirection",
    "GraphNodeNotFoundError",
    "GraphRevisionChangedError",
    "compute_graph_revision",
    "get_neighborhood",
    "get_visualization_meta",
    "list_visualization_edges",
    "list_visualization_nodes",
]
