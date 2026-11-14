from __future__ import annotations

from dataclasses import dataclass

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
