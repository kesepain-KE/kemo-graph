"""Document project/rename/move endpoints, separate from retrieval routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from core.knowledge_base import KnowledgeBaseService
from .deps import get_service
from .errors import success_response
from .schemas import StrictRequest, DocumentBatchDeleteRequest

router = APIRouter()
Service = Annotated[KnowledgeBaseService, Depends(get_service)]


class ProjectCreateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=160)


class DocumentLocationRequest(StrictRequest):
    filename: str | None = Field(default=None, min_length=1, max_length=160)
    project: str | None = Field(default=None, max_length=160)
    expected_relative_path: str | None = None


class DocumentMoveRequest(DocumentBatchDeleteRequest):
    project: str = Field(max_length=160)


@router.get("/projects")
def get_projects(service: Service) -> dict:
    return success_response(service.list_projects())


@router.post("/projects")
def post_project(payload: ProjectCreateRequest, service: Service) -> dict:
    return success_response(service.create_project(payload.name))


@router.patch("/documents/{source_id}/location")
def patch_document_location(source_id: str, payload: DocumentLocationRequest, service: Service) -> dict:
    if payload.filename is None and payload.project is None:
        raise ValueError("至少提供 filename 或 project")
    result = service.relocate_documents([{"source_id": source_id, **payload.model_dump()}])
    return success_response(result["documents"][0])


@router.post("/documents/move-batch")
def post_move_documents(payload: DocumentMoveRequest, service: Service) -> dict:
    return success_response(service.relocate_documents([{"source_id": source_id, "project": payload.project} for source_id in payload.source_ids]))
