from __future__ import annotations

from pathlib import Path
from provider.engine import chat
from core.settings.schema import DEFAULT_CONFIG_PATH, AppConfig
from core.settings.loader import load_config
from core.storage.connections import connect_sources
from core.storage.paths import get_database_paths
from core.graph.engine import GraphEngine
from core.retrieval.hybrid import HybridEngine
from core.observability.logger import DailyTSVLogger
from core.rag.engine import RAGEngine
from core.service.errors import KnowledgeBaseNotInitializedError, KnowledgeBaseProcessingError


class ServiceContext:
    """Shared runtime for the four domain services."""

    def __init__(
        self,
        *,
        settings: AppConfig | None = None,
        data_dir: Path | str | None = None,
        external_dir: Path | str | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_config()
        self.config_path = (
            Path(config_path or DEFAULT_CONFIG_PATH).expanduser().resolve()
        )
        self._data_dir_override = (
            Path(data_dir).expanduser().resolve() if data_dir is not None else None
        )
        self._external_dir_override = (
            Path(external_dir).expanduser().resolve()
            if external_dir is not None
            else None
        )
        self.data_dir = self._data_dir_override or self.settings.resolve_data_dir()
        self.external_dir = (
            self._external_dir_override or self.settings.resolve_external_dir()
        )
        self.paths = get_database_paths(self.data_dir)
        self._logger = DailyTSVLogger(
            self.settings.resolve_log_dir(),
            self.settings.log_level,
        )


    def _log_event(
        self,
        action: str,
        detail: str,
        elapsed_ms: int | float | str = "-",
        *,
        level: str = "INFO",
    ) -> None:
        try:
            self._logger.log(
                "knowledge_base",
                action,
                detail,
                elapsed_ms,
                level,
            )
        except Exception:
            pass

    def _require_initialized(self) -> None:
        if not self.paths.sources_db.exists():
            raise KnowledgeBaseNotInitializedError("知识库尚未初始化")

    def _require_available(self, *targets: str) -> None:
        self._require_initialized()
        conditions: list[str] = []
        if "graph" in targets:
            conditions.append("graph_status = 'processing'")
        if "rag" in targets:
            conditions.append("rag_status = 'processing'")
        connection = connect_sources(self.paths)
        try:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sources "
                    "WHERE exists_status = 'active' AND ("
                    + " OR ".join(conditions)
                    + ")"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        if count:
            raise KnowledgeBaseProcessingError("知识库正在处理中，请稍后重试")

    def _new_graph_engine(self):
        return GraphEngine(self.data_dir, settings=self.settings)

    def _new_rag_engine(self):
        return RAGEngine(self.data_dir, settings=self.settings)

    def _new_hybrid_engine(self):
        return HybridEngine(self.data_dir, settings=self.settings)

    def _chat(self, system: str, user: str) -> str:
        return chat(system, user, settings=self.settings)
