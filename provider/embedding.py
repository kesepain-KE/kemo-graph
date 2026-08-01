"""kemo Embedding 协议适配和向量完整性校验。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any
from urllib.parse import quote

import numpy as np

from core.config import AppConfig, load_config

from . import (
    ProviderResponseError,
    build_endpoint_url,
    get_api_key,
    kemo_headers,
    request_json,
)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    vector_space_id: str


@dataclass(frozen=True)
class _CapabilityCacheEntry:
    max_batch_size: int
    expires_at: float


_CAPABILITY_CACHE_TTL_SECONDS = 300.0
_CAPABILITY_CACHE: dict[tuple[str, str], _CapabilityCacheEntry] = {}
_CAPABILITY_CACHE_LOCK = RLock()


def embed(
    texts: list[str],
    model: str | None = None,
    *,
    settings: AppConfig | None = None,
    input_type: str = "document",
) -> EmbeddingResult:
    """返回向量及其空间 ID；数量、维度或数值非法时抛出异常。"""

    _validate_texts(texts)
    if input_type not in {"document", "query"}:
        raise ValueError("input_type 必须为 document 或 query")
    if not texts:
        return EmbeddingResult(vectors=[], vector_space_id="unknown")

    active_settings = settings or load_config()
    api_key = get_api_key(active_settings.kemo)
    selected_model = model or active_settings.models.embedding
    max_batch_size = min(
        active_settings.embedding_batch_size,
        _get_max_batch_size(
            active_settings,
            selected_model,
            api_key,
        ),
    )
    input_ids = [f"chunk-{index}" for index in range(len(texts))]
    all_vectors: list[list[float]] = []
    vector_space_id: str | None = None
    for batch_start in range(0, len(texts), max_batch_size):
        batch_end = min(batch_start + max_batch_size, len(texts))
        batch_ids = input_ids[batch_start:batch_end]
        headers = kemo_headers(api_key)
        payload = {
            "protocol_version": active_settings.kemo.protocol_version,
            "request_id": headers["X-Request-ID"],
            "model": selected_model,
            "input_type": input_type,
            "inputs": [
                {"id": identifier, "text": text}
                for identifier, text in zip(
                    batch_ids,
                    texts[batch_start:batch_end],
                    strict=True,
                )
            ],
            "dimensions": active_settings.models.embedding_dimensions,
            "normalize": True,
        }
        response = request_json(
            "POST",
            build_endpoint_url(active_settings.kemo.base_url, "model/embeddings"),
            provider="kemo",
            headers=headers,
            payload=payload,
            timeout=active_settings.kemo.request_timeout,
        )
        batch_vector_space_id = _extract_vector_space_id(response, "kemo")
        if vector_space_id is None:
            vector_space_id = batch_vector_space_id
        elif batch_vector_space_id != vector_space_id:
            raise ProviderResponseError(
                "kemo Embedding 各批次 vector_space_id 不一致："
                f"期望 {vector_space_id!r}，实际 {batch_vector_space_id!r}",
                provider="kemo",
            )
        batch_vectors = _extract_vectors(
            response,
            input_ids=batch_ids,
            provider="kemo",
        )
        all_vectors.extend(
            _validate_vectors(
                batch_vectors,
                active_settings.models.embedding_dimensions,
                "kemo",
                start_index=batch_start,
            )
        )

    if vector_space_id is None:
        raise ProviderResponseError(
            "kemo Embedding 未返回任何批次",
            provider="kemo",
        )
    return EmbeddingResult(
        vectors=all_vectors,
        vector_space_id=vector_space_id,
    )


def _get_max_batch_size(
    settings: AppConfig,
    model: str,
    api_key: str,
) -> int:
    cache_key = (settings.kemo.base_url.rstrip("/"), model)
    now = monotonic()
    with _CAPABILITY_CACHE_LOCK:
        cached = _CAPABILITY_CACHE.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return cached.max_batch_size

    headers = kemo_headers(api_key)
    response = request_json(
        "GET",
        build_endpoint_url(
            settings.kemo.base_url,
            f"model/models/{quote(model, safe='')}/capabilities",
        ),
        provider="kemo",
        headers=headers,
        timeout=settings.kemo.request_timeout,
    )
    max_batch_size = _extract_max_batch_size(response)
    with _CAPABILITY_CACHE_LOCK:
        _CAPABILITY_CACHE[cache_key] = _CapabilityCacheEntry(
            max_batch_size=max_batch_size,
            expires_at=monotonic() + _CAPABILITY_CACHE_TTL_SECONDS,
        )
    return max_batch_size


def _extract_max_batch_size(response: Any) -> int:
    if not isinstance(response, dict):
        raise ProviderResponseError(
            "kemo 模型能力响应必须是对象",
            provider="kemo",
        )
    embedding = response.get("embedding")
    if not isinstance(embedding, dict):
        raise ProviderResponseError(
            "kemo 模型能力响应缺少 embedding 对象",
            provider="kemo",
        )
    max_batch_size = embedding.get("max_batch_size")
    if (
        not isinstance(max_batch_size, int)
        or isinstance(max_batch_size, bool)
        or max_batch_size < 1
    ):
        raise ProviderResponseError(
            "kemo 模型能力响应缺少有效的 embedding.max_batch_size",
            provider="kemo",
        )
    return max_batch_size


def _clear_capability_cache() -> None:
    """仅供测试和运行时配置刷新时清空能力缓存。"""

    with _CAPABILITY_CACHE_LOCK:
        _CAPABILITY_CACHE.clear()


def _validate_texts(texts: list[str]) -> None:
    if not isinstance(texts, list):
        raise TypeError("texts 必须是字符串列表")
    if any(not isinstance(text, str) for text in texts):
        raise TypeError("texts 中的每一项都必须是字符串")


def _extract_vector_space_id(response: Any, provider: str) -> str:
    if not isinstance(response, dict):
        raise ProviderResponseError(
            f"{provider} Embedding 响应必须是对象",
            provider=provider,
        )
    vector_space_id = response.get("vector_space_id")
    if not isinstance(vector_space_id, str) or not vector_space_id.strip():
        raise ProviderResponseError(
            f"{provider} Embedding 响应缺少 vector_space_id",
            provider=provider,
        )
    return vector_space_id.strip()


def _extract_vectors(
    response: Any,
    *,
    input_ids: list[str],
    provider: str,
) -> list[Sequence[float]]:
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ProviderResponseError(
            f"{provider} Embedding 响应缺少 data 数组",
            provider=provider,
        )
    data = response["data"]
    expected_count = len(input_ids)
    if len(data) != expected_count:
        raise ProviderResponseError(
            f"{provider} Embedding 返回数量不一致：期望 {expected_count}，实际 {len(data)}",
            provider=provider,
        )
    if any(
        not isinstance(item, dict) or not isinstance(item.get("index"), int)
        for item in data
    ):
        raise ProviderResponseError(
            f"{provider} Embedding 响应项缺少 index",
            provider=provider,
        )

    data = sorted(data, key=lambda item: item["index"])
    indexes = [item["index"] for item in data]
    if indexes != list(range(expected_count)):
        raise ProviderResponseError(
            f"{provider} Embedding 响应 index 不连续",
            provider=provider,
        )

    vectors: list[Sequence[float]] = []
    for index, item in enumerate(data):
        if item.get("id") != input_ids[index]:
            raise ProviderResponseError(
                f"{provider} Embedding 响应 ID 与输入不匹配：{item.get('id')!r}",
                provider=provider,
            )
        vector = item.get("vector")
        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
            raise ProviderResponseError(
                f"{provider} Embedding 响应项缺少 vector 数组",
                provider=provider,
            )
        vectors.append(vector)
    return vectors


def _validate_vectors(
    vectors: list[Sequence[float]],
    dimensions: int,
    provider: str,
    *,
    start_index: int = 0,
) -> list[list[float]]:
    validated: list[list[float]] = []
    for index, vector in enumerate(vectors):
        global_index = start_index + index
        try:
            array = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError(
                f"{provider} 第 {global_index} 个向量含非数值内容",
                provider=provider,
            ) from exc
        if array.ndim != 1 or array.shape[0] != dimensions:
            actual = array.shape[0] if array.ndim == 1 else tuple(array.shape)
            raise ProviderResponseError(
                f"{provider} 第 {global_index} 个向量维度错误："
                f"期望 {dimensions}，实际 {actual}",
                provider=provider,
            )
        if not np.isfinite(array).all():
            raise ProviderResponseError(
                f"{provider} 第 {global_index} 个向量包含 NaN 或 Infinity",
                provider=provider,
            )
        validated.append(array.tolist())
    return validated


__all__ = ["EmbeddingResult", "embed"]
