from __future__ import annotations
from fastapi import APIRouter
from api.errors import success_response
from core.documents import library
from core.maintenance import library as maintenance_library
from api.schemas import APIResponse

from api.routes.dependencies import Context
from api.routes.store_context import _store_operation
from api.schemas import StoreLibraryList, StoreLibraryPath, StoreLibraryCreate, StoreLibraryMove

router = APIRouter()

@router.post("/stores/library/list", response_model=APIResponse)
def post_store_library_list(payload: StoreLibraryList, context: Context) -> dict:
    return success_response(_store_operation(payload.store_root, context, lambda service: library.list_library(service, payload.directory, payload.search)))

@router.post("/stores/library/create", response_model=APIResponse)
def post_store_library_create(payload: StoreLibraryCreate, context: Context) -> dict:
    return success_response(_store_operation(payload.store_root, context, lambda service: library.create_entry(service, payload.directory, payload.name, payload.kind)))

@router.post("/stores/library/move", response_model=APIResponse)
def post_store_library_move(payload: StoreLibraryMove, context: Context) -> dict:
    return success_response(_store_operation(payload.store_root, context, lambda service: library.relocate_entry(service, payload.path, payload.destination)))

@router.post("/stores/library/delete", response_model=APIResponse)
def post_store_library_delete(payload: StoreLibraryPath, context: Context) -> dict:
    return success_response(_store_operation(payload.store_root, context, lambda service: library.delete_entry(service, payload.path)))

@router.post("/stores/library/refresh", response_model=APIResponse)
def post_store_library_refresh(payload: StoreLibraryPath, context: Context) -> dict:
    return success_response(_store_operation(payload.store_root, context, lambda service: maintenance_library.refresh_library(service, payload.path)))
