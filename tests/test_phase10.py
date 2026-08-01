"""Phase 10 高速结构化图谱、来源事实层和权重契约测试。"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig
from core.db import connect_graph, connect_sources
from core.db import connect_rag, initialize_databases, read_graph_meta, write_graph_meta
from core.graph_draft import (
    GraphDraftError,
    merge_graph_drafts,
    split_markdown_for_graph,
    validate_graph_draft,
)
from core.ingestor import Ingestor
from core.graph_organizer import GraphOrganizer
from core.knowledge_base import KnowledgeBaseService
from provider import ProviderResponseError
from provider.embedding import EmbeddingResult
from provider.engine import chat_structured


def _settings(root: Path, **updates: object) -> AppConfig:
    values: dict[str, object] = {
        "graph_build_mode": "structured",
        "graph_extract_chunk_size": 2000,
        "graph_extract_concurrency": 3,
        "log_dir": str(root / "log"),
        "models": {
            "llm": "test-llm",
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
            "rerank": "test-rerank",
        },
        "kemo": {"api_key": "test-key"},
    }
    values.update(updates)
    return AppConfig(**values)


def _embedding(texts: list[str], **_: object) -> EmbeddingResult:
    return EmbeddingResult(
        vectors=[[0.5, 0.25, 0.75] for _ in texts],
        vector_space_id="test-space",
    )


def _draft(
    *,
    left: str = "Alpha",
    right: str = "Beta",
    node_weight: float = 0.7,
    edge_weight: float = 0.8,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "entities": [
            {
                "local_id": "a",
                "keyword": left,
                "summary": f"{left} summary",
                "aliases": [],
                "tags": ["concept"],
                "evidence_weight": node_weight,
                "evidence": left,
            },
            {
                "local_id": "b",
                "keyword": right,
                "summary": f"{right} summary",
                "aliases": [],
                "tags": ["concept"],
                "evidence_weight": node_weight - 0.1,
                "evidence": right,
            },
        ],
        "relations": [
            {
                "source": "a",
                "relation": "关联",
                "target": "b",
                "evidence_weight": edge_weight,
                "evidence": f"{left} relates {right}",
            }
        ],
    }


class StructuredProviderTests(unittest.TestCase):
    def test_chat_structured_makes_one_request_and_returns_arguments(self) -> None:
        payload = _draft()
        response = {
            "status": "completed",
            "request_id": "request-1",
            "output": [
                {
                    "id": "tool-1",
                    "type": "tool_call",
                    "call_id": "call-1",
                    "name": "submit_structured_output",
                    "arguments": payload,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            settings = _settings(Path(temporary_dir))
            with patch("provider.engine.request_json", return_value=response) as request:
                result = chat_structured(
                    "system", "user", {"type": "object", "properties": {}},
                    settings=settings,
                )
        self.assertEqual(result, payload)
        self.assertEqual(request.call_count, 1)
        request_payload = request.call_args.kwargs["payload"]
        self.assertEqual(len(request_payload["tools"]), 1)
        self.assertEqual(request_payload["tools"][0]["name"], "submit_structured_output")

    def test_chat_structured_rejects_multiple_or_wrong_calls(self) -> None:
        base_call = {
            "id": "tool-1",
            "type": "tool_call",
            "call_id": "call-1",
            "name": "submit_structured_output",
            "arguments": {"value": 1},
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            settings = _settings(Path(temporary_dir))
            schema = {"type": "object", "properties": {}}
            with patch(
                "provider.engine.request_json",
                return_value={"status": "completed", "output": [base_call, base_call]},
            ):
                with self.assertRaisesRegex(ProviderResponseError, "只能返回一个"):
                    chat_structured("system", "user", schema, settings=settings)
            wrong = {**base_call, "name": "other"}
            with patch(
                "provider.engine.request_json",
                return_value={"status": "completed", "output": [wrong]},
            ):
                with self.assertRaisesRegex(ProviderResponseError, "工具名错误"):
                    chat_structured("system", "user", schema, settings=settings)


class GraphDraftTests(unittest.TestCase):
    def test_strict_validation_and_cross_section_merge(self) -> None:
        first = validate_graph_draft(_draft(node_weight=0.4, edge_weight=0.5))
        second_payload = _draft(
            left="Alpha alias",
            right="Gamma",
            node_weight=0.9,
            edge_weight=0.95,
        )
        second_payload["entities"][0]["aliases"] = ["Alpha"]  # type: ignore[index]
        second = validate_graph_draft(second_payload)
        merged = merge_graph_drafts([first, second])
        self.assertEqual(len(merged.entities), 3)
        alpha = next(entity for entity in merged.entities if entity.keyword == "Alpha")
        self.assertEqual(alpha.evidence_weight, 0.9)
        self.assertIn("Alpha alias", alpha.aliases)
        self.assertEqual(len(merged.relations), 2)

        invalid = _draft()
        invalid["extra"] = True
        with self.assertRaises(GraphDraftError):
            validate_graph_draft(invalid)

    def test_markdown_split_honours_hard_limit(self) -> None:
        text = "# A\n" + ("a" * 1700) + "\n\n# B\n" + ("b" * 1700)
        sections = split_markdown_for_graph(text, 2000)
        self.assertGreaterEqual(len(sections), 2)
        self.assertTrue(all(len(section) <= 2000 for section in sections))


class StructuredIngestTests(unittest.TestCase):
    def test_structured_build_writes_mentions_hashes_and_max_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "one.md").write_text("Alpha relates Beta", encoding="utf-8")
            (external / "two.md").write_text("Alpha relates Beta again", encoding="utf-8")
            ingestor = Ingestor(root / "data", external, settings=_settings(root))

            responses = [
                _draft(node_weight=0.6, edge_weight=0.7),
                _draft(node_weight=0.95, edge_weight=0.9),
            ]
            with patch("core.ingestor.chat_structured", side_effect=responses):
                result = ingestor.ingest(mode="graph")
            self.assertEqual(result["graph_updated"], 2)

            graph = connect_graph(ingestor.paths)
            try:
                nodes = graph.execute(
                    "SELECT keyword, weight, ref_count FROM nodes ORDER BY keyword"
                ).fetchall()
                edge = graph.execute(
                    "SELECT weight, support_count FROM edges"
                ).fetchone()
                entity_mentions = graph.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT content_hash) FROM entity_mentions"
                ).fetchone()
                relation_mentions = graph.execute(
                    "SELECT COUNT(*) FROM relation_mentions"
                ).fetchone()[0]
                bindings = graph.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT content_hash) FROM node_sources"
                ).fetchone()
            finally:
                graph.close()
            self.assertEqual([(row["keyword"], row["ref_count"]) for row in nodes], [("Alpha", 2), ("Beta", 2)])
            self.assertAlmostEqual(float(nodes[0]["weight"]), 0.95)
            self.assertAlmostEqual(float(edge["weight"]), 0.9)
            self.assertEqual(int(edge["support_count"]), 2)
            self.assertEqual(tuple(entity_mentions), (4, 2))
            self.assertEqual(int(relation_mentions), 2)
            self.assertEqual(tuple(bindings), (4, 2))

            sources = connect_sources(ingestor.paths)
            try:
                stale = sources.execute(
                    "SELECT COUNT(*) FROM sources WHERE graph_hash != content_hash"
                ).fetchone()[0]
            finally:
                sources.close()
            self.assertEqual(stale, 0)


class GraphOrganizerTests(unittest.TestCase):
    def test_exact_overlap_merge_preserves_facts_edges_and_chunk_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            sources = connect_sources(paths)
            try:
                for source_id, content_hash in (("s1", "h1"), ("s2", "h2")):
                    sources.execute(
                        """
                        INSERT INTO sources (
                            source_id, original_path, relative_path, path_hash,
                            content_hash, graph_hash, rag_hash, graph_status,
                            rag_status, exists_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', 'ready', 'active')
                        """,
                        (
                            source_id,
                            f"{source_id}.md",
                            f"{source_id}.md",
                            f"p-{source_id}",
                            content_hash,
                            content_hash,
                            content_hash,
                        ),
                    )
                sources.commit()
            finally:
                sources.close()

            graph = connect_graph(paths)
            try:
                graph.executemany(
                    """
                    INSERT INTO nodes (
                        node_id, keyword, summary, aliases, tags, weight, ref_count
                    ) VALUES (?, ?, ?, ?, '[]', ?, ?)
                    """,
                    [
                        ("n1", "Alpha", "short", '["A"]', 0.6, 1),
                        ("n2", "A", "a more complete summary", "[]", 0.9, 1),
                        ("n3", "Beta", "target", "[]", 0.8, 1),
                    ],
                )
                graph.executemany(
                    """
                    INSERT INTO node_sources (
                        node_id, source_id, content_hash, evidence_weight, evidence
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        ("n1", "s1", "h1", 0.6, "alpha"),
                        ("n2", "s2", "h2", 0.9, "A"),
                        ("n3", "s1", "h1", 0.8, "beta"),
                    ],
                )
                graph.executemany(
                    """
                    INSERT INTO entity_mentions (
                        mention_id, source_id, content_hash, local_id, keyword,
                        summary, aliases, tags, evidence_weight, evidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, 'now')
                    """,
                    [
                        ("m1", "s1", "h1", "a", "Alpha", "short", 0.6, "alpha"),
                        ("m2", "s2", "h2", "a", "A", "complete", 0.9, "A"),
                    ],
                )
                graph.executemany(
                    "INSERT INTO mention_nodes (mention_id, node_id) VALUES (?, ?)",
                    [("m1", "n1"), ("m2", "n2")],
                )
                graph.executemany(
                    """
                    INSERT INTO edges (
                        edge_id, source_node_id, relation, target_node_id,
                        weight, support_count
                    ) VALUES (?, ?, '关联', 'n3', ?, 1)
                    """,
                    [("e1", "n1", 0.6), ("e2", "n2", 0.9)],
                )
                graph.executemany(
                    """
                    INSERT INTO edge_sources (
                        edge_id, source_id, content_hash, evidence_weight
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [("e1", "s1", "h1", 0.6), ("e2", "s2", "h2", 0.9)],
                )
                graph.execute(
                    "INSERT INTO groups (group_id, summary) VALUES ('g', 'old')"
                )
                graph.executemany(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES ('g', ?)",
                    [("n1",), ("n2",), ("n3",)],
                )
                graph.commit()
            finally:
                graph.close()

            rag = connect_rag(paths)
            try:
                rag.executemany(
                    "INSERT INTO chunks (chunk_id, source_id, content) VALUES (?, ?, 'x')",
                    [("c1", "s1"), ("c2", "s2")],
                )
                rag.executemany(
                    "INSERT INTO chunk_nodes (chunk_id, node_id) VALUES (?, ?)",
                    [("c1", "n1"), ("c2", "n2")],
                )
                rag.commit()
            finally:
                rag.close()

            result = GraphOrganizer(root / "data", settings=settings).organize(use_llm=False)
            self.assertEqual(result["merged_nodes"], 1)
            self.assertEqual(result["removed_edges"], 0)

            graph = connect_graph(paths)
            try:
                nodes = graph.execute(
                    "SELECT node_id, keyword, aliases, weight, ref_count FROM nodes ORDER BY node_id"
                ).fetchall()
                edge = graph.execute(
                    "SELECT edge_id, source_node_id, weight, support_count FROM edges"
                ).fetchone()
                mappings = graph.execute(
                    "SELECT mention_id, node_id FROM mention_nodes ORDER BY mention_id"
                ).fetchall()
                source_count = graph.execute(
                    "SELECT COUNT(*) FROM node_sources WHERE node_id = 'n2'"
                ).fetchone()[0]
                group_count = graph.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
            finally:
                graph.close()
            self.assertEqual([row["node_id"] for row in nodes], ["n2", "n3"])
            self.assertEqual((nodes[0]["weight"], nodes[0]["ref_count"]), (0.9, 2))
            self.assertIn("Alpha", nodes[0]["aliases"])
            self.assertEqual((edge["source_node_id"], edge["weight"], edge["support_count"]), ("n2", 0.9, 2))
            self.assertEqual([(row["mention_id"], row["node_id"]) for row in mappings], [("m1", "n2"), ("m2", "n2")])
            self.assertEqual(source_count, 2)
            self.assertEqual(group_count, 0)

            rag = connect_rag(paths)
            try:
                links = rag.execute(
                    "SELECT chunk_id, node_id FROM chunk_nodes ORDER BY chunk_id"
                ).fetchall()
            finally:
                rag.close()
            self.assertEqual(
                [(row["chunk_id"], row["node_id"]) for row in links],
                [("c1", "n2"), ("c1", "n3"), ("c2", "n2")],
            )


class GroupSummaryTests(unittest.TestCase):
    def test_group_summary_records_real_edge_count_and_rolls_back_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root, summary_trigger_file_count=1)
            paths = initialize_databases(root / "data", settings)
            graph = connect_graph(paths)
            try:
                graph.executemany(
                    """
                    INSERT INTO nodes (
                        node_id, keyword, summary, aliases, tags, weight, ref_count
                    ) VALUES (?, ?, ?, '[]', '[]', 1, 1)
                    """,
                    [(f"n{i}", f"Node {i}", f"Summary {i}") for i in range(4)],
                )
                graph.executemany(
                    """
                    INSERT INTO edges (
                        edge_id, source_node_id, relation, target_node_id,
                        weight, support_count
                    ) VALUES (?, ?, '关联', ?, 1, 1)
                    """,
                    [("e1", "n0", "n1"), ("e2", "n1", "n2"), ("e3", "n2", "n3")],
                )
                graph.commit()
            finally:
                graph.close()
            meta = read_graph_meta(paths)
            write_graph_meta(paths, {**meta, "changed_since_summary": 1})
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            with (
                patch("core.knowledge_base.chat", return_value="A connected group"),
                patch("core.rag_engine.embed", side_effect=_embedding),
            ):
                result = service.generate_group_summaries(force=True)
            self.assertEqual(result["generated"], 1)
            self.assertEqual(result["community_vectors"]["updated"], 1)
            graph = connect_graph(paths)
            try:
                group = graph.execute(
                    "SELECT summary, node_count, edge_count FROM groups"
                ).fetchone()
            finally:
                graph.close()
            self.assertEqual(tuple(group), ("A connected group", 4, 3))

            write_graph_meta(paths, {**read_graph_meta(paths), "changed_since_summary": 1})
            with patch("core.knowledge_base.chat", side_effect=RuntimeError("gateway")):
                with self.assertRaises(RuntimeError):
                    service.generate_group_summaries(force=True)
            graph = connect_graph(paths)
            try:
                still_there = graph.execute("SELECT summary FROM groups").fetchone()[0]
            finally:
                graph.close()
            self.assertEqual(still_there, "A connected group")


class StructuredIngestFailureTests(unittest.TestCase):
    def test_failed_replacement_keeps_previous_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("Alpha relates Beta", encoding="utf-8")
            ingestor = Ingestor(root / "data", external, settings=_settings(root))
            with patch("core.ingestor.chat_structured", return_value=_draft()):
                self.assertEqual(ingestor.ingest(mode="graph")["graph_updated"], 1)

            graph = connect_graph(ingestor.paths)
            try:
                old_binding = graph.execute(
                    "SELECT content_hash FROM node_sources ORDER BY node_id LIMIT 1"
                ).fetchone()[0]
            finally:
                graph.close()

            document.write_text("Gamma now replaces everything", encoding="utf-8")
            with patch("core.ingestor.chat_structured", side_effect=RuntimeError("gateway")):
                failed = ingestor.ingest(mode="graph")
            self.assertEqual(failed["failed"], 1)
            graph = connect_graph(ingestor.paths)
            try:
                keywords = {
                    row[0] for row in graph.execute("SELECT keyword FROM nodes")
                }
                current_binding = graph.execute(
                    "SELECT content_hash FROM node_sources ORDER BY node_id LIMIT 1"
                ).fetchone()[0]
            finally:
                graph.close()
            self.assertEqual(keywords, {"Alpha", "Beta"})
            self.assertEqual(current_binding, old_binding)

    def test_parallel_section_failure_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            text = "# One\n" + ("a" * 2100) + "\n# Two\n" + ("b" * 2100)
            (external / "large.md").write_text(text, encoding="utf-8")
            ingestor = Ingestor(root / "data", external, settings=_settings(root))
            lock = threading.Lock()
            calls = 0

            def fail_one(*args: object, **kwargs: object) -> dict[str, object]:
                nonlocal calls
                with lock:
                    calls += 1
                    current = calls
                if current == 2:
                    raise RuntimeError("section failed")
                return _draft(left=f"Alpha {current}", right=f"Beta {current}")

            with patch("core.ingestor.chat_structured", side_effect=fail_one):
                result = ingestor.ingest(mode="graph")
            self.assertEqual(result["failed"], 1)
            graph = connect_graph(ingestor.paths)
            try:
                self.assertEqual(graph.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], 0)
                self.assertEqual(graph.execute("SELECT COUNT(*) FROM entity_mentions").fetchone()[0], 0)
            finally:
                graph.close()


if __name__ == "__main__":
    unittest.main()
