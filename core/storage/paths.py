from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatabasePaths:
    """一个知识库中所有基础存储位置。"""

    data_dir: Path
    sources_db: Path
    search_cache_db: Path
    graph_dir: Path
    graph_db: Path
    graph_meta: Path
    rag_dir: Path
    rag_db: Path
    rag_meta: Path
    vector_index_dir: Path
    faiss_index: Path
    entity_faiss_index: Path
    community_faiss_index: Path
    rerank_cache: Path


def get_database_paths(data_dir: Path | str) -> DatabasePaths:
    """根据知识库根目录构造全部存储路径。"""

    root = Path(data_dir).expanduser().resolve()
    graph_dir = root / "Graph"
    rag_dir = root / "RAG"
    vector_index_dir = rag_dir / "vector_index"
    return DatabasePaths(
        data_dir=root,
        sources_db=root / "sources.db",
        search_cache_db=root / "search_cache.db",
        graph_dir=graph_dir,
        graph_db=graph_dir / "graph.db",
        graph_meta=graph_dir / "graph_meta.json",
        rag_dir=rag_dir,
        rag_db=rag_dir / "rag.db",
        rag_meta=rag_dir / "rag_meta.json",
        vector_index_dir=vector_index_dir,
        faiss_index=vector_index_dir / "index.faiss",
        entity_faiss_index=vector_index_dir / "entity_index.faiss",
        community_faiss_index=vector_index_dir / "community_index.faiss",
        rerank_cache=rag_dir / "rerank_cache.txt",
    )
