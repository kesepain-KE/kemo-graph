from __future__ import annotations


class GraphError(RuntimeError):
    """图谱查询错误基类。"""

class GraphIntegrityError(GraphError):
    """图谱数据库内容不符合约定结构。"""

class GraphQueryError(GraphError):
    """图谱查询参数无效。"""


class GraphOrganizerError(RuntimeError):
    """图谱整理无法安全完成。"""


class GraphNodeNotFoundError(LookupError):
    """局部图谱的锚点节点不存在。"""

class GraphRevisionChangedError(RuntimeError):
    """客户端请求的图谱 revision 已经过期。"""

    def __init__(self, expected: str, current: str) -> None:
        self.expected = expected
        self.current = current
        super().__init__(
            f"图谱已发生变化，请重新加载：expected={expected}, current={current}"
        )
