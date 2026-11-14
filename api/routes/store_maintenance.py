from __future__ import annotations
from fastapi import APIRouter
from api.errors import success_response
from api.schemas import APIResponse, StoreCacheClearRequest, StoreCacheKeyRequest, StoreCacheListRequest, StoreJobRequest, StoreJobsListRequest, StoreOrganizeGraphRequest, StoreRootRequest

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/cache/list", response_model=APIResponse)
def post_store_cache_list(payload: StoreCacheListRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.list_cached_queries(
                page=payload.page,
                page_size=payload.page_size,
            ),
        )
    )

@router.post("/stores/cache/show", response_model=APIResponse)
def post_store_cache_show(payload: StoreCacheKeyRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_cached_query(payload.cache_key),
        )
    )

@router.post("/stores/cache/clear", response_model=APIResponse)
def post_store_cache_clear(
    payload: StoreCacheClearRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.clear_search_cache(payload.stale_only),
        )
    )

@router.post("/stores/maintenance/organize-graph", response_model=APIResponse)
def post_store_organize_graph(
    payload: StoreOrganizeGraphRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.organize_graph(
                use_llm=payload.use_llm,
                summarize=payload.summarize,
            ),
        )
    )

@router.post(
    "/stores/maintenance/rebuild-knowledge-base",
    response_model=APIResponse,
)
def post_store_rebuild_knowledge_base(
    payload: StoreRootRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.rebuild_knowledge_base(),
        )
    )

@router.post("/stores/maintenance/rebuild-all", response_model=APIResponse)
def post_store_rebuild_all(payload: StoreRootRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.rebuild_all(),
        )
    )

@router.post("/stores/maintenance/summarize", response_model=APIResponse)
def post_store_summarize(payload: StoreRootRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.generate_group_summaries(),
        )
    )

@router.post("/stores/jobs/list", response_model=APIResponse)
def post_store_jobs_list(payload: StoreJobsListRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: {"jobs": service.list_jobs(limit=payload.limit)},
        )
    )

@router.post("/stores/jobs/get", response_model=APIResponse)
def post_store_job_get(payload: StoreJobRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_job(payload.job_id),
        )
    )
