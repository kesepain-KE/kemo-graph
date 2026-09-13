import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from api.deps import get_service
from core.config import AppConfig
from core.db import connect_sources
from core.ingestor import FileMapStore, Ingestor
from core.knowledge_base import KnowledgeBaseService, DocumentContentConflictError
from core.search_cache import compute_state_hash


@pytest.fixture
def service(tmp_path):
    return KnowledgeBaseService(settings=AppConfig(log_dir=str(tmp_path / "log")), data_dir=tmp_path / "data", external_dir=tmp_path / "markdown")


def imported(service, tmp_path, name="doc.txt", project=None):
    path = tmp_path / name
    path.write_text("# Knowledge\n\nA relates to B.", encoding="utf-8")
    return service.import_document(path, ingest_after_import=False, project=project)


def test_projects_persist_as_directories_and_import_into_project(service, tmp_path):
    assert service.list_projects() == {"projects": [{"name": "", "document_count": 0}]}
    service.create_project("项目甲")
    result = imported(service, tmp_path, project="项目甲")
    assert result["markdown_relative_path"].startswith("项目甲/")
    assert (service.external_dir / result["markdown_relative_path"]).is_file()
    assert service.list_projects()["projects"][-1] == {"name": "项目甲", "document_count": 1}


def test_move_and_rename_preserve_identity_hashes_mapping_and_scan(service, tmp_path):
    document = imported(service, tmp_path)
    source_id = document["source_id"]
    service.create_project("research")
    old = service.list_documents()["documents"][0]
    fingerprint = compute_state_hash(service.paths, service.settings)
    result = service.relocate_documents([{"source_id": source_id, "project": "research", "filename": "new name", "expected_relative_path": old["relative_path"]}])
    assert result["documents"][0]["relative_path"] == "research/new name.md"
    assert not (service.external_dir / old["relative_path"]).exists()
    new = service.list_documents()["documents"][0]
    for key in ("source_id", "content_hash", "graph_hash", "rag_hash", "graph_status", "rag_status", "original_path"):
        assert new[key] == old[key]
    assert compute_state_hash(service.paths, service.settings) != fingerprint
    mapping = FileMapStore(service.external_dir / "file_map.json").get_by_original(tmp_path / "doc.txt")
    assert mapping.markdown_path == "research/new name.md"
    scanner = Ingestor(service.data_dir, service.external_dir, settings=service.settings)
    scan = scanner.scan_sources()
    assert scan.new_source_ids == []
    assert scan.newly_deleted_source_ids == []
    assert service.get_document_content(source_id)["content"]
    # Re-import from the same original path follows the updated mapping.
    again = service.import_document(tmp_path / "doc.txt", ingest_after_import=False)
    assert again["source_id"] == source_id
    assert again["markdown_relative_path"] == "research/new name.md"


def test_conflicts_do_not_overwrite_or_partially_move_batch(service, tmp_path):
    one = imported(service, tmp_path, "one.txt")
    two = imported(service, tmp_path, "two.txt")
    service.create_project("target")
    occupied = service.external_dir / "target" / "occupied.md"
    occupied.write_text("keep me", encoding="utf-8")
    with pytest.raises(DocumentContentConflictError):
        service.relocate_documents([
            {"source_id": one["source_id"], "project": "target"},
            {"source_id": two["source_id"], "project": "target", "filename": "occupied.md"},
        ])
    assert (service.external_dir / one["markdown_relative_path"]).is_file()
    assert occupied.read_text(encoding="utf-8") == "keep me"


def test_file_map_failure_rolls_back_paths_and_database(service, tmp_path):
    doc = imported(service, tmp_path)
    mapping = (service.external_dir / "file_map.json").read_bytes()
    with patch.object(FileMapStore, "_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            service.relocate_documents([{"source_id": doc["source_id"], "filename": "renamed.md"}])
    assert (service.external_dir / doc["markdown_relative_path"]).is_file()
    assert not (service.external_dir / "renamed.md").exists()
    assert service.list_documents()["documents"][0]["relative_path"] == doc["markdown_relative_path"]
    assert (service.external_dir / "file_map.json").read_bytes() == mapping


@pytest.mark.parametrize("name", ["../outside", "nested/project", "C:\\temp", "CON", ".hidden", "a..b", "a:b"])
def test_invalid_project_names_are_rejected(service, name):
    with pytest.raises(ValueError):
        service.create_project(name)


def test_missing_project_stale_path_and_synced_source_rejected(service, tmp_path):
    doc = imported(service, tmp_path)
    with pytest.raises(ValueError):
        service.relocate_documents([{"source_id": doc["source_id"], "project": "missing"}])
    with pytest.raises(DocumentContentConflictError):
        service.relocate_documents([{"source_id": doc["source_id"], "filename": "new", "expected_relative_path": "stale.md"}])
    connection = connect_sources(service.paths)
    connection.execute("UPDATE sources SET source_uri = 'memory://owned' WHERE source_id = ?", (doc["source_id"],))
    connection.commit()
    connection.close()
    with pytest.raises(DocumentContentConflictError, match="上游"):
        service.relocate_documents([{"source_id": doc["source_id"], "filename": "new"}])


def test_api_project_upload_rename_move_and_conflict(service):
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as client:
        assert client.post("/api/v1/projects", json={"name": "alpha"}).status_code == 200
        assert client.post("/api/v1/projects", json={"name": "alpha"}).status_code == 409
        response = client.post("/api/v1/import?ingest=false&project=alpha", files={"file": ("same.txt", b"hello", "text/plain")})
        assert response.status_code == 200, response.text
        doc = response.json()["data"]
        assert doc["markdown_relative_path"].startswith("alpha/")
        renamed = client.patch(f'/api/v1/documents/{doc["source_id"]}/location', json={"filename": "readable"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["relative_path"] == "alpha/readable.md"
        moved = client.post("/api/v1/documents/move-batch", json={"source_ids": [doc["source_id"]], "project": ""})
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["documents"][0]["relative_path"] == "readable.md"
        assert client.get("/api/v1/projects").json()["data"]["projects"][0]["document_count"] == 1
        reimport = client.post("/api/v1/import?ingest=false&project=", files={"file": ("same.txt", b"hello again", "text/plain")})
        assert reimport.status_code == 200, reimport.text
        assert reimport.json()["data"]["source_id"] == doc["source_id"]
        assert reimport.json()["data"]["markdown_relative_path"] == "readable.md"
        other_project = client.post("/api/v1/import?ingest=false&project=alpha", files={"file": ("same.txt", b"separate project", "text/plain")})
        assert other_project.status_code == 200, other_project.text
        assert other_project.json()["data"]["source_id"] != doc["source_id"]
