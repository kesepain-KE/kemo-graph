from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Query
from api.errors import success_response
from api.schemas import APIResponse, AnswerQueryRequest, GlobalQueryRequest, GraphQueryRequest, HybridQueryRequest, RAGQueryRequest

from api.routes.dependencies import Service

router = APIRouter()

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
