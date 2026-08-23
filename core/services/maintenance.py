"""维护域服务：整理、重建、回收站和后台任务。"""

from __future__ import annotations

from typing import Any

from .protocols import ServiceOwner


class MaintenanceService:
    """把维护操作从 KnowledgeBaseService 门面中分类出来。"""

    def __init__(self, owner: ServiceOwner) -> None:
        self.owner = owner

    def status(self) -> dict[str, Any]:
        return self.owner._status_impl()

    def get_config(self) -> dict[str, Any]:
        return self.owner._get_config_impl()

    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.owner._save_config_impl(data)

    def cleanup_recycle(self, *, force: bool = False) -> dict[str, Any]:
        return self.owner._cleanup_recycle_impl(force=force)

    def generate_group_summaries(self, *, force: bool = False) -> dict[str, Any]:
        return self.owner._generate_group_summaries_impl(force=force)

    def organize_graph(
        self,
        *,
        use_llm: bool = True,
        summarize: bool = True,
    ) -> dict[str, Any]:
        return self.owner._organize_graph_impl(
            use_llm=use_llm,
            summarize=summarize,
        )

    def rebuild_knowledge_base(self, *, progress: Any = None) -> dict[str, Any]:
        return self.owner._rebuild_knowledge_base_impl(progress=progress)

    def rebuild_all(self, *, progress: Any = None) -> dict[str, Any]:
        return self.owner._rebuild_all_impl(progress=progress)

    def list_jobs(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return self.owner._list_jobs_impl(limit=limit)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.owner._get_job_impl(job_id)
