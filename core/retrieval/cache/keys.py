from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from core.settings.schema import AppConfig
from core.common.cache import READ_CACHE, databases_revision, files_revision
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_graph, connect_rag, connect_sources

CACHE_FORMAT_VERSION = "4"

_KEY_LOCKS: dict[tuple[Path, str], tuple[threading.RLock, int]] = {}

_KEY_LOCKS_GUARD = threading.Lock()

def normalize_query(query: str) -> str:
    """对缓存查询文本执行稳定且不改变内部语义的规范化。"""

    if not isinstance(query, str):
        raise TypeError("query 必须是字符串")
    normalized = unicodedata.normalize("NFKC", query.strip())
    if not normalized:
        raise ValueError("query 不能为空")
    return normalized

def search_config_hash(settings: AppConfig) -> str:
    """返回所有会影响查询结果的配置摘要，不包含密钥等敏感字段。"""

    from core.documents.query_planner import query_planner_signature

    payload = {
        "cache_format": CACHE_FORMAT_VERSION,
        "default_confidence": settings.default_confidence,
        "max_query_depth": settings.max_query_depth,
        "rag_similarity_threshold": settings.rag_similarity_threshold,
        "default_top_k": settings.default_top_k,
        "rerank_top_n": settings.rerank_top_n,
        "graph_path_limit": settings.graph_path_limit,
        "hybrid_enhancement_factor": settings.hybrid_enhancement_factor,
        "entity_extraction": settings.entity_extraction.model_dump(mode="json"),
        "models": settings.models.model_dump(mode="json"),
        "vector_search": settings.vector_search.model_dump(mode="json"),
        "query_planner": query_planner_signature(settings),
        "kemo": {
            "base_url": settings.kemo.base_url.rstrip("/"),
            "protocol_version": settings.kemo.protocol_version,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def compute_state_hash(paths: DatabasePaths, settings: AppConfig) -> str:
    """聚合查询相关权威数据与元信息，生成内容驱动的知识库指纹。"""

    def revision():
        databases = databases_revision(paths.sources_db, paths.graph_db, paths.rag_db)
        return None if databases is None else (databases, files_revision(paths.graph_meta, paths.rag_meta))

    return READ_CACHE.get_or_load(
        ("search-state", str(paths.data_dir.resolve()), search_config_hash(settings)),
        revision, lambda: _compute_state_hash_uncached(paths, settings),
    )

def _compute_state_hash_uncached(paths: DatabasePaths, settings: AppConfig) -> str:

    digest = hashlib.sha256()
    _feed_digest(digest, f"cache-format:{CACHE_FORMAT_VERSION}")
    _feed_digest(digest, f"search-config:{search_config_hash(settings)}")

    if paths.sources_db.is_file():
        connection = connect_sources(paths)
        try:
            _hash_query_rows(
                digest,
                "sources",
                connection,
                """
                SELECT source_id, relative_path, content_hash, graph_hash, rag_hash,
                       graph_status, rag_status, exists_status
                FROM sources
                WHERE exists_status = 'active'
                ORDER BY source_id
                """,
            )
        finally:
            connection.close()
    else:
        _feed_digest(digest, "sources:missing")

    if paths.graph_db.is_file():
        connection = connect_graph(paths)
        try:
            graph_queries = (
                (
                    "nodes",
                    """
                    SELECT node_id, keyword, summary, aliases, tags,
                           weight, ref_count, created_at, updated_at
                    FROM nodes ORDER BY node_id
                    """,
                ),
                (
                    "node_sources",
                    """
                    SELECT node_id, source_id, content_hash,
                           evidence_weight, evidence
                    FROM node_sources ORDER BY node_id, source_id
                    """,
                ),
                (
                    "edges",
                    """
                    SELECT edge_id, source_node_id, relation, target_node_id,
                           weight, support_count, created_at
                    FROM edges ORDER BY edge_id
                    """,
                ),
                (
                    "edge_sources",
                    """
                    SELECT edge_id, source_id, content_hash, evidence_weight
                    FROM edge_sources ORDER BY edge_id, source_id
                    """,
                ),
                (
                    "groups",
                    """
                    SELECT group_id, summary, node_count, edge_count,
                           created_at, updated_at
                    FROM groups ORDER BY group_id
                    """,
                ),
                (
                    "group_nodes",
                    """
                    SELECT group_id, node_id
                    FROM group_nodes ORDER BY group_id, node_id
                    """,
                ),
            )
            for label, query in graph_queries:
                _hash_query_rows(digest, label, connection, query)
        finally:
            connection.close()
    else:
        _feed_digest(digest, "graph:missing")

    if paths.rag_db.is_file():
        connection = connect_rag(paths)
        try:
            rag_queries = (
                (
                    "chunks",
                    """
                    SELECT chunk_id, source_id, chunk_index, token_count,
                           granularity, parent_chunk_id, token_start, token_end,
                           created_at
                    FROM chunks ORDER BY chunk_id
                    """,
                ),
                (
                    "chunk_nodes",
                    """
                    SELECT chunk_id, node_id
                    FROM chunk_nodes ORDER BY chunk_id, node_id
                    """,
                ),
                (
                    "embeddings",
                    """
                    SELECT vector_id, chunk_id, source_id, dimensions,
                           model_name, vector_space_id, created_at
                    FROM embeddings ORDER BY vector_id
                    """,
                ),
                (
                    "entity_embeddings",
                    """
                    SELECT vector_id, node_id, summary_hash, dimensions,
                           model_name, vector_space_id, created_at, updated_at
                    FROM entity_embeddings ORDER BY vector_id
                    """,
                ),
                (
                    "community_embeddings",
                    """
                    SELECT vector_id, group_id, summary_hash, dimensions,
                           model_name, vector_space_id, created_at, updated_at
                    FROM community_embeddings ORDER BY vector_id
                    """,
                ),
            )
            for label, query in rag_queries:
                _hash_query_rows(digest, label, connection, query)
        finally:
            connection.close()
    else:
        _feed_digest(digest, "rag:missing")

    for label, path in (
        ("graph_meta", paths.graph_meta),
        ("rag_meta", paths.rag_meta),
    ):
        _feed_digest(digest, label)
        if path.is_file():
            _feed_digest(digest, path.read_bytes())
        else:
            _feed_digest(digest, b"missing")
    return digest.hexdigest()

def make_cache_key(
    query: str,
    state_hash: str,
    params: Mapping[str, Any],
    *,
    query_mode: str = "unknown",
    config_hash: str = "",
) -> str:
    """生成包含检索模式、状态和有效参数的稳定缓存键。"""

    normalized = normalize_query(query)
    canonical_params = json.dumps(
        dict(params),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    value = "|".join(
        (query_mode.strip().casefold(), normalized, state_hash, config_hash, canonical_params)
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

@contextmanager
def cache_key_lock(paths: DatabasePaths, cache_key: str) -> Iterator[None]:
    """在长驻进程内合并相同缓存键的并发查询。"""

    identity = (paths.search_cache_db.resolve(), cache_key)
    with _KEY_LOCKS_GUARD:
        lock, users = _KEY_LOCKS.get(identity, (threading.RLock(), 0))
        _KEY_LOCKS[identity] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _KEY_LOCKS_GUARD:
            _, users = _KEY_LOCKS[identity]
            if users == 1:
                del _KEY_LOCKS[identity]
            else:
                _KEY_LOCKS[identity] = (lock, users - 1)

def _hash_query_rows(
    digest: Any,
    label: str,
    connection: sqlite3.Connection,
    query: str,
    parameters: Sequence[Any] = (),
) -> None:
    _feed_digest(digest, label)
    for row in connection.execute(query, tuple(parameters)):
        encoded = json.dumps(
            list(row),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _feed_digest(digest, encoded)

def _feed_digest(digest: Any, value: str | bytes) -> None:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
