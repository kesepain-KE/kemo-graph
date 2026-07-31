"""FAISS 索引管理和 RAG 查询主流程。"""

from __future__ import annotations

import os
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    import faiss
except ImportError:  # pragma: no cover - 由运行时依赖检查覆盖
    faiss = None  # type: ignore[assignment]

from provider.embedding import EmbeddingResult, embed
from provider.rerank import clear_cache, rerank

from .chunker import chunk, chunking_signature
from .config import AppConfig, load_config
from .db import (
    connect_rag,
    connect_sources,
    initialize_databases,
    read_rag_meta,
    write_rag_meta,
)
from .logger import DailyTSVLogger


class RAGError(RuntimeError):
    """RAG 引擎错误基类。"""


class FaissUnavailableError(RAGError):
    """FAISS 依赖未安装。"""


class IndexIntegrityError(RAGError):
    """FAISS 或 SQLite 中的向量数据不一致。"""


class RAGQueryError(RAGError):
    """RAG 查询参数或重排序结果无效。"""


class FaissIndexManager:
    """管理持久化的 IndexIDMap2 + IndexFlatIP 索引。"""

    def __init__(
        self,
        index_path: Path | str,
        dimensions: int,
        *,
        autoload: bool = True,
    ) -> None:
        if faiss is None:
            raise FaissUnavailableError("缺少 faiss-cpu，请先安装 requirements.txt")
        if dimensions < 1:
            raise ValueError("dimensions 必须大于等于 1")
        self.index_path = Path(index_path)
        self.dimensions = dimensions
        self._lock = threading.RLock()
        self._index: Any | None = None
        if autoload and self.index_path.exists():
            self.load()
        else:
            self.create()

    @property
    def count(self) -> int:
        with self._lock:
            return int(self._require_index().ntotal)

    @property
    def ids(self) -> set[int]:
        with self._lock:
            index = self._require_index()
            return {
                int(value) for value in faiss.vector_to_array(index.id_map).tolist()
            }

    def create(self, *, persist: bool = False) -> None:
        """创建空的精确内积索引。"""

        with self._lock:
            new_index = self._new_index()
            if persist:
                self._persist(new_index)
            self._index = new_index

    def load(self) -> None:
        """从磁盘加载并校验索引类型和维度。"""

        with self._lock:
            try:
                loaded = faiss.read_index(str(self.index_path))
            except Exception as exc:
                raise IndexIntegrityError(
                    f"无法加载 FAISS 索引：{self.index_path}: {exc}"
                ) from exc
            self._validate_index(loaded)
            self._index = loaded

    def save(self) -> None:
        """将当前索引原子写入磁盘。"""

        with self._lock:
            self._persist(self._require_index())

    def add(
        self,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> None:
        """原子添加带稳定 ID 的向量。"""

        ids = _validate_vector_ids(vector_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(ids) != len(matrix):
            raise ValueError("vector_ids 数量必须与 vectors 数量一致")
        if not ids:
            return
        if len(set(ids)) != len(ids):
            raise IndexIntegrityError("同一批次包含重复 vector_id")
        with self._lock:
            existing_ids = {
                int(value)
                for value in faiss.vector_to_array(
                    self._require_index().id_map
                ).tolist()
            }
            duplicates = existing_ids.intersection(ids)
            if duplicates:
                raise IndexIntegrityError(
                    f"FAISS 中已存在 vector_id：{sorted(duplicates)}"
                )
            candidate = faiss.clone_index(self._require_index())
            candidate.add_with_ids(matrix, np.asarray(ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate

    def delete(self, vector_ids: Sequence[int]) -> int:
        """物理删除指定向量并返回实际删除数量。"""

        ids = _validate_vector_ids(vector_ids)
        if not ids:
            return 0
        with self._lock:
            candidate = faiss.clone_index(self._require_index())
            removed = int(candidate.remove_ids(np.asarray(ids, dtype=np.int64)))
            if removed:
                self._persist(candidate)
                self._index = candidate
            return removed

    def replace(
        self,
        remove_ids: Sequence[int],
        add_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> int:
        """在单次原子持久化中删除旧向量并加入新向量。"""

        old_ids = _validate_vector_ids(remove_ids)
        new_ids = _validate_vector_ids(add_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(new_ids) != len(matrix):
            raise ValueError("add_ids 数量必须与 vectors 数量一致")
        if len(set(new_ids)) != len(new_ids):
            raise IndexIntegrityError("同一批次包含重复的新 vector_id")
        if not old_ids and not new_ids:
            return 0

        with self._lock:
            candidate = faiss.clone_index(self._require_index())
            removed = (
                int(candidate.remove_ids(np.asarray(old_ids, dtype=np.int64)))
                if old_ids
                else 0
            )
            remaining_ids = {
                int(value) for value in faiss.vector_to_array(candidate.id_map).tolist()
            }
            duplicates = remaining_ids.intersection(new_ids)
            if duplicates:
                raise IndexIntegrityError(
                    f"FAISS 中已存在新的 vector_id：{sorted(duplicates)}"
                )
            if new_ids:
                candidate.add_with_ids(matrix, np.asarray(new_ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate
            return removed

    def search(
        self,
        vectors: Sequence[Sequence[float]] | np.ndarray,
        top_k: int,
    ) -> list[list[tuple[int, float]]]:
        """对每个查询向量返回按内积分数降序排列的 ``(id, score)``。"""

        if top_k < 1:
            raise ValueError("top_k 必须大于等于 1")
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(matrix) == 0:
            return []
        with self._lock:
            index = self._require_index()
            if index.ntotal == 0:
                return [[] for _ in range(len(matrix))]
            limit = min(top_k, int(index.ntotal))
            scores, ids = index.search(matrix, limit)
        return [
            [
                (int(vector_id), float(score))
                for vector_id, score in zip(row_ids, row_scores, strict=True)
                if vector_id >= 0
            ]
            for row_ids, row_scores in zip(ids, scores, strict=True)
        ]

    def rebuild(
        self,
        vector_ids: Sequence[int],
        vectors: Sequence[Sequence[float]] | np.ndarray,
    ) -> None:
        """使用完整向量集合重建并原子替换索引。"""

        ids = _validate_vector_ids(vector_ids)
        matrix = _as_float32_matrix(vectors, self.dimensions)
        if len(ids) != len(matrix):
            raise ValueError("vector_ids 数量必须与 vectors 数量一致")
        if len(set(ids)) != len(ids):
            raise IndexIntegrityError("重建数据包含重复 vector_id")

        with self._lock:
            candidate = self._new_index()
            if ids:
                candidate.add_with_ids(matrix, np.asarray(ids, dtype=np.int64))
            self._persist(candidate)
            self._index = candidate

    def _new_index(self) -> Any:
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimensions))

    def _require_index(self) -> Any:
        if self._index is None:
            raise IndexIntegrityError("FAISS 索引尚未创建或加载")
        return self._index

    def _validate_index(self, index: Any) -> None:
        if not isinstance(index, faiss.IndexIDMap2):
            raise IndexIntegrityError("FAISS 索引类型必须是 IndexIDMap2")
        if int(index.d) != self.dimensions:
            raise IndexIntegrityError(
                f"FAISS 维度不一致：期望 {self.dimensions}，实际 {index.d}"
            )
        ids = faiss.vector_to_array(index.id_map)
        if len(ids) != len(set(int(value) for value in ids.tolist())):
            raise IndexIntegrityError("FAISS 索引包含重复 vector_id")

    def _persist(self, index: Any) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        try:
            faiss.write_index(index, str(temporary_path))
            verification = faiss.read_index(str(temporary_path))
            self._validate_index(verification)
            expected_ids = {
                int(value) for value in faiss.vector_to_array(index.id_map).tolist()
            }
            actual_ids = {
                int(value)
                for value in faiss.vector_to_array(verification.id_map).tolist()
            }
            if expected_ids != actual_ids or int(index.ntotal) != int(
                verification.ntotal
            ):
                raise IndexIntegrityError("FAISS 临时索引校验失败")
            os.replace(temporary_path, self.index_path)
        finally:
            temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _Candidate:
    vector_id: int
    chunk_id: str
    source_id: str
    content: str
    faiss_score: float
    granularity: str
    parent_chunk_id: str | None


@dataclass(frozen=True)
class _ChunkContext:
    chunk_id: str
    content: str
    granularity: str
    parent_chunk_id: str | None


Embedder = Callable[[list[str]], EmbeddingResult | list[list[float]]]
Reranker = Callable[[str, list[str], int], list[tuple[int, float]]]

_DIRECT_LEXICAL_MATCH_SCORE = 0.75
_MIN_LEXICAL_QUERY_LENGTH = 2
_LEXICAL_EDGE_PUNCTUATION = " \t\r\n,，;；:：.!！?？()（）[]【】{}<>《》\"'“”‘’"


class RAGEngine:
    """串联 query chunk、embedding、FAISS、rerank 和阈值过滤。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.paths = initialize_databases(data_dir, self.settings)
        self.index = FaissIndexManager(
            self.paths.faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self._embedder = embedder or (
            lambda texts: embed(texts, settings=self.settings)
        )
        self._reranker = reranker
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        self._ensure_index_consistency()

    def query(
        self,
        query: str,
        *,
        top_k: int | None = None,
        threshold: float | None = None,
        score_multipliers: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        """执行完整 RAG 查询并返回 API 约定的结构化结果。"""

        if not isinstance(query, str) or not query.strip():
            raise RAGQueryError("query 必须是非空字符串")
        effective_top_k = top_k if top_k is not None else self.settings.default_top_k
        effective_threshold = (
            threshold
            if threshold is not None
            else self.settings.rag_similarity_threshold
        )
        if not isinstance(effective_top_k, int) or isinstance(effective_top_k, bool):
            raise RAGQueryError("top_k 必须是整数")
        if effective_top_k < 1:
            raise RAGQueryError("top_k 必须大于等于 1")
        if not isinstance(effective_threshold, (int, float)) or isinstance(
            effective_threshold, bool
        ):
            raise RAGQueryError("threshold 必须是数字")
        if not 0.0 <= float(effective_threshold) <= 1.0:
            raise RAGQueryError("threshold 必须在 0 到 1 之间")
        if self.index.count == 0:
            return {"query": query, "results": []}

        query_chunks = chunk(query, settings=self.settings)
        embedding_started_at = time.perf_counter()
        try:
            query_embedding = _coerce_embedding_result(self._embedder(query_chunks))
        except Exception:
            self._log_event(
                "embedding_request",
                (
                    f"purpose=query, model={self.settings.models.embedding}, "
                    f"count={len(query_chunks)}, status=failed"
                ),
                _elapsed_ms(embedding_started_at),
                level="ERROR",
            )
            raise
        self._log_event(
            "embedding_request",
            (
                f"purpose=query, model={self.settings.models.embedding}, "
                f"count={len(query_chunks)}"
            ),
            _elapsed_ms(embedding_started_at),
        )
        query_vectors = query_embedding.vectors
        if len(query_vectors) != len(query_chunks):
            raise RAGQueryError("Embedding 返回向量数量与查询切片数量不一致")
        database_vector_space = self._database_vector_space_id()
        if (
            database_vector_space is not None
            and query_embedding.vector_space_id != database_vector_space
        ):
            raise RAGQueryError(
                "查询向量空间与 FAISS 索引不一致："
                f"查询={query_embedding.vector_space_id}，索引={database_vector_space}"
            )
        candidate_multiplier = 6 if self.settings.chunking_mode == "hierarchical" else 2
        raw_hits = self.index.search(
            query_vectors,
            effective_top_k * candidate_multiplier,
        )
        candidates = self._load_candidates(raw_hits)
        if not candidates:
            return {"query": query, "results": []}
        if score_multipliers:
            _validate_score_multipliers(score_multipliers)
            candidates = sorted(
                (
                    replace(
                        candidate,
                        faiss_score=candidate.faiss_score
                        * float(score_multipliers.get(candidate.chunk_id, 1.0)),
                    )
                    for candidate in candidates
                ),
                key=lambda candidate: candidate.faiss_score,
                reverse=True,
            )

        rerank_limit = min(
            len(candidates),
            max(effective_top_k, self.settings.rerank_top_n),
        )
        documents = [candidate.content for candidate in candidates]
        rerank_started_at = time.perf_counter()
        if self._reranker is None:
            try:
                ranked = rerank(
                    query,
                    documents,
                    rerank_limit,
                    settings=self.settings,
                    cache_path=self.paths.rerank_cache,
                    document_ids=[candidate.chunk_id for candidate in candidates],
                )
            except Exception:
                self._log_event(
                    "rerank_request",
                    (
                        f"model={self.settings.models.rerank}, "
                        f"candidates={len(documents)}, status=failed"
                    ),
                    _elapsed_ms(rerank_started_at),
                    level="ERROR",
                )
                raise
        else:
            ranked = self._reranker(query, documents, rerank_limit)
        self._log_event(
            "rerank_request",
            f"model={self.settings.models.rerank}, candidates={len(documents)}",
            _elapsed_ms(rerank_started_at),
        )
        source_paths = self._load_source_paths(
            {candidate.source_id for candidate in candidates}
        )
        hierarchy = self._load_chunk_hierarchy(candidates)
        results: list[dict[str, Any]] = []
        seen_indexes: set[int] = set()
        seen_families: set[str] = set()
        for candidate_index, score in ranked:
            if (
                not isinstance(candidate_index, int)
                or candidate_index in seen_indexes
                or not 0 <= candidate_index < len(candidates)
            ):
                raise RAGQueryError("rerank 返回了无效或重复的文档索引")
            if not isinstance(score, (int, float, np.integer, np.floating)):
                raise RAGQueryError("rerank 返回了非数值分数")
            if not np.isfinite(score):
                raise RAGQueryError("rerank 返回了 NaN 或 Infinity")
            seen_indexes.add(candidate_index)
            candidate = candidates[candidate_index]
            effective_score = max(
                float(score),
                _direct_lexical_match_score(query, candidate.content),
            )
            if effective_score < effective_threshold:
                continue
            family_id = _chunk_family_id(candidate.chunk_id, hierarchy)
            if family_id in seen_families:
                continue
            seen_families.add(family_id)
            parent = (
                hierarchy.get(candidate.parent_chunk_id)
                if candidate.parent_chunk_id is not None
                else None
            )
            results.append(
                {
                    "chunk_id": candidate.chunk_id,
                    "content": candidate.content,
                    "score": effective_score,
                    "granularity": candidate.granularity,
                    "parent_chunk_id": candidate.parent_chunk_id,
                    "context": (
                        {
                            "chunk_id": parent.chunk_id,
                            "content": parent.content,
                            "granularity": parent.granularity,
                        }
                        if parent is not None
                        else None
                    ),
                    "source": {
                        "source_id": candidate.source_id,
                        "relative_path": source_paths.get(candidate.source_id),
                    },
                }
            )
            if len(results) == effective_top_k:
                break
        return {"query": query, "results": results}

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

    def _load_candidates(
        self, raw_hits: Iterable[Iterable[tuple[int, float]]]
    ) -> list[_Candidate]:
        vector_scores: dict[int, float] = {}
        for query_hits in raw_hits:
            for vector_id, score in query_hits:
                vector_scores[vector_id] = max(
                    vector_scores.get(vector_id, float("-inf")), score
                )
        if not vector_scores:
            return []

        placeholders = ",".join("?" for _ in vector_scores)
        connection = connect_rag(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT e.vector_id, c.chunk_id, c.source_id, c.content,
                       c.granularity, c.parent_chunk_id
                FROM embeddings e
                JOIN chunks c ON c.chunk_id = e.chunk_id
                WHERE e.vector_id IN ({placeholders})
                """,
                tuple(vector_scores),
            ).fetchall()
        finally:
            connection.close()

        found_vector_ids = {int(row["vector_id"]) for row in rows}
        missing_vector_ids = set(vector_scores).difference(found_vector_ids)
        if missing_vector_ids:
            raise IndexIntegrityError(
                f"FAISS 中的 vector_id 在 rag.db 中不存在：{sorted(missing_vector_ids)}"
            )

        by_chunk: dict[str, _Candidate] = {}
        for row in rows:
            score = vector_scores[int(row["vector_id"])]
            candidate = _Candidate(
                vector_id=int(row["vector_id"]),
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                content=row["content"],
                faiss_score=score,
                granularity=row["granularity"],
                parent_chunk_id=row["parent_chunk_id"],
            )
            previous = by_chunk.get(candidate.chunk_id)
            if previous is None or candidate.faiss_score > previous.faiss_score:
                by_chunk[candidate.chunk_id] = candidate
        return sorted(
            by_chunk.values(),
            key=lambda candidate: candidate.faiss_score,
            reverse=True,
        )

    def _load_chunk_hierarchy(
        self,
        candidates: Sequence[_Candidate],
    ) -> dict[str, _ChunkContext]:
        hierarchy = {
            candidate.chunk_id: _ChunkContext(
                chunk_id=candidate.chunk_id,
                content=candidate.content,
                granularity=candidate.granularity,
                parent_chunk_id=candidate.parent_chunk_id,
            )
            for candidate in candidates
        }
        frontier = {
            candidate.parent_chunk_id
            for candidate in candidates
            if candidate.parent_chunk_id is not None
        }
        connection = connect_rag(self.paths)
        try:
            while frontier:
                pending = frontier.difference(hierarchy)
                if not pending:
                    break
                placeholders = ",".join("?" for _ in pending)
                rows = connection.execute(
                    f"""
                    SELECT chunk_id, content, granularity, parent_chunk_id
                    FROM chunks
                    WHERE chunk_id IN ({placeholders})
                    """,
                    tuple(sorted(pending)),
                ).fetchall()
                if len(rows) != len(pending):
                    found = {row["chunk_id"] for row in rows}
                    raise IndexIntegrityError(
                        "分层 chunk 的 parent_chunk_id 不存在："
                        f"{sorted(pending.difference(found))}"
                    )
                frontier = set()
                for row in rows:
                    context = _ChunkContext(
                        chunk_id=row["chunk_id"],
                        content=row["content"],
                        granularity=row["granularity"],
                        parent_chunk_id=row["parent_chunk_id"],
                    )
                    hierarchy[context.chunk_id] = context
                    if context.parent_chunk_id is not None:
                        frontier.add(context.parent_chunk_id)
        finally:
            connection.close()
        return hierarchy

    def _load_source_paths(self, source_ids: set[str]) -> dict[str, str]:
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        connection = connect_sources(self.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT source_id, relative_path
                FROM sources
                WHERE source_id IN ({placeholders})
                """,
                tuple(source_ids),
            ).fetchall()
        finally:
            connection.close()
        paths = {row["source_id"]: row["relative_path"] for row in rows}
        missing_source_ids = source_ids.difference(paths)
        if missing_source_ids:
            raise IndexIntegrityError(
                f"chunk 对应的 source_id 在 sources.db 中不存在：{sorted(missing_source_ids)}"
            )
        return paths

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

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log("rag_engine", action, detail, elapsed_ms, level)
        except Exception:
            pass


class _MixedVectorSpaceError(IndexIntegrityError):
    """数据库中混入了不能共同索引的向量空间。"""


def _coerce_embedding_result(
    value: EmbeddingResult | list[list[float]],
) -> EmbeddingResult:
    """消费新签名，并兼容旧注入测试返回的裸向量列表。"""

    if isinstance(value, EmbeddingResult):
        vector_space_id = _validate_vector_space_id(value.vector_space_id)
        return EmbeddingResult(
            vectors=value.vectors,
            vector_space_id=vector_space_id,
        )
    if isinstance(value, list):
        return EmbeddingResult(vectors=value, vector_space_id="unknown")
    raise TypeError("Embedding 返回值必须是 EmbeddingResult")


def _direct_lexical_match_score(query: str, document: str) -> float:
    """为文档中的直接关键词命中提供保底分，避免短词被 Rerank 尺度吞没。"""

    normalized_query = _normalize_lexical_text(query).strip(_LEXICAL_EDGE_PUNCTUATION)
    if len(normalized_query) < _MIN_LEXICAL_QUERY_LENGTH:
        return 0.0
    normalized_document = _normalize_lexical_text(document)
    if normalized_query == normalized_document:
        return 1.0
    if normalized_query in normalized_document:
        return _DIRECT_LEXICAL_MATCH_SCORE
    return 0.0


def _chunk_family_id(
    chunk_id: str,
    hierarchy: Mapping[str, _ChunkContext],
) -> str:
    current = chunk_id
    visited: set[str] = set()
    while True:
        if current in visited:
            raise IndexIntegrityError(f"分层 chunk 出现父子循环：{chunk_id}")
        visited.add(current)
        context = hierarchy.get(current)
        if context is None or context.parent_chunk_id is None:
            return current
        current = context.parent_chunk_id


def _normalize_lexical_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _validate_vector_space_id(value: Any, vector_id: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        prefix = f"vector_id={vector_id} 的" if vector_id is not None else ""
        raise IndexIntegrityError(f"{prefix}vector_space_id 为空")
    return value.strip()


def _validate_vector_ids(vector_ids: Sequence[int]) -> list[int]:
    ids = list(vector_ids)
    if any(not isinstance(value, (int, np.integer)) or int(value) < 0 for value in ids):
        raise ValueError("vector_id 必须是非负整数")
    return [int(value) for value in ids]


def _as_float32_matrix(
    vectors: Sequence[Sequence[float]] | np.ndarray,
    dimensions: int,
) -> np.ndarray:
    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError("vectors 必须是规则的二维数值数组") from exc
    if matrix.size == 0:
        return np.empty((0, dimensions), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != dimensions:
        raise ValueError(f"向量维度错误：期望 (*, {dimensions})，实际 {matrix.shape}")
    if not np.isfinite(matrix).all():
        raise ValueError("向量包含 NaN 或 Infinity")
    return np.ascontiguousarray(matrix)


def _validate_score_multipliers(values: Mapping[str, float]) -> None:
    for chunk_id, factor in values.items():
        if not isinstance(chunk_id, str) or not chunk_id:
            raise RAGQueryError("score_multipliers 的键必须是非空 chunk_id")
        if (
            not isinstance(factor, (int, float, np.integer, np.floating))
            or isinstance(factor, bool)
            or not np.isfinite(factor)
            or float(factor) < 1.0
        ):
            raise RAGQueryError("score_multipliers 的增强系数必须是大于等于 1 的有限数")
