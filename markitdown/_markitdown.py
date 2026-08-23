"""轻量 MarkItDown 调度器，仅处理本地普通文件。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from mimetypes import guess_type
from typing import BinaryIO, Iterable

from ._base_converter import DocumentConverter, DocumentConverterResult
from ._exceptions import DocumentTooLargeError, UnsupportedFormatError
from ._stream_info import StreamInfo
from ._utils import validate_local_path
from .converters import default_converters


class MarkItDown:
    """本地文档到 Markdown 的单一入口。

    这是面向 kemo-graph 的轻量实现，不包含 URL、网页抓取、视频、音频、
    OCR、云服务或浏览器能力。各格式依赖均在 Converter 内部延迟导入。
    """

    def __init__(
        self,
        *,
        max_input_bytes: int = 50 * 1024 * 1024,
        max_output_chars: int = 20_000_000,
        converters: Iterable[DocumentConverter] | None = None,
    ) -> None:
        self.max_input_bytes = max_input_bytes
        self.max_output_chars = max_output_chars
        self._converters = sorted(
            list(converters) if converters is not None else default_converters(),
            key=lambda converter: getattr(converter, "priority", 0),
            reverse=True,
        )

    @property
    def converters(self) -> tuple[DocumentConverter, ...]:
        return tuple(self._converters)

    def register_converter(self, converter: DocumentConverter) -> None:
        """注册一个额外 Converter；同一实例可安全重复使用。"""

        self._converters.append(converter)
        self._converters.sort(
            key=lambda item: getattr(item, "priority", 0), reverse=True
        )

    def convert(self, input_file: Path | str) -> DocumentConverterResult:
        """转换本地文件；不接受 URL、网络链接或字节串。"""

        source = validate_local_path(
            input_file,
            max_input_bytes=self.max_input_bytes,
        )
        extension, mime_type = _detect_file_type(source)
        info = StreamInfo(
            path=source,
            filename=source.name,
            extension=extension,
            mime_type=mime_type,
            size=source.stat().st_size,
        )
        candidates = [
            converter for converter in self._converters if converter.accepts(info)
        ]
        if not candidates:
            raise UnsupportedFormatError(
                f"不支持的本地文档格式：{source.suffix or '<无扩展名>'}"
            )
        last_error: Exception | None = None
        for converter in candidates:
            try:
                result = converter.convert(source, info)
                if not result.text_content.strip():
                    raise ValueError("转换结果为空")
                if (
                    self.max_output_chars > 0
                    and len(result.text_content) > self.max_output_chars
                ):
                    raise DocumentTooLargeError(
                        f"转换结果超过大小上限：{len(result.text_content)} 字符 > "
                        f"{self.max_output_chars} 字符：{source}"
                    )
                if result.markdown is None:
                    result.markdown = result.text_content
                if not result.text_content:
                    result.text_content = result.markdown
                return result
            except Exception as exc:
                last_error = exc
                if len(candidates) == 1:
                    raise
        assert last_error is not None
        raise last_error

    def convert_local(self, input_file: Path | str) -> DocumentConverterResult:
        return self.convert(input_file)

    def convert_stream(
        self,
        stream: BinaryIO,
        *,
        file_extension: str,
        filename: str | None = None,
    ) -> DocumentConverterResult:
        """将本地二进制流安全落盘后转换；不接受远程响应对象。"""

        if not hasattr(stream, "read"):
            raise TypeError("stream 必须提供 read() 方法")
        extension = file_extension.strip().casefold()
        if not extension.startswith("."):
            extension = f".{extension}"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=extension,
                prefix="kemo-markitdown-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                total = 0
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("stream.read() 必须返回 bytes")
                    total += len(chunk)
                    if self.max_input_bytes > 0 and total > self.max_input_bytes:
                        raise DocumentTooLargeError(
                            f"输入流超过大小上限：{total} bytes > {self.max_input_bytes} bytes"
                        )
                    temporary.write(chunk)
            result = self.convert(temporary_path)
            if filename:
                result.source_path = filename
            return result
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _detect_file_type(source: Path) -> tuple[str, str | None]:
    """优先使用扩展名；无扩展名时才延迟调用 Magika。"""

    extension = source.suffix.casefold()
    mime_type = guess_type(source.name)[0]
    if extension:
        return extension, mime_type
    try:
        from magika import Magika

        output = Magika().identify_path(source).output
        extensions = list(getattr(output, "extensions", []) or [])
        detected_extension = f".{extensions[0].lstrip('.').casefold()}" if extensions else ""
        return detected_extension, getattr(output, "mime_type", None)
    except Exception:
        return "", mime_type


__all__ = ["MarkItDown"]
