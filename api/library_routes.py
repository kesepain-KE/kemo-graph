"""Physical original-file library routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import Field

from core import library
from core.document_preview import preview_document
from core.knowledge_base import KnowledgeBaseService
from core.jobs import MaintenanceJobManager
from .deps import get_service, get_job_manager
from .errors import success_response
from .schemas import StrictRequest

router = APIRouter()
Service = Annotated[KnowledgeBaseService, Depends(get_service)]
Jobs = Annotated[MaintenanceJobManager, Depends(get_job_manager)]


class LibraryPath(StrictRequest):
    path: str = Field(default="", max_length=2048)


class LibraryCreate(StrictRequest):
    directory: str = Field(default="", max_length=2048)
    name: str = Field(min_length=1, max_length=160)
    kind: str


class LibraryMove(LibraryPath):
    destination: str = Field(min_length=1, max_length=2048)


@router.get("/library")
def get_library(service: Service, directory: str = "", search: str = "") -> dict:
    return success_response(library.list_library(service, directory, search))


@router.post("/library/refresh")
def refresh_library(payload: LibraryPath, service: Service) -> dict:
    return success_response(library.refresh_library(service, payload.path))


@router.post("/library/entries")
def create_entry(payload: LibraryCreate, service: Service) -> dict:
    return success_response(library.create_entry(service, payload.directory, payload.name, payload.kind))


@router.patch("/library/entries")
def move_entry(payload: LibraryMove, service: Service) -> dict:
    return success_response(library.relocate_entry(service, payload.path, payload.destination))


@router.delete("/library/entries")
def delete_entry(payload: LibraryPath, service: Service) -> dict:
    return success_response(library.delete_entry(service, payload.path))


@router.post("/library/build")
def build_entry(payload: LibraryPath, service: Service, jobs: Jobs) -> dict:
    paths = library.build_paths(service, payload.path)
    if not paths:
        raise ValueError("所选目录没有可构建的文档")
    return success_response(jobs.submit("ingest", paths=paths, mode="both", retry_failed=True))


@router.get("/library/preview")
def preview_original(service: Service, path: str) -> dict:
    return success_response(preview_document(library.resolve_path(service, path)))


def original_response(path: Path, download: bool) -> FileResponse:
    # HTML/SVG originals are downloaded; active content never runs in the app origin.
    inline = path.suffix.casefold() in {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
    return FileResponse(
        path,
        filename=path.name,
        content_disposition_type="inline" if inline and not download else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/library/original")
def get_original(service: Service, path: str, download: bool = False) -> FileResponse:
    original = library.resolve_path(service, path)
    if not original.is_file():
        raise ValueError("所选路径不是文件")
    return original_response(original, download)


@router.get("/documents/{source_id}/original")
def get_source_original(source_id: str, service: Service, download: bool = False) -> FileResponse:
    return original_response(library.original_for_source(service, source_id), download)


@router.get("/documents/{source_id}/source")
def get_source_location(source_id: str, service: Service) -> dict:
    path = library.original_for_source(service, source_id)
    return success_response({"path": path.relative_to(library.original_root(service)).as_posix()})
