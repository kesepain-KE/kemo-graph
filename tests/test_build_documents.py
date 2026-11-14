"""Build-page contracts: original isolation, rebuild scope and real progress."""

import yaml
import time
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import build_documents, library
from core.settings.schema import AppConfig
from core.storage.connections import connect_sources
from core.ingest.pipeline import Ingestor
from core.jobs import MaintenanceJobManager
from core.service.facade import KnowledgeBaseService
from core.documents.errors import DocumentContentConflictError
from core.storage.locks import get_knowledge_base_lock
from provider.embedding import EmbeddingResult


@pytest.fixture
def service(tmp_path):
    settings = AppConfig(
        log_dir=str(tmp_path / "log"), graph_build_mode="structured",
        models={"embedding_dimensions": 3},
    )
    return KnowledgeBaseService(settings=settings, data_dir=tmp_path / "data", external_dir=tmp_path / "external" / "markdown")


def imported(service, tmp_path, name="sample.txt"):
    path = tmp_path / name
    path.write_text("Original document", encoding="utf-8")
    return service.import_document(path, ingest_after_import=False)


def mark_ready(service, source_id):
    connection = connect_sources(service.paths)
    try:
        with connection:
            connection.execute(
                """UPDATE sources SET graph_status = 'ready', rag_status = 'ready',
                   graph_hash = content_hash, rag_hash = content_hash WHERE source_id = ?""",
                (source_id,),
            )
    finally:
        connection.close()


def empty_graph(*args, **kwargs):
    return {"schema_version": "1.0", "entities": [], "relations": []}


def embedding(texts, **kwargs):
    return EmbeddingResult([[0.5, 0.25, 0.75] for _ in texts], "test-space")


def test_processed_save_preserves_original_mapping_and_previous_index_hashes(service, tmp_path):
    document = imported(service, tmp_path)
    source_id = document["source_id"]
    mark_ready(service, source_id)
    original = library.original_for_source(service, source_id).read_bytes()
    mapping = (service.external_dir / "file_map.json").read_bytes()
    previous = build_documents.read_processed(service, source_id)
    saved = build_documents.update_processed(service, source_id, "# Edited processing copy", previous["content_hash"])
    record = service.list_documents()["documents"][0]
    assert saved["content"] == "# Edited processing copy"
    assert saved["graph_status"] == saved["rag_status"] == "pending"
    assert record["graph_hash"] == record["rag_hash"] == previous["content_hash"]
    assert record["content_hash"] == saved["content_hash"]
    assert library.original_for_source(service, source_id).read_bytes() == original
    assert (service.external_dir / "file_map.json").read_bytes() == mapping
    assert library.refresh_library(service)["updated"] == []
    assert build_documents.read_processed(service, source_id)["content"] == saved["content"]
    with pytest.raises(DocumentContentConflictError):
        build_documents.update_processed(service, source_id, "stale edit", previous["content_hash"])
    assert build_documents.read_processed(service, source_id)["content"] == saved["content"]


def test_failed_atomic_save_leaves_processed_file_and_statuses_unchanged(service, tmp_path):
    document = imported(service, tmp_path)
    source_id = document["source_id"]
    mark_ready(service, source_id)
    previous = build_documents.read_processed(service, source_id)
    with patch("core.build_documents._write_bytes_atomic", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            build_documents.update_processed(service, source_id, "changed", previous["content_hash"])
    after = build_documents.read_processed(service, source_id)
    assert after["content"] == previous["content"]
    assert after["graph_status"] == after["rag_status"] == "ready"

def test_processed_edit_is_rejected_while_another_thread_builds_the_library(service, tmp_path):
    document = imported(service, tmp_path)
    previous = build_documents.read_processed(service, document["source_id"])
    entered, release = threading.Event(), threading.Event()

    def writer():
        with get_knowledge_base_lock(service.data_dir):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(DocumentContentConflictError, match="正在构建"):
            build_documents.update_processed(service, document["source_id"], "unsafe edit", previous["content_hash"])
    finally:
        release.set()
        thread.join()
    assert build_documents.read_processed(service, document["source_id"])["content"] == previous["content"]


def test_build_failure_records_completed_steps_and_precise_pipeline_errors(service, tmp_path):
    document = imported(service, tmp_path)
    with patch("core.ingest.graph_build.chat_structured", side_effect=RuntimeError("graph gateway timeout")), patch("core.ingest.pipeline.embed", side_effect=RuntimeError("embedding unavailable")):
        result = service.ingest()
    assert result["failed"] == 1
    record = service.list_documents()["documents"][0]
    builds = record["builds"]
    assert [step["status"] for step in builds["graph"]["steps"]] == ["completed", "failed"]
    assert builds["graph"]["steps"][-1]["step"] == "extract"
    assert builds["graph"]["steps"][-1]["error"] == "graph gateway timeout"
    assert [step["step"] for step in builds["rag"]["steps"]] == ["read", "chunk", "embed"]
    assert [step["status"] for step in builds["rag"]["steps"]] == ["completed", "completed", "failed"]
    assert builds["rag"]["steps"][-1]["error"] == "embedding unavailable"
    assert all(build["content_hash"] == record["content_hash"] for build in builds.values())
    assert build_documents.read_processed(service, document["source_id"])["content"]


def test_failed_only_retries_only_failed_pipelines_and_leaves_other_pending_work(service, tmp_path):
    first = imported(service, tmp_path, "failed.txt")
    second = imported(service, tmp_path, "pending.txt")
    mark_ready(service, first["source_id"])
    connection = connect_sources(service.paths)
    try:
        with connection:
            connection.execute("UPDATE sources SET rag_status = 'failed' WHERE source_id = ?", (first["source_id"],))
    finally:
        connection.close()
    with patch("core.ingest.graph_build.chat_structured") as graph, patch("core.ingest.pipeline.embed", side_effect=embedding):
        result = service.ingest(failed_only=True)
    assert result["rag_updated"] == 1
    assert result["graph_updated"] == 0
    graph.assert_not_called()
    records = {row["source_id"]: row for row in service.list_documents()["documents"]}
    assert records[first["source_id"]]["rag_status"] == "ready"
    assert records[first["source_id"]]["builds"].keys() == {"rag"}
    assert records[second["source_id"]]["graph_status"] == records[second["source_id"]]["rag_status"] == "pending"
    assert records[second["source_id"]]["builds"] == {}


def test_api_saves_only_and_force_rebuilds_a_ready_source_with_real_steps(service, tmp_path):
    document = imported(service, tmp_path)
    source_id = document["source_id"]
    mark_ready(service, source_id)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = create_app(config_path=config, data_dir=service.data_dir, external_dir=service.external_dir)
    with TestClient(app) as client:
        url = f"/api/v1/documents/{source_id}/processed"
        previous = client.get(url).json()["data"]
        assert client.put(url, json={"content": "missing version"}).status_code == 422
        saved = client.put(url, json={"content": "# Processing revision", "expected_content_hash": previous["content_hash"]})
        assert saved.status_code == 200, saved.text
        assert client.get("/api/v1/jobs").json()["data"]["jobs"] == []
        assert client.put(url, json={"content": "outdated", "expected_content_hash": previous["content_hash"]}).status_code == 409
        # Prove force-rebuild does not silently skip ready records.
        mark_ready(service, source_id)
        with patch("core.ingest.graph_build.chat_structured", side_effect=empty_graph) as graph, patch("core.ingest.pipeline.embed", side_effect=embedding):
            response = client.post(f"/api/v1/documents/{source_id}/rebuild")
            assert response.status_code == 200, response.text
            job_id = response.json()["data"]["job_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert job["status"] == "completed", job
        assert "# Processing revision" in graph.call_args.args[1]
        assert job["result"]["graph_updated"] == job["result"]["rag_updated"] == 1
        record = client.get("/api/v1/documents").json()["data"]["documents"][0]
        assert [step["step"] for step in record["builds"]["graph"]["steps"]] == ["read", "extract", "persist", "link"]
        assert [step["step"] for step in record["builds"]["rag"]["steps"]] == ["read", "chunk", "embed", "persist", "index", "entities"]
        assert all(step["status"] == "completed" for build in record["builds"].values() for step in build["steps"])
        assert client.get(f"/api/v1/documents/{source_id}/original").content == b"Original document"
        assert client.get("/api/v1/documents/missing/processed").status_code == 404


def test_service_restart_marks_unfinished_step_failed_instead_of_spinning_forever(service, tmp_path):
    document = imported(service, tmp_path)
    from core.ingest.progress import start_build, advance_build
    start_build(service.paths, document["source_id"], "graph", "hash")
    advance_build(service.paths, document["source_id"], "graph", "extract", "抽取实体与关系")
    ingestor = Ingestor(service.data_dir, service.external_dir, settings=service.settings)
    ingestor._set_source_status(document["source_id"], "graph", "processing")
    manager = MaintenanceJobManager(lambda: service, data_dir=service.data_dir, settings=service.settings)
    manager.start()
    manager.stop()
    record = service.list_documents()["documents"][0]
    assert record["graph_status"] == "failed"
    assert record["builds"]["graph"]["steps"][0]["status"] == "completed"
    assert record["builds"]["graph"]["steps"][-1]["status"] == "failed"
    assert "退出" in record["builds"]["graph"]["steps"][-1]["error"]
