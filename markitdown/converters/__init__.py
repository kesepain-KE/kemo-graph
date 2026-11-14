"""内置本地文件 Converter 注册表。"""

from __future__ import annotations

from collections.abc import Iterable

from .._base_converter import DocumentConverter
from .data import CsvConverter, JsonConverter, XmlConverter, YamlConverter
from .docx import DocxConverter
from .html import HtmlConverter
from .legacy import EmlConverter, EpubConverter, RtfConverter, RstConverter
from .pdf import PdfConverter
from .pptx import PptxConverter
from .spreadsheet import SpreadsheetConverter
from .text import MarkdownConverter, PlainTextConverter


def default_converters() -> list[DocumentConverter]:
    """返回内置 Converter 实例；各格式依赖均在实际转换时延迟导入。"""

    return [
        PdfConverter(),
        DocxConverter(),
        PptxConverter(),
        SpreadsheetConverter(),
        HtmlConverter(),
        CsvConverter(),
        JsonConverter(),
        XmlConverter(),
        YamlConverter(),
        MarkdownConverter(),
        PlainTextConverter(),
        EmlConverter(),
        EpubConverter(),
        RtfConverter(),
        RstConverter(),
    ]


def supported_suffixes(
    converters: Iterable[DocumentConverter] | None = None,
) -> frozenset[str]:
    """从注册表派生格式能力；不包含无扩展名输入的产品准入策略。"""

    registry = default_converters() if converters is None else converters
    return frozenset(suffix for converter in registry for suffix in converter.extensions)


__all__ = [
    "CsvConverter",
    "DocxConverter",
    "EmlConverter",
    "EpubConverter",
    "HtmlConverter",
    "JsonConverter",
    "MarkdownConverter",
    "PdfConverter",
    "PlainTextConverter",
    "PptxConverter",
    "RstConverter",
    "RtfConverter",
    "SpreadsheetConverter",
    "XmlConverter",
    "YamlConverter",
    "default_converters",
    "supported_suffixes",
]
