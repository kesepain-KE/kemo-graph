"""对外 API 面契约：新增或删除端点必须同步更新本清单与 api.md。"""

from pathlib import Path
import subprocess
import sys

import yaml

from api import app


EXPECTED_OPENAPI_PATHS = {
    "/api/v1/config",
    "/api/v1/config/models",
    "/api/v1/library",
    "/api/v1/library/entries",
    "/api/v1/library/transfer",
    "/api/v1/library/build",
    "/api/v1/library/refresh",
    "/api/v1/library/preview",
    "/api/v1/library/original",
    "/api/v1/documents/{source_id}/original",
    "/api/v1/documents/{source_id}/source",
    "/api/v1/documents/{source_id}/processed",
    "/api/v1/documents/{source_id}/rebuild",
    "/api/v1/build/retry-failed",
    "/api/v1/stores/library/list",
    "/api/v1/stores/library/create",
    "/api/v1/stores/library/move",
    "/api/v1/stores/library/delete",
    "/api/v1/stores/library/refresh",
    "/api/v1/documents",
    "/api/v1/documents/delete-batch",
    "/api/v1/documents/{source_id}",
    "/api/v1/documents/{source_id}/content",
    "/api/v1/graph",
    "/api/v1/graph/search",
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
    "/api/v1/maintenance/organize-graph",
    "/api/v1/maintenance/rebuild-all",
    "/api/v1/maintenance/rebuild-knowledge-base",
    "/api/v1/maintenance/summarize",
    "/api/v1/nodes",
    "/api/v1/nodes/{node_id}",
    "/api/v1/query/answer",
    "/api/v1/query/global",
    "/api/v1/query/graph",
    "/api/v1/query/hybrid",
    "/api/v1/query/progress/{progress_id}",
    "/api/v1/query/rag",
    "/api/v1/relations",
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
    "/api/v1/stores/maintenance/organize-graph",
    "/api/v1/stores/maintenance/rebuild-all",
    "/api/v1/stores/maintenance/rebuild-knowledge-base",
    "/api/v1/stores/maintenance/summarize",
    "/api/v1/stores/nodes/delete",
    "/api/v1/stores/nodes/get",
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


def test_old_project_organization_is_removed() -> None:
    paths = set(app.openapi()["paths"])
    assert not any("/projects" in path or "move-batch" in path for path in paths)


def test_graph_full_is_compatibility_alias_only() -> None:
    assert "/api/v1/graph/full" not in app.openapi()["paths"]


def test_route_order_and_methods_match_baseline() -> None:
    def route_table(routes, prefix=""):
        for route in routes:
            if hasattr(route, "original_router"):
                yield from route_table(
                    route.original_router.routes,
                    prefix + route.include_context.prefix,
                )
            else:
                yield {
                    "path": prefix + route.path,
                    "methods": sorted(route.methods),
                    "name": route.name,
                }

    baseline = Path(__file__).parent / "fixtures" / "api_routes.yaml"
    assert list(route_table(app.routes)) == yaml.safe_load(baseline.read_text(encoding="utf-8"))


def test_api_import_is_lazy_and_uvicorn_entry_still_resolves() -> None:
    subprocess.run(
        [sys.executable, "-c", """
import api
assert 'app' not in vars(api)
from fastapi import FastAPI
from uvicorn.importer import import_from_string
app = import_from_string('api:app')
assert isinstance(app, FastAPI)
assert api.app is app
"""],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
