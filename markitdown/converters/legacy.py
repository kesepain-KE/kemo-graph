"""轻量可选格式 Converter。"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError
from .._stream_info import StreamInfo
from .._utils import finalize_markdown, read_text, title_from_path


class EmlConverter(DocumentConverter):
    extensions = frozenset({".eml"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            with source.open("rb") as stream:
                message = BytesParser(policy=policy.default).parse(stream)
        except Exception as exc:
            raise DocumentConversionError(f"无法读取 EML：{source}: {exc}") from exc

        parts = [f"# {message.get('subject') or title_from_path(source)}"]
        for label, header in (("发件人", "from"), ("收件人", "to"), ("日期", "date")):
            value = message.get(header)
            if value:
                parts.append(f"- **{label}**：{value}")
        body = _email_body(message)
        if body:
            parts.extend(["", body])
        content = finalize_markdown("\n".join(parts), source)
        return DocumentConverterResult(
            title=str(message.get("subject") or title_from_path(source)),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class EpubConverter(DocumentConverter):
    extensions = frozenset({".epub"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            from bs4 import BeautifulSoup
            from ebooklib import epub
            from markdownify import markdownify
        except ImportError as exc:
            raise MissingOptionalDependencyError(
                "EbookLib/beautifulsoup4/markdownify", "EPUB"
            ) from exc

        try:
            book = epub.read_epub(str(source), options={"ignore_ncx": True})
            chapters: list[str] = []
            for chapter_id, _linear in book.spine:
                item = book.get_item_with_id(chapter_id)
                if item is None:
                    continue
                soup = BeautifulSoup(item.get_content(), "html.parser")
                for node in soup.select("script, style, nav, svg"):
                    node.decompose()
                body = soup.body or soup
                rendered = markdownify(str(body), heading_style="ATX").strip()
                if rendered:
                    chapters.append(rendered)
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 EPUB：{source}: {exc}") from exc
        content = finalize_markdown("\n\n---\n\n".join(chapters), source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class RtfConverter(DocumentConverter):
    extensions = frozenset({".rtf"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:
            raise MissingOptionalDependencyError("striprtf", "RTF") from exc
        try:
            markdown = rtf_to_text(read_text(source), errors="ignore")
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 RTF：{source}: {exc}") from exc
        content = finalize_markdown(markdown, source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


class RstConverter(DocumentConverter):
    extensions = frozenset({".rst"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        try:
            from docutils.core import publish_parts
            from markdownify import markdownify
        except ImportError as exc:
            raise MissingOptionalDependencyError("docutils/markdownify", "RST") from exc
        try:
            html_body = publish_parts(
                source=read_text(source), writer_name="html5"
            )["html_body"]
            markdown = markdownify(html_body, heading_style="ATX")
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 RST：{source}: {exc}") from exc
        content = finalize_markdown(markdown, source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


def _email_body(message: object) -> str:
    if message.is_multipart():
        plain: list[str] = []
        html_body: list[str] = []
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            try:
                content = part.get_content()
            except Exception:
                continue
            if content_type == "text/plain":
                plain.append(str(content))
            elif content_type == "text/html":
                html_body.append(str(content))
        if plain:
            return "\n\n".join(plain).strip()
        if html_body:
            try:
                from bs4 import BeautifulSoup
                from markdownify import markdownify

                return markdownify(
                    str(BeautifulSoup(html_body[0], "html.parser")),
                    heading_style="ATX",
                ).strip()
            except ImportError:
                return html_body[0]
        return ""
    try:
        return str(message.get_content()).strip()
    except Exception:
        return ""


__all__ = ["EmlConverter", "EpubConverter", "RtfConverter", "RstConverter"]
