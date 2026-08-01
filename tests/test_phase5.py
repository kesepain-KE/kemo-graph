"""Phase 5 CLI、FastAPI 路由和文档删除验收测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from provider.embedding import EmbeddingResult

import start
from api import create_app as create_api_app
from api.deps import get_service
from core.config import AppConfig
from core.db import connect_graph, connect_rag, connect_sources
from core.ingestor import Ingestor
from core.knowledge_base import (
    KnowledgeBaseNotInitializedError,
    KnowledgeBaseService,
)
from start_web import create_app as create_web_app


def _settings() -> AppConfig:
    return AppConfig(
        graph_build_mode="tools",
        chunk_size=128,
        chunk_overlap=16,
        recycle_life_days=7,
        entity_extraction={"method": "rule", "max_entities": 10},
        models={
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
        },
    )


def _embedding(texts: list[str]) -> EmbeddingResult:
    return EmbeddingResult(
        [[0.5, 0.25, 0.75] for _ in texts],
        "test-space",
    )


def _tool_graph(system, user, tools, tool_handler, **kwargs) -> str:
    del system, user, tools, kwargs
    tool_handler("search_entities", {"query": "Alpha", "limit": 10})
    alpha_id = tool_handler(
        "add_entity",
        {
            "keyword": "Alpha",
            "summary": "Alpha concept",
            "aliases": ["A"],
            "tags": ["test"],
        },
    )
    tool_handler("search_entities", {"query": "Beta", "limit": 10})
    beta_id = tool_handler(
        "add_entity",
        {
            "keyword": "Beta",
            "summary": "Beta concept",
            "aliases": [],
            "tags": ["test"],
        },
    )
    tool_handler(
        "add_relation",
        {
            "source_node_id": alpha_id,
            "relation": "关联",
            "target_node_id": beta_id,
            "evidence_weight": 0.8,
        },
    )
    tool_handler("finish", {})
    return "done"


class KnowledgeBaseServiceTests(unittest.TestCase):
    @patch("core.ingestor.chat_with_tools", side_effect=_tool_graph)
    def test_status_full_graph_and_document_delete(self, _mock_chat) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            data_dir = root / "data"
            external_dir = root / "external" / "markdown"
            external_dir.mkdir(parents=True)
            document = external_dir / "nested" / "doc.md"
            document.parent.mkdir()
            document.write_text("Alpha links Beta", encoding="utf-8")
            settings = _settings()
            ingestor = Ingestor(
                data_dir,
                external_dir,
                settings=settings,
                embedder=_embedding,
            )
            ingestor.ingest(mode="both")
            source_id = _only_source_id(ingestor)
            service = KnowledgeBaseService(
                settings=settings,
                data_dir=data_dir,
                external_dir=external_dir,
            )

            status = service.status()
            self.assertTrue(status["initialized"])
            self.assertEqual(status["sources"]["active"], 1)
            self.assertEqual(status["graph"]["total_nodes"], 2)
            self.assertEqual(status["rag"]["total_vectors"], 1)
            self.assertTrue(status["rag"]["faiss_healthy"])
            full_graph = service.get_full_graph()
            self.assertEqual(
                {node["keyword"] for node in full_graph["nodes"]}, {"Alpha", "Beta"}
            )
            self.assertEqual(len(full_graph["edges"]), 1)
            self.assertEqual(full_graph["nodes_pagination"]["total"], 2)
            paged_graph = service.get_full_graph(nodes_page=2, nodes_page_size=1)
            self.assertEqual(len(paged_graph["nodes"]), 1)
            self.assertEqual(paged_graph["nodes_pagination"]["total_pages"], 2)
            self.assertEqual(len(paged_graph["edges"]), 1)

            document_page = service.list_documents(page=1, page_size=1)
            self.assertEqual(len(document_page["documents"]), 1)
            self.assertEqual(document_page["pagination"]["total"], 1)

            result = service.delete_document(source_id)

            self.assertEqual(result["deleted_source_id"], source_id)
            self.assertEqual(result["recycled_path"], "recycle/nested/doc.md")
            self.assertFalse(document.exists())
            recycled = root / "external" / "recycle" / "nested" / "doc.md"
            self.assertTrue(recycled.exists())
            metadata = json.loads(
                recycled.with_name("doc.md.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["original_path"], "nested/doc.md")
            self.assertIn("expires_at", metadata)
            self.assertEqual(_table_count(ingestor, "graph", "nodes"), 0)
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 0)
            deleted_status = service.status()
            self.assertEqual(deleted_status["sources"]["active"], 0)
            self.assertEqual(deleted_status["rag"]["total_vectors"], 0)
            self.assertTrue(deleted_status["rag"]["faiss_healthy"])

    def test_status_does_not_initialize_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            data_dir = root / "missing-data"
            service = KnowledgeBaseService(
                settings=_settings(),
                data_dir=data_dir,
                external_dir=root / "markdown",
            )
            self.assertFalse(service.status()["initialized"])
            self.assertFalse(data_dir.exists())


class _FakeService:
    def __init__(self) -> None:
        self.fail_graph = False

    def status(self):
        return {"initialized": True}

    def ingest(self, **kwargs):
        return {"operation": "ingest", **kwargs}

    def query_graph(self, query, **kwargs):
        if self.fail_graph:
            raise KnowledgeBaseNotInitializedError("知识库尚未初始化")
        return {"query": query, "hit_nodes": [], **kwargs}

    def query_rag(self, query, **kwargs):
        return {"query": query, "results": [], **kwargs}

    def query_hybrid(self, query, **kwargs):
        return {"query": query, "graph": {}, "rag": {}, **kwargs}

    def query_answer(self, query, **kwargs):
        return {
            "query": query,
            "answer": "hybrid answer",
            "retrieval": {"graph": {}, "rag": {}},
            **kwargs,
        }

    def query_global(self, query, **kwargs):
        return {
            "query": query,
            "answer": "global",
            "communities": [],
            "key_entities": [],
            **kwargs,
        }

    def delete_document(self, source_id):
        return {"deleted_source_id": source_id}

    def list_documents(self, status=None, page=1, page_size=20):
        return {
            "documents": [],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
            },
        }

    def get_full_graph(self, nodes_page=None, nodes_page_size=100):
        return {
            "nodes": [],
            "edges": [],
            "groups": [],
            "nodes_pagination": {
                "page": nodes_page,
                "page_size": nodes_page_size,
                "total": 0,
                "total_pages": 0,
            },
        }

    def generate_group_summaries(self):
        return {"generated": 0}

    def cleanup_recycle(self, *, force=False):
        return {"deleted": 2 if force else 0, "forced": force}


class APITests(unittest.TestCase):
    def test_external_api_prefix_routes_envelopes_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            app = create_api_app(
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            fake = _FakeService()
            app.dependency_overrides[get_service] = lambda: fake
            client = TestClient(app)

            response = client.post(
                "/api/v1/query/graph",
                json={"query": " Alpha ", "depth": 2, "direction": "both"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["query"], "Alpha")
            self.assertIsNone(response.json()["error"])
            global_response = client.post(
                "/api/v1/query/global",
                json={"query": " themes ", "top_k": 4},
            )
            self.assertEqual(global_response.status_code, 200)
            self.assertEqual(global_response.json()["data"]["query"], "themes")
            self.assertEqual(global_response.json()["data"]["top_k"], 4)
            answer_response = client.post(
                "/api/v1/query/answer",
                json={"query": " mixed context ", "graph_depth": 4, "rag_top_k": 8},
            )
            self.assertEqual(answer_response.status_code, 200)
            self.assertEqual(answer_response.json()["data"]["query"], "mixed context")
            self.assertEqual(answer_response.json()["data"]["graph_depth"], 4)
            self.assertEqual(answer_response.json()["data"]["rag_top_k"], 8)
            self.assertEqual(
                client.delete("/api/v1/documents/source-1").json()["data"],
                {"deleted_source_id": "source-1"},
            )
            self.assertEqual(client.get("/api/v1/graph").status_code, 200)
            graph_page = client.get(
                "/api/v1/graph?nodes_page=1&nodes_page_size=5"
            ).json()["data"]
            self.assertEqual(graph_page["nodes_pagination"]["page"], 1)
            document_page = client.get("/api/v1/documents?page=2&page_size=5").json()[
                "data"
            ]
            self.assertEqual(document_page["pagination"]["page"], 2)
            self.assertEqual(client.get("/api/v1/graph/full").status_code, 200)
            self.assertEqual(
                client.delete("/api/v1/maintenance/recycle").json()["data"],
                {"deleted": 2, "forced": True},
            )

            invalid = client.post("/api/v1/query/rag", json={"query": ""})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()["error"]["code"], "INVALID_PARAM")

            fake.fail_graph = True
            unavailable = client.post("/api/v1/query/graph", json={"query": "x"})
            self.assertEqual(unavailable.status_code, 503)
            self.assertEqual(unavailable.json()["error"]["code"], "NOT_INITIALIZED")

            paths = client.get("/openapi.json").json()["paths"]
            expected = {
                "/api/v1/ingest",
                "/api/v1/query/graph",
                "/api/v1/query/rag",
                "/api/v1/query/hybrid",
                "/api/v1/query/answer",
                "/api/v1/query/global",
                "/api/v1/status",
                "/api/v1/documents/{source_id}",
                "/api/v1/graph",
                "/api/v1/maintenance/recycle",
            }
            self.assertTrue(expected.issubset(paths))

    def test_web_routes_share_core_router_and_allow_local_cors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            app = create_web_app(
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            fake = _FakeService()
            app.dependency_overrides[get_service] = lambda: fake
            client = TestClient(app)

            self.assertEqual(client.get("/status").status_code, 200)
            self.assertEqual(client.get("/api/v1/status").status_code, 200)
            self.assertEqual(client.get("/graph").status_code, 200)
            preflight = client.options(
                "/status",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(
                preflight.headers["access-control-allow-origin"],
                "http://localhost:5173",
            )


class CLITests(unittest.TestCase):
    def test_all_subcommands_emit_formatted_json_and_call_core_service(self) -> None:
        fake = _FakeService()
        commands = [
            ["ingest", "doc.md", "--mode", "graph"],
            ["query-graph", "alpha", "--depth", "2"],
            ["query-rag", "alpha", "--top-k", "3"],
            ["query-hybrid", "alpha", "--graph-depth", "2"],
            ["query-global", "themes", "--top-k", "4"],
            ["status"],
            ["delete-doc", "source-1"],
            ["summarize"],
            ["cleanup-recycle"],
            ["list-docs", "--page", "2", "--page-size", "5"],
        ]
        with (
            patch.object(start, "load_config", return_value=_settings()),
            patch.object(start, "KnowledgeBaseService", return_value=fake),
        ):
            for command in commands:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = start.main(
                        [
                            "--data-dir",
                            "custom-data",
                            "--external-dir",
                            "custom-markdown",
                            "--config",
                            "custom-config.json",
                            *command,
                        ]
                    )
                payload = json.loads(output.getvalue())
                self.assertEqual(exit_code, 0)
                self.assertTrue(payload["ok"])
                self.assertIsNone(payload["error"])
                self.assertIn('\n  "ok"', output.getvalue())

    def test_cli_returns_structured_error_and_nonzero_exit(self) -> None:
        fake = _FakeService()
        fake.fail_graph = True
        with (
            patch.object(start, "load_config", return_value=_settings()),
            patch.object(start, "KnowledgeBaseService", return_value=fake),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = start.main(["query-graph", "alpha"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "NOT_INITIALIZED")


def _only_source_id(ingestor: Ingestor) -> str:
    connection = connect_sources(ingestor.paths)
    try:
        rows = connection.execute("SELECT source_id FROM sources").fetchall()
        if len(rows) != 1:
            raise AssertionError(f"expected one source, got {len(rows)}")
        return rows[0]["source_id"]
    finally:
        connection.close()


def _table_count(ingestor: Ingestor, database: str, table: str) -> int:
    connector = connect_graph if database == "graph" else connect_rag
    connection = connector(ingestor.paths)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
