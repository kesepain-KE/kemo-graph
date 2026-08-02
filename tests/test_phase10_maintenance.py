"""Phase 10 维护任务、API/CLI 入口与影子重建验收。"""

from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import start
from api import create_app
from api.deps import get_job_manager
from core.config import AppConfig
from core.db import connect_graph, connect_sources
from core.ingestor import Ingestor
from core.jobs import MaintenanceJobManager
from core.rebuilder import KnowledgeBaseRebuilder, RebuildError
from provider.embedding import EmbeddingResult


def _settings(root: Path) -> AppConfig:
    return AppConfig(
        graph_build_mode="structured",
        chunking_mode="fixed",
        chunk_size=128,
        chunk_overlap=16,
        log_dir=str(root / "log"),
        kemo={"api_key": "test-key"},
        models={
            "llm": "test-llm",
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
            "rerank": "test-rerank",
        },
    )


def _embedding(texts: list[str], **_: object) -> EmbeddingResult:
    return EmbeddingResult(
        [[0.5, 0.25, 0.75] for _ in texts],
        "test-vector-space",
    )


def _draft(source_keyword: str = "New") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "entities": [
            {
                "local_id": "a",
                "keyword": source_keyword,
                "summary": f"{source_keyword.casefold()} summary",
                "aliases": [],
                "tags": [],
                "evidence_weight": 0.9,
                "evidence": source_keyword,
            },
            {
                "local_id": "b",
                "keyword": "Target",
                "summary": "target summary",
                "aliases": [],
                "tags": [],
                "evidence_weight": 0.8,
                "evidence": "Target",
            },
        ],
        "relations": [
            {
                "source": "a",
                "relation": "关联",
                "target": "b",
                "evidence_weight": 0.85,
                "evidence": f"{source_keyword} relates Target",
            }
        ],
    }


class _JobService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def ingest(self, **kwargs):
        if self.fail:
            raise RuntimeError("simulated failure")
        return {"processed": 1, "failed": 0, **kwargs}

    def organize_graph(self, **kwargs):
        return {"merged_nodes": 1, **kwargs}

    def rebuild_knowledge_base(self, *, progress):
        progress(0.5, "half")
        return {"rebuilt": 1}

    def rebuild_all(self, *, progress):
        progress(0.8, "validated")
        return {"swapped": True}

    def generate_group_summaries(self, *, force=False):
        return {"generated": 1, "force": force}

    def cleanup_recycle(self, *, force=False):
        return {"deleted": 1, "forced": force}


class JobManagerTests(unittest.TestCase):
    def test_background_job_state_and_events_survive_request_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            manager = MaintenanceJobManager(
                lambda: _JobService(),
                data_dir=root / "data",
                settings=settings,
            )
            try:
                job = manager.submit("rebuild_knowledge_base")
                finished = self._wait(manager, job["job_id"])
                self.assertEqual(finished["status"], "completed")
                self.assertEqual(finished["progress"], 1.0)
                self.assertEqual(finished["result"], {"rebuilt": 1})
                self.assertTrue(
                    any(event["message"] == "half" for event in finished["events"])
                )

                failing = MaintenanceJobManager(
                    lambda: _JobService(fail=True),
                    data_dir=root / "data",
                    settings=settings,
                )
                failed_job = failing.submit("ingest")
                failed = self._wait(failing, failed_job["job_id"])
                self.assertEqual(failed["status"], "failed")
                self.assertIn("simulated failure", failed["error"])
                failing.stop()

                listed = manager.list()
                self.assertEqual(
                    {item["job_id"] for item in listed},
                    {job["job_id"], failed_job["job_id"]},
                )
            finally:
                manager.stop()

    @staticmethod
    def _wait(manager: MaintenanceJobManager, job_id: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(0.01)
        raise AssertionError("background job did not finish")


class RebuildTests(unittest.TestCase):
    def test_full_rebuild_preserves_source_id_and_switches_only_after_validation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            data = root / "data"
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("Old relates Target", encoding="utf-8")
            settings = _settings(root)
            initial = Ingestor(
                data,
                external,
                settings=settings,
                embedder=_embedding,
            )
            with patch("core.ingestor.chat_structured", return_value=_draft("Old")):
                self.assertEqual(initial.ingest(mode="both")["failed"], 0)
            sources = connect_sources(initial.paths)
            try:
                source_id = sources.execute("SELECT source_id FROM sources").fetchone()[
                    0
                ]
            finally:
                sources.close()

            document.write_text("New relates Target", encoding="utf-8")
            with (
                patch("core.ingestor.chat_structured", return_value=_draft()),
                patch("core.ingestor.embed", side_effect=_embedding),
            ):
                result = KnowledgeBaseRebuilder(
                    data,
                    external,
                    settings=settings,
                ).rebuild_all()
            self.assertTrue(Path(result["backup_path"]).is_dir())
            self.assertTrue(result["validation"]["faiss_healthy"])
            sources = connect_sources(initial.paths)
            try:
                rebuilt = sources.execute(
                    "SELECT source_id, graph_status, rag_status FROM sources"
                ).fetchone()
            finally:
                sources.close()
            self.assertEqual(rebuilt["source_id"], source_id)
            self.assertEqual(
                (rebuilt["graph_status"], rebuilt["rag_status"]), ("ready", "ready")
            )
            graph = connect_graph(initial.paths)
            try:
                keywords = {
                    row[0] for row in graph.execute("SELECT keyword FROM nodes")
                }
            finally:
                graph.close()
            self.assertEqual(keywords, {"New", "Target"})

    def test_failed_shadow_rebuild_keeps_formal_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            data = root / "data"
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "doc.md").write_text("Old relates Target", encoding="utf-8")
            settings = _settings(root)
            initial = Ingestor(
                data,
                external,
                settings=settings,
                embedder=_embedding,
            )
            with patch("core.ingestor.chat_structured", return_value=_draft("Old")):
                initial.ingest(mode="both")
            with patch(
                "core.ingestor.chat_structured", side_effect=RuntimeError("gateway")
            ):
                with self.assertRaises(RebuildError):
                    KnowledgeBaseRebuilder(
                        data, external, settings=settings
                    ).rebuild_all()
            graph = connect_graph(initial.paths)
            try:
                keywords = {
                    row[0] for row in graph.execute("SELECT keyword FROM nodes")
                }
            finally:
                graph.close()
            self.assertEqual(keywords, {"Old", "Target"})


class _FakeJobs:
    def submit(self, kind: str, **options):
        return {"job_id": f"job-{kind}", "kind": kind, "options": options}

    def list(self, *, limit: int, status=None):
        return [{"job_id": "job-1", "limit": limit, "status_filter": status}]

    def get(self, job_id: str):
        return {"job_id": job_id, "events": []}


class MaintenanceEntryTests(unittest.TestCase):
    def test_api_and_cli_expose_all_three_maintenance_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            app = create_app(
                config_path=config,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            app.dependency_overrides[get_job_manager] = lambda: _FakeJobs()
            client = TestClient(app)
            self.assertEqual(
                client.post(
                    "/api/v1/maintenance/organize-graph",
                    json={"use_llm": False, "summarize": False},
                ).json()["data"]["kind"],
                "organize_graph",
            )
            self.assertEqual(
                client.post("/api/v1/jobs/summarize").json()["data"]["kind"],
                "summarize",
            )
            self.assertEqual(
                client.post("/api/v1/maintenance/rebuild-knowledge-base").json()[
                    "data"
                ]["kind"],
                "rebuild_knowledge_base",
            )
            self.assertEqual(
                client.post("/api/v1/maintenance/rebuild-all").json()["data"]["kind"],
                "rebuild_all",
            )
            self.assertEqual(
                client.get("/api/v1/jobs/job-1").json()["data"]["job_id"], "job-1"
            )

            fake_service = unittest.mock.MagicMock()
            fake_service.organize_graph.return_value = {"merged_nodes": 1}
            fake_service.rebuild_knowledge_base.return_value = {"processed": 1}
            fake_service.rebuild_all.return_value = {"validation": {}}
            fake_service.list_jobs.return_value = []
            fake_service.get_job.return_value = {"job_id": "j"}
            commands = [
                ["organize-graph", "--no-llm", "--no-summarize"],
                ["rebuild-knowledge-base"],
                ["rebuild-all"],
                ["jobs", "--limit", "5"],
                ["job-status", "j"],
            ]
            with (
                patch.object(start, "load_config", return_value=_settings(root)),
                patch.object(start, "KnowledgeBaseService", return_value=fake_service),
            ):
                for command in commands:
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = start.main(command)
                    self.assertEqual(exit_code, 0, output.getvalue())
                    self.assertTrue(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
