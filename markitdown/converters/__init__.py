"""内置本地文件 Converter 注册表。"""

from __future__ import annotations

from .data import CsvConverter, JsonConverter, XmlConverter, YamlConverter
from .docx import DocxConverter
from .html import HtmlConverter
from .legacy import EmlConverter, EpubConverter, RtfConverter, RstConverter
from .pdf import PdfConverter
from .pptx import PptxConverter
from .spreadsheet import SpreadsheetConverter
from .text import MarkdownConverter, PlainTextConverter


def default_converters() -> list[object]:
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
]
