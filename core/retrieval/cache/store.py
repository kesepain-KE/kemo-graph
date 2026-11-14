from __future__ import annotations
from core.common.time import now_iso as _now_iso
import json
import sqlite3
from typing import Any, Mapping
from core.settings.schema import AppConfig
from core.storage.paths import DatabasePaths
from core.storage.connections import connect_search_cache
from core.storage.schema import initialize_search_cache
from core.retrieval.cache.keys import compute_state_hash, normalize_query
from core.retrieval.cache.errors import SearchCacheCleanupError, SearchCacheError

def clear_search_cache_after_mutation(
    owner: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    """保留已提交的变更结果，并显式报告隐私清理失败。"""

    try:
        result["search_cache_deleted"] = owner.clear_search_cache()["deleted"]
    except Exception as exc:
        result["search_cache_deleted"] = None
        result["search_cache_error"] = f"{type(exc).__name__}: {exc}"
        owner._log_event(
            "search_cache_cleanup_failed",
            result["search_cache_error"],
            level="ERROR",
        )
        raise SearchCacheCleanupError(result) from exc
    return result

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

def _validate_page(page: int, page_size: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page 必须是大于等于 1 的整数")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 100
    ):
        raise ValueError("page_size 必须是 1 到 100 之间的整数")
