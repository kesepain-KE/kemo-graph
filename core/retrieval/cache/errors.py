from __future__ import annotations

from typing import Any

class SearchCacheError(RuntimeError):
    """搜索缓存无法读取、序列化或维护。"""

class SearchCacheCleanupError(SearchCacheError):
    """数据变更已提交，但包含旧正文的搜索缓存未能清理。"""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(
            "数据变更已经生效，但搜索缓存清理失败，旧检索历史可能仍包含已删除或修改的内容。"
            "请刷新列表，并单独清理搜索缓存，不要重复提交已完成的变更。"
        )
        self.result = result
