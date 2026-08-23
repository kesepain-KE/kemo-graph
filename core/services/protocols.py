"""领域服务与兼容门面之间的最小结构化契约。

使用 Protocol 而不是导入 ``KnowledgeBaseService``，避免服务模块和门面
形成循环依赖；新增领域服务只需满足这些运行时上下文能力即可。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ServiceOwner(Protocol):
    """服务可使用的知识库运行时上下文。"""

    settings: Any
    paths: Any
    data_dir: Path
    external_dir: Path

    def _require_initialized(self) -> None: ...

    def _require_available(self, *targets: str) -> None: ...

    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None: ...

    def _new_graph_engine(self) -> Any: ...

    def _new_rag_engine(self) -> Any: ...

    def _new_hybrid_engine(self) -> Any: ...

    def _chat(self, system: str, user: str) -> str: ...
