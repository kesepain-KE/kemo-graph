"""Shared API route registry. Preserve registration order for path matching."""
from fastapi import APIRouter
from api.query_progress import router as query_progress_router
from . import build, system, documents, query, graph, jobs, stores
from . import store_library, store_sources, store_query, store_documents, store_graph, store_maintenance
from . import library, logs
from .system import _is_loopback_host, post_update_apply

router = APIRouter()
router.include_router(query_progress_router)
router.include_router(build.router)
router.include_router(system.status_router)
router.include_router(documents.ingest_router)
router.include_router(query.router)
router.include_router(documents.router)
router.include_router(graph.router)
router.include_router(system.router)
router.include_router(jobs.router)
router.include_router(stores.router)
router.include_router(store_library.router)
router.include_router(store_sources.router)
router.include_router(store_query.router)
router.include_router(store_documents.router)
router.include_router(store_graph.router)
router.include_router(store_maintenance.router)
router.include_router(library.router)
router.include_router(logs.router)

__all__ = ["router"]
