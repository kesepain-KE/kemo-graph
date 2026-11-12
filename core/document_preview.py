"""Read complete original documents for display, independently of Markdown indexing."""

from __future__ import annotations

import base64
import hashlib
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from markitdown._utils import read_text
from .library import TEXT_SUFFIXES
from .knowledge_models import MAX_IMPORT_BYTES, DocumentTooLargeError

HTML_STYLE = """
body{margin:0;padding:32px;color:#202124;font:15px/1.8 system-ui,sans-serif;overflow-wrap:anywhere}
img{max-width:100%;height:auto}table{border-collapse:collapse;max-width:100%;margin:20px 0}
th,td{border:1px solid #dadce0;padding:6px 12px;white-space:pre-wrap;vertical-align:top}
th{background:#f7f8fa}h1,h2,h3{line-height:1.4}pre{white-space:pre-wrap}
.sheet,.slide{margin-bottom:40px}.slide{border:1px solid #e6e7ea;padding:24px}
"""


def _html(body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'img-src data:; style-src \'unsafe-inline\';">'
        f"<style>{HTML_STYLE}</style></head><body>{body}</body></html>"
    )


def _image(content: bytes, content_type: str) -> str:
    return f'<img src="data:{content_type};base64,{base64.b64encode(content).decode()}">'


def preview_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("所选路径不是文件")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise DocumentTooLargeError("原文件预览超过 50 MB 上限")
    suffix = path.suffix.casefold()
    data: dict[str, Any] = {"name": path.name, "size": path.stat().st_size, "editable": suffix in TEXT_SUFFIXES}
    if suffix == ".pdf":
        return {**data, "mode": "pdf"}
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return {**data, "mode": "image"}
    if suffix in TEXT_SUFFIXES:
        content = read_text(path)
        data["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        mode = "markdown" if suffix in {".md", ".markdown"} else "text"
        if suffix in {".html", ".htm"}:
            return {**data, "mode": "html", "html": _html(content), "content": content}
        return {**data, "mode": mode, "content": content}
    if suffix == ".docx":
        import mammoth
        def convert_image(image):
            with image.open() as stream:
                return {"src": f"data:{image.content_type};base64,"
                        + base64.b64encode(stream.read()).decode()}
        body = mammoth.convert_to_html(
            BytesIO(path.read_bytes()),
            convert_image=mammoth.images.img_element(convert_image),
        ).value
    elif suffix in {".xlsx", ".xlsm", ".xls"}:
        tables = []
        if suffix == ".xls":
            import xlrd
            workbook = xlrd.open_workbook(path)
            sheets = [(sheet.name, map(sheet.row_values, range(sheet.nrows))) for sheet in workbook.sheets()]
        else:
            from openpyxl import load_workbook
            workbook = load_workbook(path, read_only=True, data_only=False)
            sheets = [(sheet.title, sheet.iter_rows(values_only=True)) for sheet in workbook.worksheets]
        try:
            for name, rows in sheets:
                cells = "".join("<tr>" + "".join(f"<td>{escape(str(cell)) if cell is not None else ''}</td>" for cell in row) + "</tr>" for row in rows)
                tables.append(f'<section class="sheet"><h2>{escape(name)}</h2><table>{cells}</table></section>')
            body = "".join(tables)
        finally:
            if suffix != ".xls":
                workbook.close()
            else:
                workbook.release_resources()
    elif suffix == ".pptx":
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        presentation = Presentation(path)

        def render_shapes(shapes) -> str:
            result = []
            for shape in shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    result.append(render_shapes(shape.shapes))
                elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    result.append(_image(shape.image.blob, shape.image.content_type))
                elif shape.has_table:
                    result.append("<table>" + "".join(
                        "<tr>" + "".join(f"<td>{escape(cell.text)}</td>" for cell in row.cells) + "</tr>"
                        for row in shape.table.rows
                    ) + "</table>")
                elif shape.has_text_frame:
                    result.extend(f"<p>{escape(paragraph.text)}</p>" for paragraph in shape.text_frame.paragraphs)
            return "".join(result)
        body = "".join(
            f'<section class="slide"><h2>第 {index + 1} 页</h2>{render_shapes(slide.shapes)}</section>'
            for index, slide in enumerate(presentation.slides)
        )
    elif suffix == ".rtf":
        from striprtf.striprtf import rtf_to_text
        return {**data, "mode": "text", "content": rtf_to_text(read_text(path))}
    elif suffix == ".eml":
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        part = message.get_body(preferencelist=("html", "plain"))
        header = "".join(f"<p><b>{label}</b> {escape(str(message.get(key, '')))}</p>" for key, label in (("Subject", "主题"), ("From", "发件人"), ("To", "收件人"), ("Date", "日期")))
        content = part.get_content() if part else ""
        body = header + (content if part and part.get_content_type() == "text/html" else f"<pre>{escape(content)}</pre>")
    elif suffix == ".epub":
        from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE
        from bs4 import BeautifulSoup
        book = epub.read_epub(str(path))
        images = {item.get_name(): f"data:{item.media_type};base64,{base64.b64encode(item.get_content()).decode()}" for item in book.get_items_of_type(ITEM_IMAGE)}
        chapters = []
        import posixpath
        for reference, _ in book.spine:
            item = book.get_item_with_id(reference)
            if item is None or item.get_type() != ITEM_DOCUMENT:
                continue
            chapter = BeautifulSoup(item.get_content(), "html.parser")
            for image in chapter.find_all("img"):
                key = posixpath.normpath(posixpath.join(posixpath.dirname(item.get_name()), image.get("src", "")))
                if key in images:
                    image["src"] = images[key]
            chapters.append(str(chapter.body or chapter))
        body = "".join(chapters)
    else:
        return {**data, "mode": "download"}
    return {**data, "mode": "html", "html": _html(body)}
