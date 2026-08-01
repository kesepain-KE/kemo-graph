"""Phase 12 LLM 切分、多向量检索、描述合成与全局搜索验收。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig
from core.db import connect_graph, connect_rag, initialize_databases
from core.graph_organizer import GraphOrganizer
from core.knowledge_base import KnowledgeBaseService
from core.rag_engine import RAGEngine
from provider.embedding import EmbeddingResult


def _settings(root: Path) -> AppConfig:
    return AppConfig(
        log_dir=str(root / "log"),
        models={
            "llm": "test-llm",
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
            "rerank": "test-rerank",
        },
        kemo={"api_key": "test-key"},
        vector_search={
            "entity_weight": 0.8,
            "community_weight": 0.6,
            "entity_top_k": 5,
            "community_top_k": 3,
        },
    )


def _insert_organizer_fixture(paths) -> None:
    sources = sqlite3.connect(paths.sources_db)
    try:
        sources.executemany(
            """
            INSERT INTO sources (
                source_id, original_path, relative_path, path_hash,
                content_hash, graph_hash, rag_hash, graph_status,
                rag_status, exists_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', 'ready', 'active')
            """,
            [
                ("s1", "s1.md", "s1.md", "p1", "h1", "h1", "h1"),
                ("s2", "s2.md", "s2.md", "p2", "h2", "h2", "h2"),
            ],
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
            ) VALUES (?, ?, ?, ?, '[]', 1, 1)
            """,
            [
                ("n1", "Alpha", "事实 A", '["A"]'),
                ("n2", "A", "更长的事实 B 描述", "[]"),
            ],
        )
        graph.executemany(
            """
            INSERT INTO node_sources (
                node_id, source_id, content_hash, evidence_weight
            ) VALUES (?, ?, ?, 1)
            """,
            [("n1", "s1", "h1"), ("n2", "s2", "h2")],
        )
        graph.commit()
    finally:
        graph.close()


def _merge_and_finish(
    _system,
    _user,
    _tools,
    tool_handler,
    **_kwargs,
):
    tool_handler("get_entity", {"node_id": "n1"})
    tool_handler("get_entity", {"node_id": "n2"})
    tool_handler(
        "merge_entities",
        {"keep_node_id": "n1", "merge_node_id": "n2"},
    )
    tool_handler("finish", {"note": "done"})
    return "done"


class MultiVectorTests(unittest.TestCase):
    def test_schema_migration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            data = root / "data"
            graph_dir = data / "Graph"
            rag_dir = data / "RAG"
            graph_dir.mkdir(parents=True)
            rag_dir.mkdir(parents=True)
            graph = sqlite3.connect(graph_dir / "graph.db")
            graph.execute(
                "CREATE TABLE nodes (node_id TEXT PRIMARY KEY, keyword TEXT, summary TEXT)"
            )
            graph.commit()
            graph.close()
            rag = sqlite3.connect(rag_dir / "rag.db")
            rag.execute(
                """
                CREATE TABLE entity_embeddings (
                    vector_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT UNIQUE,
                    summary TEXT,
                    vector_blob BLOB,
                    dimensions INTEGER,
                    model_name TEXT
                )
                """
            )
            rag.commit()
            rag.close()

            paths = initialize_databases(data, _settings(root))
            initialize_databases(data, _settings(root))

            graph = connect_graph(paths)
            rag = connect_rag(paths)
            try:
                graph_columns = {
                    row["name"] for row in graph.execute("PRAGMA table_info(nodes)")
                }
                entity_columns = {
                    row["name"]
                    for row in rag.execute("PRAGMA table_info(entity_embeddings)")
                }
                community_tables = {
                    row["name"]
                    for row in rag.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                graph.close()
                rag.close()
            self.assertIn("has_entity_embedding", graph_columns)
            self.assertTrue(
                {"summary_hash", "vector_space_id", "updated_at"}.issubset(
                    entity_columns
                )
            )
            self.assertIn("community_embeddings", community_tables)

    def test_entity_and_community_vectors_are_weighted_and_freshness_aware(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            graph = connect_graph(paths)
            try:
                graph.executemany(
                    """
                    INSERT INTO nodes (node_id, keyword, summary, aliases, tags)
                    VALUES (?, ?, ?, '[]', '[]')
                    """,
                    [
                        ("n-alpha", "Alpha", "Alpha summary"),
                        ("n-beta", "Beta", "Beta summary"),
                    ],
                )
                graph.execute(
                    """
                    INSERT INTO groups (group_id, summary, node_count, edge_count)
                    VALUES ('g-main', 'Community summary', 2, 1)
                    """
                )
                graph.commit()
            finally:
                graph.close()

            calls: list[list[str]] = []

            def embedding(texts: list[str]) -> EmbeddingResult:
                calls.append(texts.copy())
                vectors = []
                for text in texts:
                    lowered = text.casefold()
                    if "alpha" in lowered:
                        vectors.append([1.0, 0.0, 0.0])
                    elif "community" in lowered:
                        vectors.append([0.0, 0.0, 1.0])
                    else:
                        vectors.append([0.0, 1.0, 0.0])
                return EmbeddingResult(vectors, "test-space")

            engine = RAGEngine(root / "data", settings=settings, embedder=embedding)
            entity_build = engine.sync_entity_vectors(
                [("n-alpha", "Alpha summary"), ("n-beta", "Beta summary")]
            )
            community_build = engine.build_community_vectors(
                "g-main", "Community summary"
            )
            calls_after_build = len(calls)
            skipped = engine.build_entity_vectors("n-alpha", "Alpha summary")

            self.assertEqual(entity_build["updated"], 2)
            self.assertEqual(community_build["updated"], 1)
            self.assertEqual(skipped["updated"], 0)
            self.assertEqual(len(calls), calls_after_build)
            entity_result = engine.search_entities("alpha query", top_k=1)[0]
            community_result = engine.search_communities(
                "community query", top_k=1
            )[0]
            self.assertEqual(entity_result["node_id"], "n-alpha")
            self.assertAlmostEqual(entity_result["score"], 0.8)
            self.assertEqual(entity_result["source"], "entity")
            self.assertEqual(community_result["group_id"], "g-main")
            self.assertAlmostEqual(community_result["score"], 0.6)
            self.assertEqual(community_result["source"], "community")
            self.assertTrue(paths.entity_faiss_index.exists())
            self.assertTrue(paths.community_faiss_index.exists())

    def test_auxiliary_index_recovers_and_stale_summary_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            graph = connect_graph(paths)
            try:
                graph.execute(
                    """
                    INSERT INTO nodes (node_id, keyword, summary, aliases, tags)
                    VALUES ('n1', 'Node', 'Old summary', '[]', '[]')
                    """
                )
                graph.commit()
            finally:
                graph.close()

            embedding = lambda texts, **_: EmbeddingResult(  # noqa: E731
                [[1.0, 0.0, 0.0] for _ in texts],
                "test-space",
            )
            engine = RAGEngine(root / "data", settings=settings, embedder=embedding)
            with patch.object(
                engine.entity_index,
                "replace",
                side_effect=RuntimeError("persist failed"),
            ):
                result = engine.build_entity_vectors("n1", "Old summary")
            self.assertEqual(result["updated"], 1)
            self.assertEqual(engine.entity_index.count, 1)

            graph = connect_graph(paths)
            try:
                graph.execute(
                    "UPDATE nodes SET summary = 'New summary' WHERE node_id = 'n1'"
                )
                graph.commit()
            finally:
                graph.close()
            refreshed = RAGEngine(root / "data", settings=settings, embedder=embedding)
            rag = connect_rag(paths)
            graph = connect_graph(paths)
            try:
                count = rag.execute(
                    "SELECT COUNT(*) FROM entity_embeddings"
                ).fetchone()[0]
                flag = graph.execute(
                    "SELECT has_entity_embedding FROM nodes WHERE node_id = 'n1'"
                ).fetchone()[0]
            finally:
                rag.close()
                graph.close()
            self.assertEqual(count, 0)
            self.assertEqual(refreshed.entity_index.count, 0)
            self.assertEqual(flag, 0)


class DescriptionSynthesisTests(unittest.TestCase):
    def test_llm_description_is_synthesized_before_write_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            _insert_organizer_fixture(paths)

            def synthesize(_system, _user, **_kwargs):
                probe = sqlite3.connect(paths.graph_db, timeout=0.1)
                try:
                    probe.execute("BEGIN IMMEDIATE")
                    probe.rollback()
                finally:
                    probe.close()
                return "事实 A 与事实 B 的完整合成描述"

            with (
                patch(
                    "core.graph_organizer.chat_with_tools",
                    side_effect=_merge_and_finish,
                ),
                patch("core.graph_organizer.chat", side_effect=synthesize) as request,
            ):
                result = GraphOrganizer(
                    root / "data",
                    settings=settings,
                ).organize(use_llm=True)

            graph = connect_graph(paths)
            try:
                rows = graph.execute(
                    "SELECT node_id, summary FROM nodes ORDER BY node_id"
                ).fetchall()
            finally:
                graph.close()
            self.assertEqual(result["merged_nodes"], 1)
            self.assertEqual(
                [(row["node_id"], row["summary"]) for row in rows],
                [("n1", "事实 A 与事实 B 的完整合成描述")],
            )
            self.assertEqual(request.call_count, 1)

    def test_description_synthesis_failure_falls_back_to_longest_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            _insert_organizer_fixture(paths)

            with (
                patch(
                    "core.graph_organizer.chat_with_tools",
                    side_effect=_merge_and_finish,
                ),
                patch(
                    "core.graph_organizer.chat",
                    side_effect=RuntimeError("gateway unavailable"),
                ),
            ):
                GraphOrganizer(root / "data", settings=settings).organize(
                    use_llm=True
                )

            graph = connect_graph(paths)
            try:
                summary = graph.execute("SELECT summary FROM nodes").fetchone()[0]
            finally:
                graph.close()
            self.assertEqual(summary, "更长的事实 B 描述")


class GlobalSearchTests(unittest.TestCase):
    def test_global_search_uses_community_context_and_top_entities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            graph = connect_graph(paths)
            try:
                graph.executemany(
                    """
                    INSERT INTO nodes (
                        node_id, keyword, summary, aliases, tags,
                        weight, ref_count
                    ) VALUES (?, ?, ?, '[]', '[]', ?, ?)
                    """,
                    [
                        ("n1", "知识图谱", "结构化知识表示", 0.9, 5),
                        ("n2", "向量检索", "语义检索", 0.8, 3),
                    ],
                )
                graph.execute(
                    """
                    INSERT INTO groups (
                        group_id, summary, node_count, edge_count
                    ) VALUES ('g1', '知识图谱与语义检索主题', 2, 1)
                    """
                )
                graph.executemany(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES ('g1', ?)",
                    [("n1",), ("n2",)],
                )
                graph.commit()
            finally:
                graph.close()
            embedding = lambda texts, **_: EmbeddingResult(  # noqa: E731
                [[1.0, 0.0, 0.0] for _ in texts],
                "test-space",
            )
            RAGEngine(
                root / "data",
                settings=settings,
                embedder=embedding,
            ).build_community_vectors("g1", "知识图谱与语义检索主题")

            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            with (
                patch("core.rag_engine.embed", side_effect=embedding),
                patch(
                    "core.knowledge_base.chat",
                    return_value="知识库主要讨论知识图谱与向量检索。",
                ) as request,
            ):
                result = service.query_global("有哪些主要主题？", top_k=3)

            self.assertEqual(result["communities"][0]["group_id"], "g1")
            self.assertEqual(result["communities"][0]["node_ids"], ["n1", "n2"])
            self.assertEqual(result["key_entities"][0]["node_id"], "n1")
            self.assertEqual(
                result["answer"],
                "知识库主要讨论知识图谱与向量检索。",
            )
            self.assertIn("知识图谱与语义检索主题", request.call_args.args[1])

    def test_global_search_without_communities_returns_guidance_without_llm(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            initialize_databases(root / "data", settings)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            with patch("core.knowledge_base.chat") as request:
                result = service.query_global("整体主题", top_k=5)

            self.assertEqual(result["communities"], [])
            self.assertEqual(result["key_entities"], [])
            self.assertIn("请先生成节点群总结", result["answer"])
            request.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
