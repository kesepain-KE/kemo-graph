from __future__ import annotations
"""Document lifecycle errors shared by conversion and service layers."""
class DocumentImportError(RuntimeError):
    """文档导入入口错误基类。"""

class UnsupportedDocumentFormatError(DocumentImportError):
    """用户上传了不受支持的文件格式。"""

class DocumentImportPathError(DocumentImportError):
    """导入路径不安全、缺失或不是普通文件。"""

class DocumentTooLargeError(DocumentImportError):
    """导入文件超过服务端大小上限。"""

class DocumentIngestError(DocumentImportError):
    """转换已完成，但后续知识库整理无法启动。"""

class DocumentContentConflictError(DocumentImportError):
    """文档在编辑期间已变化，拒绝覆盖较新的内容。"""

class DocumentImportConflictError(DocumentImportError):
    """源文件在调用方确认后发生变化，拒绝导入不同内容。"""


class IngestError(RuntimeError):
    """文档扫描或整理失败。"""

class SourceChangedDuringIngest(IngestError):
    """扫描后、整理前文档再次发生变化。"""

class GraphExtractionError(IngestError):
    """LLM 返回的文档图谱数据不符合约定。"""


class DocumentNotFoundError(IngestError):
    """请求删除的活动文档不存在。"""
