"""Converter 协议与统一结果对象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ._stream_info import StreamInfo


@dataclass(slots=True)
class DocumentConverterResult:
    """文档转换结果。

    ``markdown`` 是规范字段，``text_content`` 保留 MarkItDown 风格的
    调用方式。两者默认保持同一份已规范化 Markdown 文本，避免图谱和
    预览链路出现两份内容不一致。
    """

    title: str | None = None
    text_content: str = ""
    markdown: str | None = None
    source_path: str | None = None
    converter: str | None = None

    def __post_init__(self) -> None:
        if self.markdown is None:
            self.markdown = self.text_content
        elif not self.text_content:
            self.text_content = self.markdown


class DocumentConverter(ABC):
    """单一格式 Converter 的最小接口。"""

    #: 同一扩展名存在多个候选时，数值越大越优先。
    priority = 0
    #: Converter 声明的本地扩展名集合。
    extensions: frozenset[str] = frozenset()

    def accepts(self, info: StreamInfo) -> bool:
        return info.extension.casefold() in self.extensions

    @abstractmethod
    def convert(self, source: Path, info: StreamInfo) -> DocumentConverterResult:
        """将一个已经通过安全校验的本地文件转换为 Markdown。"""


__all__ = ["DocumentConverter", "DocumentConverterResult"]
