from fastapi import APIRouter
from core.documents import build as build_documents
from api.errors import success_response

from api.routes.dependencies import Service, Jobs
from api.schemas import ProcessedContentUpdate

router = APIRouter()

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
