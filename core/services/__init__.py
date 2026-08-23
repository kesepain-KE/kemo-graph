"""知识库领域服务。

``KnowledgeBaseService`` 仍然是对外唯一的兼容门面；本包只承载按
职责分类的领域入口，避免 API、CLI 和 Web 直接依赖底层实现。
"""

from .document import DocumentService
from .graph import GraphService
from .maintenance import MaintenanceService
from .protocols import ServiceOwner
from .retrieval import RetrievalService

__all__ = [
    "DocumentService",
    "GraphService",
    "MaintenanceService",
    "RetrievalService",
    "ServiceOwner",
]
