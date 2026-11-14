from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime, timezone
import numpy as np
from provider.rerank import clear_cache
from core.documents.chunker.engine import chunking_signature
from core.storage.connections import connect_rag
from core.storage.meta import read_rag_meta, write_rag_meta
from core.rag.errors import IndexIntegrityError
from core.rag.models import _MixedVectorSpaceError
from core.common.time import elapsed_ms as _elapsed_ms
from core.rag.vectors import _validate_vector_space_id

def add_vectors(
    self,
    vector_ids: Sequence[int],
    vectors: Sequence[Sequence[float]] | np.ndarray,
) -> None:
    """将已提交到 SQLite 的向量加入 FAISS 并清空 rerank 缓存。"""

    self.index.add(vector_ids, vectors)
    self._refresh_meta()
    clear_cache(self.paths.rerank_cache)

def delete_vectors(self, vector_ids: Sequence[int]) -> int:
    """从 FAISS 物理删除向量并清空 rerank 缓存。"""

    removed = self.index.delete(vector_ids)
    if removed:
        self._refresh_meta()
        clear_cache(self.paths.rerank_cache)
    return removed

def replace_vectors(
    self,
    remove_ids: Sequence[int],
    add_ids: Sequence[int],
    vectors: Sequence[Sequence[float]] | np.ndarray,
) -> int:
    """提交 SQLite 后，以一次原子索引替换完成单文档向量更新。"""

    removed = self.index.replace(remove_ids, add_ids, vectors)
    self._refresh_meta()
    clear_cache(self.paths.rerank_cache)
    return removed

def refresh_meta(self) -> None:
    """根据当前 rag.db 计数刷新 rag_meta.json。"""

    self._refresh_meta()

def ensure_index_consistency(self) -> None:
    """校验 FAISS 与 rag.db 的 ID 集合，不一致时从数据库原子重建。"""

    self._ensure_index_consistency()

def search_vectors(
    self,
    vectors: Sequence[Sequence[float]] | np.ndarray,
    top_k: int,
) -> list[list[tuple[int, float]]]:
    return self.index.search(vectors, top_k)

def rebuild_index(self) -> int:
    """从 rag.db 原始 embedding 全量重建 FAISS。"""

    started_at = time.perf_counter()
    connection = connect_rag(self.paths)
    try:
        rows = connection.execute(
            """
            SELECT vector_id, vector_blob, dimensions, model_name,
                   vector_space_id
            FROM embeddings
            ORDER BY vector_id
            """
        ).fetchall()
    finally:
        connection.close()

    vector_space_ids = {
        _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
        for row in rows
    }
    if len(vector_space_ids) > 1:
        raise IndexIntegrityError(
            "embeddings 包含多个 vector_space_id，不能构建同一个 FAISS 索引："
            f"{sorted(vector_space_ids)}"
        )

    vector_ids: list[int] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        if row["dimensions"] != self.settings.models.embedding_dimensions:
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 的维度与当前配置不一致"
            )
        if row["model_name"] != self.settings.models.embedding:
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 的模型与当前配置不一致"
            )
        vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
        if len(vector) != self.settings.models.embedding_dimensions:
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 的 BLOB 长度与 dimensions 不一致"
            )
        if not np.isfinite(vector).all():
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 包含 NaN 或 Infinity"
            )
        vector_ids.append(int(row["vector_id"]))
        vectors.append(vector.copy())

    matrix = (
        np.vstack(vectors).astype(np.float32, copy=False)
        if vectors
        else np.empty(
            (0, self.settings.models.embedding_dimensions), dtype=np.float32
        )
    )
    self.index.rebuild(vector_ids, matrix)
    self._refresh_meta()
    clear_cache(self.paths.rerank_cache)
    self._log_event(
        "faiss_rebuild",
        f"vectors={len(vector_ids)}",
        _elapsed_ms(started_at),
    )
    return len(vector_ids)

def _ensure_index_consistency(self) -> None:
    try:
        database_ids = self._database_vector_ids()
    except _MixedVectorSpaceError:
        # 明确触发一次从数据库重建；rebuild_index 会给出不可混用的诊断。
        self.rebuild_index()
        return
    if self.paths.faiss_index.exists():
        try:
            self.index.load()
            if self.index.ids == database_ids:
                return
        except IndexIntegrityError:
            pass
    self.rebuild_index()

def _database_vector_ids(self) -> set[int]:
    connection = connect_rag(self.paths)
    try:
        rows = connection.execute(
            """
            SELECT vector_id, dimensions, model_name, vector_space_id
            FROM embeddings
            """
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        if row["dimensions"] != self.settings.models.embedding_dimensions:
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 的维度与当前配置不一致，需重新 embedding"
            )
        if row["model_name"] != self.settings.models.embedding:
            raise IndexIntegrityError(
                f"vector_id={row['vector_id']} 的模型与当前配置不一致，需重新 embedding"
            )
    vector_space_ids = {
        _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
        for row in rows
    }
    if len(vector_space_ids) > 1:
        raise _MixedVectorSpaceError(
            "embeddings 包含多个 vector_space_id，需重新 embedding："
            f"{sorted(vector_space_ids)}"
        )
    return {int(row["vector_id"]) for row in rows}

def _database_vector_space_id(self) -> str | None:
    connection = connect_rag(self.paths)
    try:
        rows = connection.execute(
            "SELECT vector_id, vector_space_id FROM embeddings"
        ).fetchall()
    finally:
        connection.close()
    spaces = {
        _validate_vector_space_id(row["vector_space_id"], int(row["vector_id"]))
        for row in rows
    }
    if len(spaces) > 1:
        raise IndexIntegrityError(
            f"embeddings 包含多个 vector_space_id：{sorted(spaces)}"
        )
    return next(iter(spaces), None)

def _refresh_meta(self) -> None:
    connection = connect_rag(self.paths)
    try:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM chunks) AS total_chunks,
                (SELECT COUNT(*) FROM embeddings) AS total_vectors
            """
        ).fetchone()
    finally:
        connection.close()
    vector_space_id = self._database_vector_space_id()
    meta = read_rag_meta(self.paths, self.settings)
    meta.update(
        {
            "total_chunks": int(counts["total_chunks"]),
            "total_vectors": int(counts["total_vectors"]),
            "vector_dimensions": self.settings.models.embedding_dimensions,
            "embedding_model": self.settings.models.embedding,
            "vector_space_id": vector_space_id,
            "chunking_signature": chunking_signature(self.settings),
            "faiss_index_type": "IndexIDMap2+IndexFlatIP",
            "last_built_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_rag_meta(self.paths, meta)
