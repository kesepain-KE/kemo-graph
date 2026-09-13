"""Server-side document filtering, summaries and project-scoped deletion."""

from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app
from core.config import AppConfig
from core.db import connect_sources
from core.knowledge_base import KnowledgeBaseService


def _service(tmp_path: Path) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        settings=AppConfig(),
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "external" / "markdown",
    )


def _seed_projects(service: KnowledgeBaseService) -> dict[str, str]:
    root = service.upload_file("# Root", "root.md")
    first = service.upload_file("# Alpha one", "alpha-one.md")
    second = service.upload_file("# Alpha two", "alpha-two.md")
    service.create_project("Alpha")
    service.relocate_documents(
        [
            {"source_id": first["source_id"], "project": "Alpha"},
            {"source_id": second["source_id"], "project": "Alpha"},
        ]
    )
    connection = connect_sources(service.paths)
    try:
        connection.execute(
            "UPDATE sources SET graph_status = 'ready', rag_status = 'failed' WHERE source_id = ?",
            (first["source_id"],),
        )
        connection.execute(
            "UPDATE sources SET graph_status = 'processing', rag_status = 'ready' WHERE source_id = ?",
            (second["source_id"],),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "root": str(root["source_id"]),
        "first": str(first["source_id"]),
        "second": str(second["source_id"]),
    }


def test_list_documents_filters_and_summary_are_computed_on_server(tmp_path):
    service = _service(tmp_path)
    ids = _seed_projects(service)

    result = service.list_documents(
        status="active",
        page=1,
        page_size=6,
        project="Alpha",
        search="ONE",
        graph_status="ready",
        rag_status="failed",
        include_summary=True,
    )

    assert [item["source_id"] for item in result["documents"]] == [ids["first"]]
    assert result["pagination"] == {
        "page": 1,
        "page_size": 6,
        "total": 1,
        "total_pages": 1,
    }
    # Summary cards describe the complete active library rather than only the
    # current search/status filter.
    assert result["summary"]["total_active"] == 3
    assert result["summary"]["needs_rebuild_documents"] == 2
    assert result["summary"]["graph"] == {
        "pending": 1,
        "processing": 1,
        "ready": 1,
        "failed": 0,
    }
    assert result["summary"]["rag"] == {
        "pending": 1,
        "processing": 0,
        "ready": 1,
        "failed": 1,
    }
    root_only = service.list_documents(status="active", project="")
    assert [item["source_id"] for item in root_only["documents"]] == [ids["root"]]


def test_project_scoped_delete_does_not_require_client_side_id_enumeration(tmp_path):
    service = _service(tmp_path)
    ids = _seed_projects(service)

    deleted = service.delete_all_documents(project="Alpha")

    assert deleted["requested"] == 2
    assert deleted["deleted"] == 2
    remaining = service.list_documents(status="active")
    assert [item["source_id"] for item in remaining["documents"]] == [ids["root"]]


def test_documents_api_exposes_server_filters_and_summary(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(AppConfig().model_dump_json(), encoding="utf-8")
    service = KnowledgeBaseService(
        settings=AppConfig(),
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "external" / "markdown",
        config_path=config,
    )
    ids = _seed_projects(service)
    app = create_app(
        config_path=config,
        data_dir=service.data_dir,
        external_dir=service.external_dir,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/documents",
            params={
                "status": "active",
                "page": 1,
                "page_size": 6,
                "project": "Alpha",
                "search": "alpha-one",
                "graph_status": "ready",
                "rag_status": "failed",
                "include_summary": "true",
            },
        )
        assert response.status_code == 200
        assert [item["source_id"] for item in response.json()["data"]["documents"]] == [
            ids["first"]
        ]
        deleted = client.delete(
            "/api/v1/documents",
            params={"confirm": "delete-all", "project": "Alpha"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted"] == 2
