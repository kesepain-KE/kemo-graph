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

CREATE TABLE IF NOT EXISTS maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES maintenance_jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_updated
ON maintenance_jobs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_job_events_job
ON maintenance_job_events(job_id, created_at);
"""


GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    summary TEXT NOT NULL,
    aliases TEXT,
    tags TEXT,
    weight REAL DEFAULT 1.0,
    ref_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS node_sources (
    node_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evidence_weight REAL DEFAULT 1.0,
    evidence TEXT,
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

CREATE TABLE IF NOT EXISTS entity_mentions (
    mention_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    local_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    summary TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    evidence_weight REAL NOT NULL DEFAULT 1.0,
    evidence TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, content_hash, local_id)
);

CREATE TABLE IF NOT EXISTS relation_mentions (
    mention_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_mention_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    target_mention_id TEXT NOT NULL,
    evidence_weight REAL NOT NULL DEFAULT 1.0,
    evidence TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_mention_id) REFERENCES entity_mentions(mention_id) ON DELETE CASCADE,
    FOREIGN KEY(target_mention_id) REFERENCES entity_mentions(mention_id) ON DELETE CASCADE,
    UNIQUE(source_id, content_hash, source_mention_id, relation, target_mention_id)
);

CREATE TABLE IF NOT EXISTS mention_nodes (
    mention_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    FOREIGN KEY(mention_id) REFERENCES entity_mentions(mention_id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS idx_entity_mentions_source ON entity_mentions(source_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_keyword ON entity_mentions(keyword);
CREATE INDEX IF NOT EXISTS idx_relation_mentions_source ON relation_mentions(source_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_mention_nodes_node ON mention_nodes(node_id);
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

CREATE TABLE IF NOT EXISTS entity_embeddings (
    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    summary_hash TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    vector_space_id TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS community_embeddings (
    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    summary_hash TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    vector_space_id TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunk_nodes_chunk ON chunk_nodes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_nodes_node ON chunk_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_chunk ON embeddings(chunk_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_vector_id ON embeddings(vector_id);
CREATE INDEX IF NOT EXISTS idx_entity_embeddings_node ON entity_embeddings(node_id);
CREATE INDEX IF NOT EXISTS idx_community_embeddings_group ON community_embeddings(group_id);
"""


SEARCH_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key TEXT PRIMARY KEY,
    query_mode TEXT NOT NULL,
    query TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL,
    result_size INTEGER NOT NULL DEFAULT 0,
    state_hash TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_hit_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_search_cache_query
ON search_cache(normalized_query, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_cache_state
ON search_cache(state_hash);
CREATE INDEX IF NOT EXISTS idx_search_cache_recent
ON search_cache(last_hit_at DESC, updated_at DESC);
"""


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


def initialize_databases(
    data_dir: Path | str | None = None,
    settings: AppConfig | None = None,
) -> DatabasePaths:
    """幂等创建目录、权威 SQLite、搜索缓存、meta 和空重排缓存。"""

    active_settings = settings or load_config()
    resolved_data_dir = data_dir or active_settings.resolve_data_dir()
    paths = get_database_paths(resolved_data_dir)

    paths.graph_dir.mkdir(parents=True, exist_ok=True)
    paths.vector_index_dir.mkdir(parents=True, exist_ok=True)

    _initialize_sqlite(paths.sources_db, SOURCES_SCHEMA)
    _initialize_sqlite(paths.graph_db, GRAPH_SCHEMA)
    _initialize_sqlite(paths.rag_db, RAG_SCHEMA)
    initialize_search_cache(paths)
    _ensure_rag_vector_space_column(paths.rag_db)
    _ensure_rag_chunk_hierarchy_columns(paths.rag_db)
    _ensure_rag_entity_community_tables(paths.rag_db)
    _ensure_graph_phase10_columns(paths.graph_db)
    _ensure_graph_entity_embedding_column(paths.graph_db)

    if not paths.graph_meta.exists():
        write_graph_meta(paths, default_graph_meta())
    if not paths.rag_meta.exists():
        write_rag_meta(paths, default_rag_meta(active_settings))
    paths.rerank_cache.touch(exist_ok=True)

    return paths


def connect_sources(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.sources_db)


def initialize_search_cache(paths: DatabasePaths) -> None:
    """只初始化独立搜索缓存，不触碰 Graph/RAG 权威数据。"""

    _initialize_sqlite(paths.search_cache_db, SEARCH_CACHE_SCHEMA)
    _ensure_search_cache_columns(paths.search_cache_db)


def connect_search_cache(paths: DatabasePaths) -> sqlite3.Connection:
    return _connect(paths.search_cache_db)


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


def _ensure_rag_entity_community_tables(path: Path) -> None:
    """幂等创建实体/群组向量表，并补齐早期试验版缺失列。"""

    connection = _connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS entity_embeddings (
                vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                summary_hash TEXT NOT NULL DEFAULT '',
                vector_blob BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                vector_space_id TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS community_embeddings (
                vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                summary_hash TEXT NOT NULL DEFAULT '',
                vector_blob BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                vector_space_id TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        for table in ("entity_embeddings", "community_embeddings"):
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            additions = {
                "summary_hash": "TEXT NOT NULL DEFAULT ''",
                "vector_space_id": "TEXT NOT NULL DEFAULT 'unknown'",
                "updated_at": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                    )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_embeddings_node "
            "ON entity_embeddings(node_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_community_embeddings_group "
            "ON community_embeddings(group_id)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_graph_phase10_columns(path: Path) -> None:
    """幂等迁移旧 graph.db，增加节点证据权重和来源证据字段。"""

    connection = _connect(path)
    try:
        node_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(nodes)").fetchall()
        }
        if "weight" not in node_columns:
            connection.execute(
                "ALTER TABLE nodes ADD COLUMN weight REAL DEFAULT 1.0"
            )
        source_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(node_sources)").fetchall()
        }
        if "evidence_weight" not in source_columns:
            connection.execute(
                "ALTER TABLE node_sources ADD COLUMN evidence_weight REAL DEFAULT 1.0"
            )
        if "evidence" not in source_columns:
            connection.execute("ALTER TABLE node_sources ADD COLUMN evidence TEXT")
        connection.execute(
            "UPDATE nodes SET weight = 1.0 WHERE weight IS NULL"
        )
        connection.execute(
            "UPDATE node_sources SET evidence_weight = 1.0 WHERE evidence_weight IS NULL"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_graph_entity_embedding_column(path: Path) -> None:
    """增加兼容性状态列；向量表中的新鲜度元数据仍是权威来源。"""

    connection = _connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(nodes)").fetchall()
        }
        if "has_entity_embedding" not in columns:
            connection.execute(
                "ALTER TABLE nodes ADD COLUMN has_entity_embedding INTEGER DEFAULT 0"
            )
        connection.execute(
            "UPDATE nodes SET has_entity_embedding = 0 "
            "WHERE has_entity_embedding IS NULL"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_search_cache_columns(path: Path) -> None:
    """兼容早期搜索缓存草案，并补齐历史浏览所需字段。"""

    connection = _connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(search_cache)").fetchall()
        }
        additions = {
            "query_mode": "TEXT NOT NULL DEFAULT 'hybrid'",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "last_hit_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE search_cache ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "UPDATE search_cache SET updated_at = created_at WHERE updated_at = ''"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_cache_recent "
            "ON search_cache(last_hit_at DESC, updated_at DESC)"
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
