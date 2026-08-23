from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig
from core.db import read_graph_meta
from core.graph_draft import DraftEntity, DraftRelation, GraphDraft
from core.ingestor import Ingestor
from core.ingestor._graph_build import _sparsify_graph_relations


class GraphExtractionGranularityTests(unittest.TestCase):
    def test_large_profile_filters_weak_and_overdense_relations(self) -> None:
        entities = tuple(
            DraftEntity(f"n{index}", f"概念{index}", "摘要", (), (), 0.9, "证据")
            for index in range(2)
        )
        draft = GraphDraft(
            entities,
            tuple(
                DraftRelation("n0", f"关系{index}", "n1", weight, "证据")
                for index, weight in enumerate((0.99, 0.95, 0.9, 0.85, 0.79))
            ),
        )

        sparse = _sparsify_graph_relations(draft, "large")

        self.assertEqual(len(sparse.relations), 3)
        self.assertEqual(
            [relation.evidence_weight for relation in sparse.relations],
            [0.99, 0.95, 0.9],
        )

    def test_granularity_scales_the_existing_chunk_size_without_breaking_old_config(self) -> None:
        default = AppConfig()
        self.assertEqual(default.graph_extract_granularity, "large")
        self.assertEqual(default.effective_graph_extract_chunk_size(), 24000)
        base = AppConfig(
            graph_extract_chunk_size=12000,
            graph_extract_granularity="medium",
        )
        self.assertEqual(base.effective_graph_extract_chunk_size(), 12000)
        self.assertEqual(
            AppConfig(
                graph_extract_chunk_size=12000,
                graph_extract_granularity="fine",
            ).graph_extract_granularity,
            "small",
        )
        self.assertEqual(
            AppConfig(
                graph_extract_chunk_size=12000,
                graph_extract_granularity="coarse",
            ).graph_extract_granularity,
            "large",
        )
        self.assertEqual(
            AppConfig(
                graph_extract_chunk_size=12000,
                graph_extract_granularity="small",
            ).effective_graph_extract_chunk_size(),
            6000,
        )
        self.assertEqual(
            AppConfig(
                graph_extract_chunk_size=12000,
                graph_extract_granularity="large",
            ).effective_graph_extract_chunk_size(),
            24000,
        )

    def test_tools_mode_extracts_long_documents_in_configured_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            document = external / "long.md"
            document.write_text(
                "# 第一节\n\n" + "甲" * 2400 + "\n\n"
                "# 第二节\n\n" + "乙" * 2400,
                encoding="utf-8",
            )
            settings = AppConfig(
                graph_build_mode="tools",
                graph_extract_granularity="small",
                graph_extract_chunk_size=2000,
                log_dir=str(root / "log"),
            )
            ingestor = Ingestor(root / "data", external, settings=settings)

            def finish_section(*, system, user, tools, tool_handler, **kwargs):
                del user, tools, kwargs
                self.assertIn('"section_count"', system)
                self.assertIn('"max_entities"', system)
                self.assertIn('"max_relations"', system)
                self.assertIn("summary", system)
                tool_handler("finish", {})
                return "done"

            with patch(
                "core.ingestor.chat_with_tools", side_effect=finish_section
            ) as chat:
                result = ingestor.ingest(mode="graph")

            self.assertEqual(result["graph_updated"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertGreaterEqual(chat.call_count, 2)
            self.assertTrue(
                all(
                    len(call.kwargs["user"]) <= 2000
                    for call in chat.call_args_list
                )
            )

    def test_structured_mode_receives_coarse_budget_schema_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "coarse.md").write_text(
                "# 主题\n\n这是一个稳定主题及其主要对象的说明。",
                encoding="utf-8",
            )
            settings = AppConfig(
                graph_build_mode="structured",
                graph_extract_granularity="large",
                graph_extract_chunk_size=12000,
                log_dir=str(root / "log"),
            )
            ingestor = Ingestor(root / "data", external, settings=settings)
            payload = {
                "schema_version": "1.0",
                "entities": [
                    {
                        "local_id": "n1",
                        "keyword": "稳定主题",
                        "summary": "文档中反复说明的核心主题。",
                        "aliases": [],
                        "tags": ["主题"],
                        "evidence_weight": 0.9,
                        "evidence": "稳定主题",
                    }
                ],
                "relations": [],
            }
            with patch("core.ingestor.chat_structured", return_value=payload) as chat:
                result = ingestor.ingest(mode="graph")

            self.assertEqual(result["failed"], 0)
            self.assertEqual(chat.call_count, 1)
            system_prompt = chat.call_args.args[0]
            schema = chat.call_args.args[2]
            self.assertIn('"extract_granularity": "large"', system_prompt)
            self.assertIn('"max_entities": 12', system_prompt)
            self.assertIn('"max_relations": 16', system_prompt)
            self.assertTrue(any(word in system_prompt for word in ("碎片", "示例", "单步")))
            self.assertEqual(schema["properties"]["entities"]["maxItems"], 12)
            self.assertEqual(schema["properties"]["relations"]["maxItems"], 16)

    def test_tools_mode_enforces_the_coarse_entity_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "budget.md").write_text(
                "# 稳定主题\n\n只保留少量核心概念。",
                encoding="utf-8",
            )
            settings = AppConfig(
                graph_build_mode="tools",
                graph_extract_granularity="large",
                log_dir=str(root / "log"),
            )
            ingestor = Ingestor(root / "data", external, settings=settings)
            errors: list[str] = []

            def over_budget(*, tool_handler, **kwargs):
                del kwargs
                for index in range(20):
                    try:
                        tool_handler(
                            "add_entity",
                            {
                                "keyword": f"概念{index}",
                                "summary": "用于验证预算的稳定概念。",
                                "aliases": [],
                                "tags": [],
                            },
                        )
                    except Exception as exc:
                        errors.append(str(exc))
                        break
                tool_handler("finish", {})
                return "done"

            with patch("core.ingestor.chat_with_tools", side_effect=over_budget):
                result = ingestor.ingest(mode="graph")

            self.assertEqual(result["failed"], 0)
            self.assertTrue(any("budget_exhausted" in message for message in errors))
            db = sqlite3.connect(ingestor.paths.graph_db)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], 12)
            finally:
                db.close()

    def test_changing_graph_profile_marks_built_sources_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "profile.md").write_text("# 主题\n\n核心说明。", encoding="utf-8")
            first_settings = AppConfig(
                graph_build_mode="tools",
                graph_extract_granularity="small",
                log_dir=str(root / "log"),
            )
            first_ingestor = Ingestor(root / "data", external, settings=first_settings)
            with patch("core.ingestor.chat_with_tools") as chat:
                chat.side_effect = lambda *, tool_handler, **kwargs: (
                    tool_handler("finish", {})
                )
                first = first_ingestor.ingest(mode="graph")
            self.assertEqual(first["failed"], 0)

            second_settings = first_settings.model_copy(
                update={"graph_extract_granularity": "large"}
            )
            second_ingestor = Ingestor(root / "data", external, settings=second_settings)
            scan = second_ingestor.scan_sources()

            self.assertEqual(len(scan.changed_source_ids), 1)
            connection = sqlite3.connect(second_ingestor.paths.sources_db)
            try:
                status = connection.execute(
                    "SELECT graph_status FROM sources"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(status, "pending")

    def test_failed_scan_does_not_publish_new_graph_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external = root / "external" / "markdown"
            external.mkdir(parents=True)
            (external / "one.md").write_text("# 主题\n\n说明。", encoding="utf-8")
            first_settings = AppConfig(
                graph_build_mode="tools",
                graph_extract_granularity="small",
                max_documents=1,
                log_dir=str(root / "log"),
            )
            first = Ingestor(root / "data", external, settings=first_settings)
            with patch("core.ingestor.chat_with_tools") as chat:
                chat.side_effect = lambda *, tool_handler, **kwargs: tool_handler("finish", {})
                self.assertEqual(first.ingest(mode="graph")["failed"], 0)
            old_signature = read_graph_meta(first.paths)["extraction_signature"]

            (external / "two.md").write_text("# 第二主题\n\n说明。", encoding="utf-8")
            second_settings = first_settings.model_copy(
                update={"graph_extract_granularity": "large"}
            )
            second = Ingestor(root / "data", external, settings=second_settings)
            with self.assertRaisesRegex(Exception, "超过配置上限"):
                second.scan_sources()

            self.assertEqual(
                read_graph_meta(second.paths)["extraction_signature"],
                old_signature,
            )


if __name__ == "__main__":
    unittest.main()
