"""Phase 4 文档扫描、增量构建和删除级联验收测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.embedding import EmbeddingResult

from core.config import AppConfig
from core.db import (
    connect_graph,
    connect_rag,
    connect_sources,
    read_graph_meta,
)
from core.ingestor import Ingestor


def _settings() -> AppConfig:
    return AppConfig(
        graph_build_mode="tools",
        # Preserve the historical shared-edge weight contract in these
        # cascade tests; coarse-mode weak-edge filtering has dedicated tests.
        graph_extract_granularity="medium",
        chunk_size=128,
        chunk_overlap=16,
        models={
            "embedding": "test-embedding",
            "embedding_dimensions": 3,
        },
    )


def _embedding(texts: list[str]) -> EmbeddingResult:
    vectors: list[list[float]] = []
    for text in texts:
        marker = (sum(text.encode("utf-8")) % 7 + 1) / 10
        vectors.append([marker, 1.0 - marker, 0.25])
    return EmbeddingResult(vectors, "test-space")


def _upsert_tool_entity(tool_handler, keyword: str, summary: str) -> str:
    matches = tool_handler("search_entities", {"query": keyword, "limit": 10})
    exact = next((item for item in matches if item["keyword"] == keyword), None)
    if exact is not None:
        updated = tool_handler(
            "update_entity",
            {
                "node_id": exact["node_id"],
                "summary": summary,
                "tags": ["test"],
            },
        )
        return str(updated["node_id"])
    return str(
        tool_handler(
            "add_entity",
            {
                "keyword": keyword,
                "summary": summary,
                "aliases": ["A"] if keyword == "Alpha" else [],
                "tags": ["test"],
            },
        )
    )


def _versioned_tool_graph(system, user, tools, tool_handler, **kwargs) -> str:
    del system, tools, kwargs
    second_keyword = "Gamma" if "version-two" in user else "Beta"
    alpha_id = _upsert_tool_entity(tool_handler, "Alpha", "Alpha shared concept")
    second_id = _upsert_tool_entity(
        tool_handler,
        second_keyword,
        f"{second_keyword} concept",
    )
    tool_handler(
        "add_relation",
        {
            "source_node_id": alpha_id,
            "relation": "关联",
            "target_node_id": second_id,
            "evidence_weight": 0.8,
        },
    )
    tool_handler("finish", {})
    return "done"


class SourceScanTests(unittest.TestCase):
    def test_scan_tracks_new_unchanged_changed_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "nested" / "doc.md"
            document.parent.mkdir()
            document.write_text("version-one", encoding="utf-8")
            ingestor = Ingestor(root / "data", external, settings=_settings())
            ingestor.file_map.upsert("C:/original/doc.docx", "nested/doc.md")

            first = ingestor.scan_sources()
            self.assertEqual(len(first.new_source_ids), 1)
            source_id = first.new_source_ids[0]
            row = _source_row(ingestor, source_id)
            first_updated_at = row["updated_at"]
            self.assertEqual(row["original_path"], "C:/original/doc.docx")
            self.assertEqual(row["graph_status"], "pending")
            self.assertEqual(row["rag_status"], "pending")

            second = ingestor.scan_sources()
            self.assertEqual(second.unchanged_source_ids, [source_id])
            self.assertEqual(
                _source_row(ingestor, source_id)["updated_at"], first_updated_at
            )

            document.write_text("version-two", encoding="utf-8")
            third = ingestor.scan_sources()
            self.assertEqual(third.changed_source_ids, [source_id])
            self.assertEqual(
                _source_row(ingestor, source_id)["graph_status"], "pending"
            )

            document.unlink()
            fourth = ingestor.scan_sources()
            self.assertEqual(fourth.newly_deleted_source_ids, [source_id])
            self.assertEqual(
                _source_row(ingestor, source_id)["exists_status"], "deleted"
            )


class IncrementalIngestTests(unittest.TestCase):
    @patch("core.ingestor.chat_with_tools")
    def test_first_build_unchanged_skip_and_atomic_incremental_replace(
        self, mock_chat
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("version-one Alpha Beta", encoding="utf-8")
            graph_calls: list[str] = []
            embedding_calls: list[list[str]] = []

            def graph_builder(system, user, tools, tool_handler, **kwargs):
                graph_calls.append(user)
                return _versioned_tool_graph(
                    system, user, tools, tool_handler, **kwargs
                )

            mock_chat.side_effect = graph_builder

            def embedder(texts: list[str]) -> EmbeddingResult:
                embedding_calls.append(texts.copy())
                return _embedding(texts)

            ingestor = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=embedder,
            )
            first = ingestor.ingest(mode="both")

            self.assertEqual(first["graph_updated"], 1)
            self.assertEqual(first["rag_updated"], 1)
            source = _only_source(ingestor)
            self.assertEqual(source["graph_hash"], source["content_hash"])
            self.assertEqual(source["rag_hash"], source["content_hash"])
            self.assertEqual(source["graph_status"], "ready")
            self.assertEqual(source["rag_status"], "ready")
            original_nodes = _nodes_by_keyword(ingestor)
            self.assertEqual(set(original_nodes), {"Alpha", "Beta"})
            self.assertEqual(_table_count(ingestor, "graph", "edges"), 1)
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 1)
            self.assertEqual(_table_count(ingestor, "rag", "chunk_nodes"), 2)
            original_chunk_ids = _column_values(ingestor, "rag", "chunks", "chunk_id")
            original_vector_ids = _column_values(
                ingestor, "rag", "embeddings", "vector_id"
            )
            self.assertEqual(
                _column_values(ingestor, "rag", "embeddings", "vector_space_id"),
                {"test-space"},
            )
            self.assertEqual(ingestor._get_rag_engine().index.ids, original_vector_ids)

            calls_before_skip = (len(graph_calls), len(embedding_calls))
            skipped = ingestor.ingest(mode="both")
            self.assertEqual(skipped["processed"], 0)
            self.assertEqual(
                (len(graph_calls), len(embedding_calls)), calls_before_skip
            )

            ingestor.paths.rerank_cache.write_text("stale-cache\n", encoding="utf-8")
            document.write_text("version-two Alpha Gamma", encoding="utf-8")
            second = ingestor.ingest(mode="both")

            self.assertEqual(second["graph_updated"], 1)
            self.assertEqual(second["rag_updated"], 1)
            updated_source = _only_source(ingestor)
            self.assertEqual(updated_source["source_id"], source["source_id"])
            updated_nodes = _nodes_by_keyword(ingestor)
            self.assertEqual(set(updated_nodes), {"Alpha", "Gamma"})
            self.assertNotEqual(updated_nodes["Alpha"], original_nodes["Alpha"])
            self.assertFalse(
                original_chunk_ids.intersection(
                    _column_values(ingestor, "rag", "chunks", "chunk_id")
                )
            )
            current_vector_ids = _column_values(
                ingestor, "rag", "embeddings", "vector_id"
            )
            self.assertFalse(original_vector_ids.intersection(current_vector_ids))
            self.assertEqual(ingestor._get_rag_engine().index.ids, current_vector_ids)
            self.assertEqual(
                ingestor.paths.rerank_cache.read_text(encoding="utf-8"), ""
            )
            self.assertEqual(
                read_graph_meta(ingestor.paths)["changed_since_summary"], 2
            )

    @patch("core.ingestor.chat_with_tools", side_effect=_versioned_tool_graph)
    def test_graph_and_rag_modes_fill_chunk_nodes_when_both_are_current(
        self, _mock_chat
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "doc.md").write_text("version-one", encoding="utf-8")
            ingestor = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=_embedding,
            )

            graph_result = ingestor.ingest(mode="graph")
            self.assertEqual(graph_result["graph_updated"], 1)
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 0)
            self.assertEqual(_only_source(ingestor)["rag_status"], "pending")

            rag_result = ingestor.ingest(mode="rag")
            self.assertEqual(rag_result["rag_updated"], 1)
            self.assertEqual(_table_count(ingestor, "rag", "chunk_nodes"), 2)
            source = _only_source(ingestor)
            self.assertEqual(source["graph_hash"], source["rag_hash"])

    def test_preparation_failure_preserves_old_graph_rag_and_faiss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("version-one", encoding="utf-8")
            initial = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=_embedding,
            )
            with patch(
                "core.ingestor.chat_with_tools", side_effect=_versioned_tool_graph
            ):
                initial.ingest(mode="both")
            old_source = _only_source(initial)
            old_nodes = _nodes_by_keyword(initial)
            old_chunks = _column_values(initial, "rag", "chunks", "chunk_id")
            old_vectors = _column_values(initial, "rag", "embeddings", "vector_id")

            document.write_text("version-two", encoding="utf-8")

            def fail_embedding(texts: list[str]) -> EmbeddingResult:
                raise RuntimeError("embedding preparation failed")

            failing = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=fail_embedding,
            )
            with patch(
                "core.ingestor.chat_with_tools",
                side_effect=RuntimeError("graph preparation failed"),
            ):
                result = failing.ingest(mode="both")

            self.assertEqual(result["failed"], 1)
            source = _only_source(failing)
            self.assertEqual(source["graph_status"], "failed")
            self.assertEqual(source["rag_status"], "failed")
            self.assertEqual(source["graph_hash"], old_source["graph_hash"])
            self.assertEqual(source["rag_hash"], old_source["rag_hash"])
            self.assertEqual(_nodes_by_keyword(failing), old_nodes)
            self.assertEqual(
                _column_values(failing, "rag", "chunks", "chunk_id"), old_chunks
            )
            self.assertEqual(
                _column_values(failing, "rag", "embeddings", "vector_id"), old_vectors
            )
            self.assertEqual(failing._get_rag_engine().index.ids, old_vectors)

    @patch("core.ingestor.chat_with_tools", side_effect=_versioned_tool_graph)
    def test_faiss_replace_failure_recovers_from_committed_rag_database(
        self, _mock_chat
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("version-one", encoding="utf-8")
            ingestor = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=_embedding,
            )
            ingestor.ingest(mode="both")
            old_vector_ids = _column_values(ingestor, "rag", "embeddings", "vector_id")
            rag_engine = ingestor._get_rag_engine()

            document.write_text("version-two", encoding="utf-8")
            with patch.object(
                rag_engine.index,
                "replace",
                side_effect=RuntimeError("transient FAISS replace failure"),
            ):
                result = ingestor.ingest(mode="rag")

            new_vector_ids = _column_values(ingestor, "rag", "embeddings", "vector_id")
            self.assertEqual(result["rag_updated"], 1)
            self.assertFalse(old_vector_ids.intersection(new_vector_ids))
            self.assertEqual(rag_engine.index.ids, new_vector_ids)
            source = _only_source(ingestor)
            self.assertEqual(source["rag_status"], "ready")
            self.assertEqual(source["rag_hash"], source["content_hash"])


class DeleteCascadeTests(unittest.TestCase):
    @patch("core.ingestor.chat_with_tools")
    def test_delete_recalculates_shared_references_and_removes_last_source(
        self, mock_chat
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            high = external / "high.md"
            low = external / "low.md"
            high.write_text("shared high", encoding="utf-8")
            low.write_text("shared low", encoding="utf-8")

            def shared_tool_graph(system, user, tools, tool_handler, **kwargs):
                del system, tools, kwargs
                weight = 0.9 if "high" in user else 0.5
                source_id = _upsert_tool_entity(tool_handler, "SharedA", "Shared A")
                target_id = _upsert_tool_entity(tool_handler, "SharedB", "Shared B")
                tool_handler(
                    "add_relation",
                    {
                        "source_node_id": source_id,
                        "relation": "关联",
                        "target_node_id": target_id,
                        "evidence_weight": weight,
                    },
                )
                tool_handler("finish", {})
                return "done"

            mock_chat.side_effect = shared_tool_graph

            ingestor = Ingestor(
                root / "data",
                external,
                settings=_settings(),
                embedder=_embedding,
            )
            ingestor.ingest(mode="both")
            self.assertEqual(_node_ref_counts(ingestor), {"SharedA": 2, "SharedB": 2})
            self.assertEqual(_edge_stats(ingestor), (2, 0.9))
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 2)

            high.unlink()
            first_delete = ingestor.ingest(mode="both")
            self.assertEqual(first_delete["failed"], 0)
            self.assertEqual(_node_ref_counts(ingestor), {"SharedA": 1, "SharedB": 1})
            self.assertEqual(_edge_stats(ingestor), (1, 0.5))
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 1)
            remaining_vectors = _column_values(
                ingestor, "rag", "embeddings", "vector_id"
            )
            self.assertEqual(ingestor._get_rag_engine().index.ids, remaining_vectors)

            low.unlink()
            second_delete = ingestor.ingest(mode="both")
            self.assertEqual(second_delete["failed"], 0)
            self.assertEqual(_table_count(ingestor, "graph", "nodes"), 0)
            self.assertEqual(_table_count(ingestor, "graph", "edges"), 0)
            self.assertEqual(_table_count(ingestor, "rag", "chunks"), 0)
            self.assertEqual(_table_count(ingestor, "rag", "embeddings"), 0)
            self.assertEqual(ingestor._get_rag_engine().index.count, 0)
            self.assertEqual(_deleted_source_count(ingestor), 2)


def _source_row(ingestor: Ingestor, source_id: str):
    connection = connect_sources(ingestor.paths)
    try:
        return connection.execute(
            "SELECT * FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    finally:
        connection.close()


def _only_source(ingestor: Ingestor):
    connection = connect_sources(ingestor.paths)
    try:
        rows = connection.execute("SELECT * FROM sources").fetchall()
        self_contained = rows[0]
        if len(rows) != 1:
            raise AssertionError(f"expected one source, got {len(rows)}")
        return self_contained
    finally:
        connection.close()


def _table_count(ingestor: Ingestor, database: str, table: str) -> int:
    connector = connect_graph if database == "graph" else connect_rag
    connection = connector(ingestor.paths)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def _column_values(
    ingestor: Ingestor,
    database: str,
    table: str,
    column: str,
) -> set:
    connector = connect_graph if database == "graph" else connect_rag
    connection = connector(ingestor.paths)
    try:
        rows = connection.execute(f"SELECT {column} FROM {table}").fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


def _nodes_by_keyword(ingestor: Ingestor) -> dict[str, str]:
    connection = connect_graph(ingestor.paths)
    try:
        rows = connection.execute("SELECT keyword, node_id FROM nodes").fetchall()
        return {row["keyword"]: row["node_id"] for row in rows}
    finally:
        connection.close()


def _node_ref_counts(ingestor: Ingestor) -> dict[str, int]:
    connection = connect_graph(ingestor.paths)
    try:
        rows = connection.execute("SELECT keyword, ref_count FROM nodes").fetchall()
        return {row["keyword"]: int(row["ref_count"]) for row in rows}
    finally:
        connection.close()


def _edge_stats(ingestor: Ingestor) -> tuple[int, float]:
    connection = connect_graph(ingestor.paths)
    try:
        row = connection.execute("SELECT support_count, weight FROM edges").fetchone()
        return int(row["support_count"]), float(row["weight"])
    finally:
        connection.close()


def _deleted_source_count(ingestor: Ingestor) -> int:
    connection = connect_sources(ingestor.paths)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM sources WHERE exists_status = 'deleted'"
            ).fetchone()[0]
        )
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
