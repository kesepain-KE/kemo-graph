"""Public exceptions and configuration constants for the service facade."""
from core.documents.errors import (
    DocumentImportError, UnsupportedDocumentFormatError, DocumentImportPathError,
    DocumentTooLargeError, DocumentIngestError, DocumentContentConflictError,
    DocumentImportConflictError,
)
from core.documents.formats import SUPPORTED_IMPORT_SUFFIXES, MAX_IMPORT_BYTES


class KnowledgeBaseNotInitializedError(RuntimeError):
    """知识库的 sources.db 尚不存在。"""


class KnowledgeBaseProcessingError(RuntimeError):
    """所请求的数据当前正在整理。"""


CONFIG_API_KEY_MASK = "************"
