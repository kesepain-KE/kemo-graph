"""图谱结果增强向量候选的混合检索。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .db import connect_rag
from .graph_engine import GraphEngine
from .rag_engine import RAGEngine


class HybridConfigurationError(RuntimeError):
    """Graph 与 RAG 引擎不属于同一知识库。"""


class HybridEngine:
    """先执行完整图谱检索，再用图谱节点增强 RAG 候选。"""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        settings: AppConfig | None = None,
        graph_engine: GraphEngine | None = None,
        rag_engine: RAGEngine | None = None,
    ) -> None:
        self.settings = (
            settings
            or (graph_engine.settings if graph_engine is not None else None)
            or (rag_engine.settings if rag_engine is not None else None)
            or load_config()
        )
        selected_data_dir = _select_data_dir(data_dir, graph_engine, rag_engine)
        self.graph_engine = graph_engine or GraphEngine(
            selected_data_dir,
            settings=self.settings,
        )
        self.rag_engine = rag_engine or RAGEngine(
            selected_data_dir,
            settings=self.settings,
        )
        if self.graph_engine.paths.data_dir != self.rag_engine.paths.data_dir:
            raise HybridConfigurationError(
                "GraphEngine 与 RAGEngine 必须使用同一个知识库目录"
            )

    def query(
        self,
        query: str,
        *,
        graph_depth: int = 3,
        rag_top_k: int | None = None,
        graph_confidence: float | None = None,
        rag_threshold: float | None = None,
        direction: str = "both",
    ) -> dict[str, Any]:
        """执行图谱锚定 RAG，并补充实体与群组两路语义结果。"""

        prepared_query = self.rag_engine.prepare_query(query)
        graph_result = self.graph_engine.query(
            query,
            depth=graph_depth,
            direction=direction,
            confidence=graph_confidence,
            query_plan=prepared_query.plan,
        )
        graph_node_ids = {
            node["node_id"]
            for field in ("hit_nodes", "expanded_nodes")
            for node in graph_result[field]
        }
        anchored_chunk_ids = self.get_anchored_chunk_ids(graph_node_ids)
        score_multipliers = {
            chunk_id: self.settings.hybrid_enhancement_factor
            for chunk_id in anchored_chunk_ids
        }
        rag_result = self.rag_engine.query(
            query,
            top_k=rag_top_k,
            threshold=rag_threshold,
            score_multipliers=score_multipliers,
            prepared_query=prepared_query,
        )
        entity_results = self.rag_engine.search_entities(
            query,
            prepared_query=prepared_query,
        )
        community_results = self.rag_engine.search_communities(
            query,
            prepared_query=prepared_query,
        )
        return {
            "graph": graph_result,
            "rag": rag_result,
            "entities": entity_results,
            "communities": community_results,
        }

    def get_anchored_chunk_ids(self, node_ids: set[str]) -> set[str]:
        """通过 rag.db.chunk_nodes 找到图谱节点锚定的 chunk。"""

        if not node_ids:
            return set()
        placeholders = ",".join("?" for _ in node_ids)
        connection = connect_rag(self.rag_engine.paths)
        try:
            rows = connection.execute(
                f"""
                SELECT DISTINCT chunk_id
                FROM chunk_nodes
                WHERE node_id IN ({placeholders})
                """,
                tuple(sorted(node_ids)),
            ).fetchall()
        finally:
            connection.close()
        return {row["chunk_id"] for row in rows}


def _select_data_dir(
    data_dir: Path | str | None,
    graph_engine: GraphEngine | None,
    rag_engine: RAGEngine | None,
) -> Path | str | None:
    requested = Path(data_dir).expanduser().resolve() if data_dir is not None else None
    supplied_paths = [
        engine.paths.data_dir
        for engine in (graph_engine, rag_engine)
        if engine is not None
    ]
    if requested is not None and any(path != requested for path in supplied_paths):
        raise HybridConfigurationError(
            "data_dir 与传入的 GraphEngine/RAGEngine 知识库目录不一致"
        )
    if len(set(supplied_paths)) > 1:
        raise HybridConfigurationError(
            "传入的 GraphEngine 与 RAGEngine 知识库目录不一致"
        )
    return requested or (supplied_paths[0] if supplied_paths else None)
