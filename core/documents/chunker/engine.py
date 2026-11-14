from __future__ import annotations
import hashlib
import json
from dataclasses import replace
from core.settings import AppConfig
from core.settings import load_config
from core.documents.chunker.models import DocumentChunk, _ChunkSpan
from core.documents.chunker.llm import LOGGER, _CHUNKING_PROMPT_VERSION, _chunking_prompt_digest, _llm_chunk_ranges
from core.documents.chunker.rules import _GRANULARITY_ORDER, _TOKEN_PATTERN, _char_ranges_to_spans, _chunk_spans, _find_parent_index, _span_from_token_range

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
    """按配置生成固定、分层或 LLM 语义文档切片。"""

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

    if active_settings.chunking_mode == "llm":
        try:
            ranges = _llm_chunk_ranges(text, active_settings)
            spans = _char_ranges_to_spans(text, ranges)
            if not spans:
                raise ValueError("LLM 未生成有效的语义切片")
        except Exception as exc:
            LOGGER.warning("LLM 语义切分失败，已回退固定切片：%s", exc)
            spans = _chunk_spans(
                text,
                active_settings.chunk_size,
                active_settings.chunk_overlap,
                "medium",
            )
        return [
            DocumentChunk(
                content=span.content,
                granularity="medium",
                chunk_index=index,
                token_start=span.token_start,
                token_end=span.token_end,
            )
            for index, span in enumerate(spans)
        ]

    if active_settings.chunking_mode == "semantic_hierarchical":
        try:
            ranges = _llm_chunk_ranges(text, active_settings)
            semantic_spans = _char_ranges_to_spans(text, ranges)
            if not semantic_spans:
                raise ValueError("LLM 未生成有效的语义切片")
            spans = _semantic_hierarchy_spans(text, semantic_spans, active_settings)
            return _hierarchical_document_chunks(spans)
        except Exception as exc:
            LOGGER.warning("LLM 语义分层切分失败，已回退机械分层切片：%s", exc)

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

    return _hierarchical_document_chunks(
        [
            span
            for granularity in ("small", "medium", "large")
            for span in spans_by_level[granularity]
        ]
    )

def chunking_signature(settings: AppConfig) -> str:
    """返回影响文档向量切片结构的稳定配置签名。"""

    payload = {
        "mode": settings.chunking_mode,
        "small": settings.chunk_small_size,
        "medium": settings.chunk_size,
        "large": settings.chunk_large_size,
        "overlap": settings.chunk_overlap,
    }
    if settings.chunking_mode in {"llm", "semantic_hierarchical"}:
        payload.update(
            {
                "llm_max_input_chars": settings.chunking_llm_max_input_chars,
                "llm_prompt_version": _CHUNKING_PROMPT_VERSION,
                "llm_prompt_sha256": _chunking_prompt_digest(),
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

def llm_chunk(text: str, settings: AppConfig | None = None) -> list[str]:
    """让 LLM 选择语义边界，正文始终由服务端从原文截取。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not text.strip():
        return []
    active_settings = settings or load_config()
    try:
        ranges = _llm_chunk_ranges(text, active_settings)
        values = [text[start:end].strip() for start, end in ranges]
        if not values or any(not value for value in values):
            raise ValueError("LLM 未生成有效的语义切片")
        return values
    except Exception as exc:
        LOGGER.warning("LLM 语义切分失败，已回退固定切片：%s", exc)
        return chunk(text, settings=active_settings)

def _semantic_hierarchy_spans(
    text: str,
    semantic_spans: list[_ChunkSpan],
    settings: AppConfig,
) -> list[_ChunkSpan]:
    """以 LLM 语义片段为叶子，确定性组合中、大粒度父级。"""

    leaves = _normalize_semantic_leaf_spans(
        text,
        semantic_spans,
        settings.chunk_small_size,
    )
    medium = _group_adjacent_semantic_spans(
        text,
        leaves,
        settings.chunk_size,
        "medium",
    )
    large = _group_adjacent_semantic_spans(
        text,
        medium,
        settings.chunk_large_size,
        "large",
    )
    return [*leaves, *medium, *large]

def _normalize_semantic_leaf_spans(
    text: str,
    semantic_spans: list[_ChunkSpan],
    target_tokens: int,
) -> list[_ChunkSpan]:
    """Bound LLM-selected leaves and merge heading-sized semantic fragments.

    The model remains responsible for choosing semantic boundaries, while the
    service enforces the configured small-granularity target.  This prevents a
    title or a one-line paragraph from becoming an independent embedding.
    """

    if not semantic_spans:
        return []
    if target_tokens < 1:
        raise ValueError("chunk_small_size 必须大于等于 1")
    token_matches = list(_TOKEN_PATTERN.finditer(text))
    units: list[_ChunkSpan] = []
    for span in semantic_spans:
        cursor = span.token_start
        while cursor < span.token_end:
            end = min(cursor + target_tokens, span.token_end)
            units.append(
                _span_from_token_range(
                    text,
                    token_matches,
                    cursor,
                    end,
                    "small",
                )
            )
            cursor = end
    if len(units) == 1:
        return units

    minimum_tokens = max(16, target_tokens // 2)
    grouped: list[_ChunkSpan] = []
    group_start = units[0].token_start
    group_end = units[0].token_end
    for unit in units[1:]:
        current_size = group_end - group_start
        proposed_size = unit.token_end - group_start
        if current_size >= minimum_tokens and proposed_size > target_tokens:
            grouped.append(
                _span_from_token_range(
                    text,
                    token_matches,
                    group_start,
                    group_end,
                    "small",
                )
            )
            group_start = unit.token_start
        group_end = unit.token_end

    tail = _span_from_token_range(
        text,
        token_matches,
        group_start,
        group_end,
        "small",
    )
    if grouped and tail.token_end - tail.token_start < minimum_tokens:
        previous = grouped.pop()
        grouped.append(
            _span_from_token_range(
                text,
                token_matches,
                previous.token_start,
                tail.token_end,
                "small",
            )
        )
    else:
        grouped.append(tail)
    return grouped

def _group_adjacent_semantic_spans(
    text: str,
    spans: list[_ChunkSpan],
    target_tokens: int,
    granularity: str,
) -> list[_ChunkSpan]:
    if not spans:
        return []
    token_matches = list(_TOKEN_PATTERN.finditer(text))
    grouped: list[_ChunkSpan] = []
    group_start = spans[0].token_start
    group_end = spans[0].token_end
    for span in spans[1:]:
        proposed_size = span.token_end - group_start
        if proposed_size > target_tokens and group_end > group_start:
            grouped.append(
                _span_from_token_range(
                    text,
                    token_matches,
                    group_start,
                    group_end,
                    granularity,
                )
            )
            group_start = span.token_start
        group_end = span.token_end
    grouped.append(
        _span_from_token_range(
            text,
            token_matches,
            group_start,
            group_end,
            granularity,
        )
    )
    return grouped

def _hierarchical_document_chunks(spans: list[_ChunkSpan]) -> list[DocumentChunk]:
    """去除重复区间、稳定排序并建立最近一级父子关系。"""

    unique_spans: dict[tuple[int, int], _ChunkSpan] = {}
    for span in sorted(
        spans,
        key=lambda item: (
            _GRANULARITY_ORDER[item.granularity],
            item.token_start,
            item.token_end,
        ),
    ):
        # 短文档多个层级区间完全相同时，只保留最小有效粒度。
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
