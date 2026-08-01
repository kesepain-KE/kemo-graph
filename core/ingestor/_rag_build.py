"""RAG 切片、向量写入和 FAISS 同步。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import numpy as np

from provider.embedding import EmbeddingResult

from ..chunker import document_chunks
from ..db import connect_graph, connect_rag, connect_sources
from ..rag_engine import RAGEngine
from . import IngestError
from ._scan import _SourceRecord
from ._utils import _elapsed_ms, _now_iso


def _update_rag_for_source(self, record: _SourceRecord, text: str) -> None:
    started_at = time.perf_counter()
    self._log_event(
        "rag_build_start",
        f"path={record.relative_path}, source_id={record.source_id}",
    )
    chunk_specs = document_chunks(text, settings=self.settings)
    text_chunks = [item.content for item in chunk_specs]
    embedding_started_at = time.perf_counter()
    try:
        embedding_result = _coerce_embedding_result(self._embedder(text_chunks))
    except Exception:
        self._log_event(
            "embedding_request",
            (
                f"purpose=rag_build, model={self.settings.models.embedding}, "
                f"count={len(text_chunks)}, status=failed"
            ),
            _elapsed_ms(embedding_started_at),
            level="ERROR",
        )
        raise
    self._log_event(
        "embedding_request",
        (
            f"purpose=rag_build, model={self.settings.models.embedding}, "
            f"count={len(text_chunks)}"
        ),
        _elapsed_ms(embedding_started_at),
    )
    vectors = _validate_embedding_batch(
        embedding_result.vectors,
        expected_count=len(text_chunks),
        dimensions=self.settings.models.embedding_dimensions,
    )
    chunk_ids = [str(uuid4()) for _ in text_chunks]
    node_ids = self._current_graph_node_ids(record.source_id, record.content_hash)
    rag_engine = self._get_rag_engine()
    rag_engine.ensure_index_consistency()

    connection = connect_rag(self.paths)
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_vector_ids = [
            int(row["vector_id"])
            for row in connection.execute(
                "SELECT vector_id FROM embeddings WHERE source_id = ?",
                (record.source_id,),
            ).fetchall()
        ]
        connection.execute(
            "DELETE FROM chunks WHERE source_id = ?", (record.source_id,)
        )
        existing_vector_spaces = {
            str(row["vector_space_id"])
            for row in connection.execute(
                "SELECT DISTINCT vector_space_id FROM embeddings"
            ).fetchall()
        }
        if vectors and existing_vector_spaces.difference(
            {embedding_result.vector_space_id}
        ):
            raise IngestError(
                "不能在同一个 FAISS 索引中混用 vector_space_id："
                f"现有={sorted(existing_vector_spaces)}，"
                f"新增={embedding_result.vector_space_id}"
            )
        new_vector_ids: list[int] = []
        for chunk_id, spec, vector in zip(chunk_ids, chunk_specs, vectors, strict=True):
            parent_chunk_id = (
                chunk_ids[spec.parent_index] if spec.parent_index is not None else None
            )
            connection.execute(
                """
                    INSERT INTO chunks (
                        chunk_id, source_id, content, chunk_index,
                        token_count, granularity, parent_chunk_id,
                        token_start, token_end, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    chunk_id,
                    record.source_id,
                    spec.content,
                    spec.chunk_index,
                    spec.token_end - spec.token_start,
                    spec.granularity,
                    parent_chunk_id,
                    spec.token_start,
                    spec.token_end,
                    _now_iso(),
                ),
            )
            cursor = connection.execute(
                """
                    INSERT INTO embeddings (
                        chunk_id, source_id, vector_blob,
                        dimensions, model_name, vector_space_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    chunk_id,
                    record.source_id,
                    vector.tobytes(),
                    self.settings.models.embedding_dimensions,
                    self.settings.models.embedding,
                    embedding_result.vector_space_id,
                    _now_iso(),
                ),
            )
            new_vector_ids.append(int(cursor.lastrowid))
        connection.executemany(
            "INSERT INTO chunk_nodes (chunk_id, node_id) VALUES (?, ?)",
            [
                (chunk_id, node_id)
                for chunk_id in chunk_ids
                for node_id in sorted(node_ids)
            ],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    matrix = (
        np.vstack(vectors).astype(np.float32, copy=False)
        if vectors
        else np.empty((0, self.settings.models.embedding_dimensions), dtype=np.float32)
    )
    self._replace_rag_vectors_with_recovery(
        rag_engine,
        old_vector_ids,
        new_vector_ids,
        matrix,
    )
    entity_result = _build_entity_vectors_for_source(
        self,
        record.source_id,
        record.content_hash,
    )
    self._log_event(
        "rag_build_done",
        (
            f"path={record.relative_path}, chunks={len(chunk_ids)}, "
            f"entity_vectors={entity_result['updated']}"
        ),
        _elapsed_ms(started_at),
    )


def _build_entity_vectors_for_source(
    self,
    source_id: str,
    content_hash: str,
) -> dict[str, Any]:
    """按来源关联批量补齐实体向量，摘要哈希和向量元数据决定是否跳过。"""

    connection = connect_graph(self.paths)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT n.node_id, n.summary
            FROM nodes AS n
            JOIN node_sources AS ns ON ns.node_id = n.node_id
            WHERE ns.source_id = ? AND ns.content_hash = ?
            ORDER BY n.node_id
            """,
            (source_id, content_hash),
        ).fetchall()
    finally:
        connection.close()
    return self._get_rag_engine().sync_entity_vectors(
        [(str(row["node_id"]), str(row["summary"])) for row in rows]
    )


def _replace_rag_vectors_with_recovery(
    self,
    rag_engine: RAGEngine,
    remove_ids: Sequence[int],
    add_ids: Sequence[int],
    vectors: np.ndarray,
) -> None:
    """增量替换失败时，以已提交的 rag.db 为权威源恢复索引。"""

    try:
        rag_engine.replace_vectors(remove_ids, add_ids, vectors)
    except Exception as replace_error:
        try:
            rebuild_started_at = time.perf_counter()
            rag_engine.rebuild_index()
            self._log_event(
                "faiss_rebuild",
                f"reason=replace_failed, vectors={len(add_ids)}",
                _elapsed_ms(rebuild_started_at),
                level="WARNING",
            )
        except Exception as rebuild_error:
            raise IngestError(
                "FAISS 增量替换失败，且无法从 rag.db 恢复："
                f"replace={replace_error}; rebuild={rebuild_error}"
            ) from rebuild_error


def _sync_chunk_nodes_for_source(
    self,
    source_id: str,
    content_hash: str,
    node_ids: set[str],
) -> None:
    source_connection = connect_sources(self.paths)
    try:
        row = source_connection.execute(
            "SELECT rag_hash FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    finally:
        source_connection.close()
    should_link = row is not None and row["rag_hash"] == content_hash

    connection = connect_rag(self.paths)
    try:
        connection.execute("BEGIN IMMEDIATE")
        chunk_ids = [
            row["chunk_id"]
            for row in connection.execute(
                "SELECT chunk_id FROM chunks WHERE source_id = ?", (source_id,)
            ).fetchall()
        ]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            connection.execute(
                f"DELETE FROM chunk_nodes WHERE chunk_id IN ({placeholders})",
                tuple(chunk_ids),
            )
            if should_link:
                connection.executemany(
                    "INSERT INTO chunk_nodes (chunk_id, node_id) VALUES (?, ?)",
                    [
                        (chunk_id, node_id)
                        for chunk_id in chunk_ids
                        for node_id in sorted(node_ids)
                    ],
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _remove_chunk_node_ids(self, node_ids: set[str]) -> None:
    if not node_ids:
        return
    placeholders = ",".join("?" for _ in node_ids)
    connection = connect_rag(self.paths)
    try:
        connection.execute(
            f"DELETE FROM chunk_nodes WHERE node_id IN ({placeholders})",
            tuple(sorted(node_ids)),
        )
        connection.commit()
    finally:
        connection.close()


def _current_graph_node_ids(self, source_id: str, content_hash: str) -> set[str]:
    source_connection = connect_sources(self.paths)
    try:
        row = source_connection.execute(
            "SELECT graph_hash FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    finally:
        source_connection.close()
    if row is None or row["graph_hash"] != content_hash:
        return set()
    graph_connection = connect_graph(self.paths)
    try:
        rows = graph_connection.execute(
            """
                SELECT node_id FROM node_sources
                WHERE source_id = ? AND content_hash = ?
                """,
            (source_id, content_hash),
        ).fetchall()
    finally:
        graph_connection.close()
    return {row["node_id"] for row in rows}


def _coerce_embedding_result(
    value: EmbeddingResult | list[list[float]],
) -> EmbeddingResult:
    """消费 EmbeddingResult，并暂时兼容旧测试注入的裸向量列表。"""

    if isinstance(value, EmbeddingResult):
        if (
            not isinstance(value.vector_space_id, str)
            or not value.vector_space_id.strip()
        ):
            raise IngestError("EmbeddingResult.vector_space_id 不能为空")
        return EmbeddingResult(
            vectors=value.vectors,
            vector_space_id=value.vector_space_id.strip(),
        )
    if isinstance(value, list):
        return EmbeddingResult(vectors=value, vector_space_id="unknown")
    raise IngestError("Embedding 返回值必须是 EmbeddingResult")


def _validate_embedding_batch(
    vectors: list[list[float]],
    *,
    expected_count: int,
    dimensions: int,
) -> list[np.ndarray]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        actual = len(vectors) if isinstance(vectors, list) else "非列表"
        raise IngestError(
            f"Embedding 返回数量不一致：期望 {expected_count}，实际 {actual}"
        )
    validated: list[np.ndarray] = []
    for index, vector in enumerate(vectors):
        try:
            array = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise IngestError(f"第 {index} 个 embedding 含非数值内容") from exc
        if array.ndim != 1 or len(array) != dimensions:
            raise IngestError(
                f"第 {index} 个 embedding 维度错误：期望 {dimensions}，实际 {array.shape}"
            )
        if not np.isfinite(array).all():
            raise IngestError(f"第 {index} 个 embedding 包含 NaN 或 Infinity")
        validated.append(array)
    return validated
