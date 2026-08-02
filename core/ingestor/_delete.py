"""文档回收与 Graph/RAG 级联删除。"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..db import connect_graph, connect_rag, connect_sources
from . import IngestError
from ._graph_build import _recalculate_edges, _recalculate_nodes
from ._utils import _now_iso, _safe_source_path, _write_json_atomic


class DocumentNotFoundError(IngestError):
    """请求删除的活动文档不存在。"""


class RecycleConflictError(IngestError):
    """回收站中已存在同路径文件，拒绝覆盖。"""


def delete_document(self, source_id: str) -> dict[str, Any]:
    """按 source_id 删除文档数据，并将 Markdown 移入回收站。"""

    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id 必须是非空字符串")
    source_id = source_id.strip()
    with self._write_lock:
        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                """
                    SELECT source_id, relative_path
                    FROM sources
                    WHERE source_id = ? AND exists_status = 'active'
                    """,
                (source_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise DocumentNotFoundError(f"活动文档不存在：{source_id}")

        relative_path = row["relative_path"]
        recycled_path = self._move_source_to_recycle(relative_path)
        connection = connect_sources(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                    UPDATE sources
                    SET exists_status = 'deleted', updated_at = ?
                    WHERE source_id = ? AND exists_status = 'active'
                    """,
                (_now_iso(), source_id),
            )
            if cursor.rowcount != 1:
                raise DocumentNotFoundError(f"活动文档不存在：{source_id}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        graph_changed, rag_changed = self._delete_source_data(source_id)
        result = {
            "deleted_source_id": source_id,
            "relative_path": relative_path,
            "recycled_path": recycled_path,
            "graph_deleted": graph_changed,
            "rag_deleted": rag_changed,
        }
        self._log_event(
            "delete_document",
            f"{relative_path}: graph={graph_changed}, rag={rag_changed}",
        )
        return result


def _move_source_to_recycle(self, relative_path: str) -> str | None:
    source_path = _safe_source_path(self.external_dir, relative_path)
    if not source_path.exists():
        return None

    recycle_root = (self.external_dir.parent / "recycle").resolve()
    destination = (recycle_root / relative_path).resolve()
    try:
        destination.relative_to(recycle_root)
    except ValueError as exc:
        raise IngestError(f"非法回收站相对路径：{relative_path}") from exc
    meta_path = destination.with_name(destination.name + ".meta.json")
    if destination.exists() or meta_path.exists():
        raise RecycleConflictError(f"回收站中已存在同路径文件：{relative_path}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    metadata = {
        "original_path": relative_path,
        "recycled_at": now.isoformat(),
        "expires_at": (
            now + timedelta(days=self.settings.recycle_life_days)
        ).isoformat(),
    }
    shutil.move(str(source_path), str(destination))
    try:
        _write_json_atomic(meta_path, metadata)
    except Exception:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source_path))
        raise
    return destination.relative_to(recycle_root.parent).as_posix()


def _delete_source_data(self, source_id: str) -> tuple[bool, bool]:
    graph_changed = self._delete_graph_for_source(source_id)
    rag_changed = self._delete_rag_for_source(source_id)
    return graph_changed, rag_changed


def _delete_graph_for_source(self, source_id: str) -> bool:
    connection = connect_graph(self.paths)
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_node_ids = {
            row["node_id"]
            for row in connection.execute(
                "SELECT node_id FROM node_sources WHERE source_id = ?", (source_id,)
            ).fetchall()
        }
        old_edge_ids = {
            row["edge_id"]
            for row in connection.execute(
                "SELECT edge_id FROM edge_sources WHERE source_id = ?", (source_id,)
            ).fetchall()
        }
        connection.execute("DELETE FROM node_sources WHERE source_id = ?", (source_id,))
        connection.execute("DELETE FROM edge_sources WHERE source_id = ?", (source_id,))
        connection.execute(
            "DELETE FROM relation_mentions WHERE source_id = ?", (source_id,)
        )
        connection.execute(
            "DELETE FROM entity_mentions WHERE source_id = ?", (source_id,)
        )
        _recalculate_edges(connection, old_edge_ids)
        orphan_node_ids = _recalculate_nodes(connection, old_node_ids)
        if old_node_ids or old_edge_ids:
            connection.execute("DELETE FROM group_nodes")
            connection.execute("DELETE FROM groups")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    changed = bool(old_node_ids or old_edge_ids)
    if changed:
        self._get_rag_engine().refresh_auxiliary_consistency()
    if orphan_node_ids:
        self._remove_chunk_node_ids(orphan_node_ids)
    if changed:
        self._refresh_graph_meta(changed=True)
    return changed


def _delete_rag_for_source(self, source_id: str) -> bool:
    rag_engine = self._get_rag_engine()
    rag_engine.ensure_index_consistency()
    connection = connect_rag(self.paths)
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_vector_ids = [
            int(row["vector_id"])
            for row in connection.execute(
                "SELECT vector_id FROM embeddings WHERE source_id = ?", (source_id,)
            ).fetchall()
        ]
        chunk_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_id = ?", (source_id,)
            ).fetchone()[0]
        )
        connection.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    changed = bool(old_vector_ids or chunk_count)
    if changed:
        empty = np.empty(
            (0, self.settings.models.embedding_dimensions), dtype=np.float32
        )
        self._replace_rag_vectors_with_recovery(
            rag_engine,
            old_vector_ids,
            [],
            empty,
        )
    return changed
