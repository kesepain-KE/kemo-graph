from __future__ import annotations
from fastapi import APIRouter
from core.settings import load_config
from core.service.federation import federated_query
from api.errors import success_response
from api.schemas import APIResponse, StoreAnswerQueryRequest, StoreFederatedQueryRequest, StoreGlobalQueryRequest, StoreGraphQueryRequest, StoreHybridQueryRequest, StoreRAGQueryRequest

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/query/graph", response_model=APIResponse)
def post_store_query_graph(
    payload: StoreGraphQueryRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.query_graph(
                payload.query,
                depth=payload.depth,
                direction=payload.direction,
                confidence=payload.confidence,
                force=payload.force,
            ),
        )
    )

@router.post("/stores/query/rag", response_model=APIResponse)
def post_store_query_rag(payload: StoreRAGQueryRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.query_rag(
                payload.query,
                top_k=payload.top_k,
                threshold=payload.threshold,
                force=payload.force,
            ),
        )
    )

@router.post("/stores/query/hybrid", response_model=APIResponse)
def post_store_query_hybrid(
    payload: StoreHybridQueryRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.query_hybrid(
                payload.query,
                graph_depth=payload.graph_depth,
                rag_top_k=payload.rag_top_k,
                graph_confidence=payload.graph_confidence,
                rag_threshold=payload.rag_threshold,
                direction=payload.direction,
                force=payload.force,
            ),
        )
    )

@router.post("/stores/query/answer", response_model=APIResponse)
def post_store_query_answer(
    payload: StoreAnswerQueryRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.query_answer(
                payload.query,
                graph_depth=payload.graph_depth,
                rag_top_k=payload.rag_top_k,
                graph_confidence=payload.graph_confidence,
                rag_threshold=payload.rag_threshold,
                direction=payload.direction,
                force=payload.force,
            ),
        )
    )

@router.post("/stores/query/global", response_model=APIResponse)
def post_store_query_global(
    payload: StoreGlobalQueryRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.query_global(
                payload.query,
                top_k=payload.top_k,
                force=payload.force,
            ),
        )
    )

@router.post("/stores/query/federated", response_model=APIResponse)
def post_store_query_federated(
    payload: StoreFederatedQueryRequest,
    context: Context,
) -> dict:
    return success_response(
        federated_query(
            payload.store_roots,
            payload.query,
            mode=payload.mode,
            settings=load_config(context.config_path),
            config_path=context.config_path,
            force=payload.force,
            top_k=payload.top_k,
            graph_depth=payload.graph_depth,
        )
    )
