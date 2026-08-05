"""Phase 1 基础设施验收测试。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig, load_config
from core.db import (
    initialize_databases,
    read_graph_meta,
    read_rag_meta,
    write_graph_meta,
    write_rag_meta,
)
from core.ingestor import FileMapStore


class ConfigTests(unittest.TestCase):
    def test_missing_config_is_created_with_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "config" / "config.json"
            settings = load_config(config_path=config_path, env_path=None)

            self.assertTrue(config_path.exists())
            self.assertEqual(settings.max_query_depth, 5)
            self.assertEqual(settings.models.embedding_dimensions, 4096)
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                AppConfig().model_dump(mode="json"),
            )

    def test_missing_and_invalid_values_fall_back_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "max_query_depth": 99,
                        "default_top_k": 25,
                        "models": {"embedding_dimensions": -1},
                        "graph_tool_max_iterations": 0,
                        "log_level": "TRACE",
                        "kemo": {
                            "protocol_version": "2.0",
                            "request_timeout": 10,
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = load_config(config_path=config_path, env_path=None)

            self.assertEqual(settings.max_query_depth, 5)
            self.assertEqual(settings.default_top_k, 25)
            self.assertEqual(settings.models.embedding_dimensions, 4096)
            self.assertEqual(
                settings.models.embedding,
                "siliconflow-Qwen-Qwen3-VL-Embedding-8B",
            )
            self.assertEqual(settings.kemo.api_key_env, "KEMO_API_KEY")
            self.assertEqual(settings.kemo.api_key, "")
            self.assertEqual(settings.graph_tool_max_iterations, 40)
            self.assertEqual(settings.log_level, "INFO")
            self.assertEqual(settings.kemo.protocol_version, "1.0")
            self.assertEqual(settings.kemo.request_timeout, 900)

    def test_legacy_provider_sections_are_migrated_without_being_exported(self) -> None:
        settings = AppConfig.model_validate(
            {
                "kemo": {
                    "base_url": "https://gateway.test",
                    "api_key_env": "ONE_KEY",
                },
                "llm_api": {"model": "legacy-llm"},
                "embedding_api": {
                    "model": "legacy-embedding",
                    "dimensions": 8,
                },
                "rerank_api": {"model": "legacy-rerank"},
            }
        )

        self.assertEqual(settings.models.llm, "legacy-llm")
        self.assertEqual(settings.models.embedding, "legacy-embedding")
        self.assertEqual(settings.models.embedding_dimensions, 8)
        self.assertEqual(settings.models.rerank, "legacy-rerank")
        exported = settings.model_dump(mode="json")
        self.assertNotIn("llm_api", exported)
        self.assertNotIn("embedding_api", exported)
        self.assertNotIn("rerank_api", exported)

    def test_dotenv_runtime_paths_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            env_path = root / ".env"
            env_path.write_text(
                "KEMO_GRAPH_DATA_DIR=custom-data\n"
                "KEMO_GRAPH_EXTERNAL_DIR=custom-markdown\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_config(root / "config.json", env_path)
                self.assertEqual(
                    settings.resolve_data_dir(root), (root / "custom-data").resolve()
                )
                self.assertEqual(
                    settings.resolve_external_dir(root),
                    (root / "custom-markdown").resolve(),
                )


class DatabaseTests(unittest.TestCase):
    def test_initialization_creates_all_schemas_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, AppConfig())
            initialize_databases(temporary_dir, AppConfig())  # 幂等验收

            self.assertEqual(
                _table_names(paths.sources_db),
                {"sources", "maintenance_jobs", "maintenance_job_events"},
            )
            self.assertEqual(
                _table_names(paths.graph_db),
                {
                    "nodes",
                    "node_sources",
                    "edges",
                    "edge_sources",
                    "groups",
                    "group_nodes",
                    "entity_mentions",
                    "relation_mentions",
                    "mention_nodes",
                },
            )
            self.assertEqual(
                _table_names(paths.rag_db),
                {
                    "chunks",
                    "chunk_nodes",
                    "embeddings",
                    "entity_embeddings",
                    "community_embeddings",
                },
            )
            self.assertIn(
                "has_entity_embedding",
                _column_names(paths.graph_db, "nodes"),
            )
            self.assertEqual(
                _column_names(paths.sources_db, "sources"),
                {
                    "source_id",
                    "original_path",
                    "relative_path",
                    "path_hash",
                    "content_hash",
                    "graph_hash",
                    "rag_hash",
                    "graph_status",
                    "rag_status",
                    "exists_status",
                    "origin_hash",
                    "origin_size",
                    "origin_modified_at",
                    "source_uri",
                    "source_type",
                    "source_revision",
                    "source_updated_at",
                    "source_metadata_json",
                    "external_content_hash",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertEqual(
                _column_names(paths.graph_db, "edges"),
                {
                    "edge_id",
                    "source_node_id",
                    "relation",
                    "target_node_id",
                    "weight",
                    "support_count",
                    "created_at",
                },
            )
            self.assertEqual(
                _column_names(paths.rag_db, "embeddings"),
                {
                    "vector_id",
                    "chunk_id",
                    "source_id",
                    "vector_blob",
                    "dimensions",
                    "model_name",
                    "vector_space_id",
                    "created_at",
                },
            )

            self.assertEqual(
                _index_names(paths.sources_db),
                {
                    "idx_sources_path_hash",
                    "idx_sources_graph_status",
                    "idx_sources_rag_status",
                    "idx_sources_source_uri",
                    "idx_sources_source_type",
                    "idx_maintenance_jobs_updated",
                    "idx_maintenance_job_events_job",
                },
            )
            self.assertTrue(
                {
                    "idx_nodes_keyword",
                    "idx_node_sources_node",
                    "idx_node_sources_source",
                    "idx_edges_source",
                    "idx_edges_target",
                    "idx_edge_sources_edge",
                    "idx_edge_sources_source",
                    "idx_group_nodes_group",
                    "idx_group_nodes_node",
                }.issubset(_index_names(paths.graph_db))
            )
            self.assertTrue(
                {
                    "idx_chunks_source",
                    "idx_chunk_nodes_chunk",
                    "idx_chunk_nodes_node",
                    "idx_embeddings_chunk",
                    "idx_embeddings_vector_id",
                    "idx_entity_embeddings_node",
                    "idx_community_embeddings_group",
                }.issubset(_index_names(paths.rag_db))
            )

            self.assertEqual(
                set(read_graph_meta(paths)),
                {
                    "total_nodes",
                    "total_edges",
                    "total_groups",
                    "last_summary_at",
                    "changed_since_summary",
                },
            )
            self.assertEqual(read_rag_meta(paths)["vector_dimensions"], 4096)
            self.assertEqual(
                read_rag_meta(paths)["faiss_index_type"],
                "IndexIDMap2+IndexFlatIP",
            )
            self.assertTrue(paths.vector_index_dir.is_dir())
            self.assertTrue(paths.rerank_cache.is_file())

    def test_meta_read_write_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = initialize_databases(temporary_dir, AppConfig())
            graph_meta = read_graph_meta(paths)
            graph_meta["total_nodes"] = 3
            write_graph_meta(paths, graph_meta)
            rag_meta = read_rag_meta(paths)
            rag_meta["total_chunks"] = 8
            write_rag_meta(paths, rag_meta)

            self.assertEqual(read_graph_meta(paths)["total_nodes"], 3)
            self.assertEqual(read_rag_meta(paths)["total_chunks"], 8)


class FileMapTests(unittest.TestCase):
    def test_file_map_crud_and_one_to_one_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            file_path = Path(temporary_dir) / "markdown" / "file_map.json"
            store = FileMapStore(file_path)

            created = store.upsert("C:/docs/a.docx", "a.md")
            self.assertEqual(store.get_by_original("C:/docs/a.docx"), created)
            self.assertEqual(store.get_by_markdown("a.md"), created)

            updated = store.upsert("C:/docs/a.docx", "renamed/a.md")
            self.assertEqual(store.list(), [updated])
            self.assertIsNone(store.get_by_markdown("a.md"))

            replacement = store.upsert("C:/docs/b.docx", "renamed/a.md")
            self.assertEqual(store.list(), [replacement])
            self.assertIsNone(store.get_by_original("C:/docs/a.docx"))

            self.assertTrue(store.delete_by_markdown("renamed/a.md"))
            self.assertFalse(store.delete_by_markdown("renamed/a.md"))
            self.assertEqual(store.list(), [])


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows if not row[0].startswith("sqlite_")}


def _index_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows if not row[0].startswith("sqlite_")}


def _column_names(database_path: Path, table_name: str) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        connection.close()
    return {row[1] for row in rows}


if __name__ == "__main__":
    unittest.main()
