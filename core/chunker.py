"""查询与文档共用的轻量级文本切片器。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace

from .config import AppConfig, load_config


_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)
_GRANULARITY_ORDER = {"small": 0, "medium": 1, "large": 2}


@dataclass(frozen=True)
class DocumentChunk:
    """带层级和原文 token 区间的文档切片。"""

    content: str
    granularity: str
    chunk_index: int
    token_start: int
    token_end: int
    parent_index: int | None = None


@dataclass(frozen=True)
class _ChunkSpan:
    content: str
    granularity: str
    token_start: int
    token_end: int


def chunk(
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    *,
    settings: AppConfig | None = None,
) -> list[str]:
    """按估算 token 切分文本，并保留相邻切片之间的重叠。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text.strip():
        return []

    active_settings = settings or load_config()
    size = chunk_size if chunk_size is not None else active_settings.chunk_size
    overlap = (
        chunk_overlap if chunk_overlap is not None else active_settings.chunk_overlap
    )
    if size < 1:
        raise ValueError("chunk_size 必须大于等于 1")
    if overlap < 0:
        raise ValueError("chunk_overlap 不能小于 0")
    if overlap >= size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")

    return [span.content for span in _chunk_spans(text, size, overlap, "medium")]


def document_chunks(
    text: str,
    *,
    settings: AppConfig | None = None,
) -> list[DocumentChunk]:
    """按配置生成固定或小/中/大三层文档切片。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text.strip():
        return []
    active_settings = settings or load_config()
    if active_settings.chunking_mode == "fixed":
        spans = _chunk_spans(
            text,
            active_settings.chunk_size,
            active_settings.chunk_overlap,
            "medium",
        )
        return [
            DocumentChunk(
                content=span.content,
                granularity=span.granularity,
                chunk_index=index,
                token_start=span.token_start,
                token_end=span.token_end,
            )
            for index, span in enumerate(spans)
        ]

    sizes = {
        "small": active_settings.chunk_small_size,
        "medium": active_settings.chunk_size,
        "large": active_settings.chunk_large_size,
    }
    if not sizes["small"] < sizes["medium"] < sizes["large"]:
        raise ValueError("分层切片必须满足：小粒度 < 中粒度 < 大粒度")
    overlap_ratio = active_settings.chunk_overlap / active_settings.chunk_size
    spans_by_level: dict[str, list[_ChunkSpan]] = {}
    for granularity, size in sizes.items():
        overlap = min(size - 1, round(size * overlap_ratio))
        spans_by_level[granularity] = _chunk_spans(
            text,
            size,
            overlap,
            granularity,
        )

    # 短文档在多个层级可能产生完全相同的区间，只保留最小有效粒度。
    unique_spans: dict[tuple[int, int], _ChunkSpan] = {}
    for granularity in ("small", "medium", "large"):
        for span in spans_by_level[granularity]:
            unique_spans.setdefault((span.token_start, span.token_end), span)
    ordered_spans = sorted(
        unique_spans.values(),
        key=lambda span: (
            -_GRANULARITY_ORDER[span.granularity],
            span.token_start,
            span.token_end,
        ),
    )
    chunks = [
        DocumentChunk(
            content=span.content,
            granularity=span.granularity,
            chunk_index=index,
            token_start=span.token_start,
            token_end=span.token_end,
        )
        for index, span in enumerate(ordered_spans)
    ]
    for index, child in enumerate(chunks):
        parent_index = _find_parent_index(chunks, index)
        if parent_index is not None:
            chunks[index] = replace(child, parent_index=parent_index)
    return chunks


def chunking_signature(settings: AppConfig) -> str:
    """返回影响文档向量切片结构的稳定配置签名。"""

    payload = {
        "mode": settings.chunking_mode,
        "small": settings.chunk_small_size,
        "medium": settings.chunk_size,
        "large": settings.chunk_large_size,
        "overlap": settings.chunk_overlap,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def estimate_token_count(text: str) -> int:
    """返回与切片算法一致的轻量 token 数估计。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    return sum(1 for _ in _TOKEN_PATTERN.finditer(text))


def _chunk_spans(
    text: str,
    size: int,
    overlap: int,
    granularity: str,
) -> list[_ChunkSpan]:
    if size < 1:
        raise ValueError("chunk_size 必须大于等于 1")
    if overlap < 0:
        raise ValueError("chunk_overlap 不能小于 0")
    if overlap >= size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")
    token_matches = list(_TOKEN_PATTERN.finditer(text))
    if not token_matches:
        return []

    spans: list[_ChunkSpan] = []
    step = size - overlap
    for token_start in range(0, len(token_matches), step):
        token_end = min(token_start + size, len(token_matches))
        char_start = token_matches[token_start].start()
        char_end = token_matches[token_end - 1].end()
        value = text[char_start:char_end].strip()
        if value:
            spans.append(
                _ChunkSpan(
                    content=value,
                    granularity=granularity,
                    token_start=token_start,
                    token_end=token_end,
                )
            )
        if token_end == len(token_matches):
            break
    return spans


def _find_parent_index(chunks: list[DocumentChunk], child_index: int) -> int | None:
    child = chunks[child_index]
    child_rank = _GRANULARITY_ORDER[child.granularity]
    center = (child.token_start + child.token_end - 1) / 2
    candidates = [
        (index, candidate)
        for index, candidate in enumerate(chunks)
        if _GRANULARITY_ORDER[candidate.granularity] > child_rank
        and candidate.token_start <= center < candidate.token_end
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            _GRANULARITY_ORDER[item[1].granularity],
            item[1].token_end - item[1].token_start,
            item[1].token_start,
        ),
    )[0]
