"""kemo Embedding 协议适配和向量完整性校验。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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


def embed(
    texts: list[str],
    model: str | None = None,
    *,
    settings: AppConfig | None = None,
) -> EmbeddingResult:
    """返回向量及其空间 ID；数量、维度或数值非法时抛出异常。"""

    _validate_texts(texts)
    if not texts:
        return EmbeddingResult(vectors=[], vector_space_id="unknown")

    active_settings = settings or load_config()
    api_key = get_api_key(active_settings.kemo)
    headers = kemo_headers(api_key)
    input_ids = [f"chunk-{index}" for index in range(len(texts))]
    payload = {
        "protocol_version": active_settings.kemo.protocol_version,
        "request_id": headers["X-Request-ID"],
        "model": model or active_settings.models.embedding,
        "input_type": "document",
        "inputs": [
            {"id": identifier, "text": text}
            for identifier, text in zip(input_ids, texts, strict=True)
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
    vector_space_id = _extract_vector_space_id(response, "kemo")
    vectors = _extract_vectors(
        response,
        input_ids=input_ids,
        provider="kemo",
    )
    return EmbeddingResult(
        vectors=_validate_vectors(
            vectors,
            active_settings.models.embedding_dimensions,
            "kemo",
        ),
        vector_space_id=vector_space_id,
    )


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
    vectors: list[Sequence[float]], dimensions: int, provider: str
) -> list[list[float]]:
    validated: list[list[float]] = []
    for index, vector in enumerate(vectors):
        try:
            array = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError(
                f"{provider} 第 {index} 个向量含非数值内容",
                provider=provider,
            ) from exc
        if array.ndim != 1 or array.shape[0] != dimensions:
            actual = array.shape[0] if array.ndim == 1 else tuple(array.shape)
            raise ProviderResponseError(
                f"{provider} 第 {index} 个向量维度错误：期望 {dimensions}，实际 {actual}",
                provider=provider,
            )
        if not np.isfinite(array).all():
            raise ProviderResponseError(
                f"{provider} 第 {index} 个向量包含 NaN 或 Infinity",
                provider=provider,
            )
        validated.append(array.tolist())
    return validated


__all__ = ["EmbeddingResult", "embed"]
