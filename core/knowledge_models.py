"""Public errors and immutable import metadata for the knowledge service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from markitdown import supported_suffixes

class KnowledgeBaseNotInitializedError(RuntimeError):
    """知识库的 sources.db 尚不存在。"""


class KnowledgeBaseProcessingError(RuntimeError):
    """所请求的数据当前正在整理。"""
















# 产品只接收注册表中的本地文档格式；无扩展名输入留给 Magika 识别，
# 识别出的格式仍须由已注册 Converter 处理，不按普通文本兜底。
CONFIG_API_KEY_MASK = "************"



from .documents.errors import DocumentImportError, UnsupportedDocumentFormatError, DocumentImportPathError, DocumentTooLargeError, DocumentIngestError, DocumentContentConflictError, DocumentImportConflictError
from .documents.formats import SUPPORTED_IMPORT_SUFFIXES, MAX_IMPORT_BYTES, _ImportSnapshot
