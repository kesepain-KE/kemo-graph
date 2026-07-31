"""本地文档到 Markdown 的安全转换与导入工具。"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from pathlib import Path
from typing import Callable
from uuid import uuid4


class DocumentConversionError(RuntimeError):
    """文档无法读取或转换。"""


def convert_pdf(path: Path | str) -> str:
    """使用 pdfplumber 逐页提取 PDF 文本。"""

    source = _validate_input_file(path, {".pdf"})
    try:
        import pdfplumber

        with pdfplumber.open(source) as document:
            pages = [(page.extract_text() or "").strip() for page in document.pages]
    except Exception as exc:
        raise DocumentConversionError(f"无法转换 PDF：{source}: {exc}") from exc
    return "\n\n".join(page for page in pages if page).strip()


def convert_docx(path: Path | str) -> str:
    """将 DOCX 的标题、段落和表格转换为 Markdown。"""

    source = _validate_input_file(path, {".docx"})
    try:
        from docx import Document

        document = Document(source)
    except Exception as exc:
        raise DocumentConversionError(f"无法读取 DOCX：{source}: {exc}") from exc

    blocks: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = str(getattr(paragraph.style, "name", "") or "")
        match = re.fullmatch(r"Heading\s+([1-6])", style_name, flags=re.IGNORECASE)
        blocks.append(f"{'#' * int(match.group(1))} {text}" if match else text)
    for table in document.tables:
        rows = [[_escape_markdown_cell(cell.text) for cell in row.cells] for row in table.rows]
        if rows:
            blocks.append(_markdown_table(rows))
    return "\n\n".join(blocks).strip()


def convert_html(path: Path | str) -> str:
    """使用 markdownify 将 HTML 转换为 Markdown。"""

    source = _validate_input_file(path, {".html", ".htm"})
    html = _read_text(source)
    try:
        from markdownify import markdownify

        return markdownify(html, heading_style="ATX").strip()
    except Exception as exc:
        raise DocumentConversionError(f"无法转换 HTML：{source}: {exc}") from exc


def convert_txt(path: Path | str) -> str:
    """读取 TXT，并用不会与正文冲突的 Markdown 代码围栏包裹。"""

    source = _validate_input_file(path, {".txt"})
    text = _read_text(source)
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text.rstrip()}\n{fence}\n"


def convert_rst(path: Path | str) -> str:
    """通过 docutils 将 RST 转 HTML，再转 Markdown。"""

    source = _validate_input_file(path, {".rst"})
    rst = _read_text(source)
    try:
        from docutils.core import publish_parts
    except ImportError as exc:
        raise DocumentConversionError(
            "转换 RST 需要依赖 docutils，请执行：python -m pip install -r requirements.txt"
        ) from exc
    try:
        from markdownify import markdownify

        html = publish_parts(source=rst, writer_name="html5")["html_body"]
        return markdownify(html, heading_style="ATX").strip()
    except Exception as exc:
        raise DocumentConversionError(f"无法转换 RST：{source}: {exc}") from exc


def convert_csv(path: Path | str) -> str:
    """将 CSV 的首行作为表头转换为 Markdown 表格。"""

    source = _validate_input_file(path, {".csv"})
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = [list(row) for row in csv.reader(stream)]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DocumentConversionError(f"无法读取 CSV：{source}: {exc}") from exc
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [
        [_escape_markdown_cell(value) for value in row + [""] * (width - len(row))]
        for row in rows
    ]
    return _markdown_table(normalized)


def import_document(
    path: Path | str,
    external_dir: Path | str,
    *,
    destination_name: str | None = None,
) -> dict[str, object]:
    """检测格式、转换并原子写入 external/markdown。"""

    source = _validate_input_file(path, _SUPPORTED_SUFFIXES)
    markdown_dir = _validate_output_dir(external_dir)
    suffix = source.suffix.lower()
    if suffix in {".md", ".markdown"}:
        markdown = _read_text(source)
        format_name = "markdown"
    else:
        converter = _CONVERTERS.get(suffix)
        if converter is None:
            raise DocumentConversionError(f"不支持的文档格式：{suffix or '<无扩展名>'}")
        markdown = converter(source)
        format_name = suffix.removeprefix(".")

    markdown_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _safe_filename(source.stem)
    destination = (
        _validated_destination(markdown_dir, destination_name)
        if destination_name is not None
        else markdown_dir / f"{safe_stem}.md"
    )
    encoded = markdown.encode("utf-8")
    if (
        destination_name is None
        and destination.exists()
        and destination.read_bytes() != encoded
    ):
        digest = hashlib.sha256(str(source).encode("utf-8") + b"\0" + encoded).hexdigest()[:10]
        destination = markdown_dir / f"{safe_stem}-{digest}.md"

    if not destination.exists() or destination.read_bytes() != encoded:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DocumentConversionError(f"无法写入 Markdown：{destination}: {exc}") from exc

    return {
        "source_path": str(destination.resolve()),
        "markdown_relative_path": destination.relative_to(markdown_dir).as_posix(),
        "format": format_name,
        "ok": True,
    }


def _validate_input_file(
    value: Path | str,
    allowed_suffixes: set[str],
) -> Path:
    path = _raw_absolute_path(value, "path")
    if path.suffix.lower() not in allowed_suffixes:
        expected = "、".join(sorted(allowed_suffixes))
        raise DocumentConversionError(f"文件格式不匹配，期望 {expected}：{path}")
    resolved = path.resolve(strict=False)
    if not resolved.exists():
        raise FileNotFoundError(f"文件不存在：{resolved}")
    if not resolved.is_file():
        raise DocumentConversionError(f"路径不是普通文件：{resolved}")
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise DocumentConversionError(f"文件不可读：{resolved}: {exc}") from exc
    return resolved


def _validate_output_dir(value: Path | str) -> Path:
    root = _raw_absolute_path(value, "external_dir").resolve(strict=False)
    return root if root.name.casefold() == "markdown" else root / "markdown"


def _validated_destination(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DocumentConversionError("destination_name 必须是非空相对路径")
    relative = Path(value.strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise DocumentConversionError("destination_name 必须是安全的相对路径")
    if relative.suffix.casefold() != ".md":
        raise DocumentConversionError("destination_name 必须以 .md 结尾")
    destination = (root / relative).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise DocumentConversionError("destination_name 超出 Markdown 目录") from exc
    return destination


def _raw_absolute_path(value: Path | str, name: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{name} 必须是路径字符串")
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{name} 不能为空")
    path = Path(raw).expanduser()
    if ".." in path.parts:
        raise DocumentConversionError(f"拒绝包含 '..' 的路径：{raw}")
    if not path.is_absolute():
        raise DocumentConversionError(f"必须使用绝对路径：{raw}")
    return path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise DocumentConversionError(f"无法按 UTF-8 读取文件：{path}: {exc}") from exc


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    header = rows[0] + [""] * (width - len(rows[0]))
    body = [row + [""] * (width - len(row)) for row in rows[1:]]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _escape_markdown_cell(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>").strip()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "document"


_SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".html",
    ".htm",
    ".txt",
    ".rst",
    ".csv",
    ".md",
    ".markdown",
}

_CONVERTERS: dict[str, Callable[[Path | str], str]] = {
    ".pdf": convert_pdf,
    ".docx": convert_docx,
    ".html": convert_html,
    ".htm": convert_html,
    ".txt": convert_txt,
    ".rst": convert_rst,
    ".csv": convert_csv,
}


__all__ = [
    "DocumentConversionError",
    "convert_pdf",
    "convert_docx",
    "convert_html",
    "convert_txt",
    "convert_rst",
    "convert_csv",
    "import_document",
]
