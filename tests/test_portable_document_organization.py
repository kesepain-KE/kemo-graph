from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app
from core.config import AppConfig


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    settings = AppConfig(
        log_dir=str(tmp_path / "log"),
        portable_stores={"enabled": True, "allowed_roots": []},
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    app = create_app(
        config_path=config_path,
        data_dir=tmp_path / "default-data",
        external_dir=tmp_path / "default-markdown",
    )
    return TestClient(app), tmp_path / "portable-store"


def _initialize_and_import(client: TestClient, store_root: Path, tmp_path: Path) -> dict:
    initialized = client.post(
        "/api/v1/stores/initialize",
        json={"store_root": str(store_root), "scope": "knowledge.user"},
    )
    assert initialized.status_code == 200, initialized.text
    source = tmp_path / "source.txt"
    source.write_text("portable project document", encoding="utf-8")
    imported = client.post(
        "/api/v1/stores/import-path",
        json={
            "store_root": str(store_root),
            "path": str(source),
            "ingest_after_import": False,
        },
    )
    assert imported.status_code == 200, imported.text
    return imported.json()["data"]["result"]


def test_portable_projects_and_document_location_round_trip(tmp_path: Path) -> None:
    client, store_root = _client(tmp_path)
    with client:
        document = _initialize_and_import(client, store_root, tmp_path)
        created = client.post(
            "/api/v1/stores/projects/create",
            json={"store_root": str(store_root), "name": "research"},
        )
        assert created.status_code == 200, created.text
        projects = client.post(
            "/api/v1/stores/projects/list",
            json={"store_root": str(store_root)},
        )
        assert projects.status_code == 200, projects.text
        assert {item["name"] for item in projects.json()["data"]["result"]["projects"]} == {"", "research"}

        moved = client.post(
            "/api/v1/stores/documents/location",
            json={
                "store_root": str(store_root),
                "source_id": document["source_id"],
                "filename": "renamed",
                "project": "research",
                "expected_relative_path": document["markdown_relative_path"],
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["result"]["relative_path"] == "research/renamed.md"

        returned = client.post(
            "/api/v1/stores/documents/move-batch",
            json={
                "store_root": str(store_root),
                "source_ids": [document["source_id"], document["source_id"]],
                "project": "",
            },
        )
        assert returned.status_code == 200, returned.text
        assert returned.json()["data"]["result"]["documents"][0]["relative_path"] == "renamed.md"


def test_portable_document_organization_rejects_invalid_requests(tmp_path: Path) -> None:
    client, store_root = _client(tmp_path)
    with client:
        document = _initialize_and_import(client, store_root, tmp_path)

        missing_root = client.post("/api/v1/stores/projects/list", json={})
        assert missing_root.status_code == 422

        long_project = "x" * 161
        assert client.post(
            "/api/v1/stores/projects/create",
            json={"store_root": str(store_root), "name": long_project},
        ).status_code == 422
        assert client.post(
            "/api/v1/stores/documents/move-batch",
            json={
                "store_root": str(store_root),
                "source_ids": [document["source_id"]],
                "project": long_project,
            },
        ).status_code == 422

        no_change = client.post(
            "/api/v1/stores/documents/location",
            json={"store_root": str(store_root), "source_id": document["source_id"]},
        )
        assert no_change.status_code == 422

        missing_project = client.post(
            "/api/v1/stores/documents/location",
            json={
                "store_root": str(store_root),
                "source_id": document["source_id"],
                "project": "missing",
            },
        )
        assert missing_project.status_code == 422

        missing_source = client.post(
            "/api/v1/stores/documents/location",
            json={
                "store_root": str(store_root),
                "source_id": "missing-source",
                "filename": "renamed",
            },
        )
        assert missing_source.status_code == 404

        uninitialized = client.post(
            "/api/v1/stores/projects/list",
            json={"store_root": str(tmp_path / "uninitialized")},
        )
        assert uninitialized.status_code == 404

        oversized = client.post(
            "/api/v1/stores/documents/move-batch",
            json={
                "store_root": str(store_root),
                "source_ids": [f"source-{index}" for index in range(1001)],
                "project": "",
            },
        )
        assert oversized.status_code == 422
