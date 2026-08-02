"""查询与文档共用的轻量级文本切片器。"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from provider.engine import chat_structured

from .config import AppConfig, load_config


_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)
_PARAGRAPH_BREAK_PATTERN = re.compile(r"\n[ \t]*\n")
_GRANULARITY_ORDER = {"small": 0, "medium": 1, "large": 2}
_CHUNKING_PROMPT_PATH = Path(__file__).resolve().parents[1] / "config" / "chunking_prompt.md"
_CHUNKING_PROMPT_VERSION = "boundary-v2"
_CHUNKING_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "start_block": {"type": "integer", "minimum": 1},
                    "end_block": {"type": "integer", "minimum": 1},
                },
                "required": ["start_block", "end_block"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["chunks"],
    "additionalProperties": False,
}
LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _TextBlock:
    text: str
    char_start: int
    char_end: int


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


def _pre_split_by_paragraphs(text: str, max_chars: int) -> list[str]:
    """按自然边界预切长文本，并保证每段不超过 ``max_chars``。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if max_chars < 1:
        raise ValueError("max_chars 必须大于等于 1")
    if not text:
        return []

    parts: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        end = hard_end
        if hard_end < len(text):
            search_start = start + max(1, max_chars // 2)
            paragraph_end = text.rfind("\n\n", search_start, hard_end)
            line_end = text.rfind("\n", search_start, hard_end)
            if paragraph_end >= search_start:
                end = paragraph_end + 2
            elif line_end >= search_start:
                end = line_end + 1
            else:
                sentence_end = max(
                    (text.rfind(mark, search_start, hard_end) for mark in "。！？.!?"),
                    default=-1,
                )
                whitespace_end = max(
                    text.rfind(" ", search_start, hard_end),
                    text.rfind("\t", search_start, hard_end),
                )
                natural_end = max(sentence_end, whitespace_end)
                if natural_end >= search_start:
                    end = natural_end + 1
        if end <= start:
            end = hard_end
        parts.append(text[start:end])
        start = end
    return parts


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


def estimate_token_count(text: str) -> int:
    """返回与切片算法一致的轻量 token 数估计。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    return sum(1 for _ in _TOKEN_PATTERN.finditer(text))


def _llm_chunk_ranges(text: str, settings: AppConfig) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    offset = 0
    for part in _pre_split_by_paragraphs(
        text,
        settings.chunking_llm_max_input_chars,
    ):
        if part.strip():
            ranges.extend(
                (offset + start, offset + end)
                for start, end in _llm_part_ranges(part, settings)
            )
        offset += len(part)
    if offset != len(text):
        raise ValueError("长文档预切结果未完整覆盖原文")
    return ranges


def _llm_part_ranges(text: str, settings: AppConfig) -> list[tuple[int, int]]:
    blocks = _semantic_blocks(text)
    if not blocks:
        return []
    if len(blocks) == 1:
        return [(blocks[0].char_start, blocks[0].char_end)]

    prompt = _CHUNKING_PROMPT_PATH.read_text(encoding="utf-8")
    user = json.dumps(
        {
            "blocks": [
                {"id": index, "text": block.text}
                for index, block in enumerate(blocks, start=1)
            ]
        },
        ensure_ascii=False,
    )
    response = chat_structured(
        prompt,
        user,
        _CHUNKING_SCHEMA,
        settings=settings,
        tool_name="submit_chunk_boundaries",
    )
    return _validated_block_ranges(response, blocks)


def _semantic_blocks(text: str) -> list[_TextBlock]:
    blocks: list[_TextBlock] = []
    start = 0
    for match in _PARAGRAPH_BREAK_PATTERN.finditer(text):
        if text[start : match.start()].strip():
            end = match.end()
            blocks.append(
                _TextBlock(
                    text=text[start:end],
                    char_start=start,
                    char_end=end,
                )
            )
            start = end
    if text[start:].strip():
        blocks.append(
            _TextBlock(text=text[start:], char_start=start, char_end=len(text))
        )
    return blocks


def _validated_block_ranges(
    response: object,
    blocks: list[_TextBlock],
) -> list[tuple[int, int]]:
    if not isinstance(response, dict) or not isinstance(response.get("chunks"), list):
        raise ValueError("LLM 切分响应缺少 chunks 数组")
    raw_chunks = response["chunks"]
    if not raw_chunks:
        raise ValueError("LLM 切分响应的 chunks 不能为空")

    ranges: list[tuple[int, int]] = []
    expected_start = 1
    for item in raw_chunks:
        if not isinstance(item, dict):
            raise ValueError("LLM 切分范围必须是对象")
        start_block = item.get("start_block")
        end_block = item.get("end_block")
        if (
            isinstance(start_block, bool)
            or isinstance(end_block, bool)
            or not isinstance(start_block, int)
            or not isinstance(end_block, int)
        ):
            raise ValueError("LLM 切分块编号必须是整数")
        if start_block != expected_start or not start_block <= end_block <= len(blocks):
            raise ValueError("LLM 切分范围存在跳号、重叠、倒序或越界")
        ranges.append(
            (blocks[start_block - 1].char_start, blocks[end_block - 1].char_end)
        )
        expected_start = end_block + 1
    if expected_start != len(blocks) + 1:
        raise ValueError("LLM 切分范围未完整覆盖全部原文块")
    return ranges


def _char_ranges_to_spans(
    text: str,
    ranges: list[tuple[int, int]],
) -> list[_ChunkSpan]:
    token_matches = list(_TOKEN_PATTERN.finditer(text))
    token_starts = [match.start() for match in token_matches]
    spans: list[_ChunkSpan] = []
    for char_start, char_end in ranges:
        token_start = bisect.bisect_left(token_starts, char_start)
        token_end = bisect.bisect_left(token_starts, char_end)
        content = text[char_start:char_end].strip()
        if content and token_end > token_start:
            spans.append(
                _ChunkSpan(
                    content=content,
                    granularity="medium",
                    token_start=token_start,
                    token_end=token_end,
                )
            )
    return spans


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


def _span_from_token_range(
    text: str,
    token_matches: list[re.Match[str]],
    token_start: int,
    token_end: int,
    granularity: str,
) -> _ChunkSpan:
    if not 0 <= token_start < token_end <= len(token_matches):
        raise ValueError("语义层级切片 token 区间无效")
    char_start = token_matches[token_start].start()
    char_end = token_matches[token_end - 1].end()
    return _ChunkSpan(
        content=text[char_start:char_end].strip(),
        granularity=granularity,
        token_start=token_start,
        token_end=token_end,
    )


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


def _chunking_prompt_digest() -> str:
    try:
        content = _CHUNKING_PROMPT_PATH.read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(content).hexdigest()


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
