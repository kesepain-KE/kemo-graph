from __future__ import annotations
class IngestError(RuntimeError):
    """文档扫描或整理失败。"""

class SourceChangedDuringIngest(IngestError):
    """扫描后、整理前文档再次发生变化。"""

class GraphExtractionError(IngestError):
    """LLM 返回的文档图谱数据不符合约定。"""


class DocumentNotFoundError(IngestError):
    """请求删除的活动文档不存在。"""
