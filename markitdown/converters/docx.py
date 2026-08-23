"""DOCX Converter：Mammoth 优先，python-docx 结构化回退。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError
from .._stream_info import StreamInfo
from .._utils import (
    escape_markdown_cell,
    finalize_markdown,
    markdown_table,
    title_from_path,
)


class DocxConverter(DocumentConverter):
    extensions = frozenset({".docx"})
    priority = 10

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            import mammoth
        except ImportError:
            mammoth = None

        if mammoth is not None:
            try:
                with source.open("rb") as stream:
                    html_result = mammoth.convert_to_html(stream)
                from markdownify import markdownify

                markdown = markdownify(
                    html_result.value,
                    heading_style="ATX",
                    bullets="-",
                )
                content = finalize_markdown(markdown, source)
                return DocumentConverterResult(
                    title=title_from_path(source),
                    text_content=content,
                    source_path=str(source),
                    converter="DocxMammothConverter",
                )
            except ImportError as exc:
                # Mammoth is present but markdownify is not; use the native
                # fallback only if python-docx is available.
                if not _module_available("docx"):
                    raise MissingOptionalDependencyError(
                        "markdownify", "DOCX"
                    ) from exc
            except Exception as exc:
                # A malformed/feature-heavy document may fail in Mammoth. The
                # native converter still preserves paragraphs and tables.
                if not _module_available("docx"):
                    raise DocumentConversionError(
                        f"无法转换 DOCX：{source}: {exc}"
                    ) from exc

        content = _convert_with_python_docx(source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter="DocxPythonConverter",
        )


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def _convert_with_python_docx(source: Path) -> str:
    try:
        from docx import Document
        from docx.document import Document as DocumentType
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise MissingOptionalDependencyError("python-docx", "DOCX") from exc

    try:
        document = Document(source)
        blocks: list[str] = []
        for block in _iter_docx_blocks(
            document, DocumentType, CT_P, CT_Tbl, Paragraph, Table
        ):
            if isinstance(block, Paragraph):
                rendered = _render_docx_paragraph(block)
            else:
                rows = [
                    [escape_markdown_cell(cell.text) for cell in row.cells]
                    for row in block.rows
                ]
                rendered = markdown_table(rows) if rows else ""
            if rendered:
                blocks.append(rendered)
        return finalize_markdown("\n\n".join(blocks), source)
    except Exception as exc:
        raise DocumentConversionError(f"无法读取 DOCX：{source}: {exc}") from exc


def _iter_docx_blocks(
    document: object,
    document_type: type,
    paragraph_xml: type,
    table_xml: type,
    paragraph_type: type,
    table_type: type,
) -> Iterator[object]:
    if not isinstance(document, document_type):
        return
    for child in document.element.body.iterchildren():
        if isinstance(child, paragraph_xml):
            yield paragraph_type(child, document)
        elif isinstance(child, table_xml):
            yield table_type(child, document)


def _render_docx_paragraph(paragraph: object) -> str:
    from docx.oxml.ns import qn

    parts: list[str] = []
    for child in paragraph._p.iterchildren():
        if child.tag == qn("w:r"):
            parts.append(_render_docx_run_element(child, paragraph))
        elif child.tag == qn("w:hyperlink"):
            label = "".join(child.itertext()).strip()
            relationship_id = child.get(qn("r:id"))
            target = ""
            if relationship_id:
                relationship = paragraph.part.rels.get(relationship_id)
                target = str(getattr(relationship, "target_ref", "") or "")
            parts.append(f"[{label}]({target})" if label and target else label)
    text = "".join(parts).strip() or paragraph.text.strip()
    if not text:
        return ""
    style_name = str(getattr(paragraph.style, "name", "") or "")
    heading = re.fullmatch(
        r"(?:Heading|标题)\s*([1-6])", style_name, flags=re.IGNORECASE
    )
    if heading:
        return f"{'#' * int(heading.group(1))} {text}"
    if paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None:
        marker = "1." if "number" in style_name.casefold() else "-"
        return f"{marker} {text}"
    return text


def _render_docx_run_element(element: object, paragraph: object) -> str:
    from docx.text.run import Run

    run = Run(element, paragraph)
    text = run.text
    if not text:
        return ""
    if run.bold:
        text = f"**{text}**"
    if run.italic:
        text = f"*{text}*"
    if run.font.strike:
        text = f"~~{text}~~"
    return text


__all__ = ["DocxConverter"]
