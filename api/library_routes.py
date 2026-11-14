"""Physical original-file library routes."""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from markitdown import detect_file_type
from pydantic import Field

from core import library
from core.document_preview import preview_document
from core.service.facade import KnowledgeBaseService
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

class LibraryTransfer(LibraryPath):
    directory: str = Field(default="", max_length=2048)
    mode: Literal["copy", "cut"]
    conflict: Literal["error", "keep_both"] = "error"


class LibraryBuild(LibraryPath):
    reprocess: bool = False


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

@router.post("/library/transfer")
def transfer_entry(payload: LibraryTransfer, service: Service) -> dict:
    return success_response(library.transfer_entry(
        service, payload.path, payload.directory, payload.mode, conflict=payload.conflict,
    ))


@router.post("/library/build")
def build_entry(payload: LibraryBuild, service: Service, jobs: Jobs) -> dict:
    selected = library.resolve_path(service, payload.path)
    if selected.is_file() and selected.suffix.casefold() not in library.SUPPORTED_IMPORT_SUFFIXES:
        raise ValueError("该文件格式不支持构建")
    return success_response(jobs.submit(
        "ingest", library_path=payload.path, reprocess=payload.reprocess, mode="both", force=True,
    ))


@router.get("/library/preview")
def preview_original(service: Service, path: str) -> dict:
    return success_response(preview_document(library.resolve_path(service, path)))


def original_response(path: Path, download: bool) -> FileResponse:
    # HTML/SVG originals are downloaded; active content never runs in the app origin.
    suffix = path.suffix.casefold()
    if not suffix and not download:
        suffix, _ = detect_file_type(path)
    inline_types = {
        ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    }
    media_type = inline_types.get(suffix)
    return FileResponse(
        path,
        filename=path.name,
        media_type=media_type,
        content_disposition_type="inline" if media_type and not download else "attachment",
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
