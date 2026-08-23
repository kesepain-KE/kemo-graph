"""本地 HTML 文件转换器；不执行网络抓取。"""

from __future__ import annotations

from pathlib import Path

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import DocumentConversionError, MissingOptionalDependencyError
from .._stream_info import StreamInfo
from .._utils import finalize_markdown, read_text, title_from_path


class HtmlConverter(DocumentConverter):
    extensions = frozenset({".html", ".htm"})

    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        html = read_text(source)
        try:
            from bs4 import BeautifulSoup
            from markdownify import markdownify
        except ImportError as exc:
            raise MissingOptionalDependencyError(
                "beautifulsoup4/markdownify", "HTML"
            ) from exc
        try:
            soup = BeautifulSoup(html, "html.parser")
            for node in soup.select(
                "script, style, noscript, template, svg, canvas, iframe, form, nav, button, input"
            ):
                node.decompose()
            main = soup.find("article") or soup.find("main") or soup.body or soup
            markdown = markdownify(str(main), heading_style="ATX", bullets="-")
        except Exception as exc:
            raise DocumentConversionError(f"无法转换 HTML：{source}: {exc}") from exc
        content = finalize_markdown(markdown, source)
        return DocumentConverterResult(
            title=title_from_path(source),
            text_content=content,
            source_path=str(source),
            converter=self.__class__.__name__,
        )


__all__ = ["HtmlConverter"]
