from __future__ import annotations
from fastapi import APIRouter
from api.errors import success_response
from api.schemas import APIResponse, StoreIngestRequest, StoreSourceDeleteRequest, StoreSourceStatusRequest, StoreSourceSyncRequest

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/sources/sync", response_model=APIResponse)
def post_store_sources_sync(
    payload: StoreSourceSyncRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.sync_sources(
                [record.model_dump() for record in payload.records],
                ingest_after_sync=payload.ingest_after_sync,
            ),
        )
    )

@router.post("/stores/sources/status", response_model=APIResponse)
def post_store_sources_status(
    payload: StoreSourceStatusRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.list_synced_sources(
                source_type=payload.source_type,
                include_deleted=payload.include_deleted,
                page=payload.page,
                page_size=payload.page_size,
            ),
        )
    )

@router.post("/stores/sources/delete", response_model=APIResponse)
def post_store_sources_delete(
    payload: StoreSourceDeleteRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_synced_sources(payload.source_uris),
        )
    )

@router.post("/stores/ingest", response_model=APIResponse)
def post_store_ingest(payload: StoreIngestRequest, context: Context) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.ingest(paths=payload.paths, mode=payload.mode),
        )
    )
