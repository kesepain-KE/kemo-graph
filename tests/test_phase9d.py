"""Phase 9D 工具式图谱构建与向量空间迁移验收测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from provider.embedding import EmbeddingResult

from core.chunker import chunking_signature
from core.config import AppConfig
from core.db import (
    connect_graph,
    connect_rag,
    connect_sources,
    initialize_databases,
    read_rag_meta,
    write_rag_meta,
)
from core.ingestor import Ingestor
from core.rag_engine import IndexIntegrityError, RAGEngine
from provider.tools import get_tool_schemas


def _settings(root: Path, dimensions: int = 3) -> AppConfig:
    return AppConfig(
        chunk_size=128,
        chunk_overlap=16,
        graph_build_mode="tools",
        log_dir=str(root / "log"),
        models={
            "embedding": "test-embedding",
            "embedding_dimensions": dimensions,
        },
    )


class ToolGraphBuildTests(unittest.TestCase):
    def test_tool_loop_commits_as_one_transaction_and_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("Alpha relates Beta", encoding="utf-8")
            ingestor = Ingestor(
                root / "data",
                external,
                settings=_settings(root),
            )
            captured: dict[str, object] = {}

            def build_graph(system, user, tools, tool_handler, **kwargs):
                captured["system"] = system
                captured["schemas"] = [tool["name"] for tool in tools]
                self.assertEqual(
                    tool_handler("search_entities", {"query": "Alpha"}), []
                )
                alpha = tool_handler(
                    "add_entity",
                    {
                        "keyword": "Alpha",
                        "summary": "Alpha concept",
                        "aliases": [],
                        "tags": ["test"],
                    },
                )
                beta = tool_handler(
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
                        "source_node_id": alpha,
                        "relation": "关联",
                        "target_node_id": beta,
                        "evidence_weight": 0.9,
                    },
                )
                self.assertEqual(tool_handler("finish", {}), {"finished": True})
                return "finished"

            with patch("core.ingestor.chat_with_tools", side_effect=build_graph):
                first = ingestor.ingest(mode="graph")
            self.assertEqual(first["graph_updated"], 1)
            self.assertIn("doc.md", str(captured["system"]))
            self.assertIn("source_id", str(captured["system"]))
            self.assertEqual(captured["schemas"][-1], "finish")
            self.assertEqual(
                [schema["name"] for schema in get_tool_schemas("graph")][-1],
                "finish",
            )
            for schema in get_tool_schemas("graph"):
                self.assertEqual(schema["type"], "function")
                self.assertIs(schema["strict"], True)
                self.assertNotIn("function", schema)
                self.assertEqual(schema["parameters"]["type"], "object")
            self.assertEqual(self._keywords(ingestor), {"Alpha", "Beta"})

            document.write_text("Gamma should roll back", encoding="utf-8")

            def fail_after_write(system, user, tools, tool_handler, **kwargs):
                tool_handler(
                    "add_entity",
                    {
                        "keyword": "Gamma",
                        "summary": "Temporary concept",
                        "aliases": [],
                        "tags": [],
                    },
                )
                raise RuntimeError("simulated gateway failure")

            with patch("core.ingestor.chat_with_tools", side_effect=fail_after_write):
                failed = ingestor.ingest(mode="graph")
            self.assertEqual(failed["failed"], 1)
            self.assertEqual(self._keywords(ingestor), {"Alpha", "Beta"})
            self.assertTrue(any((root / "log").glob("*.tsv")))

    def test_update_entity_binds_existing_node_to_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "existing.md").write_text("Shared", encoding="utf-8")
            (external / "new.md").write_text("Shared details", encoding="utf-8")
            ingestor = Ingestor(root / "data", external, settings=_settings(root))
            ingestor.scan_sources()

            source_connection = connect_sources(ingestor.paths)
            try:
                rows = {
                    row["relative_path"]: row
                    for row in source_connection.execute("SELECT * FROM sources")
                }
                existing_source = rows["existing.md"]
                source_connection.execute(
                    """
                    UPDATE sources SET graph_status = 'ready', graph_hash = content_hash
                    WHERE source_id = ?
                    """,
                    (existing_source["source_id"],),
                )
                source_connection.commit()
            finally:
                source_connection.close()

            now = datetime.now(timezone.utc).isoformat()
            graph_connection = connect_graph(ingestor.paths)
            try:
                graph_connection.execute(
                    """
                    INSERT INTO nodes (
                        node_id, keyword, summary, aliases, tags,
                        weight, ref_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("shared", "Shared", "old", "[]", "[]", 1.0, 1, now, now),
                )
                graph_connection.execute(
                    """
                    INSERT INTO node_sources (
                        node_id, source_id, content_hash, evidence_weight
                    ) VALUES (?, ?, ?, 1.0)
                    """,
                    (
                        "shared",
                        existing_source["source_id"],
                        existing_source["content_hash"],
                    ),
                )
                graph_connection.commit()
            finally:
                graph_connection.close()

            def update_existing(system, user, tools, tool_handler, **kwargs):
                found = tool_handler("search_entities", {"query": "Shared"})
                self.assertEqual(found[0]["node_id"], "shared")
                updated = tool_handler(
                    "update_entity",
                    {"node_id": "shared", "summary": "new details"},
                )
                self.assertEqual(updated["ref_count"], 2)
                tool_handler("finish", {})
                return "finished"

            with patch("core.ingestor.chat_with_tools", side_effect=update_existing):
                result = ingestor.ingest(mode="graph")
            self.assertEqual(result["graph_updated"], 1)
            graph_connection = connect_graph(ingestor.paths)
            try:
                row = graph_connection.execute(
                    "SELECT ref_count, summary FROM nodes WHERE node_id = 'shared'"
                ).fetchone()
                bindings = graph_connection.execute(
                    "SELECT COUNT(*) FROM node_sources WHERE node_id = 'shared'"
                ).fetchone()[0]
            finally:
                graph_connection.close()
            self.assertEqual(
                (int(row["ref_count"]), row["summary"]), (2, "new details")
            )
            self.assertEqual(bindings, 2)

    @staticmethod
    def _keywords(ingestor: Ingestor) -> set[str]:
        connection = connect_graph(ingestor.paths)
        try:
            return {
                str(row["keyword"])
                for row in connection.execute("SELECT keyword FROM nodes")
            }
        finally:
            connection.close()


class VectorSpaceTests(unittest.TestCase):
    def test_chunking_config_change_marks_existing_sources_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "doc.md"
            document.write_text("Alpha Beta Gamma", encoding="utf-8")

            fixed_settings = _settings(root)
            fixed_settings.chunk_small_size = 64
            fixed_settings.chunk_large_size = 256
            ingestor = Ingestor(root / "data", external, settings=fixed_settings)
            ingestor.scan_sources()

            source_connection = connect_sources(ingestor.paths)
            try:
                source = source_connection.execute(
                    "SELECT source_id, content_hash FROM sources WHERE relative_path = 'doc.md'"
                ).fetchone()
                source_connection.execute(
                    """
                    UPDATE sources
                    SET graph_hash = content_hash, rag_hash = content_hash,
                        graph_status = 'ready', rag_status = 'ready'
                    WHERE source_id = ?
                    """,
                    (source["source_id"],),
                )
                source_connection.commit()
            finally:
                source_connection.close()

            rag_connection = connect_rag(ingestor.paths)
            try:
                rag_connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, source_id, content, chunk_index, token_count
                    ) VALUES ('chunk-1', ?, 'Alpha Beta Gamma', 0, 3)
                    """,
                    (source["source_id"],),
                )
                rag_connection.execute(
                    """
                    INSERT INTO embeddings (
                        vector_id, chunk_id, source_id, vector_blob,
                        dimensions, model_name, vector_space_id
                    ) VALUES (1, 'chunk-1', ?, ?, 3, ?, 'test-space')
                    """,
                    (
                        source["source_id"],
                        np.asarray([1.0, 0.0, 0.0], dtype=np.float32).tobytes(),
                        fixed_settings.models.embedding,
                    ),
                )
                rag_connection.commit()
            finally:
                rag_connection.close()

            meta = read_rag_meta(ingestor.paths, fixed_settings)
            meta["chunking_signature"] = chunking_signature(fixed_settings)
            write_rag_meta(ingestor.paths, meta)

            hierarchical_settings = fixed_settings.model_copy(deep=True)
            hierarchical_settings.chunking_mode = "hierarchical"
            changed_ingestor = Ingestor(
                root / "data",
                external,
                settings=hierarchical_settings,
            )
            scan = changed_ingestor.scan_sources()

            source_connection = connect_sources(changed_ingestor.paths)
            try:
                row = source_connection.execute(
                    "SELECT graph_status, rag_status FROM sources WHERE source_id = ?",
                    (source["source_id"],),
                ).fetchone()
            finally:
                source_connection.close()

            self.assertIn(source["source_id"], scan.changed_source_ids)
            # 旧来源没有图谱抽取指纹；首次扫描需要把 Graph 标记为 pending，
            # 让新的稀疏 Prompt/颗粒度配置真正应用到历史文档。
            self.assertEqual(row["graph_status"], "pending")
            self.assertEqual(row["rag_status"], "pending")

    def test_old_chunks_table_is_migrated_for_hierarchical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "data"
            rag_dir = data_dir / "RAG"
            rag_dir.mkdir(parents=True)
            database = rag_dir / "rag.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE chunks (
                        chunk_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        chunk_index INTEGER,
                        token_count INTEGER,
                        created_at TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO chunks VALUES ('c', 's', 'text', 0, 1, NULL)"
                )
                connection.commit()
            finally:
                connection.close()

            paths = initialize_databases(data_dir, _settings(Path(temporary_dir), 1))
            connection = connect_rag(paths)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(chunks)")
                }
                row = connection.execute(
                    """
                    SELECT granularity, parent_chunk_id, token_start, token_end
                    FROM chunks WHERE chunk_id = 'c'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertTrue(
                {"granularity", "parent_chunk_id", "token_start", "token_end"}.issubset(
                    columns
                )
            )
            self.assertEqual(row["granularity"], "medium")
            self.assertIsNone(row["parent_chunk_id"])

    def test_old_rag_database_is_migrated_with_unknown_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            data_dir = Path(temporary_dir) / "data"
            rag_dir = data_dir / "RAG"
            rag_dir.mkdir(parents=True)
            database = rag_dir / "rag.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE embeddings (
                        vector_id INTEGER PRIMARY KEY, chunk_id TEXT, source_id TEXT,
                        vector_blob BLOB, dimensions INTEGER, model_name TEXT,
                        created_at TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO embeddings VALUES (1, 'c', 's', X'00000000', 1, 'm', NULL)"
                )
                connection.commit()
            finally:
                connection.close()

            paths = initialize_databases(data_dir, _settings(Path(temporary_dir), 1))
            connection = connect_rag(paths)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(embeddings)")
                }
                value = connection.execute(
                    "SELECT vector_space_id FROM embeddings"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertIn("vector_space_id", columns)
            self.assertEqual(value, "unknown")

    def test_mixed_vector_spaces_are_rejected_during_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            settings = _settings(root, 2)
            paths = initialize_databases(root / "data", settings)
            connection = connect_rag(paths)
            try:
                vector_blob = np.ones(2, dtype=np.float32).tobytes()
                for vector_id, space in ((1, "space-a"), (2, "space-b")):
                    chunk_id = f"chunk-{vector_id}"
                    connection.execute(
                        "INSERT INTO chunks (chunk_id, source_id, content) VALUES (?, 's', 'x')",
                        (chunk_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO embeddings (
                            vector_id, chunk_id, source_id, vector_blob,
                            dimensions, model_name, vector_space_id
                        ) VALUES (?, ?, 's', ?, 2, ?, ?)
                        """,
                        (
                            vector_id,
                            chunk_id,
                            vector_blob,
                            settings.models.embedding,
                            space,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(IndexIntegrityError, "多个 vector_space_id"):
                RAGEngine(
                    root / "data",
                    settings=settings,
                    embedder=lambda texts: EmbeddingResult([], "space-a"),
                )


if __name__ == "__main__":
    unittest.main()
