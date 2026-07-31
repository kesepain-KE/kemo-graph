"""Rerank API 兼容层和七天本地缓存。"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.config import AppConfig, load_config
from core.db import get_database_paths

from . import (
    ProviderResponseError,
    build_endpoint_url,
    get_api_key,
    kemo_headers,
    request_json,
)


CACHE_MAX_AGE = timedelta(days=7)
_CACHE_LOCK = threading.RLock()


def rerank(
    query: str,
    documents: list[str],
    top_n: int | None = None,
    *,
    settings: AppConfig | None = None,
    cache_path: Path | str | None = None,
    document_ids: list[str] | None = None,
) -> list[tuple[int, float]]:
    """按相关度降序返回 ``(doc_index, score)``，并缓存结果。"""

    _validate_inputs(query, documents, top_n, document_ids)
    if not documents:
        return []

    active_settings = settings or load_config()
    effective_top_n = min(top_n or active_settings.rerank_top_n, len(documents))
    resolved_document_ids = document_ids or [str(index) for index in range(len(documents))]
    resolved_cache_path = Path(cache_path) if cache_path else get_database_paths(
        active_settings.resolve_data_dir()
    ).rerank_cache
    cache_key = _cache_key(query, effective_top_n)

    cached = _get_cached_results(
        resolved_cache_path,
        cache_key,
        document_ids=resolved_document_ids,
    )
    if cached is not None:
        return cached[:effective_top_n]

    api_key = get_api_key(active_settings.kemo)
    headers = kemo_headers(api_key)
    response = request_json(
        "POST",
        build_endpoint_url(active_settings.kemo.base_url, "model/rerank"),
        provider="kemo",
        headers=headers,
        payload={
            "protocol_version": active_settings.kemo.protocol_version,
            "request_id": headers["X-Request-ID"],
            "model": active_settings.models.rerank,
            "query": query,
            "documents": [
                {"id": identifier, "text": document}
                for identifier, document in zip(
                    resolved_document_ids,
                    documents,
                    strict=True,
                )
            ],
            "top_n": effective_top_n,
            "return_documents": False,
        },
        timeout=active_settings.kemo.request_timeout,
    )
    results = _parse_results(
        response,
        provider="kemo",
        document_ids=resolved_document_ids,
    )[:effective_top_n]
    _put_cached_results(
        resolved_cache_path,
        cache_key,
        results,
        document_ids=resolved_document_ids,
    )
    return results


def clear_cache(cache_path: Path | str) -> None:
    """原子清空指定重排序缓存。"""

    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        _write_cache_lines(path, [])


def _validate_inputs(
    query: str,
    documents: list[str],
    top_n: int | None,
    document_ids: list[str] | None,
) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须是非空字符串")
    if not isinstance(documents, list) or any(
        not isinstance(document, str) for document in documents
    ):
        raise TypeError("documents 必须是字符串列表")
    if top_n is not None:
        if not isinstance(top_n, int) or isinstance(top_n, bool):
            raise TypeError("top_n 必须是整数")
        if top_n < 1:
            raise ValueError("top_n 必须大于等于 1")
    if document_ids is not None:
        if (
            not isinstance(document_ids, list)
            or len(document_ids) != len(documents)
            or any(
                not isinstance(identifier, str)
                or not identifier
                or "\t" in identifier
                or "\n" in identifier
                for identifier in document_ids
            )
        ):
            raise ValueError("document_ids 必须是与 documents 等长的非空字符串列表")
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("document_ids 不能重复")


def _cache_key(query: str, top_n: int) -> str:
    return hashlib.sha256(f"{query}{top_n}".encode("utf-8")).hexdigest()[:16]


def _parse_results(
    response: Any, *, provider: str, document_ids: list[str]
) -> list[tuple[int, float]]:
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise ProviderResponseError(
            f"{provider} Rerank 响应缺少 results 数组",
            provider=provider,
        )
    positions = {identifier: index for index, identifier in enumerate(document_ids)}
    parsed: dict[int, float] = {}
    for item in response["results"]:
        if not isinstance(item, dict):
            raise ProviderResponseError(
                f"{provider} Rerank 响应项必须是对象",
                provider=provider,
            )
        rank = item.get("rank")
        document_id = item.get("document_id")
        score = item.get("relevance_score")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ProviderResponseError(
                f"{provider} Rerank 返回了无效排名：{rank!r}",
                provider=provider,
            )
        if not isinstance(document_id, str) or document_id not in positions:
            raise ProviderResponseError(
                f"{provider} Rerank 返回了未知 document_id：{document_id!r}",
                provider=provider,
            )
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise ProviderResponseError(
                f"{provider} Rerank 返回了无效分数：{score!r}",
                provider=provider,
            )
        index = positions[document_id]
        parsed[index] = max(parsed.get(index, float("-inf")), float(score))
    return sorted(parsed.items(), key=lambda item: item[1], reverse=True)


def _get_cached_results(
    path: Path, cache_key: str, *, document_ids: list[str]
) -> list[tuple[int, float]] | None:
    now = datetime.now(timezone.utc)
    with _CACHE_LOCK:
        valid_lines, records = _read_valid_cache(path, now)
        if path.exists() and valid_lines != path.read_text(encoding="utf-8").splitlines():
            _write_cache_lines(path, valid_lines)
    cached = records.get(cache_key)
    positions = {identifier: index for index, identifier in enumerate(document_ids)}
    if not cached or any(identifier not in positions for identifier, _ in cached):
        return None
    return sorted(
        ((positions[identifier], score) for identifier, score in cached),
        key=lambda item: item[1],
        reverse=True,
    )


def _put_cached_results(
    path: Path,
    cache_key: str,
    results: list[tuple[int, float]],
    *,
    document_ids: list[str],
) -> None:
    now = datetime.now(timezone.utc)
    with _CACHE_LOCK:
        valid_lines, _ = _read_valid_cache(path, now)
        valid_lines = [
            line for line in valid_lines if not line.startswith(f"{cache_key}\t")
        ]
        timestamp = now.isoformat()
        valid_lines.extend(
            f"{cache_key}\t{document_ids[index]}\t{score:.17g}\t{timestamp}"
            for index, score in results
        )
        _write_cache_lines(path, valid_lines)


def _read_valid_cache(
    path: Path, now: datetime
) -> tuple[list[str], dict[str, list[tuple[str, float]]]]:
    if not path.exists():
        return [], {}
    valid_lines: list[str] = []
    records: dict[str, list[tuple[str, float]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        cache_key, document_id, score_text, cached_at_text = parts
        try:
            score = float(score_text)
            cached_at = datetime.fromisoformat(cached_at_text)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or now - cached_at.astimezone(timezone.utc) > CACHE_MAX_AGE:
            continue
        valid_lines.append(line)
        records.setdefault(cache_key, []).append((document_id, score))
    return valid_lines, records


def _write_cache_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    content = "\n".join(lines)
    temporary_path.write_text(content + ("\n" if content else ""), encoding="utf-8")
    os.replace(temporary_path, path)
