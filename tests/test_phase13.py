"""Phase 13 搜索缓存、状态失效和三端入口回归测试。"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import start
from api import create_app
from api.deps import get_service
from core.config import AppConfig
from core.db import connect_graph, connect_sources, initialize_databases
from core.knowledge_base import KnowledgeBaseService
from core.rebuilder import _copy_search_cache_history
from core.search_cache import (
    SearchCache,
    compute_state_hash,
    make_cache_key,
    normalize_query,
)


def _settings(**overrides) -> AppConfig:
    values = {
        "search_cache_enabled": True,
        "search_cache_max_entries": 100,
        "search_cache_max_bytes": 1024 * 1024,
        "entity_extraction": {"method": "rule", "max_entities": 10},
        "models": {
            "llm": "test-llm",
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
            "rerank": "test-rerank",
        },
    }
    values.update(overrides)
    return AppConfig(**values)


def _seed_ready_source(paths) -> None:
    connection = connect_sources(paths)
    try:
        connection.execute(
            """
            INSERT INTO sources (
                source_id, original_path, relative_path, path_hash,
                content_hash, graph_hash, rag_hash, graph_status,
                rag_status, exists_status
            ) VALUES (
                's1', 'doc.md', 'doc.md', 'path-hash', 'content-v1',
                'content-v1', 'content-v1', 'ready', 'ready', 'active'
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class SearchCacheStorageTests(unittest.TestCase):
    def test_state_hash_tracks_content_not_only_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            settings = _settings()
            paths = initialize_databases(Path(temporary_dir) / "data", settings)
            _seed_ready_source(paths)
            graph = connect_graph(paths)
            try:
                graph.execute(
                    """
                    INSERT INTO nodes (
                        node_id, keyword, summary, aliases, tags, weight, ref_count
                    ) VALUES ('n1', 'Alpha', 'first', '[]', '[]', 1, 1)
                    """
                )
                graph.commit()
            finally:
                graph.close()

            before = compute_state_hash(paths, settings)
            graph = connect_graph(paths)
            try:
                graph.execute(
                    "UPDATE nodes SET summary = 'second' WHERE node_id = 'n1'"
                )
                graph.commit()
            finally:
                graph.close()
            after_summary = compute_state_hash(paths, settings)
            self.assertNotEqual(before, after_summary)

            graph = connect_graph(paths)
            try:
                graph.execute(
                    """
                    INSERT INTO groups (group_id, summary, node_count, edge_count)
                    VALUES ('g1', 'group one', 1, 0)
                    """
                )
                graph.execute(
                    "INSERT INTO group_nodes (group_id, node_id) VALUES ('g1', 'n1')"
                )
                graph.commit()
            finally:
                graph.close()
            after_group = compute_state_hash(paths, settings)
            self.assertNotEqual(after_summary, after_group)

            changed_settings = _settings(vector_search={"entity_weight": 1.2})
            self.assertNotEqual(
                after_group,
                compute_state_hash(paths, changed_settings),
            )

    def test_crud_normalization_stale_detection_and_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            settings = _settings()
            paths = initialize_databases(Path(temporary_dir) / "data", settings)
            _seed_ready_source(paths)
            cache = SearchCache(paths, settings)
            state = compute_state_hash(paths, settings)
            self.assertEqual(normalize_query("  ＡＢＣ  "), "ABC")
            key = make_cache_key(
                " ＡＢＣ ",
                state,
                {"top_k": 5},
                query_mode="rag",
            )
            cache.set(
                key,
                "ＡＢＣ",
                state,
                {"top_k": 5},
                {"query": "ABC", "results": []},
                query_mode="rag",
            )
            self.assertEqual(cache.get(key, state_hash=state)["hit_count"], 1)
            listed = cache.list()
            self.assertFalse(listed["items"][0]["is_stale"])
            self.assertEqual(cache.detail(key)["result"]["query"], "ABC")

            sources = connect_sources(paths)
            try:
                sources.execute(
                    "UPDATE sources SET content_hash = 'content-v2' WHERE source_id = 's1'"
                )
                sources.commit()
            finally:
                sources.close()
            self.assertTrue(cache.list()["items"][0]["is_stale"])
            self.assertEqual(cache.clear(stale_only=True), 1)
            self.assertEqual(cache.list()["total"], 0)

            current = compute_state_hash(paths, settings)
            for index in range(101):
                item_key = make_cache_key(
                    f"query-{index}",
                    current,
                    {},
                    query_mode="graph",
                )
                cache.set(
                    item_key,
                    f"query-{index}",
                    current,
                    {},
                    {"query": f"query-{index}"},
                    query_mode="graph",
                )
            self.assertEqual(cache.list(page_size=100)["total"], 100)

    def test_rebuild_copy_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            source_paths = initialize_databases(root / "source", settings)
            target_paths = initialize_databases(root / "target", settings)
            state = compute_state_hash(source_paths, settings)
            cache = SearchCache(source_paths, settings)
            key = make_cache_key("history", state, {}, query_mode="graph")
            cache.set(
                key,
                "history",
                state,
                {},
                {"query": "history"},
                query_mode="graph",
            )

            _copy_search_cache_history(
                source_paths.search_cache_db,
                target_paths.search_cache_db,
            )
            copied = SearchCache(target_paths, settings).detail(key)
            self.assertIsNotNone(copied)
            self.assertEqual(copied["result"]["query"], "history")


class TransparentCacheTests(unittest.TestCase):
    def test_five_modes_hit_force_params_and_state_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            paths = initialize_databases(root / "data", settings)
            _seed_ready_source(paths)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")

            graph_engine = MagicMock()
            graph_engine.query.side_effect = lambda query, **_: {
                "query": query,
                "hit_nodes": [],
            }
            rag_engine = MagicMock()
            rag_engine.query.side_effect = lambda query, **_: {
                "query": query,
                "results": [],
            }
            hybrid_engine = MagicMock()
            hybrid_engine.query.side_effect = lambda query, **_: {
                "query": query,
                "graph": {},
                "rag": {},
                "entities": [],
                "communities": [],
            }
            global_result = {
                "query": "themes",
                "answer": "answer",
                "communities": [],
                "key_entities": [],
            }
            with (
                patch("core.knowledge_base.GraphEngine", return_value=graph_engine),
                patch("core.knowledge_base.RAGEngine", return_value=rag_engine),
                patch("core.knowledge_base.HybridEngine", return_value=hybrid_engine),
                patch.object(
                    service,
                    "_query_global_uncached",
                    return_value=global_result,
                ) as global_query,
            ):
                service.query_graph("alpha")
                service.query_graph("alpha")
                service.query_rag("alpha")
                service.query_rag("alpha")
                service.query_hybrid("alpha")
                service.query_hybrid("alpha")
                service.query_answer("alpha")
                service.query_answer("alpha")
                service.query_global("themes")
                service.query_global("themes")

                self.assertEqual(graph_engine.query.call_count, 1)
                self.assertEqual(rag_engine.query.call_count, 1)
                self.assertEqual(hybrid_engine.query.call_count, 2)
                self.assertEqual(global_query.call_count, 1)

                service.query_graph("alpha", force=True)
                service.query_graph("alpha", depth=2)
                self.assertEqual(graph_engine.query.call_count, 3)

                sources = connect_sources(paths)
                try:
                    sources.execute(
                        "UPDATE sources SET content_hash = 'changed' WHERE source_id = 's1'"
                    )
                    sources.commit()
                finally:
                    sources.close()
                service.query_graph("alpha")
                self.assertEqual(graph_engine.query.call_count, 4)

    def test_answer_uses_hybrid_context_sources_relationships_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            paths = initialize_databases(root / "data", settings)
            _seed_ready_source(paths)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            hybrid_result = {
                "graph": {
                    "query": "画布是什么",
                    "hit_nodes": [
                        {
                            "node_id": "n1",
                            "keyword": "中央画布",
                            "summary": "用于呈现知识节点。",
                            "aliases": ["画布"],
                            "tags": ["界面"],
                        }
                    ],
                    "expanded_nodes": [
                        {
                            "node_id": "n2",
                            "keyword": "知识节点",
                            "summary": "图谱中的概念。",
                            "aliases": [],
                            "tags": [],
                        }
                    ],
                    "edges": [
                        {
                            "edge_id": "e1",
                            "source_node_id": "n1",
                            "relation": "显示",
                            "target_node_id": "n2",
                            "weight": 0.9,
                        }
                    ],
                    "paths": [],
                    "groups": [],
                },
                "rag": {
                    "query": "画布是什么",
                    "results": [
                        {
                            "chunk_id": "c1",
                            "content": "中央画布使用图形方式展示知识节点。",
                            "score": 0.88,
                            "source": {
                                "source_id": "s1",
                                "relative_path": "README.md",
                            },
                        }
                    ],
                },
                "entities": [],
                "communities": [],
            }
            engine = MagicMock()
            engine.query.return_value = hybrid_result
            with (
                patch("core.knowledge_base.HybridEngine", return_value=engine),
                patch("core.knowledge_base.chat", return_value="画布用于展示节点。") as llm,
            ):
                first = service.query_answer(" 画布是什么 ")
                second = service.query_answer("画布是什么")

            self.assertEqual(first, second)
            self.assertEqual(first["answer"], "画布用于展示节点。")
            self.assertEqual(first["retrieval"], hybrid_result)
            self.assertEqual(engine.query.call_count, 1)
            self.assertEqual(llm.call_count, 1)
            prompt = llm.call_args.args[1]
            self.assertIn("中央画布 →[显示]→ 知识节点", prompt)
            self.assertIn("README.md", prompt)
            cached = SearchCache(paths, settings).list()["items"]
            self.assertEqual(cached[0]["query_mode"], "answer")

    def test_same_key_is_single_flight_within_web_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            initialize_databases(root / "data", settings)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            calls = 0
            calls_lock = threading.Lock()

            def execute(query, **_):
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.05)
                return {"query": query, "hit_nodes": []}

            engine = MagicMock()
            engine.query.side_effect = execute
            results: list[dict] = []
            with patch("core.knowledge_base.GraphEngine", return_value=engine):
                threads = [
                    threading.Thread(
                        target=lambda: results.append(service.query_graph("same"))
                    )
                    for _ in range(2)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(calls, 1)
            self.assertEqual(len(results), 2)

    def test_state_change_during_query_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            paths = initialize_databases(root / "data", settings)
            _seed_ready_source(paths)
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            calls = 0

            def execute(query, **_):
                nonlocal calls
                calls += 1
                if calls == 1:
                    connection = connect_sources(paths)
                    try:
                        connection.execute(
                            "UPDATE sources SET content_hash = 'during-query' "
                            "WHERE source_id = 's1'"
                        )
                        connection.commit()
                    finally:
                        connection.close()
                return {"query": query, "hit_nodes": []}

            engine = MagicMock()
            engine.query.side_effect = execute
            with patch("core.knowledge_base.GraphEngine", return_value=engine):
                service.query_graph("moving")
                service.query_graph("moving")
            self.assertEqual(calls, 2)

    def test_document_deletion_clears_cached_result_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings()
            paths = initialize_databases(root / "data", settings)
            _seed_ready_source(paths)
            cache = SearchCache(paths, settings)
            state = compute_state_hash(paths, settings)
            key = make_cache_key("secret", state, {}, query_mode="rag")
            cache.set(
                key,
                "secret",
                state,
                {},
                {"query": "secret", "results": [{"content": "private"}]},
                query_mode="rag",
            )
            service = KnowledgeBaseService(settings=settings, data_dir=root / "data")
            with patch(
                "core.knowledge_base.Ingestor.delete_document",
                return_value={"deleted_source_id": "s1"},
            ):
                result = service.delete_document("s1")
            self.assertEqual(result["search_cache_deleted"], 1)
            self.assertEqual(cache.list()["total"], 0)


class _FakeCacheService:
    def query_graph(self, query, **kwargs):
        return {"query": query, **kwargs}

    def query_rag(self, query, **kwargs):
        return {"query": query, **kwargs}

    def query_hybrid(self, query, **kwargs):
        return {"query": query, **kwargs}

    def query_answer(self, query, **kwargs):
        return {"query": query, "answer": "answer", **kwargs}

    def query_global(self, query, **kwargs):
        return {"query": query, **kwargs}

    def list_cached_queries(self, page=1, page_size=20):
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    def get_cached_query(self, cache_key):
        return {"cache_key": cache_key, "result": {}}

    def clear_search_cache(self, stale_only=False):
        return {"deleted": 2, "stale_only": stale_only}


class EntryPointTests(unittest.TestCase):
    def test_api_force_history_detail_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            application = create_app(
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            application.dependency_overrides[get_service] = _FakeCacheService
            client = TestClient(application)

            query = client.post(
                "/api/v1/query/graph?force=true",
                json={"query": "alpha"},
            ).json()["data"]
            self.assertTrue(query["force"])
            answer = client.post(
                "/api/v1/query/answer?force=true",
                json={"query": "alpha"},
            ).json()["data"]
            self.assertTrue(answer["force"])
            listed = client.get(
                "/api/v1/search/cache?page=2&page_size=5"
            ).json()["data"]
            self.assertEqual(listed["page"], 2)
            detail = client.get("/api/v1/search/cache/cache-key").json()["data"]
            self.assertEqual(detail["cache_key"], "cache-key")
            cleared = client.delete(
                "/api/v1/search/cache?stale_only=true"
            ).json()["data"]
            self.assertEqual(cleared, {"deleted": 2, "stale_only": True})

    def test_cli_force_and_cache_commands(self) -> None:
        service = _FakeCacheService()
        commands = [
            ["query-graph", "alpha", "--force"],
            ["query-rag", "alpha", "--force"],
            ["query-hybrid", "alpha", "--force"],
            ["query-global", "alpha", "--force"],
            ["cache-list", "--page", "2", "--page-size", "5"],
            ["cache-show", "cache-key"],
            ["cache-clear", "--stale"],
        ]
        with (
            patch.object(start, "load_config", return_value=_settings()),
            patch.object(start, "KnowledgeBaseService", return_value=service),
        ):
            for command in commands:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = start.main(command)
                self.assertEqual(exit_code, 0)
                self.assertTrue(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
