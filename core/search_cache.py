"""跨 CLI、FastAPI 与网页端共享的持久化搜索缓存。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .config import AppConfig
from .db import (
    DatabasePaths,
    connect_graph,
    connect_rag,
    connect_search_cache,
    connect_sources,
    initialize_search_cache,
)


# Bump whenever retrieval semantics change.  Existing entries are still
# inspectable in the cache history, but must not mask a newly improved recall
# pipeline after an upgrade.
CACHE_FORMAT_VERSION = "3"


class SearchCacheError(RuntimeError):
    """搜索缓存无法读取、序列化或维护。"""


_KEY_LOCKS: dict[tuple[Path, str], threading.RLock] = {}
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

    from .query_planner import query_planner_signature

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
                SELECT source_id, content_hash, graph_hash, rag_hash,
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
        lock = _KEY_LOCKS.setdefault(identity, threading.RLock())
    with lock:
        yield


class SearchCache:
    """搜索结果缓存及历史记录的 SQLite 门面。"""

    def __init__(self, paths: DatabasePaths, settings: AppConfig) -> None:
        self.paths = paths
        self.settings = settings
        initialize_search_cache(paths)

    def get(
        self,
        cache_key: str,
        *,
        state_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """读取有效缓存并原子增加真实缓存命中次数。"""

        connection = connect_search_cache(self.paths)
        now = _now_iso()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None or (
                state_hash is not None and str(row["state_hash"]) != state_hash
            ):
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE search_cache
                SET hit_count = hit_count + 1,
                    last_hit_at = ?, updated_at = ?
                WHERE cache_key = ?
                """,
                (now, now, cache_key),
            )
            connection.commit()
            result = dict(row)
            result["hit_count"] = int(result["hit_count"]) + 1
            result["last_hit_at"] = now
            result["updated_at"] = now
            return result
        except sqlite3.Error as exc:
            connection.rollback()
            raise SearchCacheError(f"读取搜索缓存失败：{exc}") from exc
        finally:
            connection.close()

    def set(
        self,
        cache_key: str,
        query: str,
        state_hash: str,
        params: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        query_mode: str,
    ) -> dict[str, Any]:
        """写入成功查询；刷新时保留首次创建时间和累计命中次数。"""

        normalized = normalize_query(query)
        try:
            params_json = json.dumps(
                dict(params),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            result_json = json.dumps(
                dict(result),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise SearchCacheError(f"搜索结果无法 JSON 序列化：{exc}") from exc
        result_size = len(result_json.encode("utf-8"))
        now = _now_iso()
        connection = connect_search_cache(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO search_cache (
                    cache_key, query_mode, query, normalized_query,
                    params_json, result_json, result_size, state_hash,
                    hit_count, created_at, updated_at, last_hit_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(cache_key) DO UPDATE SET
                    query_mode = excluded.query_mode,
                    query = excluded.query,
                    normalized_query = excluded.normalized_query,
                    params_json = excluded.params_json,
                    result_json = excluded.result_json,
                    result_size = excluded.result_size,
                    state_hash = excluded.state_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key,
                    query_mode,
                    query,
                    normalized,
                    params_json,
                    result_json,
                    result_size,
                    state_hash,
                    now,
                    now,
                ),
            )
            self._prune_if_needed(connection)
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise SearchCacheError(f"写入搜索缓存失败：{exc}") from exc
        finally:
            connection.close()
        return {
            "cache_key": cache_key,
            "query_mode": query_mode,
            "result_size": result_size,
            "state_hash": state_hash,
        }

    def list(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """分页列出历史元数据，并标记相对于当前知识库是否过期。"""

        _validate_page(page, page_size)
        current_state = compute_state_hash(self.paths, self.settings)
        offset = (page - 1) * page_size
        connection = connect_search_cache(self.paths)
        try:
            total = int(
                connection.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT cache_key, query_mode, query, normalized_query,
                       params_json, result_size, state_hash, hit_count,
                       created_at, updated_at, last_hit_at
                FROM search_cache
                ORDER BY COALESCE(last_hit_at, updated_at, created_at) DESC,
                         cache_key
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SearchCacheError(f"列出搜索缓存失败：{exc}") from exc
        finally:
            connection.close()
        return {
            "items": [
                _metadata_from_row(row, current_state=current_state) for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def detail(self, cache_key: str) -> dict[str, Any] | None:
        """查看历史详情；浏览历史不会增加查询命中次数。"""

        connection = connect_search_cache(self.paths)
        try:
            row = connection.execute(
                "SELECT * FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise SearchCacheError(f"读取缓存详情失败：{exc}") from exc
        finally:
            connection.close()
        if row is None:
            return None
        current_state = compute_state_hash(self.paths, self.settings)
        result = _metadata_from_row(row, current_state=current_state)
        try:
            result["result"] = json.loads(str(row["result_json"]))
        except json.JSONDecodeError as exc:
            raise SearchCacheError(f"缓存结果 JSON 已损坏：{cache_key}") from exc
        return result

    def clear(self, stale_only: bool = False) -> int:
        """清空全部缓存，或只清理与当前知识库状态不匹配的记录。"""

        current_state = (
            compute_state_hash(self.paths, self.settings) if stale_only else None
        )
        connection = connect_search_cache(self.paths)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if stale_only:
                cursor = connection.execute(
                    "DELETE FROM search_cache WHERE state_hash != ?",
                    (current_state,),
                )
            else:
                cursor = connection.execute("DELETE FROM search_cache")
            removed = max(0, int(cursor.rowcount))
            connection.commit()
            return removed
        except sqlite3.Error as exc:
            connection.rollback()
            raise SearchCacheError(f"清理搜索缓存失败：{exc}") from exc
        finally:
            connection.close()

    def _prune_if_needed(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(result_size), 0) FROM search_cache"
        ).fetchone()
        count = int(row[0])
        total_bytes = int(row[1])
        excess_count = max(0, count - self.settings.search_cache_max_entries)
        excess_bytes = max(0, total_bytes - self.settings.search_cache_max_bytes)
        if not excess_count and not excess_bytes:
            return
        candidates = connection.execute(
            """
            SELECT cache_key, result_size FROM search_cache
            ORDER BY COALESCE(last_hit_at, updated_at, created_at), cache_key
            """
        ).fetchall()
        delete_keys: list[str] = []
        reclaimed = 0
        for row in candidates:
            if len(delete_keys) >= excess_count and reclaimed >= excess_bytes:
                break
            delete_keys.append(str(row["cache_key"]))
            reclaimed += int(row["result_size"])
        if delete_keys:
            placeholders = ",".join("?" for _ in delete_keys)
            connection.execute(
                f"DELETE FROM search_cache WHERE cache_key IN ({placeholders})",
                tuple(delete_keys),
            )


def decode_cached_result(record: Mapping[str, Any]) -> dict[str, Any]:
    """把 ``SearchCache.get`` 的记录还原为查询响应。"""

    try:
        result = json.loads(str(record["result_json"]))
    except (KeyError, json.JSONDecodeError) as exc:
        raise SearchCacheError("搜索缓存结果 JSON 已损坏") from exc
    if not isinstance(result, dict):
        raise SearchCacheError("搜索缓存结果根节点必须是对象")
    return result


def _metadata_from_row(
    row: sqlite3.Row | Mapping[str, Any],
    *,
    current_state: str,
) -> dict[str, Any]:
    try:
        params = json.loads(str(row["params_json"]))
    except json.JSONDecodeError:
        params = {}
    return {
        "cache_key": str(row["cache_key"]),
        "query_mode": str(row["query_mode"]),
        "query": str(row["query"]),
        "normalized_query": str(row["normalized_query"]),
        "params": params if isinstance(params, dict) else {},
        "params_json": str(row["params_json"]),
        "result_size": int(row["result_size"]),
        "state_hash": str(row["state_hash"]),
        "is_stale": str(row["state_hash"]) != current_state,
        "hit_count": int(row["hit_count"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "last_hit_at": row["last_hit_at"],
    }


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


def _validate_page(page: int, page_size: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page 必须是大于等于 1 的整数")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 100
    ):
        raise ValueError("page_size 必须是 1 到 100 之间的整数")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "SearchCache",
    "SearchCacheError",
    "cache_key_lock",
    "compute_state_hash",
    "decode_cached_result",
    "make_cache_key",
    "normalize_query",
    "search_config_hash",
]
