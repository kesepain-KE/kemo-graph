from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Query
from api.errors import success_response
from api.schemas import APIResponse, IngestRequest, OrganizeGraphRequest

from api.routes.dependencies import Service, Jobs

router = APIRouter()

@router.post("/jobs/ingest", response_model=APIResponse)
def post_ingest_job(payload: IngestRequest, jobs: Jobs) -> dict:
    options = {"paths": payload.paths, "mode": payload.mode}
    if payload.retry_failed:
        options["retry_failed"] = True
    return success_response(jobs.submit("ingest", **options))

@router.post("/jobs/summarize", response_model=APIResponse)
def post_summarize_job(jobs: Jobs) -> dict:
    """在后台生成节点群摘要，供 Web 端持续追踪执行状态。"""

    return success_response(jobs.submit("summarize"))

@router.post("/maintenance/organize-graph", response_model=APIResponse)
def post_organize_graph(payload: OrganizeGraphRequest, jobs: Jobs) -> dict:
    return success_response(
        jobs.submit(
            "organize_graph",
            use_llm=payload.use_llm,
            summarize=payload.summarize,
        )
    )

@router.post("/maintenance/rebuild-knowledge-base", response_model=APIResponse)
def post_rebuild_knowledge_base(jobs: Jobs) -> dict:
    return success_response(jobs.submit("rebuild_knowledge_base"))

@router.post("/maintenance/rebuild-all", response_model=APIResponse)
def post_rebuild_all(jobs: Jobs) -> dict:
    return success_response(jobs.submit("rebuild_all"))

@router.get("/jobs", response_model=APIResponse)
def get_jobs(
    jobs: Jobs,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    status: Annotated[str | None, Query()] = None,
) -> dict:
    return success_response({"jobs": jobs.list(limit=limit, status=status)})

@router.get("/jobs/{job_id}", response_model=APIResponse)
def get_job(job_id: str, jobs: Jobs) -> dict:
    return success_response(jobs.get(job_id))

@router.post("/maintenance/summarize", response_model=APIResponse)
def post_summarize(service: Service) -> dict:
    return success_response(service.generate_group_summaries())
