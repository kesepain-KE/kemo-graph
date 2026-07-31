"""Web 和外部 API 共用的路由定义。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile

from core.knowledge_base import (
    MAX_IMPORT_BYTES,
    SUPPORTED_IMPORT_SUFFIXES,
    DocumentIngestError,
    DocumentTooLargeError,
    KnowledgeBaseService,
    UnsupportedDocumentFormatError,
)

from .deps import get_service
from .errors import success_response
from .schemas import (
    APIResponse,
    ConfigSaveRequest,
    GraphQueryRequest,
    HybridQueryRequest,
    IngestRequest,
    RAGQueryRequest,
    UploadRequest,
)


router = APIRouter()
Service = Annotated[KnowledgeBaseService, Depends(get_service)]


@router.get("/status", response_model=APIResponse)
def get_status(service: Service) -> dict:
    return success_response(service.status())


@router.post("/ingest", response_model=APIResponse)
def post_ingest(payload: IngestRequest, service: Service) -> dict:
    return success_response(service.ingest(paths=payload.paths, mode=payload.mode))


@router.post("/query/graph", response_model=APIResponse)
def post_query_graph(payload: GraphQueryRequest, service: Service) -> dict:
    return success_response(
        service.query_graph(
            payload.query,
            depth=payload.depth,
            direction=payload.direction,
            confidence=payload.confidence,
        )
    )


@router.post("/query/rag", response_model=APIResponse)
def post_query_rag(payload: RAGQueryRequest, service: Service) -> dict:
    return success_response(
        service.query_rag(
            payload.query,
            top_k=payload.top_k,
            threshold=payload.threshold,
        )
    )


@router.post("/query/hybrid", response_model=APIResponse)
def post_query_hybrid(payload: HybridQueryRequest, service: Service) -> dict:
    return success_response(
        service.query_hybrid(
            payload.query,
            graph_depth=payload.graph_depth,
            rag_top_k=payload.rag_top_k,
            graph_confidence=payload.graph_confidence,
            rag_threshold=payload.rag_threshold,
        )
    )


# ── 文档管理 ──

@router.get("/documents", response_model=APIResponse)
def get_documents(
    service: Service,
    status: Annotated[str | None, Query(description="active / pending / all")] = None,
) -> dict:
    return success_response({"documents": service.list_documents(status=status)})


@router.get("/documents/{source_id}/content", response_model=APIResponse)
def get_document_content(source_id: str, service: Service) -> dict:
    return success_response(service.get_document_content(source_id))


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
) -> dict:
    filename = _validated_upload_filename(file.filename)
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_IMPORT_SUFFIXES:
        raise UnsupportedDocumentFormatError(
            f"不支持的文档格式：{suffix or '<无扩展名>'}"
        )

    content = bytearray()
    try:
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_IMPORT_BYTES:
                raise DocumentTooLargeError(
                    f"文件超过 {MAX_IMPORT_BYTES // (1024 * 1024)} MB 上限：{filename}"
                )
    finally:
        await file.close()

    with tempfile.TemporaryDirectory(prefix="kemo-graph-import-") as temporary_dir:
        staged = Path(temporary_dir) / filename
        staged.write_bytes(content)
        result = service.import_document(
            staged,
            ingest_after_import=ingest_after_import,
            _original_identity=f"upload://{filename}",
        )
    if result.get("ingest_status") == "failed":
        raise DocumentIngestError(
            str(result.get("ingest_error") or "文档已转换，但知识库整理失败")
        )
    return success_response(result)


# ── 节点管理 ──

@router.delete("/nodes/{node_id}", response_model=APIResponse)
def delete_node(node_id: str, service: Service) -> dict:
    return success_response(service.delete_node(node_id))


# ── 图谱全量 ──

@router.get("/graph", response_model=APIResponse)
@router.get("/graph/full", response_model=APIResponse, include_in_schema=False)
def get_full_graph(service: Service) -> dict:
    return success_response(service.get_full_graph())


# ── 配置管理 ──

@router.get("/config", response_model=APIResponse)
def get_config(service: Service) -> dict:
    return success_response(service.get_config())


@router.put("/config", response_model=APIResponse)
def put_config(payload: ConfigSaveRequest, service: Service) -> dict:
    return success_response(service.save_config(payload.model_dump()))


# ── 运维 ──

@router.post("/maintenance/summarize", response_model=APIResponse)
def post_summarize(service: Service) -> dict:
    return success_response(service.generate_group_summaries())


@router.post("/maintenance/cleanup-recycle", response_model=APIResponse)
def post_cleanup_recycle(service: Service) -> dict:
    return success_response(service.cleanup_recycle())


@router.delete("/maintenance/recycle", response_model=APIResponse)
def delete_recycle(service: Service) -> dict:
    """永久清空回收站；调用方应在执行前向用户二次确认。"""

    return success_response(service.cleanup_recycle(force=True))


def _validated_upload_filename(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("上传文件名不能为空")
    filename = value.strip()
    if filename != Path(filename).name or any(part in filename for part in ("/", "\\", "..")):
        raise ValueError("上传文件名不能包含路径")
    return filename
