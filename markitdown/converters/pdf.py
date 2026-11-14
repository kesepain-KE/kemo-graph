"""文本型 PDF 转换器；扫描版/OCR 不在本层处理。"""

from __future__ import annotations

from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError
from .._stream_info import StreamInfo
from .._utils import (
    clean_pdf_page,
    finalize_markdown,
    markdown_table,
    repeated_pdf_edge_lines,
    title_from_path,
)


class PdfConverter(DocumentConverter):
    extensions = frozenset({".pdf"})
    extract_tables = True

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
                repeated_edges = repeated_pdf_edge_lines(raw_pages)
                pages: list[str] = []
                for index, page in enumerate(document.pages, start=1):
                    cleaned = _render_page(
                        page, raw_pages[index - 1], repeated_edges,
                        extract_tables=self.extract_tables,
                    )
                    if cleaned:
                        pages.append(f"## 第 {index} 页\n\n{cleaned}")
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 PDF：{source}: {exc}") from exc

        if not any(page.strip() for page in raw_pages):
            raise DocumentConversionError(
                f"PDF 没有可提取的文本层：{source}。扫描版 PDF/OCR 请先由上游智能体处理。"
            )

        content = finalize_markdown("\n\n".join(pages), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


def _render_page(page, raw_page: str, repeated_edges: set[str], *, extract_tables: bool) -> str:
    tables = []
    if extract_tables:
        for table in page.find_tables():
            rows = [
                [cell or "" for cell in row]
                for row in table.extract(x_tolerance=2, y_tolerance=3)
            ]
            if (
                len(rows) < 2
                or max(len(row) for row in rows) < 2
                or sum(len(cell.strip()) for row in rows for cell in row) < 8
            ):
                continue
            tables.append((table.bbox, markdown_table(rows)))
    if not tables:
        return clean_pdf_page(raw_page, repeated_edges)
    tables.sort(key=lambda item: (item[0][1], item[0][0]))

    def outside_tables(obj):
        if obj["object_type"] != "char":
            return True
        x = (obj["x0"] + obj["x1"]) / 2
        y = (obj["top"] + obj["bottom"]) / 2
        return not any(
            left <= x < right and top <= y < bottom
            for (left, top, right, bottom), _ in tables
        )

    prose = page.filter(outside_tables)
    # Remove only table characters, not full-width table bands: prose beside a
    # table must survive. Character centres assign every remaining glyph once.
    boundaries = [page.bbox[1], *(bbox[1] for bbox, _ in tables), page.bbox[3]]
    blocks: list[str] = []
    for index, (top, bottom) in enumerate(zip(boundaries, boundaries[1:])):
        band = prose.filter(
            lambda obj: obj["object_type"] != "char"
            or top <= (obj["top"] + obj["bottom"]) / 2 < bottom
        )
        text = band.extract_text(x_tolerance=2, y_tolerance=3, layout=False) or ""
        cleaned = clean_pdf_page(text, repeated_edges)
        if cleaned:
            blocks.append(cleaned)
        if index < len(tables):
            blocks.append(tables[index][1])
    return "\n\n".join(blocks)


__all__ = ["PdfConverter"]
