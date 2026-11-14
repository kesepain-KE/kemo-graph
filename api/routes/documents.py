from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from fastapi import APIRouter, File, Query, UploadFile
from api.errors import success_response
from api.schemas import APIResponse, DocumentBatchDeleteRequest, DocumentContentUpdateRequest, IngestRequest, UploadRequest

from api.routes.dependencies import Service
from api.routes.uploads import _stage_uploaded_document, _raise_import_failure

router = APIRouter()

ingest_router = APIRouter()

@ingest_router.post("/ingest", response_model=APIResponse)
def post_ingest(payload: IngestRequest, service: Service) -> dict:
    options = {"paths": payload.paths, "mode": payload.mode}
    if payload.retry_failed:
        options["retry_failed"] = True
    return success_response(service.ingest(**options))

@router.get("/documents", response_model=APIResponse)
def get_documents(
    service: Service,
    status: Annotated[str | None, Query(description="active / pending / all")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    directory: Annotated[str | None, Query(max_length=2048, description="原文件相对目录，省略时查询全库")] = None,
    search: Annotated[str | None, Query(max_length=200, description="按相对路径字面搜索")] = None,
    graph_status: Annotated[str | None, Query(description="Graph 状态过滤")] = None,
    rag_status: Annotated[str | None, Query(description="RAG 状态过滤")] = None,
    include_summary: Annotated[bool, Query(description="是否返回当前范围的状态摘要")] = False,
) -> dict:
    if (
        directory is None
        and search is None
        and graph_status is None
        and rag_status is None
        and not include_summary
    ):
        # Keep the legacy call shape for external adapters and test doubles
        # that implement only the original three pagination arguments.
        result = service.list_documents(status=status, page=page, page_size=page_size)
    else:
        result = service.list_documents(
            status=status,
            page=page,
            page_size=page_size,
            directory=directory,
            search=search,
            graph_status=graph_status,
            rag_status=rag_status,
            include_summary=include_summary,
        )
    return success_response(result)

@router.get("/documents/{source_id}/content", response_model=APIResponse)
def get_document_content(source_id: str, service: Service) -> dict:
    return success_response(service.get_document_content(source_id))

@router.put("/documents/{source_id}/content", response_model=APIResponse)
def put_document_content(
    source_id: str,
    payload: DocumentContentUpdateRequest,
    service: Service,
) -> dict:
    return success_response(
        service.update_document_content(
            source_id,
            payload.content,
            expected_content_hash=payload.expected_content_hash,
        )
    )

@router.post("/documents/delete-batch", response_model=APIResponse)
def post_delete_documents(
    payload: DocumentBatchDeleteRequest,
    service: Service,
) -> dict:
    return success_response(service.delete_documents(payload.source_ids))

@router.delete("/documents", response_model=APIResponse)
def delete_all_documents(
    service: Service,
    confirm: Annotated[
        Literal["delete-all"],
        Query(description="破坏性操作确认值，必须为 delete-all"),
    ],
    directory: Annotated[
        str | None,
        Query(max_length=2048, description="仅清空指定原文件目录"),
    ] = None,
) -> dict:
    if confirm != "delete-all":  # Literal 已校验；保留显式防线。
        raise ValueError("清空文档必须显式确认 delete-all")
    return success_response(
        service.delete_all_documents(directory=directory)
        if directory is not None
        else service.delete_all_documents()
    )

@router.delete("/documents/{source_id}", response_model=APIResponse)
def delete_document(source_id: str, service: Service) -> dict:
    return success_response(service.delete_document(source_id))

@router.post("/upload", response_model=APIResponse)
def post_upload(payload: UploadRequest, service: Service) -> dict:
    return success_response(service.upload_file(payload.content, payload.filename))

@router.post("/import", response_model=APIResponse)
async def post_import(
    service: Service,
    file: Annotated[UploadFile, File(description="要转换并导入的本地文档")],
    ingest_after_import: Annotated[
        bool,
        Query(alias="ingest", description="转换后是否立即整理图谱与 RAG"),
    ] = True,
    directory: Annotated[str | None, Query(max_length=2048, description="原文件目标相对目录")] = None,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="kemo-graph-import-") as temporary_dir:
        filename, staged = await _stage_uploaded_document(file, Path(temporary_dir))
        result = service.import_document(
            staged,
            ingest_after_import=ingest_after_import,
            **({"directory": directory} if directory is not None else {}),
        )
        _raise_import_failure(result)
    return success_response(result)
