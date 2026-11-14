"""Phase 4 文档扫描、增量构建和删除级联验收测试。"""

from __future__ import annotations

import yaml
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from provider.embedding import EmbeddingResult

from api import create_app
from core import library
from core.settings.schema import AppConfig
from core.storage.connections import connect_graph, connect_rag, connect_sources
from core.storage.meta import read_graph_meta
from core.ingestor import Ingestor
from core.service.facade import KnowledgeBaseService


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
            original = root / "doc.docx"
            original.write_bytes(b"original document")
            ingestor.file_map.upsert(str(original), "nested/doc.md")

            first = ingestor.scan_sources()
            self.assertEqual(len(first.new_source_ids), 1)
            source_id = first.new_source_ids[0]
            row = _source_row(ingestor, source_id)
            first_updated_at = row["updated_at"]
            self.assertEqual(row["original_path"], str(original))
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


class LibraryDeleteCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = _settings().model_copy(update={"log_dir": str(self.root / "log")})
        self.service = KnowledgeBaseService(
            settings=settings,
            data_dir=self.root / "data",
            external_dir=self.root / "external" / "markdown",
        )
        originals = library.original_root(self.service)
        self.removed_original = originals / "discarded" / "nested" / "remove.txt"
        self.removed_original.parent.mkdir(parents=True)
        self.removed_original.write_text("version-one Alpha Beta", encoding="utf-8")
        (originals / "keep.txt").write_text("version-two Alpha Gamma", encoding="utf-8")
        self.assertEqual(library.refresh_library(self.service)["errors"], [])
        self.ingestor = Ingestor(
            self.service.data_dir,
            self.service.external_dir,
            settings=settings,
            embedder=_embedding,
        )
        with patch("core.ingestor.chat_with_tools", side_effect=_versioned_tool_graph):
            result = self.ingestor.ingest()
        self.assertEqual(result["failed"], 0)
        self.removed = next(
            row for row in library.source_rows(self.service)
            if row["library_path"] == "discarded/nested/remove.txt"
        )
        self.assertEqual(_node_ref_counts(self.ingestor), {"Alpha": 2, "Beta": 1, "Gamma": 1})
        connection = connect_graph(self.ingestor.paths)
        try:
            connection.execute(
                "INSERT INTO groups (group_id, summary, node_count, edge_count) VALUES (?, ?, ?, ?)",
                ("test-group", "Alpha Beta Gamma", 3, 2),
            )
            connection.commit()
        finally:
            connection.close()
        self.ingestor._get_rag_engine().sync_community_vectors([("test-group", "Alpha Beta Gamma")])
        self.assertGreater(_table_count(self.ingestor, "rag", "entity_embeddings"), 0)
        self.assertGreater(_table_count(self.ingestor, "rag", "community_embeddings"), 0)

    def assert_removed(self) -> None:
        source_id = self.removed["source_id"]
        self.assertEqual(_source_row(self.ingestor, source_id)["exists_status"], "deleted")
        self.assertFalse(self.removed_original.exists())
        self.assertFalse((self.service.external_dir / self.removed["relative_path"]).exists())
        self.assertIsNone(self.ingestor.file_map.get_by_markdown(self.removed["relative_path"]))
        for connector, tables in (
            (connect_graph, ("node_sources", "edge_sources", "entity_mentions", "relation_mentions")),
            (connect_rag, ("chunks", "embeddings")),
        ):
            connection = connector(self.ingestor.paths)
            try:
                for table in tables:
                    self.assertEqual(connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE source_id = ?", (source_id,),
                    ).fetchone()[0], 0, table)
            finally:
                connection.close()
        self.assertEqual(_node_ref_counts(self.ingestor), {"Alpha": 1, "Gamma": 1})
        self.assertEqual(_table_count(self.ingestor, "graph", "edges"), 1)
        self.assertEqual(_table_count(self.ingestor, "rag", "chunks"), 1)
        self.assertEqual(_table_count(self.ingestor, "graph", "groups"), 0)
        self.assertEqual(_table_count(self.ingestor, "rag", "community_embeddings"), 0)
        # Read the persisted indices directly: constructing a RAGEngine here
        # could repair a stale index and hide an incomplete delete operation.
        import faiss

        for path, table in (
            (self.ingestor.paths.faiss_index, "embeddings"),
            (self.ingestor.paths.entity_faiss_index, "entity_embeddings"),
            (self.ingestor.paths.community_faiss_index, "community_embeddings"),
        ):
            index = faiss.read_index(str(path))
            self.assertEqual(
                set(map(int, faiss.vector_to_array(index.id_map))),
                _column_values(self.ingestor, "rag", table, "vector_id"),
            )

    def test_library_api_delete_clears_both_indices_and_mapping(self) -> None:
        config = self.root / "config.yaml"
        config.write_text(yaml.safe_dump(self.service.settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
        app = create_app(
            config_path=config,
            data_dir=self.service.data_dir,
            external_dir=self.service.external_dir,
        )
        with TestClient(app) as client:
            response = client.request("DELETE", "/api/v1/library/entries", json={
                "path": self.removed["library_path"],
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["sources_deleted"], 1)
        self.assert_removed()
        self.ingestor.scan_sources()
        self.assert_removed()

    def test_directory_delete_cleans_nested_sources_and_keeps_other_documents(self) -> None:
        library.delete_entry(self.service, "discarded")
        self.assert_removed()
        self.assertFalse((library.original_root(self.service) / "discarded").exists())
        remaining = library.source_rows(self.service)[0]
        self.service.delete_document(remaining["source_id"])
        self.assertEqual(self.ingestor.file_map.list(), [])
        for database, tables in (
            ("graph", ("nodes", "edges", "entity_mentions", "relation_mentions", "mention_nodes")),
            ("rag", ("chunks", "embeddings", "chunk_nodes", "entity_embeddings", "community_embeddings")),
        ):
            for table in tables:
                self.assertEqual(_table_count(self.ingestor, database, table), 0, table)
        import faiss

        for path in (
            self.ingestor.paths.faiss_index,
            self.ingestor.paths.entity_faiss_index,
            self.ingestor.paths.community_faiss_index,
        ):
            self.assertEqual(faiss.read_index(str(path)).ntotal, 0)

    def test_scan_cleans_missing_original_even_when_processed_markdown_remains(self) -> None:
        self.removed_original.unlink()
        scan = self.ingestor.scan_sources()
        self.assertIn(self.removed["source_id"], scan.newly_deleted_source_ids)
        self.assert_removed()
        self.ingestor.scan_sources()
        self.assert_removed()

    def test_scan_cleans_orphans_after_embedding_configuration_changes(self) -> None:
        from core.rag.engine import RAGEngine
        from core.rag.errors import IndexIntegrityError

        settings = self.service.settings.model_copy(update={
            "models": self.service.settings.models.model_copy(update={
                "embedding": "replacement-embedding",
                "embedding_dimensions": 4,
            }),
        })
        self.ingestor = Ingestor(
            self.service.data_dir, self.service.external_dir, settings=settings,
        )
        self.removed_original.unlink()
        scan = self.ingestor.scan_sources()
        self.assertIn(self.removed["source_id"], scan.newly_deleted_source_ids)
        self.assert_removed()
        # Deleting an old index must not relax model checks for new queries.
        with self.assertRaises(IndexIntegrityError):
            RAGEngine(self.service.data_dir, settings=settings)
        remaining = library.source_rows(self.service)[0]
        self.ingestor.delete_document(remaining["source_id"])
        self.assertEqual(RAGEngine(self.service.data_dir, settings=settings).index.count, 0)

    def test_refresh_of_removed_directory_cleans_its_mapped_documents(self) -> None:
        self.removed_original.unlink()
        self.removed_original.parent.rmdir()
        result = library.refresh_library(self.service, "discarded/nested")
        self.assertEqual(result["deleted"], [self.removed["library_path"]])
        self.assert_removed()

    def test_refresh_handles_added_and_removed_originals_together(self) -> None:
        self.removed_original.unlink()
        (library.original_root(self.service) / "new.txt").write_text("new document", encoding="utf-8")
        result = library.refresh_library(self.service)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["updated"], ["new.txt"])
        self.assertEqual(result["deleted"], [self.removed["library_path"]])
        self.assert_removed()

    def test_failed_cascade_remains_retryable_from_original_library_path(self) -> None:
        with patch.object(Ingestor, "_delete_rag_for_source", side_effect=OSError("index locked")):
            with self.assertRaisesRegex(OSError, "index locked"):
                library.delete_entry(self.service, self.removed["library_path"])
        self.assertEqual(
            _source_row(self.ingestor, self.removed["source_id"])["exists_status"], "active",
        )
        self.assertIsNotNone(self.ingestor.file_map.get_by_markdown(self.removed["relative_path"]))
        library.delete_entry(self.service, self.removed["library_path"])
        self.assert_removed()

    def test_failed_mapping_cleanup_can_retry_after_both_stores_were_cleaned(self) -> None:
        with patch.object(type(self.ingestor.file_map), "delete_by_markdown", side_effect=OSError("map locked")):
            with self.assertRaisesRegex(OSError, "map locked"):
                library.delete_entry(self.service, self.removed["library_path"])
        self.assertEqual(
            _source_row(self.ingestor, self.removed["source_id"])["exists_status"], "active",
        )
        library.delete_entry(self.service, self.removed["library_path"])
        self.assert_removed()

    def test_refresh_removes_legacy_upload_without_original_and_its_indices(self) -> None:
        self.removed_original.unlink()
        connection = connect_sources(self.ingestor.paths)
        try:
            connection.execute(
                "UPDATE sources SET library_path = NULL, original_path = ? WHERE source_id = ?",
                ("upload://remove.txt", self.removed["source_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        self.ingestor.file_map.upsert("upload://remove.txt", self.removed["relative_path"])
        result = library.refresh_library(self.service)
        self.assertIn(self.removed["relative_path"], result["deleted"])
        self.assert_removed()
        self.ingestor.scan_sources()
        self.assert_removed()

    def test_scan_cleans_legacy_source_after_its_file_mapping_was_lost(self) -> None:
        self.removed_original.unlink()
        connection = connect_sources(self.ingestor.paths)
        try:
            connection.execute(
                "UPDATE sources SET library_path = NULL, original_path = ? WHERE source_id = ?",
                ("upload://remove.txt", self.removed["source_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        self.ingestor.file_map.delete_by_markdown(self.removed["relative_path"])
        self.ingestor.scan_sources()
        self.assert_removed()


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
