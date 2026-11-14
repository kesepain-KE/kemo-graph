from __future__ import annotations
from fastapi import APIRouter
from api.errors import success_response
from api.schemas import APIResponse, StoreEdgeRequest, StoreFullGraphRequest, StoreNeighborhoodRequest, StoreNodeRequest, StoreRootRequest, StoreVisualizationPageRequest

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/nodes/delete", response_model=APIResponse)
def post_store_node_delete(payload: StoreNodeRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_node(payload.node_id),
        )
    )

@router.post("/stores/nodes/get", response_model=APIResponse)
def post_store_node_get(payload: StoreNodeRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_node(payload.node_id),
        )
    )

@router.post("/stores/relations/get", response_model=APIResponse)
def post_store_relation_get(payload: StoreEdgeRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_relation(payload.edge_id),
        )
    )

@router.post("/stores/relations/delete", response_model=APIResponse)
def post_store_relation_delete(payload: StoreEdgeRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_relation(payload.edge_id),
        )
    )

@router.post("/stores/graph/full", response_model=APIResponse)
def post_store_graph_full(payload: StoreFullGraphRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_full_graph(
                nodes_page=payload.nodes_page,
                nodes_page_size=payload.nodes_page_size,
            ),
        )
    )

@router.post("/stores/graph/visualization/meta", response_model=APIResponse)
def post_store_graph_visualization_meta(
    payload: StoreRootRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_graph_visualization_meta(),
        )
    )

@router.post("/stores/graph/visualization/nodes", response_model=APIResponse)
def post_store_graph_visualization_nodes(
    payload: StoreVisualizationPageRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.list_graph_visualization_nodes(
                page=payload.page,
                page_size=payload.page_size,
                expected_revision=payload.expected_revision,
            ),
        )
    )

@router.post("/stores/graph/visualization/edges", response_model=APIResponse)
def post_store_graph_visualization_edges(
    payload: StoreVisualizationPageRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.list_graph_visualization_edges(
                page=payload.page,
                page_size=payload.page_size,
                expected_revision=payload.expected_revision,
            ),
        )
    )

@router.post("/stores/graph/neighborhood", response_model=APIResponse)
def post_store_graph_neighborhood(
    payload: StoreNeighborhoodRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_graph_neighborhood(
                payload.node_id,
                depth=payload.depth,
                direction=payload.direction,
                limit=payload.limit,
                edge_limit=payload.edge_limit,
                expected_revision=payload.expected_revision,
            ),
        )
    )
