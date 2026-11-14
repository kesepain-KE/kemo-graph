from __future__ import annotations
from typing import Annotated, Literal
from fastapi import APIRouter, Query
from api.errors import success_response
from api.schemas import APIResponse, NodeCreateRequest, NodeSummaryUpdateRequest, RelationCreateRequest

from api.routes.dependencies import Service

router = APIRouter()

@router.post("/nodes", response_model=APIResponse)
def create_node(payload: NodeCreateRequest, service: Service) -> dict:
    return success_response(service.graph.create_node(**payload.model_dump()))

@router.post("/relations", response_model=APIResponse)
def create_relation(payload: RelationCreateRequest, service: Service) -> dict:
    return success_response(service.graph.create_relation(**payload.model_dump()))

@router.delete("/nodes/{node_id}", response_model=APIResponse)
def delete_node(node_id: str, service: Service) -> dict:
    return success_response(service.delete_node(node_id))

@router.get("/nodes/{node_id}", response_model=APIResponse)
def get_node(node_id: str, service: Service) -> dict:
    return success_response(service.get_node(node_id))

@router.patch("/nodes/{node_id}", response_model=APIResponse)
def patch_node_summary(node_id: str, payload: NodeSummaryUpdateRequest, service: Service) -> dict:
    return success_response(service.graph.update_node_summary(
        node_id, payload.summary, payload.expected_summary,
    ))

@router.get("/graph/search", response_model=APIResponse)
def search_graph_nodes(
    service: Service,
    query: Annotated[str, Query(min_length=1, max_length=1000)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return success_response(service.graph.search_nodes(query, limit=limit, offset=offset))

@router.get("/relations/{edge_id}", response_model=APIResponse)
def get_relation(edge_id: str, service: Service) -> dict:
    return success_response(service.get_relation(edge_id))

@router.delete("/relations/{edge_id}", response_model=APIResponse)
def delete_relation(edge_id: str, service: Service) -> dict:
    return success_response(service.delete_relation(edge_id))

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
