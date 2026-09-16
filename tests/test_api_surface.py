"""对外 API 面契约：新增或删除端点必须同步更新本清单与 api.md。"""

from api import app


EXPECTED_OPENAPI_PATHS = {
    "/api/v1/config",
    "/api/v1/documents",
    "/api/v1/documents/delete-batch",
    "/api/v1/documents/move-batch",
    "/api/v1/documents/{source_id}",
    "/api/v1/documents/{source_id}/content",
    "/api/v1/documents/{source_id}/location",
    "/api/v1/graph",
    "/api/v1/graph/neighborhood/{node_id}",
    "/api/v1/graph/visualization/edges",
    "/api/v1/graph/visualization/meta",
    "/api/v1/graph/visualization/nodes",
    "/api/v1/import",
    "/api/v1/ingest",
    "/api/v1/jobs",
    "/api/v1/jobs/ingest",
    "/api/v1/jobs/summarize",
    "/api/v1/jobs/{job_id}",
    "/api/v1/maintenance/cleanup-recycle",
    "/api/v1/maintenance/organize-graph",
    "/api/v1/maintenance/rebuild-all",
    "/api/v1/maintenance/rebuild-knowledge-base",
    "/api/v1/maintenance/recycle",
    "/api/v1/maintenance/summarize",
    "/api/v1/nodes/{node_id}",
    "/api/v1/projects",
    "/api/v1/query/answer",
    "/api/v1/query/global",
    "/api/v1/query/graph",
    "/api/v1/query/hybrid",
    "/api/v1/query/progress/{progress_id}",
    "/api/v1/query/rag",
    "/api/v1/relations/{edge_id}",
    "/api/v1/search/cache",
    "/api/v1/search/cache/{cache_key}",
    "/api/v1/status",
    "/api/v1/stores/cache/clear",
    "/api/v1/stores/cache/list",
    "/api/v1/stores/cache/show",
    "/api/v1/stores/documents/content",
    "/api/v1/stores/documents/delete",
    "/api/v1/stores/documents/delete-all",
    "/api/v1/stores/documents/delete-batch",
    "/api/v1/stores/documents/list",
    "/api/v1/stores/documents/location",
    "/api/v1/stores/documents/move-batch",
    "/api/v1/stores/documents/update",
    "/api/v1/stores/graph/full",
    "/api/v1/stores/graph/neighborhood",
    "/api/v1/stores/graph/visualization/edges",
    "/api/v1/stores/graph/visualization/meta",
    "/api/v1/stores/graph/visualization/nodes",
    "/api/v1/stores/import",
    "/api/v1/stores/import-path",
    "/api/v1/stores/info",
    "/api/v1/stores/ingest",
    "/api/v1/stores/initialize",
    "/api/v1/stores/jobs/get",
    "/api/v1/stores/jobs/list",
    "/api/v1/stores/maintenance/cleanup-recycle",
    "/api/v1/stores/maintenance/organize-graph",
    "/api/v1/stores/maintenance/rebuild-all",
    "/api/v1/stores/maintenance/rebuild-knowledge-base",
    "/api/v1/stores/maintenance/summarize",
    "/api/v1/stores/nodes/delete",
    "/api/v1/stores/nodes/get",
    "/api/v1/stores/projects/create",
    "/api/v1/stores/projects/list",
    "/api/v1/stores/query/answer",
    "/api/v1/stores/query/federated",
    "/api/v1/stores/query/global",
    "/api/v1/stores/query/graph",
    "/api/v1/stores/query/hybrid",
    "/api/v1/stores/query/rag",
    "/api/v1/stores/relations/delete",
    "/api/v1/stores/relations/get",
    "/api/v1/stores/sources/delete",
    "/api/v1/stores/sources/status",
    "/api/v1/stores/sources/sync",
    "/api/v1/stores/status",
    "/api/v1/stores/upload",
    "/api/v1/system/logs",
    "/api/v1/update/apply",
    "/api/v1/update/check",
    "/api/v1/update/status",
    "/api/v1/upload",
}


def test_openapi_path_count_and_key_paths() -> None:
    paths = set(app.openapi()["paths"])
    assert paths == EXPECTED_OPENAPI_PATHS


def test_portable_document_organization_is_exposed() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/v1/stores/projects/list" in paths
    assert "/api/v1/stores/projects/create" in paths
    assert "/api/v1/stores/documents/location" in paths
    assert "/api/v1/stores/documents/move-batch" in paths


def test_graph_full_is_compatibility_alias_only() -> None:
    assert "/api/v1/graph/full" not in app.openapi()["paths"]
