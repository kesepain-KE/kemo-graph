"""Web 和外部 API 共用的路由定义。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile

from core.knowledge_base import (
    MAX_IMPORT_BYTES,
    SUPPORTED_IMPORT_SUFFIXES,
    DocumentIngestError,
    DocumentTooLargeError,
    KnowledgeBaseService,
    UnsupportedDocumentFormatError,
)

from core.jobs import MaintenanceJobManager

from .deps import get_job_manager, get_service
from .errors import success_response
from .schemas import (
    APIResponse,
    AnswerQueryRequest,
    ConfigSaveRequest,
    GlobalQueryRequest,
    GraphQueryRequest,
    HybridQueryRequest,
    IngestRequest,
    OrganizeGraphRequest,
    RAGQueryRequest,
    UploadRequest,
)


router = APIRouter()
Service = Annotated[KnowledgeBaseService, Depends(get_service)]
Jobs = Annotated[MaintenanceJobManager, Depends(get_job_manager)]


@router.get("/status", response_model=APIResponse)
def get_status(service: Service) -> dict:
    return success_response(service.status())


@router.post("/ingest", response_model=APIResponse)
def post_ingest(payload: IngestRequest, service: Service) -> dict:
    return success_response(service.ingest(paths=payload.paths, mode=payload.mode))


@router.post("/query/graph", response_model=APIResponse)
def post_query_graph(
    payload: GraphQueryRequest,
    service: Service,
    force: Annotated[bool, Query(description="跳过缓存并刷新结果")] = False,
) -> dict:
    return success_response(
        service.query_graph(
            payload.query,
            depth=payload.depth,
            direction=payload.direction,
            confidence=payload.confidence,
            force=force,
        )
    )


@router.post("/query/rag", response_model=APIResponse)
def post_query_rag(
    payload: RAGQueryRequest,
    service: Service,
    force: Annotated[bool, Query(description="跳过缓存并刷新结果")] = False,
) -> dict:
    return success_response(
        service.query_rag(
            payload.query,
            top_k=payload.top_k,
            threshold=payload.threshold,
            force=force,
        )
    )


@router.post("/query/hybrid", response_model=APIResponse)
def post_query_hybrid(
    payload: HybridQueryRequest,
    service: Service,
    force: Annotated[bool, Query(description="跳过缓存并刷新结果")] = False,
) -> dict:
    return success_response(
        service.query_hybrid(
            payload.query,
            graph_depth=payload.graph_depth,
            rag_top_k=payload.rag_top_k,
            graph_confidence=payload.graph_confidence,
            rag_threshold=payload.rag_threshold,
            force=force,
        )
    )


@router.post("/query/answer", response_model=APIResponse)
def post_query_answer(
    payload: AnswerQueryRequest,
    service: Service,
    force: Annotated[bool, Query(description="跳过缓存并刷新结果")] = False,
) -> dict:
    return success_response(
        service.query_answer(
            payload.query,
            graph_depth=payload.graph_depth,
            rag_top_k=payload.rag_top_k,
            graph_confidence=payload.graph_confidence,
            rag_threshold=payload.rag_threshold,
            force=force,
        )
    )


@router.post("/query/global", response_model=APIResponse)
def post_query_global(
    payload: GlobalQueryRequest,
    service: Service,
    force: Annotated[bool, Query(description="跳过缓存并刷新结果")] = False,
) -> dict:
    return success_response(
        service.query_global(
            payload.query,
            top_k=payload.top_k,
            force=force,
        )
    )


# ── 搜索缓存与历史 ──


@router.get("/search/cache", response_model=APIResponse)
def get_search_cache(
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return success_response(service.list_cached_queries(page, page_size))


@router.get("/search/cache/{cache_key}", response_model=APIResponse)
def get_search_cache_detail(cache_key: str, service: Service) -> dict:
    return success_response(service.get_cached_query(cache_key))


@router.delete("/search/cache", response_model=APIResponse)
def delete_search_cache(
    service: Service,
    stale_only: Annotated[bool, Query(description="只删除已过期记录")] = False,
) -> dict:
    return success_response(service.clear_search_cache(stale_only))


# ── 文档管理 ──


@router.get("/documents", response_model=APIResponse)
def get_documents(
    service: Service,
    status: Annotated[str | None, Query(description="active / pending / all")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return success_response(
        service.list_documents(status=status, page=page, page_size=page_size)
    )


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
def get_full_graph(
    service: Service,
    nodes_page: Annotated[int | None, Query(ge=1)] = None,
    nodes_page_size: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict:
    return success_response(
        service.get_full_graph(
            nodes_page=nodes_page,
            nodes_page_size=nodes_page_size,
        )
    )


@router.get("/graph/visualization/meta", response_model=APIResponse)
def get_graph_visualization_meta(service: Service) -> dict:
    return success_response(service.get_graph_visualization_meta())


@router.get("/graph/visualization/nodes", response_model=APIResponse)
def get_graph_visualization_nodes(
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=5000)] = 1000,
    expected_revision: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> dict:
    return success_response(
        service.list_graph_visualization_nodes(
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )
    )


@router.get("/graph/visualization/edges", response_model=APIResponse)
def get_graph_visualization_edges(
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=10000)] = 2000,
    expected_revision: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> dict:
    return success_response(
        service.list_graph_visualization_edges(
            page=page,
            page_size=page_size,
            expected_revision=expected_revision,
        )
    )


@router.get("/graph/neighborhood/{node_id}", response_model=APIResponse)
def get_graph_neighborhood(
    node_id: str,
    service: Service,
    depth: Annotated[int, Query(ge=1, le=10)] = 2,
    direction: Annotated[
        Literal["forward", "backward", "both"],
        Query(),
    ] = "both",
    limit: Annotated[int, Query(ge=1, le=10000)] = 2000,
    edge_limit: Annotated[int, Query(ge=1, le=50000)] = 10000,
    expected_revision: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> dict:
    return success_response(
        service.get_graph_neighborhood(
            node_id,
            depth=depth,
            direction=direction,
            limit=limit,
            edge_limit=edge_limit,
            expected_revision=expected_revision,
        )
    )


# ── 配置管理 ──


@router.get("/config", response_model=APIResponse)
def get_config(service: Service) -> dict:
    return success_response(service.get_config())


@router.put("/config", response_model=APIResponse)
def put_config(payload: ConfigSaveRequest, service: Service) -> dict:
    return success_response(service.save_config(payload.model_dump()))


# ── 运维 ──


@router.post("/jobs/ingest", response_model=APIResponse)
def post_ingest_job(payload: IngestRequest, jobs: Jobs) -> dict:
    return success_response(
        jobs.submit("ingest", paths=payload.paths, mode=payload.mode)
    )


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
    if filename != Path(filename).name or any(
        part in filename for part in ("/", "\\", "..")
    ):
        raise ValueError("上传文件名不能包含路径")
    return filename
