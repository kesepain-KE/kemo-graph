from __future__ import annotations
from fastapi import APIRouter
from api.errors import success_response
from api.schemas import APIResponse, StoreDocumentListRequest, StoreDocumentBatchDeleteRequest, StoreDocumentContentUpdateRequest, StoreDeleteAllDocumentsRequest, StoreSourceRequest

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation

router = APIRouter()

@router.post("/stores/documents/list", response_model=APIResponse)
def post_store_documents_list(
    payload: StoreDocumentListRequest,
    context: Context,
) -> dict:
    status = None if payload.status == "all" else payload.status
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.list_documents(
                status=status,
                page=payload.page,
                page_size=payload.page_size,
                directory=payload.directory,
                search=payload.search,
                graph_status=payload.graph_status,
                rag_status=payload.rag_status,
                include_summary=payload.include_summary,
            ),
        )
    )

@router.post("/stores/documents/content", response_model=APIResponse)
def post_store_document_content(
    payload: StoreSourceRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.get_document_content(payload.source_id),
        )
    )

@router.post("/stores/documents/update", response_model=APIResponse)
def post_store_document_update(
    payload: StoreDocumentContentUpdateRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.update_document_content(
                payload.source_id,
                payload.content,
                expected_content_hash=payload.expected_content_hash,
            ),
        )
    )

@router.post("/stores/documents/delete-batch", response_model=APIResponse)
def post_store_documents_delete_batch(
    payload: StoreDocumentBatchDeleteRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_documents(payload.source_ids),
        )
    )

@router.post("/stores/documents/delete-all", response_model=APIResponse)
def post_store_documents_delete_all(
    payload: StoreDeleteAllDocumentsRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_all_documents(),
        )
    )

@router.post("/stores/documents/delete", response_model=APIResponse)
def post_store_document_delete(
    payload: StoreSourceRequest,
    context: Context,
) -> dict:
    return success_response(
        _store_operation(
            payload.store_root,
            context,
            lambda service: service.delete_document(payload.source_id),
        )
    )
