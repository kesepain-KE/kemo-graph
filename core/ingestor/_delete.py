"""文档文件与 Graph/RAG 级联删除。"""

from __future__ import annotations

from typing import Any

import numpy as np

from core.storage.connections import connect_graph, connect_rag, connect_sources
from core.ingestor import IngestError
from core.ingestor._graph_build import _recalculate_edges, _recalculate_nodes
from core.common.time import now_iso as _now_iso
from core.ingestor._utils import _safe_source_path


class DocumentNotFoundError(IngestError):
    """请求删除的活动文档不存在。"""


def delete_document(self, source_id: str) -> dict[str, Any]:
    """按 source_id 永久删除文档文件及其派生数据。"""

    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id 必须是非空字符串")
    source_id = source_id.strip()
    with self._write_lock:
        connection = connect_sources(self.paths)
        try:
            row = connection.execute(
                """
                SELECT source_id, relative_path, library_path FROM sources
                WHERE source_id = ? AND exists_status = 'active'
                """,
                (source_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise DocumentNotFoundError(f"活动文档不存在：{source_id}")

        relative_path = row["relative_path"]
        original = None
        if row["library_path"] is not None:
            from core.library import resolve_path
            original = resolve_path(self, row["library_path"], must_exist=False)
        self._delete_source_file(relative_path)
        if original is not None:
            original.unlink(missing_ok=True)

        # Keep the source active until all derived stores are clean, so a
        # failed cleanup can be retried even after the files are removed.
        graph_changed, rag_changed = self._delete_source_data(source_id)
        connection = connect_sources(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE sources SET exists_status = 'deleted', updated_at = ?
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

        result = {
            "deleted_source_id": source_id,
            "relative_path": relative_path,
            "graph_deleted": graph_changed,
            "rag_deleted": rag_changed,
        }
        self._log_event(
            "delete_document",
            f"{relative_path}: graph={graph_changed}, rag={rag_changed}",
        )
        return result


def _delete_source_file(self, relative_path: str) -> None:
    _safe_source_path(self.external_dir, relative_path).unlink(missing_ok=True)


def _delete_source_data(self, source_id: str) -> tuple[bool, bool]:
    graph_changed = self._delete_graph_for_source(source_id)
    rag_changed = self._delete_rag_for_source(source_id)
    connection = connect_sources(self.paths)
    try:
        relative_path = connection.execute(
            "SELECT relative_path FROM sources WHERE source_id = ?", (source_id,),
        ).fetchone()["relative_path"]
    finally:
        connection.close()
    self.file_map.delete_by_markdown(relative_path)
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
        self._get_rag_engine(for_deletion=True).refresh_auxiliary_consistency()
    if orphan_node_ids:
        self._remove_chunk_node_ids(orphan_node_ids)
    if changed:
        self._refresh_graph_meta(changed=True)
    return changed


def _delete_rag_for_source(self, source_id: str) -> bool:
    rag_engine = self._get_rag_engine(for_deletion=True)
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
            (0, rag_engine.settings.models.embedding_dimensions), dtype=np.float32
        )
        self._replace_rag_vectors_with_recovery(
            rag_engine,
            old_vector_ids,
            [],
            empty,
        )
    return changed
