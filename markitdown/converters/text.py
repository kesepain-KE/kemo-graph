"""纯文本与 Markdown Converter。"""

from __future__ import annotations

from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._utils import finalize_markdown, read_text, title_from_path


class PlainTextConverter(DocumentConverter):
    extensions = frozenset({".txt", ".log"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        content = finalize_markdown(read_text(source), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class MarkdownConverter(DocumentConverter):
    extensions = frozenset({".md", ".markdown"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        content = finalize_markdown(read_text(source), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


__all__ = ["MarkdownConverter", "PlainTextConverter"]
