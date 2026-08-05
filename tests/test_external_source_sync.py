from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import start
from api import create_app
from core.config import AppConfig
from core.db import initialize_databases
from core.portable_store import (
    create_store_service,
    initialize_store,
    resolve_store_paths,
)


def _settings(root: Path) -> AppConfig:
    return AppConfig(
        log_dir=str(root / "log"),
        portable_stores={"enabled": True, "allowed_roots": []},
    )


def _record(
    *,
    content: str = "# Stable memory\n\nRemember this.",
    revision: str = "1",
    updated_at: datetime | None = None,
    deleted: bool = False,
) -> dict:
    return {
        "source_uri": "kemo-agent://users/alice/memory/stable.md",
        "source_type": "memory.fragment",
        "display_name": "stable.md",
        "content": content,
        "revision": revision,
        "updated_at": (updated_at or datetime.now(timezone.utc)).isoformat(),
        "metadata": {"tier": "permanent", "weight": 2},
        "deleted": deleted,
        "ingest_mode": "both",
    }


def test_external_source_columns_migrate_old_sources_db(tmp_path: Path) -> None:
    data_dir = tmp_path / "legacy"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "sources.db") as connection:
        connection.execute(
            """
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                original_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                path_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                graph_hash TEXT,
                rag_hash TEXT,
                graph_status TEXT DEFAULT 'pending',
                rag_status TEXT DEFAULT 'pending',
                exists_status TEXT DEFAULT 'active',
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO sources VALUES "
            "('legacy', 'x', 'x.md', 'path', 'content', NULL, NULL, "
            "'pending', 'pending', 'active', NULL, NULL)"
        )

    paths = initialize_databases(data_dir, _settings(tmp_path))
    with sqlite3.connect(paths.sources_db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sources)")}
        source_id = connection.execute(
            "SELECT source_id FROM sources WHERE source_id = 'legacy'"
        ).fetchone()[0]
    assert {
        "source_uri",
        "source_type",
        "source_revision",
        "source_updated_at",
        "source_metadata_json",
        "external_content_hash",
        "last_synced_at",
    } <= columns
    assert source_id == "legacy"


def test_external_source_sync_is_idempotent_and_preserves_source_identity(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    root = tmp_path / "users" / "alice" / "improve"
    initialize_store(root, scope="memory.user", owner_id="alice", settings=settings)
    service = create_store_service(root, settings=settings)
    first_time = datetime.now(timezone.utc)

    created = service.sync_sources([_record(updated_at=first_time)])
    first = service.list_synced_sources()["sources"][0]
    source_id = first["source_id"]
    markdown = service.external_dir / first["relative_path"]
    assert created["created"] == 1
    assert markdown.is_file()
    assert first["metadata"]["tier"] == "permanent"
    assert first["graph_status"] == "pending"
    assert first["rag_status"] == "pending"

    repeated = service.sync_sources([_record(updated_at=first_time)])
    assert repeated["unchanged"] == 1

    metadata_only = _record(revision="2", updated_at=first_time + timedelta(seconds=1))
    metadata_only["metadata"]["tier"] = "half_year"
    metadata_result = service.sync_sources([metadata_only])
    after_metadata = service.list_synced_sources()["sources"][0]
    assert metadata_result["metadata_updated"] == 1
    assert after_metadata["source_id"] == source_id
    assert after_metadata["metadata"]["tier"] == "half_year"

    stale = service.sync_sources(
        [_record(revision="0", updated_at=first_time - timedelta(seconds=1))]
    )
    assert stale["stale"] == 1

    conflict = service.sync_sources(
        [
            _record(
                content="Different content",
                revision="2",
                updated_at=first_time + timedelta(seconds=2),
            )
        ]
    )
    assert conflict["conflicts"] == 1

    updated = service.sync_sources(
        [
            _record(
                content="Updated body",
                revision="3",
                updated_at=first_time + timedelta(seconds=3),
            )
        ]
    )
    assert updated["updated"] == 1
    assert service.list_synced_sources()["sources"][0]["source_id"] == source_id

    deleted = service.sync_sources(
        [
            _record(
                revision="4",
                updated_at=first_time + timedelta(seconds=4),
                deleted=True,
            )
        ]
    )
    assert deleted["deleted"] == 1
    assert not markdown.exists()
    recycle = resolve_store_paths(root, settings=settings).recycle_dir
    assert not list(recycle.rglob("*.md"))

    restored = service.sync_sources(
        [
            _record(
                content="Restored body",
                revision="5",
                updated_at=first_time + timedelta(seconds=5),
            )
        ]
    )
    assert restored["updated"] == 1
    restored_source = service.list_synced_sources()["sources"][0]
    assert restored_source["source_id"] == source_id
    assert (service.external_dir / restored_source["relative_path"]).is_file()


def test_source_sync_store_api_and_cli(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    store_root = tmp_path / "users" / "alice" / "improve"
    initialize_store(store_root, scope="memory.user", owner_id="alice", settings=settings)
    record = _record()
    record["source_uri"] = "kemo-agent://users/alice/memory/fragments/1"
    record["source_type"] = "memory.fragment"

    app = create_app(
        config_path=config_path,
        data_dir=tmp_path / "default-data",
        external_dir=tmp_path / "default-content",
    )
    with TestClient(app) as client:
        synced = client.post(
            "/api/v1/stores/sources/sync",
            json={
                "store_root": str(store_root),
                "records": [record],
                "ingest_after_sync": False,
            },
        )
        assert synced.status_code == 200, synced.text
        assert synced.json()["data"]["result"]["created"] == 1
        status = client.post(
            "/api/v1/stores/sources/status",
            json={"store_root": str(store_root), "source_type": "memory.fragment"},
        )
        assert status.status_code == 200
        assert status.json()["data"]["result"]["pagination"]["total"] == 1
        deleted = client.post(
            "/api/v1/stores/sources/delete",
            json={"store_root": str(store_root), "source_uris": [record["source_uri"]]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["data"]["result"]["deleted"] == 1

    record["revision"] = "2"
    record["updated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    payload_path = tmp_path / "sources.json"
    payload_path.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    output = io.StringIO()
    with redirect_stdout(output):
        code = start.main(
            [
                "--config",
                str(config_path),
                "--store-root",
                str(store_root),
                "source-sync",
                str(payload_path),
            ]
        )
    assert code == 0
    assert json.loads(output.getvalue())["data"]["updated"] == 1
