from __future__ import annotations

import bisect
import re
from core.documents.chunker.models import DocumentChunk, _ChunkSpan, _TextBlock

_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)

_PARAGRAPH_BREAK_PATTERN = re.compile(r"\n[ \t]*\n")

_GRANULARITY_ORDER = {"small": 0, "medium": 1, "large": 2}

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

def estimate_token_count(text: str) -> int:
    """返回与切片算法一致的轻量 token 数估计。"""

    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    return sum(1 for _ in _TOKEN_PATTERN.finditer(text))

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
