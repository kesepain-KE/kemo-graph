"""轻量文档转换层的异常类型。"""

from __future__ import annotations


class MarkItDownError(RuntimeError):
    """所有 MarkItDown 转换错误的基类。"""


class DocumentConversionError(MarkItDownError):
    """文档存在但无法安全转换。"""


class UnsupportedFormatError(MarkItDownError):
    """没有注册 Converter 能处理输入格式。"""


class MissingOptionalDependencyError(DocumentConversionError):
    """某种格式需要的可选依赖没有安装。"""

    def __init__(self, package: str, format_name: str) -> None:
        self.package = package
        self.format_name = format_name
        super().__init__(
            f"转换 {format_name} 需要可选依赖 {package}，"
            f"请先安装该依赖后重试。"
        )


class UnsafeInputError(MarkItDownError):
    """拒绝 URL、网络地址或不安全的输入。"""


class DocumentTooLargeError(MarkItDownError):
    """输入文档超过转换层的大小上限。"""


__all__ = [
    "DocumentConversionError",
    "DocumentTooLargeError",
    "MarkItDownError",
    "MissingOptionalDependencyError",
    "UnsupportedFormatError",
    "UnsafeInputError",
]
