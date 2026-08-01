"""Phase 14A 图谱可视化数据契约与确定性局部 BFS 测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import create_app
from api.deps import get_service
from core.config import AppConfig
from core.db import connect_graph, initialize_databases
from core.graph_visualization import (
    GraphNodeNotFoundError,
    GraphRevisionChangedError,
)
from core.knowledge_base import KnowledgeBaseService


class GraphVisualizationServiceTests(unittest.TestCase):
    def test_separate_pages_revision_and_legacy_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            service = _seed_service(root)

            meta = service.get_graph_visualization_meta()
            self.assertEqual(meta["node_count"], 4)
            self.assertEqual(meta["edge_count"], 3)
            self.assertEqual(meta["group_count"], 1)
            self.assertEqual(len(meta["revision"]), 64)
            self.assertEqual(meta["groups"][0]["group_id"], "g1")

            first_nodes = service.list_graph_visualization_nodes(
                page=1,
                page_size=2,
                expected_revision=meta["revision"],
            )
            second_nodes = service.list_graph_visualization_nodes(
                page=2,
                page_size=2,
                expected_revision=meta["revision"],
            )
            self.assertEqual(first_nodes["revision"], meta["revision"])
            self.assertEqual(first_nodes["pagination"]["total_pages"], 2)
            self.assertNotIn("edges", first_nodes)
            node_ids = {
                node["node_id"]
                for page in (first_nodes, second_nodes)
                for node in page["nodes"]
            }
            self.assertEqual(node_ids, {"a", "b", "c", "d"})
            alpha = next(
                node
                for page in (first_nodes, second_nodes)
                for node in page["nodes"]
                if node["node_id"] == "a"
            )
            self.assertEqual(alpha["source_ids"], ["source-1"])
            self.assertEqual(alpha["group_ids"], ["g1"])

            edge_pages = [
                service.list_graph_visualization_edges(
                    page=page,
                    page_size=1,
                    expected_revision=meta["revision"],
                )
                for page in range(1, 4)
            ]
            self.assertTrue(all(len(page["edges"]) == 1 for page in edge_pages))
            self.assertEqual(
                {
                    edge["edge_id"]
                    for page in edge_pages
                    for edge in page["edges"]
                },
                {"e1", "e2", "e3"},
            )

            legacy = service.get_full_graph(nodes_page=1, nodes_page_size=2)
            self.assertEqual(len(legacy["nodes"]), 2)
            self.assertEqual(len(legacy["edges"]), 3)
            self.assertIn("nodes_pagination", legacy)

            graph = connect_graph(service.paths)
            try:
                graph.execute("UPDATE nodes SET summary = 'changed' WHERE node_id = 'a'")
                graph.commit()
            finally:
                graph.close()
            changed = service.get_graph_visualization_meta()
            self.assertNotEqual(changed["revision"], meta["revision"])
            with self.assertRaises(GraphRevisionChangedError):
                service.list_graph_visualization_nodes(
                    expected_revision=meta["revision"]
                )

    def test_neighborhood_direction_depth_limits_and_no_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            service = _seed_service(Path(temporary_dir))
            revision = service.get_graph_visualization_meta()["revision"]

            with patch("core.knowledge_base.chat") as model_chat:
                forward = service.get_graph_neighborhood(
                    "b",
                    depth=1,
                    direction="forward",
                    expected_revision=revision,
                )
                backward = service.get_graph_neighborhood(
                    "b",
                    depth=1,
                    direction="backward",
                    expected_revision=revision,
                )
                both = service.get_graph_neighborhood(
                    "b",
                    depth=2,
                    direction="both",
                    expected_revision=revision,
                )
                model_chat.assert_not_called()

            self.assertEqual(
                {node["node_id"] for node in forward["nodes"]},
                {"b", "c"},
            )
            self.assertEqual(
                {node["node_id"] for node in backward["nodes"]},
                {"a", "b", "d"},
            )
            self.assertEqual(
                {node["node_id"] for node in both["nodes"]},
                {"a", "b", "c", "d"},
            )
            self.assertEqual(
                {node["node_id"]: node["depth"] for node in both["nodes"]},
                {"b": 0, "a": 1, "c": 1, "d": 1},
            )
            self.assertEqual(both["groups"][0]["node_ids"], ["a", "b", "c"])

            node_limited = service.get_graph_neighborhood("b", depth=2, limit=2)
            self.assertTrue(node_limited["truncated"])
            self.assertEqual(len(node_limited["nodes"]), 2)
            edge_limited = service.get_graph_neighborhood(
                "b",
                depth=2,
                edge_limit=1,
            )
            self.assertTrue(edge_limited["edges_truncated"])
            self.assertEqual(len(edge_limited["edges"]), 1)

            depth_ten = service.get_graph_neighborhood("b", depth=10)
            self.assertEqual(depth_ten["depth"], 10)
            with self.assertRaises(ValueError):
                service.get_graph_neighborhood("b", depth=11)

            with self.assertRaises(GraphNodeNotFoundError):
                service.get_graph_neighborhood("missing")


class GraphVisualizationAPITests(unittest.TestCase):
    def test_routes_revision_conflict_and_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            service = _seed_service(root)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            app = create_app(
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            app.dependency_overrides[get_service] = lambda: service
            client = TestClient(app)

            meta_response = client.get("/api/v1/graph/visualization/meta")
            self.assertEqual(meta_response.status_code, 200)
            revision = meta_response.json()["data"]["revision"]
            nodes = client.get(
                "/api/v1/graph/visualization/nodes",
                params={
                    "page": 1,
                    "page_size": 2,
                    "expected_revision": revision,
                },
            )
            self.assertEqual(nodes.status_code, 200)
            self.assertEqual(len(nodes.json()["data"]["nodes"]), 2)
            edges = client.get(
                "/api/v1/graph/visualization/edges",
                params={"page_size": 1, "expected_revision": revision},
            )
            self.assertEqual(edges.status_code, 200)
            self.assertEqual(len(edges.json()["data"]["edges"]), 1)
            neighborhood = client.get(
                "/api/v1/graph/neighborhood/b",
                params={"depth": 10, "direction": "both"},
            )
            self.assertEqual(neighborhood.status_code, 200)
            self.assertEqual(len(neighborhood.json()["data"]["nodes"]), 4)
            depth_too_large = client.get(
                "/api/v1/graph/neighborhood/b",
                params={"depth": 11},
            )
            self.assertEqual(depth_too_large.status_code, 422)

            missing = client.get("/api/v1/graph/neighborhood/missing")
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["error"]["code"], "NOT_FOUND")
            invalid = client.get(
                "/api/v1/graph/visualization/nodes",
                params={"expected_revision": "short"},
            )
            self.assertEqual(invalid.status_code, 422)

            graph = connect_graph(service.paths)
            try:
                graph.execute("UPDATE edges SET weight = 0.4 WHERE edge_id = 'e1'")
                graph.commit()
            finally:
                graph.close()
            conflict = client.get(
                "/api/v1/graph/visualization/edges",
                params={"expected_revision": revision},
            )
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["error"]["code"], "GRAPH_CHANGED")

            paths = client.get("/openapi.json").json()["paths"]
            self.assertIn("/api/v1/graph/visualization/meta", paths)
            self.assertIn("/api/v1/graph/visualization/nodes", paths)
            self.assertIn("/api/v1/graph/visualization/edges", paths)
            self.assertIn("/api/v1/graph/neighborhood/{node_id}", paths)


def _seed_service(root: Path) -> KnowledgeBaseService:
    settings = AppConfig()
    paths = initialize_databases(root / "data", settings)
    graph = connect_graph(paths)
    try:
        graph.executemany(
            """
            INSERT INTO nodes (
                node_id, keyword, summary, aliases, tags, weight, ref_count,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("a", "Alpha", "A", '["A1"]', '["core"]', 1.0, 4, None, None),
                ("b", "Beta", "B", "[]", "[]", 0.8, 3, None, None),
                ("c", "Gamma", "C", "[]", "[]", 0.7, 2, None, None),
                ("d", "Delta", "D", "[]", "[]", 0.6, 1, None, None),
            ],
        )
        graph.execute(
            """
            INSERT INTO node_sources (
                node_id, source_id, content_hash, evidence_weight
            ) VALUES ('a', 'source-1', 'hash-1', 1.0)
            """
        )
        graph.executemany(
            """
            INSERT INTO edges (
                edge_id, source_node_id, relation, target_node_id,
                weight, support_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("e1", "a", "to-beta", "b", 0.9, 1, None),
                ("e2", "b", "to-gamma", "c", 0.8, 1, None),
                ("e3", "d", "to-beta", "b", 0.7, 1, None),
            ],
        )
        graph.execute(
            """
            INSERT INTO groups (
                group_id, summary, node_count, edge_count, created_at, updated_at
            ) VALUES ('g1', 'ABC group', 3, 2, NULL, NULL)
            """
        )
        graph.executemany(
            "INSERT INTO group_nodes (group_id, node_id) VALUES ('g1', ?)",
            [("a",), ("b",), ("c",)],
        )
        graph.commit()
    finally:
        graph.close()
    return KnowledgeBaseService(
        settings=settings,
        data_dir=root / "data",
        external_dir=root / "markdown",
    )


if __name__ == "__main__":
    unittest.main()
