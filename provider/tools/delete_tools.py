"""知识库删除与删除前查询工具。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
from uuid import uuid4

from core.config import load_config
from core.db import (
    DatabasePaths,
    get_database_paths,
    read_graph_meta,
    write_graph_meta,
)
from core.ingestor import Ingestor
from core.logger import DailyTSVLogger


def search_documents(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """按相对路径和原文件路径搜索 sources 表。"""

    query = _required_text(query, "query")
    pattern = f"%{_escape_like(query)}%"
    data_dir = _try_data_dir(conn)
    with _database_connection(conn, data_dir, "sources") as source_conn:
        return _fetch_all(
            source_conn,
            """
            SELECT source_id, original_path, relative_path, content_hash,
                   graph_status, rag_status, exists_status, created_at, updated_at
            FROM sources
            WHERE relative_path LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR original_path LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY
                CASE WHEN relative_path = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                exists_status,
                relative_path
            """,
            (pattern, pattern, query),
        )


def get_document_nodes(
    conn: sqlite3.Connection,
    source_id: str,
) -> list[dict[str, Any]]:
    """返回某文档绑定的全部节点。"""

    source_id = _required_text(source_id, "source_id")
    data_dir = _try_data_dir(conn)
    with _database_connection(conn, data_dir, "graph") as graph_conn:
        rows = _fetch_all(
            graph_conn,
            """
            SELECT n.node_id, n.keyword, n.summary, n.aliases, n.tags,
                   n.ref_count, ns.content_hash
            FROM node_sources ns
            JOIN nodes n ON n.node_id = ns.node_id
            WHERE ns.source_id = ?
            ORDER BY n.keyword, n.node_id
            """,
            (source_id,),
        )
    for row in rows:
        row["aliases"] = _decode_json_array(row.get("aliases"))
        row["tags"] = _decode_json_array(row.get("tags"))
    return rows


def get_document_relations(
    conn: sqlite3.Connection,
    source_id: str,
) -> list[dict[str, Any]]:
    """返回某文档提供证据的全部关系。"""

    source_id = _required_text(source_id, "source_id")
    data_dir = _try_data_dir(conn)
    with _database_connection(conn, data_dir, "graph") as graph_conn:
        return _fetch_all(
            graph_conn,
            """
            SELECT e.edge_id, e.source_node_id, sn.keyword AS source_keyword,
                   e.relation, e.target_node_id, tn.keyword AS target_keyword,
                   e.weight, e.support_count, es.evidence_weight, es.content_hash
            FROM edge_sources es
            JOIN edges e ON e.edge_id = es.edge_id
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE es.source_id = ?
            ORDER BY sn.keyword, e.relation, tn.keyword, e.edge_id
            """,
            (source_id,),
        )


def delete_node(conn: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    """按 06 文档执行用户主动删节点的跨库级联。"""

    node_id = _required_text(node_id, "node_id")
    data_dir = _require_data_dir(conn)
    paths = get_database_paths(data_dir)
    settings = load_config()
    conn.commit()

    with _open_sqlite(paths.graph_db) as graph_conn:
        node = _fetch_one(
            graph_conn,
            "SELECT node_id, keyword FROM nodes WHERE node_id = ?",
            (node_id,),
        )
        if node is None:
            raise LookupError(f"节点不存在：{node_id}")
        bindings = _fetch_all(
            graph_conn,
            """
            SELECT ns.source_id,
                   (SELECT COUNT(*) FROM node_sources other
                    WHERE other.source_id = ns.source_id
                      AND other.node_id != ns.node_id) AS other_node_count
            FROM node_sources ns WHERE ns.node_id = ?
            ORDER BY ns.source_id
            """,
            (node_id,),
        )

    source_ids = [str(binding["source_id"]) for binding in bindings]
    source_rows = _source_rows(paths, source_ids)
    source_by_id = {str(row["source_id"]): row for row in source_rows}
    isolated = [
        str(binding["source_id"])
        for binding in bindings
        if int(binding["other_node_count"] or 0) == 0
        and source_by_id.get(str(binding["source_id"]), {}).get("exists_status") == "active"
    ]
    unlinked = [
        str(source_by_id[str(binding["source_id"])]["relative_path"])
        for binding in bindings
        if int(binding["other_node_count"] or 0) > 0
        and str(binding["source_id"]) in source_by_id
    ]
    external_dir = _resolve_external_dir(data_dir, source_rows, settings.resolve_external_dir())

    recycled: list[str] = []
    deleted_sources: list[str] = []
    ingestor = Ingestor(
        data_dir=data_dir,
        external_dir=external_dir,
        settings=settings,
    )
    for source_id in isolated:
        result = ingestor.delete_document(source_id)
        deleted_sources.append(source_id)
        recycled_path = result.get("recycled_path")
        if recycled_path:
            recycled.append(str(recycled_path))

    with _open_sqlite(paths.graph_db) as graph_conn:
        with _transaction(graph_conn):
            edge_ids = {
                str(row["edge_id"])
                for row in _fetch_all(
                    graph_conn,
                    """
                    SELECT edge_id FROM edges
                    WHERE source_node_id = ? OR target_node_id = ?
                    """,
                    (node_id, node_id),
                )
            }
            if edge_ids:
                placeholders = ",".join("?" for _ in edge_ids)
                graph_conn.execute(
                    f"DELETE FROM edge_sources WHERE edge_id IN ({placeholders})",
                    tuple(sorted(edge_ids)),
                )
                graph_conn.execute(
                    f"DELETE FROM edges WHERE edge_id IN ({placeholders})",
                    tuple(sorted(edge_ids)),
                )
            graph_conn.execute("DELETE FROM node_sources WHERE node_id = ?", (node_id,))
            graph_conn.execute("DELETE FROM group_nodes WHERE node_id = ?", (node_id,))
            graph_conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            graph_conn.execute("DELETE FROM group_nodes")
            graph_conn.execute("DELETE FROM groups")

    with _open_sqlite(paths.rag_db) as rag_conn:
        with _transaction(rag_conn):
            rag_conn.execute("DELETE FROM chunk_nodes WHERE node_id = ?", (node_id,))

    _refresh_graph_meta(paths)
    result = {
        "deleted": True,
        "deleted_node_id": node_id,
        "keyword": node["keyword"],
        "cascade_deleted_edges": len(edge_ids),
        "deleted_source_ids": deleted_sources,
        "recycled": recycled,
        "unlinked": unlinked,
    }
    _log_delete_action(
        settings,
        "delete_node",
        f"node_id={node_id}, edges={len(edge_ids)}, sources={len(deleted_sources)}",
    )
    return result


def delete_relation(conn: sqlite3.Connection, edge_id: str) -> dict[str, Any]:
    """删除整条关系及所有证据；边不存在时明确报错。"""

    edge_id = _required_text(edge_id, "edge_id")
    data_dir = _try_data_dir(conn)
    with _database_connection(conn, data_dir, "graph") as graph_conn:
        with _transaction(graph_conn):
            row = _fetch_one(
                graph_conn,
                """
                SELECT edge_id, source_node_id, relation, target_node_id,
                       weight, support_count
                FROM edges WHERE edge_id = ?
                """,
                (edge_id,),
            )
            if row is None:
                raise LookupError(f"关系不存在：{edge_id}")
            evidence_count = int(
                graph_conn.execute(
                    "SELECT COUNT(*) FROM edge_sources WHERE edge_id = ?", (edge_id,)
                ).fetchone()[0]
            )
            graph_conn.execute("DELETE FROM edge_sources WHERE edge_id = ?", (edge_id,))
            graph_conn.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            graph_conn.execute("DELETE FROM group_nodes")
            graph_conn.execute("DELETE FROM groups")
    if data_dir is not None:
        _refresh_graph_meta(get_database_paths(data_dir))
    result = {"deleted": True, "edge": row, "deleted_evidence_count": evidence_count}
    _log_delete_action(
        load_config(),
        "delete_relation",
        f"edge_id={edge_id}, evidence={evidence_count}",
    )
    return result


def delete_document(conn: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    """委托 Ingestor 完成文档回收及 Graph/RAG/FAISS 全级联。"""

    source_id = _required_text(source_id, "source_id")
    data_dir = _require_data_dir(conn)
    settings = load_config()
    paths = get_database_paths(data_dir)
    source_rows = _source_rows(paths, [source_id])
    if not source_rows:
        raise LookupError(f"文档不存在：{source_id}")
    external_dir = _resolve_external_dir(data_dir, source_rows, settings.resolve_external_dir())
    conn.commit()
    return Ingestor(
        data_dir=data_dir,
        external_dir=external_dir,
        settings=settings,
    ).delete_document(source_id)


def _source_rows(paths: DatabasePaths, source_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    placeholders = ",".join("?" for _ in source_ids)
    with _open_sqlite(paths.sources_db) as conn:
        return _fetch_all(
            conn,
            f"""
            SELECT source_id, original_path, relative_path, exists_status
            FROM sources WHERE source_id IN ({placeholders})
            """,
            tuple(source_ids),
        )


def _resolve_external_dir(
    data_dir: Path,
    source_rows: Sequence[dict[str, Any]],
    configured: Path,
) -> Path:
    candidates = [
        configured.expanduser().resolve(),
        (data_dir.parent / "external" / "markdown").resolve(),
    ]
    for row in source_rows:
        original = Path(str(row.get("original_path") or ""))
        relative = Path(str(row.get("relative_path") or ""))
        if not original.is_absolute() or not relative.parts:
            continue
        root = original.resolve()
        for _ in relative.parts:
            root = root.parent
        candidates.append(root)
    for candidate in candidates:
        if any((candidate / str(row["relative_path"])).exists() for row in source_rows):
            return candidate
    return candidates[0]


def _refresh_graph_meta(paths: DatabasePaths) -> None:
    with _open_sqlite(paths.graph_db) as conn:
        row = _fetch_one(
            conn,
            """
            SELECT (SELECT COUNT(*) FROM nodes) AS total_nodes,
                   (SELECT COUNT(*) FROM edges) AS total_edges,
                   (SELECT COUNT(*) FROM groups) AS total_groups
            """,
        )
    meta = read_graph_meta(paths)
    meta.update(row or {})
    meta["changed_since_summary"] = int(meta.get("changed_since_summary") or 0) + 1
    write_graph_meta(paths, meta)


@contextmanager
def _database_connection(
    supplied: sqlite3.Connection,
    data_dir: Path | None,
    kind: str,
) -> Iterator[sqlite3.Connection]:
    table = "sources" if kind == "sources" else "nodes"
    if _has_table(supplied, table):
        yield supplied
        return
    if data_dir is None:
        raise ValueError(f"无法从内存连接定位 {kind} 数据库")
    paths = get_database_paths(data_dir)
    path = paths.sources_db if kind == "sources" else paths.graph_db
    with _open_sqlite(path) as opened:
        yield opened


@contextmanager
def _open_sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在：{path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _try_data_dir(conn: sqlite3.Connection) -> Path | None:
    cursor = conn.execute("PRAGMA database_list")
    for row in cursor.fetchall():
        name = row[1]
        filename = row[2]
        if name != "main" or not filename:
            continue
        path = Path(str(filename)).resolve()
        if path.name == "sources.db":
            return path.parent
        if path.name in {"graph.db", "rag.db"} and path.parent.name in {"Graph", "RAG"}:
            return path.parent.parent
    return None


def _require_data_dir(conn: sqlite3.Connection) -> Path:
    data_dir = _try_data_dir(conn)
    if data_dir is None:
        raise ValueError("级联删除需要连接到磁盘上的 sources.db、graph.db 或 rag.db")
    return data_dir


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


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


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    return text


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _decode_json_array(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _log_delete_action(
    settings: Any,
    action: str,
    detail: str,
) -> None:
    try:
        DailyTSVLogger(
            settings.resolve_log_dir(),
            settings.log_level,
        ).log("delete_tools", action, detail)
    except Exception:
        pass


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


__all__ = [
    "search_documents",
    "get_document_nodes",
    "get_document_relations",
    "delete_node",
    "delete_relation",
    "delete_document",
]
