"""兼容旧工具签名的本地文档导入适配层。

真正的格式识别与转换统一由根目录 ``markitdown`` 包完成。本模块只负责：

* 保留历史公开函数签名，避免 API/工具调用破坏；
* 执行 kemo-graph 的绝对路径和路径遍历校验；
* 将 Markdown 结果原子写入知识库目录。

网络链接、视频、音频、OCR 和云服务不属于此层职责。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from markitdown import DocumentConversionError, DocumentConverterResult, MarkItDown
from markitdown._exceptions import (
    DocumentTooLargeError as ConversionTooLargeError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
    UnsafeInputError,
)
from .errors import (
    DocumentImportPathError,
    DocumentTooLargeError,
    UnsupportedDocumentFormatError,
)


from .formats import SUPPORTED_IMPORT_SUFFIXES

_MARKITDOWN = MarkItDown()

SUPPORTED_DOCUMENT_SUFFIXES = SUPPORTED_IMPORT_SUFFIXES


def _validate_input_file(value: Path | str, allowed_suffixes: set[str]) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError("path 必须是路径字符串")
    raw = str(value).strip()
    if not raw:
        raise DocumentImportPathError("path 不能为空")
    path = Path(raw).expanduser()
    if ".." in path.parts:
        raise DocumentImportPathError(f"拒绝包含 '..' 的路径：{raw}")
    if not path.is_absolute():
        raise DocumentImportPathError(f"必须使用绝对路径：{raw}")
    if path.suffix.casefold() not in allowed_suffixes:
        expected = "、".join(sorted(allowed_suffixes))
        raise UnsupportedDocumentFormatError(f"文件格式不匹配，期望 {expected}：{path}")
    resolved = path.resolve(strict=False)
    if not resolved.exists():
        raise DocumentImportPathError(f"文件不存在：{resolved}")
    if not resolved.is_file():
        raise DocumentImportPathError(f"路径不是普通文件：{resolved}")
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise DocumentImportPathError(f"文件不可读：{resolved}: {exc}") from exc
    return resolved


def _convert_with_markitdown(path: Path | str) -> DocumentConverterResult:
    try:
        result = _MARKITDOWN.convert(path)
    except ConversionTooLargeError as exc:
        raise DocumentTooLargeError(str(exc)) from exc
    except MissingOptionalDependencyError as exc:
        raise DocumentConversionError(str(exc)) from exc
    except UnsafeInputError as exc:
        raise DocumentImportPathError(str(exc)) from exc
    except UnsupportedFormatError as exc:
        raise UnsupportedDocumentFormatError(str(exc)) from exc
    except DocumentConversionError:
        raise
    except Exception as exc:
        raise DocumentConversionError(f"无法转换文档：{path}: {exc}") from exc
    return result


def convert_pdf(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".pdf"})).text_content


def convert_docx(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".docx", ".docm"})).text_content


def convert_html(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".html", ".htm"})).text_content


def convert_txt(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".txt", ".log"})).text_content


def convert_rst(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".rst"})).text_content


def convert_csv(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".csv", ".tsv"})).text_content


def convert_spreadsheet(path: Path | str) -> str:
    return _convert_with_markitdown(
        _validate_input_file(path, {".xlsx", ".xlsm", ".xls"})
    ).text_content


def convert_pptx(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".pptx", ".pptm"})).text_content


def convert_epub(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".epub"})).text_content


def convert_rtf(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".rtf"})).text_content


def convert_eml(path: Path | str) -> str:
    return _convert_with_markitdown(_validate_input_file(path, {".eml"})).text_content


def convert_data(path: Path | str) -> str:
    return _convert_with_markitdown(
        _validate_input_file(
            path,
            {".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml"},
        )
    ).text_content


def import_document(
    path: Path | str,
    external_dir: Path | str,
    *,
    destination_name: str | None = None,
) -> dict[str, object]:
    """检测格式、转换并原子写入 ``external/markdown``。"""

    source = _validate_input_file(path, set(SUPPORTED_DOCUMENT_SUFFIXES))
    markdown_dir = _validate_output_dir(external_dir)
    result = _convert_with_markitdown(source)
    markdown = result.text_content
    suffix = result.file_extension
    format_name = (
        "markdown" if suffix in {".md", ".markdown"}
        else "html" if suffix in {".html", ".htm"}
        else suffix.removeprefix(".")
    )

    markdown_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _safe_filename(source.stem)
    destination = (
        _validated_destination(markdown_dir, destination_name)
        if destination_name is not None
        else markdown_dir / f"{safe_stem}.md"
    )
    encoded = markdown.encode("utf-8")
    if destination_name is None and destination.exists():
        try:
            same_content = destination.read_bytes() == encoded
        except OSError:
            same_content = False
        if not same_content:
            digest = hashlib.sha256(
                str(source).encode("utf-8") + b"\0" + encoded
            ).hexdigest()[:10]
            destination = markdown_dir / f"{safe_stem}-{digest}.md"

    if not destination.exists() or destination.read_bytes() != encoded:
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DocumentConversionError(
                f"无法写入 Markdown：{destination}: {exc}"
            ) from exc

    return {
        "source_path": str(destination.resolve()),
        "markdown_relative_path": destination.relative_to(markdown_dir).as_posix(),
        "format": format_name,
        "ok": True,
    }


def _validate_output_dir(value: Path | str) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError("external_dir 必须是路径字符串")
    root = Path(value).expanduser().resolve(strict=False)
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


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "document"


__all__ = [
    "DocumentConversionError",
    "DocumentTooLargeError",
    "SUPPORTED_DOCUMENT_SUFFIXES",
    "UnsupportedFormatError",
    "UnsafeInputError",
    "convert_csv",
    "convert_data",
    "convert_docx",
    "convert_eml",
    "convert_epub",
    "convert_html",
    "convert_pdf",
    "convert_pptx",
    "convert_rst",
    "convert_rtf",
    "convert_spreadsheet",
    "convert_txt",
    "import_document",
]
