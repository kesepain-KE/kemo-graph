from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
import numpy as np
from provider.embedding import EmbeddingResult
from core.settings import AppConfig
from core.settings import load_config
from core.storage import initialize_databases
from core.observability import DailyTSVLogger
from core.rag.index import FaissIndexManager
from core.observability import tracked_step
from core.rag.models import PreparedQuery, _Candidate, _ChunkContext
from core.documents import QueryPlan

from core.rag import query as query_ops, loading, vectors, store
from core.rag.auxiliary import RAGAuxiliary

Embedder = Callable[[list[str]], EmbeddingResult]
Reranker = Callable[[str, list[str], int], list[tuple[int, float]]]

class RAGEngine:
    """Coordinate query, chunk-index and auxiliary-vector domains."""

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
        self.entity_index = FaissIndexManager(
            self.paths.entity_faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self.community_index = FaissIndexManager(
            self.paths.community_faiss_index,
            self.settings.models.embedding_dimensions,
            autoload=False,
        )
        self._embedder = embedder
        self._reranker = reranker
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )
        self.auxiliary = RAGAuxiliary(self)
        self._prune_stale_auxiliary_embeddings()
        self._ensure_index_consistency()
        self._ensure_entity_index_consistency()
        self._ensure_community_index_consistency()

    def query(self, query: str, *, top_k: int | None=None, threshold: float | None=None, score_multipliers: Mapping[str, float] | None=None, prepared_query: PreparedQuery | None=None) -> dict[str, Any]:
        return query_ops.query(self, query, top_k=top_k, threshold=threshold, score_multipliers=score_multipliers, prepared_query=prepared_query)

    def prepare_query(self, query: str) -> PreparedQuery:
        return query_ops.prepare_query(self, query)

    def build_entity_vectors(self, node_id: str, summary: str) -> dict[str, Any]:
        """为单个实体建立或刷新向量；新鲜度由摘要及向量元数据决定。"""

        return self.sync_entity_vectors([(node_id, summary)])

    def build_community_vectors(
        self,
        group_id: str,
        summary: str,
    ) -> dict[str, Any]:
        """为单个群组建立或刷新向量。"""

        return self.sync_community_vectors([(group_id, summary)])

    def sync_entity_vectors(
        self,
        entities: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        """批量补齐或刷新实体向量，跳过摘要和模型元数据均未变化的项。"""

        result = self._sync_auxiliary_vectors("entity", entities)
        self._refresh_entity_embedding_flags()
        return result

    def sync_community_vectors(
        self,
        communities: Sequence[tuple[str, str]],
    ) -> dict[str, Any]:
        """批量补齐或刷新群组向量。"""

        return self._sync_auxiliary_vectors("community", communities)

    @tracked_step("entities")
    def search_entities(
        self,
        query: str,
        top_k: int | None = None,
        *,
        prepared_query: PreparedQuery | None = None,
    ) -> list[dict[str, Any]]:
        """在实体摘要索引中执行语义检索。"""

        effective_top_k = (
            top_k if top_k is not None else self.settings.vector_search.entity_top_k
        )
        return self._search_auxiliary_vectors(
            "entity",
            query,
            effective_top_k,
            self.settings.vector_search.entity_weight,
            prepared_query=prepared_query,
        )

    @tracked_step("communities")
    def search_communities(
        self,
        query: str,
        top_k: int | None = None,
        *,
        prepared_query: PreparedQuery | None = None,
    ) -> list[dict[str, Any]]:
        """在群组总结索引中执行语义检索。"""

        effective_top_k = (
            top_k
            if top_k is not None
            else self.settings.vector_search.community_top_k
        )
        return self._search_auxiliary_vectors(
            "community",
            query,
            effective_top_k,
            self.settings.vector_search.community_weight,
            prepared_query=prepared_query,
        )

    def delete_entity_vectors(self, node_ids: Sequence[str]) -> int:
        """删除实体向量，并在索引增量删除失败时从 SQLite 恢复。"""

        removed = self._delete_auxiliary_vectors("entity", node_ids)
        self._refresh_entity_embedding_flags()
        return removed

    def delete_community_vectors(self, group_ids: Sequence[str]) -> int:
        """删除群组向量，并在索引增量删除失败时从 SQLite 恢复。"""

        return self._delete_auxiliary_vectors("community", group_ids)

    def rebuild_entity_index(self) -> int:
        """从 entity_embeddings 全量重建实体索引。"""

        return self._rebuild_auxiliary_index("entity")

    def rebuild_community_index(self) -> int:
        """从 community_embeddings 全量重建群组索引。"""

        return self._rebuild_auxiliary_index("community")

    def ensure_entity_index_consistency(self) -> None:
        self._ensure_entity_index_consistency()

    def ensure_community_index_consistency(self) -> None:
        self._ensure_community_index_consistency()

    def refresh_auxiliary_consistency(self) -> None:
        """清理图谱对象已删除或摘要已变化的辅助向量并同步两个索引。"""

        self._prune_stale_auxiliary_embeddings()
        self._ensure_entity_index_consistency()
        self._ensure_community_index_consistency()

    def add_vectors(self, vector_ids: Sequence[int], vectors: Sequence[Sequence[float]] | np.ndarray) -> None:
        return store.add_vectors(self, vector_ids, vectors)

    def delete_vectors(self, vector_ids: Sequence[int]) -> int:
        return store.delete_vectors(self, vector_ids)

    def replace_vectors(self, remove_ids: Sequence[int], add_ids: Sequence[int], vectors: Sequence[Sequence[float]] | np.ndarray) -> int:
        return store.replace_vectors(self, remove_ids, add_ids, vectors)

    def refresh_meta(self) -> None:
        return store.refresh_meta(self)

    def ensure_index_consistency(self) -> None:
        return store.ensure_index_consistency(self)

    def search_vectors(self, vectors: Sequence[Sequence[float]] | np.ndarray, top_k: int) -> list[list[tuple[int, float]]]:
        return store.search_vectors(self, vectors, top_k)

    def rebuild_index(self) -> int:
        return store.rebuild_index(self)

    def _embed_texts(self, texts: list[str], *, input_type: str) -> EmbeddingResult:
        return vectors._embed_texts(self, texts, input_type=input_type)

    def _ensure_index_consistency(self) -> None:
        return store._ensure_index_consistency(self)

    def _database_vector_ids(self) -> set[int]:
        return store._database_vector_ids(self)

    def _database_vector_space_id(self) -> str | None:
        return store._database_vector_space_id(self)

    def _load_candidates(self, raw_hits: Iterable[Iterable[tuple[int, float]]], *, query_weights: Sequence[float] | None=None, rrf_k: int=60) -> list[_Candidate]:
        return loading._load_candidates(self, raw_hits, query_weights=query_weights, rrf_k=rrf_k)

    def _load_lexical_candidates(self, plan: QueryPlan, *, limit: int) -> list[_Candidate]:
        return loading._load_lexical_candidates(self, plan, limit=limit)

    def _load_chunk_hierarchy(self, candidates: Sequence[_Candidate]) -> dict[str, _ChunkContext]:
        return loading._load_chunk_hierarchy(self, candidates)

    def _load_source_paths(self, source_ids: set[str]) -> dict[str, dict[str, str | None]]:
        return loading._load_source_paths(self, source_ids)

    def _refresh_meta(self) -> None:
        return store._refresh_meta(self)

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

    def _sync_auxiliary_vectors(self, kind: str, records: Sequence[tuple[str, str]]) -> dict[str, Any]:
        return self.auxiliary._sync_auxiliary_vectors(kind, records)

    def _search_auxiliary_vectors(self, kind: str, query: str, top_k: int, weight: float, *, prepared_query: PreparedQuery | None=None) -> list[dict[str, Any]]:
        return self.auxiliary._search_auxiliary_vectors(kind, query, top_k, weight, prepared_query=prepared_query)

    def _load_auxiliary_results(self, kind: str, vector_scores: Mapping[int, float], weight: float) -> list[dict[str, Any]]:
        return self.auxiliary._load_auxiliary_results(kind, vector_scores, weight)

    def _delete_auxiliary_vectors(self, kind: str, object_ids: Sequence[str]) -> int:
        return self.auxiliary._delete_auxiliary_vectors(kind, object_ids)

    def _ensure_entity_index_consistency(self) -> None:
        return self.auxiliary._ensure_entity_index_consistency()

    def _ensure_community_index_consistency(self) -> None:
        return self.auxiliary._ensure_community_index_consistency()

    def _ensure_auxiliary_index_consistency(self, kind: str) -> None:
        return self.auxiliary._ensure_auxiliary_index_consistency(kind)

    def _rebuild_auxiliary_index(self, kind: str) -> int:
        return self.auxiliary._rebuild_auxiliary_index(kind)

    def _database_auxiliary_vector_ids(self, kind: str) -> set[int]:
        return self.auxiliary._database_auxiliary_vector_ids(kind)

    def _validated_auxiliary_matrix(self, rows: Sequence[Any], table: str) -> tuple[list[int], np.ndarray]:
        return self.auxiliary._validated_auxiliary_matrix(rows, table)

    def _auxiliary_vector_space_id(self, kind: str) -> str | None:
        return self.auxiliary._auxiliary_vector_space_id(kind)

    def _prune_stale_auxiliary_embeddings(self) -> None:
        return self.auxiliary._prune_stale_auxiliary_embeddings()

    def _refresh_entity_embedding_flags(self) -> None:
        return self.auxiliary._refresh_entity_embedding_flags()

    def _auxiliary_spec(self, kind: str) -> tuple[str, str, FaissIndexManager]:
        return self.auxiliary._auxiliary_spec(kind)
