"""SQLite schema initialization and metadata file management."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config


SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    original_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    path_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    graph_hash TEXT,
    rag_hash TEXT,
    graph_status TEXT DEFAULT 'pending',
    rag_status TEXT DEFAULT 'pending',
    exists_status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_path_hash ON sources(path_hash);
CREATE INDEX IF NOT EXISTS idx_sources_graph_status ON sources(graph_status);
CREATE INDEX IF NOT EXISTS idx_sources_rag_status ON sources(rag_status);
"""


GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    summary TEXT NOT NULL,
    aliases TEXT,
    tags TEXT,
    ref_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS node_sources (
    node_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(node_id, source_id),
    FOREIGN KEY(node_id) REFERENCES nodes(node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    weight REAL DEFAULT 0,
    support_count INTEGER DEFAULT 0,
    created_at TEXT,
    UNIQUE(source_node_id, relation, target_node_id),
    FOREIGN KEY(source_node_id) REFERENCES nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(target_node_id) REFERENCES nodes(node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edge_sources (
    edge_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evidence_weight REAL DEFAULT 1.0,
    UNIQUE(edge_id, source_id),
    FOREIGN KEY(edge_id) REFERENCES edges(edge_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    node_count INTEGER,
    edge_count INTEGER,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS group_nodes (
    group_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    UNIQUE(group_id, node_id),
    FOREIGN KEY(group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY(node_id) REFERENCES nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nodes_keyword ON nodes(keyword);
CREATE INDEX IF NOT EXISTS idx_node_sources_node ON node_sources(node_id);
CREATE INDEX IF NOT EXISTS idx_node_sources_source ON node_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_sources_edge ON edge_sources(edge_id);
CREATE INDEX IF NOT EXISTS idx_edge_sources_source ON edge_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_group_nodes_group ON group_nodes(group_id);
CREATE INDEX IF NOT EXISTS idx_group_nodes_node ON group_nodes(node_id);
"""


RAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_index INTEGER,
    token_count INTEGER,
    granularity TEXT NOT NULL DEFAULT 'medium',
    parent_chunk_id TEXT,
    token_start INTEGER,
    token_end INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS chunk_nodes (
    chunk_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    UNIQUE(chunk_id, node_id),
    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embeddings (
    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    vector_space_id TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT,
    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunk_nodes_chunk ON chunk_nodes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_nodes_node ON chunk_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_chunk ON embeddings(chunk_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_vector_id ON embeddings(vector_id);
"""


@dataclass(frozen=True)
class DatabasePaths:
    """一个知识库中所有基础存储位置。"""

    data_dir: Path
    sources_db: Path
    graph_dir: Path
    graph_db: Path
    graph_meta: Path
    rag_dir: Path
    rag_db: Path
    rag_meta: Path
    vector_index_dir: Path
    faiss_index: Path
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
        graph_dir=graph_dir,
        graph_db=graph_dir / "graph.db",
        graph_meta=graph_dir / "graph_meta.json",
        rag_dir=rag_dir,
        rag_db=rag_dir / "rag.db",
        rag_meta=rag_dir / "rag_meta.json",
        vector_index_dir=vector_index_dir,
        faiss_index=vector_index_dir / "index.faiss",
        rerank_cache=rag_dir / "rerank_cache.txt",
    )


def initialize_databases(
    data_dir: Path | str | None = None,
    settings: AppConfig | None = None,
) -> DatabasePaths:
    """幂等创建目录、三套 SQLite schema、meta 和空重排缓存。"""

    active_settings = settings or load_config()
    resolved_data_dir = data_dir or active_settings.resolve_data_dir()
    paths = get_database_paths(resolved_data_dir)

    paths.graph_dir.mkdir(parents=True, exist_ok=True)
    paths.vector_index_dir.mkdir(parents=True, exist_ok=True)

    _initialize_sqlite(paths.sources_db, SOURCES_SCHEMA)
    _initialize_sqlite(paths.graph_db, GRAPH_SCHEMA)
    _initialize_sqlite(paths.rag_db, RAG_SCHEMA)
    _ensure_rag_vector_space_column(paths.rag_db)
    _ensure_rag_chunk_hierarchy_columns(paths.rag_db)

    if not paths.graph_meta.exists():
        write_graph_meta(paths, default_graph_meta())
    if not paths.rag_meta.exists():
        write_rag_meta(paths, default_rag_meta(active_settings))
    paths.rerank_cache.touch(exist_ok=True)

    return paths


def connect_sources(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.sources_db)


def connect_graph(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.graph_db)


def connect_rag(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.rag_db)


def default_graph_meta() -> dict[str, Any]:
    return {
        "total_nodes": 0,
        "total_edges": 0,
        "total_groups": 0,
        "last_summary_at": None,
        "changed_since_summary": 0,
    }


def default_rag_meta(settings: AppConfig | None = None) -> dict[str, Any]:
    active_settings = settings or load_config()
    return {
        "total_chunks": 0,
        "total_vectors": 0,
        "vector_dimensions": active_settings.models.embedding_dimensions,
        "embedding_model": active_settings.models.embedding,
        "vector_space_id": None,
        "chunking_signature": None,
        "faiss_index_type": "IndexIDMap2+IndexFlatIP",
        "last_built_at": None,
    }


def read_graph_meta(paths: DatabasePaths) -> dict[str, Any]:
    return _read_meta(paths.graph_meta, default_graph_meta())


def write_graph_meta(paths: DatabasePaths, meta: dict[str, Any]) -> None:
    _write_json_atomic(paths.graph_meta, meta)


def read_rag_meta(
    paths: DatabasePaths, settings: AppConfig | None = None
) -> dict[str, Any]:
    return _read_meta(paths.rag_meta, default_rag_meta(settings))


def write_rag_meta(paths: DatabasePaths, meta: dict[str, Any]) -> None:
    _write_json_atomic(paths.rag_meta, meta)


def _initialize_sqlite(path: Path, schema: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(path)
    try:
        connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()


def _ensure_rag_vector_space_column(path: Path) -> None:
    """幂等迁移旧 rag.db，为 embeddings 增加向量空间标识。"""

    connection = _connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(embeddings)").fetchall()
        }
        if "vector_space_id" not in columns:
            connection.execute(
                """
                ALTER TABLE embeddings
                ADD COLUMN vector_space_id TEXT NOT NULL DEFAULT 'unknown'
                """
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_rag_chunk_hierarchy_columns(path: Path) -> None:
    """幂等迁移旧 rag.db，增加分层切片元数据。"""

    connection = _connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
        }
        additions = {
            "granularity": "TEXT NOT NULL DEFAULT 'medium'",
            "parent_chunk_id": "TEXT",
            "token_start": "INTEGER",
            "token_end": "INTEGER",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE chunks ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_granularity ON chunks(granularity)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_chunk_id)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _read_meta(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        _write_json_atomic(path, defaults)
        return deepcopy_dict(defaults)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"元数据文件不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"元数据文件根节点必须是对象：{path}")
    return {**defaults, **loaded}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def deepcopy_dict(value: dict[str, Any]) -> dict[str, Any]:
    """通过 JSON 往返复制仅含 JSON 值的元数据。"""

    return json.loads(json.dumps(value))
