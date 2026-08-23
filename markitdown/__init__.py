"""kemo-graph 的轻量 MarkItDown 兼容转换层。

本模块参考 Microsoft MarkItDown 的 Converter 调度思路重新实现，
仅处理本地普通文档。它不包含 Microsoft MarkItDown 的外围功能，
也不代表 Microsoft 官方项目。

Portions of the architecture are inspired by Microsoft MarkItDown.
Copyright (c) Microsoft Corporation. Licensed under the MIT License.
See ``THIRD_PARTY_NOTICES.md`` for the applicable notice.
"""

from ._base_converter import DocumentConverter, DocumentConverterResult
from ._exceptions import (
    DocumentConversionError,
    DocumentTooLargeError,
    MarkItDownError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
    UnsafeInputError,
)
from ._markitdown import MarkItDown

__version__ = "0.1.0"

__all__ = [
    "DocumentConversionError",
    "DocumentConverter",
    "DocumentConverterResult",
    "DocumentTooLargeError",
    "MarkItDown",
    "MarkItDownError",
    "MissingOptionalDependencyError",
    "UnsupportedFormatError",
    "UnsafeInputError",
    "__version__",
]
