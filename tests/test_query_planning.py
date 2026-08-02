"""查询规划、多路召回和安全降级回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core.config import AppConfig
from core.chunker import document_chunks
from core.db import connect_rag, connect_sources, initialize_databases
from core.query_planner import filter_semantic_drift, plan_query
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
        query_planning={
            "mode": "auto",
            "max_rewrites": 4,
            "max_subqueries": 3,
            "max_total_queries": 6,
            "semantic_drift_threshold": 0.58,
            "candidate_pool_size": 5,
            "rrf_k": 60,
            "low_confidence_rescue_count": 2,
        },
    )


def _plan_payload() -> dict:
    return {
        "intent": "查找阅读相关内容",
        "rewrites": [
            {"text": "阅读", "type": "synonym"},
            {"text": "阅读理解", "type": "related"},
        ],
        "subqueries": [],
        "entities": [
            {
                "text": "看书",
                "normalized": "阅读",
                "aliases": ["看书"],
                "type": "concept",
                "confidence": 0.95,
            }
        ],
    }


class QueryPlannerTests(unittest.TestCase):
    def test_original_is_kept_and_duplicates_are_removed(self) -> None:
        settings = _settings(Path("."))
        payload = _plan_payload()
        payload["rewrites"].append({"text": "看书", "type": "paraphrase"})
        plan = plan_query(
            " 看书 ",
            settings=settings,
            structured_planner=lambda system, user, schema: payload,
        )

        self.assertEqual(plan.texts, ["看书", "阅读", "阅读理解"])
        self.assertEqual(plan.weights, [1.0, 0.85, 0.65])
        self.assertEqual(plan.entities[0].normalized, "阅读")

    def test_failure_falls_back_to_original_query(self) -> None:
        settings = _settings(Path("."))

        def fail(system, user, schema):
            raise TimeoutError("gateway timeout")

        plan = plan_query("看书", settings=settings, structured_planner=fail)

        self.assertEqual(plan.texts, ["看书"])
        self.assertTrue(plan.degraded)

    def test_embedding_guard_discards_semantic_drift(self) -> None:
        settings = _settings(Path("."))
        plan = plan_query(
            "看书",
            settings=settings,
            structured_planner=lambda system, user, schema: _plan_payload(),
        )
        filtered, indexes = filter_semantic_drift(
            plan,
            [
                [1.0, 0.0, 0.0],
                [0.8, 0.6, 0.0],
                [0.0, 1.0, 0.0],
            ],
            0.58,
        )

        self.assertEqual(filtered.texts, ["看书", "阅读"])
        self.assertEqual(indexes, [0, 1])

    def test_semantic_hierarchical_builds_parent_context(self) -> None:
        settings = _settings(Path("."))
        settings.chunking_mode = "semantic_hierarchical"
        settings.chunk_small_size = 64
        settings.chunk_size = 128
        settings.chunk_large_size = 256
        text = "甲" * 50 + "\n\n" + "乙" * 50 + "\n\n" + "丙" * 50
        boundaries = {
            "chunks": [
                {"start_block": 1, "end_block": 1},
                {"start_block": 2, "end_block": 2},
                {"start_block": 3, "end_block": 3},
            ]
        }

        with patch("core.chunker.chat_structured", return_value=boundaries):
            chunks = document_chunks(text, settings=settings)

        self.assertEqual(
            {chunk.granularity for chunk in chunks},
            {"small", "medium", "large"},
        )
        small_chunks = [chunk for chunk in chunks if chunk.granularity == "small"]
        self.assertEqual(len(small_chunks), 3)
        self.assertTrue(any(chunk.parent_index is not None for chunk in small_chunks))


class MultiQueryRetrievalTests(unittest.TestCase):
    def test_expansion_recalls_synonym_and_batches_embedding_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root)
            paths = initialize_databases(root / "data", settings)
            sources = connect_sources(paths)
            try:
                sources.execute(
                    """
                    INSERT INTO sources (
                        source_id, original_path, relative_path, path_hash, content_hash
                    ) VALUES ('source-1', 'C:/notes.md', 'notes.md', 'path', 'content')
                    """
                )
                sources.commit()
            finally:
                sources.close()

            records = [
                ("unrelated-1", "一般知识一", [1.0, 0.0, 0.0]),
                ("unrelated-2", "一般知识二", [0.99, 0.01, 0.0]),
                ("unrelated-3", "一般知识三", [0.98, 0.02, 0.0]),
                ("reading", "阅读可以帮助形成阅读理解。", [0.0, 1.0, 0.0]),
            ]
            rag = connect_rag(paths)
            try:
                for vector_id, (chunk_id, content, vector) in enumerate(records, start=1):
                    rag.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, source_id, content, chunk_index, token_count
                        ) VALUES (?, 'source-1', ?, ?, 5)
                        """,
                        (chunk_id, content, vector_id - 1),
                    )
                    rag.execute(
                        """
                        INSERT INTO embeddings (
                            vector_id, chunk_id, source_id, vector_blob,
                            dimensions, model_name, vector_space_id
                        ) VALUES (?, ?, 'source-1', ?, 3, 'test-embedding', 'test-space')
                        """,
                        (
                            vector_id,
                            chunk_id,
                            np.asarray(vector, dtype=np.float32).tobytes(),
                        ),
                    )
                rag.commit()
            finally:
                rag.close()

            embedding_calls: list[list[str]] = []

            def embedder(texts: list[str]) -> EmbeddingResult:
                embedding_calls.append(texts.copy())
                values = {
                    "看书": [1.0, 0.0, 0.0],
                    "阅读": [0.8, 0.6, 0.0],
                    "阅读理解": [0.0, 1.0, 0.0],
                }
                return EmbeddingResult([values[text] for text in texts], "test-space")

            engine = RAGEngine(
                root / "data",
                settings=settings,
                embedder=embedder,
                reranker=lambda query, documents, top_n: [
                    (index, 0.05) for index in range(min(top_n, len(documents)))
                ],
            )
            with patch("core.query_planner.chat_structured", return_value=_plan_payload()):
                result = engine.query("看书", top_k=2, threshold=0.6)

            self.assertEqual(embedding_calls, [["看书", "阅读", "阅读理解"]])
            self.assertEqual(result["results"][0]["chunk_id"], "reading")
            self.assertGreaterEqual(result["results"][0]["score"], 0.6)


if __name__ == "__main__":
    unittest.main()
