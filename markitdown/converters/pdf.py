"""文本型 PDF 转换器；扫描版/OCR 不在本层处理。"""

from __future__ import annotations

from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError
from .._stream_info import StreamInfo
from .._utils import (
    clean_pdf_page,
    finalize_markdown,
    repeated_pdf_edge_lines,
    title_from_path,
)


class PdfConverter(DocumentConverter):
    extensions = frozenset({".pdf"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            import pdfplumber
        except ImportError as exc:
            raise MissingOptionalDependencyError("pdfplumber", "PDF") from exc

        try:
            with pdfplumber.open(source) as document:
                raw_pages = [
                    page.extract_text(x_tolerance=2, y_tolerance=3, layout=False) or ""
                    for page in document.pages
                ]
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 PDF：{source}: {exc}") from exc

        if not any(page.strip() for page in raw_pages):
            raise DocumentConversionError(
                f"PDF 没有可提取的文本层：{source}。扫描版 PDF/OCR 请先由上游智能体处理。"
            )

        repeated_edges = repeated_pdf_edge_lines(raw_pages)
        pages: list[str] = []
        for index, raw_page in enumerate(raw_pages, start=1):
            cleaned = clean_pdf_page(raw_page, repeated_edges)
            if cleaned:
                pages.append(f"## 第 {index} 页\n\n{cleaned}")
        content = finalize_markdown("\n\n".join(pages), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


__all__ = ["PdfConverter"]
