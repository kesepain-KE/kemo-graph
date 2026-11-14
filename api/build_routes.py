"""Build actions and derived Markdown, separate from original-file editing."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from core import build_documents
from core.maintenance.jobs import MaintenanceJobManager
from core.service.facade import KnowledgeBaseService
from .deps import get_job_manager, get_service
from .errors import success_response
from .schemas import StrictRequest

router = APIRouter()
Service = Annotated[KnowledgeBaseService, Depends(get_service)]
Jobs = Annotated[MaintenanceJobManager, Depends(get_job_manager)]


class ProcessedContentUpdate(StrictRequest):
    content: str
    expected_content_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@router.get("/documents/{source_id}/processed")
def get_processed(source_id: str, service: Service) -> dict:
    return success_response(build_documents.read_processed(service, source_id))


@router.put("/documents/{source_id}/processed")
def update_processed(source_id: str, payload: ProcessedContentUpdate, service: Service) -> dict:
    return success_response(build_documents.update_processed(
        service, source_id, payload.content, payload.expected_content_hash,
    ))


@router.post("/documents/{source_id}/rebuild")
def rebuild_document(source_id: str, service: Service, jobs: Jobs) -> dict:
    row, _ = build_documents.processed_source(service, source_id)
    return success_response(jobs.submit("ingest", paths=[row["relative_path"]], mode="both", force=True))


@router.post("/build/retry-failed")
def retry_failed(jobs: Jobs) -> dict:
    return success_response(jobs.submit("ingest", mode="both", failed_only=True))
