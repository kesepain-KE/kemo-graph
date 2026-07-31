"""Phase 3B 图谱检索与 Phase 3C 混合检索验收测试。"""

from __future__ import annotations

import json
import tempfile
import unittest

import numpy as np

from provider.embedding import EmbeddingResult

from core.config import AppConfig
from core.db import connect_graph, connect_rag, connect_sources, initialize_databases
from core.entity_extractor import Entity, EntityExtractionError, extract
from core.graph_engine import GraphEngine, GraphQueryError
from core.hybrid import HybridEngine
from core.rag_engine import RAGEngine


def _settings(*, method: str = "rule", dimensions: int = 3) -> AppConfig:
    return AppConfig(
        entity_extraction={"method": method, "max_entities": 10},
        hybrid_enhancement_factor=2.0,
        rerank_top_n=2,
        models={
            "embedding": "test-embedding",
            "embedding_dimensions": dimensions,
        },
    )


class EntityExtractorTests(unittest.TestCase):
    def test_rule_mode_extracts_chinese_and_english_concepts(self) -> None:
        settings = _settings()
        entities = extract(
            ["知识图谱和向量检索的关系", "knowledge graph vs RAG"],
            settings=settings,
        )

        self.assertEqual(
            [entity.normalized for entity in entities],
            ["知识图谱", "向量检索", "knowledge graph", "RAG"],
        )
        self.assertTrue(all(entity.confidence == 1.0 for entity in entities))

    def test_llm_mode_parses_fenced_json_and_deduplicates(self) -> None:
        response = """```json
        {"entities":[
          {"text":"KG","type":"concept","normalized":"知识图谱","aliases":["KG"],"confidence":0.8},
          {"text":"知识图谱","type":"concept","normalized":"知识图谱","aliases":["knowledge graph"],"confidence":0.95}
        ]}
        ```"""
        calls: list[tuple[str, str]] = []

        def fake_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return response

        entities = extract(["什么是 KG"], settings=_settings(method="llm"), llm=fake_llm)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].normalized, "知识图谱")
        self.assertEqual(entities[0].confidence, 0.95)
        self.assertEqual(entities[0].aliases, ["KG", "knowledge graph"])

    def test_llm_mode_rejects_non_json_response(self) -> None:
        with self.assertRaises(EntityExtractionError):
            extract(
                ["query"],
                settings=_settings(method="llm"),
                llm=lambda system, user: "not-json",
            )


class GraphEngineTests(unittest.TestCase):
    def test_alias_match_bidirectional_bfs_and_group_summary(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_graph_fixture(paths)
            engine = GraphEngine(
                temporary_dir,
                settings=settings,
                entity_extractor=lambda chunks: [
                    Entity("KG", "concept", "KG", [], 1.0)
                ],
            )

            result = engine.query("KG", depth=2, direction="both", confidence=0.7)

            self.assertEqual(result["hit_nodes"][0]["node_id"], "node-kg")
            self.assertEqual(result["hit_nodes"][0]["match_score"], 0.9)
            expanded = {
                node["node_id"]: (node["depth"], node["direction"])
                for node in result["expanded_nodes"]
            }
            self.assertEqual(expanded["node-vector"], (1, "forward"))
            self.assertEqual(expanded["node-structure"], (1, "backward"))
            self.assertEqual(expanded["node-embedding"], (2, "forward"))
            self.assertEqual(
                {edge["edge_id"] for edge in result["edges"]},
                {"edge-complement", "edge-uses", "edge-supports"},
            )
            self.assertEqual(
                result["groups"],
                [{"group_id": "group-ai", "summary": "AI 检索技术群"}],
            )

    def test_direction_depth_fuzzy_match_and_parameter_limits(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_graph_fixture(paths)
            engine = GraphEngine(
                temporary_dir,
                settings=settings,
                entity_extractor=lambda chunks: [
                    Entity("知识图谱", "concept", "知识图谱", [], 1.0)
                ],
            )

            result = engine.query("知识图谱", depth=1, direction="forward")
            self.assertEqual(
                [node["node_id"] for node in result["expanded_nodes"]],
                ["node-vector"],
            )
            fuzzy = engine.match_nodes(
                [Entity("知识图普", "concept", "知识图普", [], 1.0)],
                0.7,
            )
            self.assertEqual(fuzzy[0]["node_id"], "node-kg")
            with self.assertRaises(GraphQueryError):
                engine.query("知识图谱", depth=settings.max_query_depth + 1)

    def test_short_keyword_matches_containing_node_names(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            connection = connect_graph(paths)
            try:
                connection.executemany(
                    """
                    INSERT INTO nodes (node_id, keyword, summary, aliases, tags)
                    VALUES (?, ?, ?, '[]', '[]')
                    """,
                    [
                        ("node-center-canvas", "中央画布", "中央知识图谱画布"),
                        ("node-svg-canvas", "SVG画布", "SVG 图谱画布"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            engine = GraphEngine(
                temporary_dir,
                settings=settings,
                entity_extractor=lambda chunks: [
                    Entity("画布", "concept", "画布", [], 0.95)
                ],
            )
            result = engine.query("画布", depth=1, confidence=0.7)

            self.assertEqual(
                {node["keyword"] for node in result["hit_nodes"]},
                {"中央画布", "SVG画布"},
            )
            self.assertTrue(
                all(node["match_score"] > 0.7 for node in result["hit_nodes"])
            )

            one_character = engine.match_nodes(
                [Entity("布", "concept", "布", [], 1.0)],
                0.7,
            )
            self.assertEqual(one_character, [])


class HybridEngineTests(unittest.TestCase):
    def test_graph_anchor_boosts_rag_candidate_before_rerank(self) -> None:
        settings = _settings()
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, settings)
            _insert_hybrid_fixture(paths, settings)
            graph_engine = GraphEngine(
                temporary_dir,
                settings=settings,
                entity_extractor=lambda chunks: [
                    Entity("知识图谱", "concept", "知识图谱", [], 1.0)
                ],
            )
            rerank_document_orders: list[list[str]] = []

            def fake_reranker(
                query: str, documents: list[str], top_n: int
            ) -> list[tuple[int, float]]:
                rerank_document_orders.append(documents.copy())
                return [
                    (index, 0.9 - index * 0.1)
                    for index in range(min(top_n, len(documents)))
                ]

            rag_engine = RAGEngine(
                temporary_dir,
                settings=settings,
                embedder=lambda chunks: EmbeddingResult(
                    [[1.0, 0.0, 0.0] for _ in chunks],
                    "test-space",
                ),
                reranker=fake_reranker,
            )
            engine = HybridEngine(
                temporary_dir,
                settings=settings,
                graph_engine=graph_engine,
                rag_engine=rag_engine,
            )

            result = engine.query(
                "知识图谱",
                graph_depth=1,
                rag_top_k=2,
                rag_threshold=0.0,
            )

            self.assertEqual(engine.get_anchored_chunk_ids({"node-kg"}), {"chunk-anchor"})
            self.assertEqual(rerank_document_orders[0][0], "anchored document")
            self.assertEqual(result["graph"]["hit_nodes"][0]["node_id"], "node-kg")
            self.assertEqual(result["rag"]["results"][0]["chunk_id"], "chunk-anchor")
            self.assertEqual(set(result), {"graph", "rag"})


def _insert_graph_fixture(paths) -> None:
    nodes = [
        ("node-kg", "知识图谱", "结构化知识表示", ["KG"], ["AI"]),
        ("node-vector", "向量检索", "语义向量检索", [], ["AI"]),
        ("node-embedding", "Embedding", "文本向量表示", ["嵌入"], ["AI"]),
        ("node-structure", "数据结构", "组织数据的方式", [], ["计算机"]),
    ]
    edges = [
        ("edge-complement", "node-kg", "互补", "node-vector", 0.9, 2),
        ("edge-uses", "node-vector", "使用", "node-embedding", 0.8, 1),
        ("edge-supports", "node-structure", "支持", "node-kg", 0.7, 1),
    ]
    connection = connect_graph(paths)
    try:
        for node_id, keyword, summary, aliases, tags in nodes:
            connection.execute(
                """
                INSERT INTO nodes (node_id, keyword, summary, aliases, tags)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    keyword,
                    summary,
                    json.dumps(aliases, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
        connection.executemany(
            """
            INSERT INTO edges (
                edge_id, source_node_id, relation, target_node_id,
                weight, support_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            edges,
        )
        connection.execute(
            """
            INSERT INTO groups (group_id, summary, node_count, edge_count)
            VALUES ('group-ai', 'AI 检索技术群', 4, 3)
            """
        )
        connection.executemany(
            "INSERT INTO group_nodes (group_id, node_id) VALUES ('group-ai', ?)",
            [(node[0],) for node in nodes],
        )
        connection.commit()
    finally:
        connection.close()


def _insert_hybrid_fixture(paths, settings: AppConfig) -> None:
    graph_connection = connect_graph(paths)
    try:
        graph_connection.execute(
            """
            INSERT INTO nodes (node_id, keyword, summary, aliases, tags)
            VALUES ('node-kg', '知识图谱', '结构化知识表示', '[]', '[]')
            """
        )
        graph_connection.commit()
    finally:
        graph_connection.close()

    sources_connection = connect_sources(paths)
    try:
        sources_connection.execute(
            """
            INSERT INTO sources (
                source_id, original_path, relative_path, path_hash, content_hash
            ) VALUES ('source-1', 'C:/docs/a.md', 'a.md', 'path-a', 'content-a')
            """
        )
        sources_connection.commit()
    finally:
        sources_connection.close()

    chunks = [
        (1, "chunk-anchor", "anchored document", [0.6, 0.0, 0.0]),
        (2, "chunk-plain", "plain document", [0.9, 0.0, 0.0]),
    ]
    rag_connection = connect_rag(paths)
    try:
        for vector_id, chunk_id, content, vector in chunks:
            rag_connection.execute(
                """
                INSERT INTO chunks (chunk_id, source_id, content, chunk_index, token_count)
                VALUES (?, 'source-1', ?, ?, 2)
                """,
                (chunk_id, content, vector_id - 1),
            )
            rag_connection.execute(
                """
                INSERT INTO embeddings (
                    vector_id, chunk_id, source_id, vector_blob,
                    dimensions, model_name, vector_space_id
                ) VALUES (?, ?, 'source-1', ?, ?, ?, ?)
                """,
                (
                    vector_id,
                    chunk_id,
                    np.asarray(vector, dtype=np.float32).tobytes(),
                    settings.models.embedding_dimensions,
                    settings.models.embedding,
                    "test-space",
                ),
            )
        rag_connection.execute(
            """
            INSERT INTO chunk_nodes (chunk_id, node_id)
            VALUES ('chunk-anchor', 'node-kg')
            """
        )
        rag_connection.commit()
    finally:
        rag_connection.close()


if __name__ == "__main__":
    unittest.main()
