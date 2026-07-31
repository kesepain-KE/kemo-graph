"""Phase 9E 全格式导入、入口集成与日志验收测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import start
from api import create_app
from api.deps import get_service
from core.config import AppConfig, load_config
from core.knowledge_base import (
    DocumentImportPathError,
    KnowledgeBaseService,
    UnsupportedDocumentFormatError,
)
from core.logger import DailyTSVLogger
from provider.tools.document_tools import DocumentConversionError


def _settings(root: Path) -> AppConfig:
    return AppConfig(
        chunk_size=128,
        chunk_overlap=16,
        log_dir=str(root / "log"),
        entity_extraction={"method": "rule", "max_entities": 10},
        models={
            "embedding": "siliconflow-Qwen-Qwen3-VL-Embedding-8B",
            "embedding_dimensions": 3,
            "rerank": "siliconflow-Qwen-Qwen3-VL-Reranker-8B",
        },
    )


def _service(root: Path) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        settings=_settings(root),
        data_dir=root / "data",
        external_dir=root / "external" / "markdown",
        config_path=root / "config.json",
    )


class ConfigurationTests(unittest.TestCase):
    def test_public_siliconflow_model_ids_do_not_contain_slashes(self) -> None:
        settings = load_config(Path(__file__).resolve().parents[1] / "config" / "config.json")
        self.assertEqual(
            settings.models.embedding,
            "siliconflow-Qwen-Qwen3-VL-Embedding-8B",
        )
        self.assertEqual(
            settings.models.rerank,
            "siliconflow-Qwen-Qwen3-VL-Reranker-8B",
        )
        self.assertNotIn("/", settings.models.embedding)
        self.assertNotIn("/", settings.models.rerank)
        self.assertNotEqual(settings.models.llm, settings.models.embedding)


class DocumentImportTests(unittest.TestCase):
    def test_txt_markdown_and_csv_import_and_file_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_dir = root / "sources"
            source_dir.mkdir()
            files = {
                "notes.txt": "line one\nline two",
                "guide.markdown": "# Guide\n\nBody",
                "table.csv": "name,value\nalpha,1\n",
                "reference.rst": "RST Guide\n=========\n\nConverted body.",
            }
            service = _service(root)
            results = []
            for name, content in files.items():
                source = source_dir / name
                source.write_text(content, encoding="utf-8")
                results.append(
                    service.import_document(source, ingest_after_import=False)
                )

            markdown_dir = root / "external" / "markdown"
            self.assertEqual({item["ingest_status"] for item in results}, {"pending"})
            self.assertTrue(all(item["source_id"] for item in results))
            self.assertIn("```text", (markdown_dir / results[0]["markdown_relative_path"]).read_text(encoding="utf-8"))
            self.assertEqual(
                (markdown_dir / results[1]["markdown_relative_path"]).read_text(encoding="utf-8"),
                files["guide.markdown"],
            )
            self.assertIn("| name | value |", (markdown_dir / results[2]["markdown_relative_path"]).read_text(encoding="utf-8"))
            self.assertIn(
                "# RST Guide",
                (markdown_dir / results[3]["markdown_relative_path"]).read_text(encoding="utf-8"),
            )
            mapping = json.loads((markdown_dir / "file_map.json").read_text(encoding="utf-8"))
            self.assertEqual(len(mapping["mappings"]), 4)

            repeated = service.import_document(
                source_dir / "notes.txt",
                ingest_after_import=False,
            )
            self.assertEqual(
                repeated["markdown_relative_path"],
                results[0]["markdown_relative_path"],
            )
            mapping = json.loads((markdown_dir / "file_map.json").read_text(encoding="utf-8"))
            self.assertEqual(len(mapping["mappings"]), 4)

    def test_invalid_inputs_and_conversion_failure_leave_no_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source_dir = root / "sources"
            source_dir.mkdir()
            unsupported = source_dir / "payload.exe"
            unsupported.write_bytes(b"MZ")
            service = _service(root)

            with self.assertRaises(UnsupportedDocumentFormatError):
                service.import_document(unsupported, ingest_after_import=False)
            traversal = str(source_dir / "nested" / ".." / "document.txt")
            with self.assertRaises(DocumentImportPathError):
                service.import_document(traversal, ingest_after_import=False)

            source = source_dir / "broken.txt"
            source.write_text("broken", encoding="utf-8")
            with patch(
                "core.knowledge_base.convert_document",
                side_effect=DocumentConversionError("conversion failed"),
            ):
                with self.assertRaises(DocumentConversionError):
                    service.import_document(source, ingest_after_import=False)
            markdown_dir = root / "external" / "markdown"
            self.assertEqual(list(markdown_dir.glob("*.md")), [])

    def test_no_ingest_skips_models_and_default_import_runs_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "document.md"
            source.write_text("# Document", encoding="utf-8")
            service = _service(root)
            ingest_result = {
                "processed": 1,
                "graph_updated": 1,
                "rag_updated": 1,
                "skipped": 0,
                "failed": 0,
                "details": [],
            }

            with patch("core.knowledge_base.Ingestor.ingest") as ingest_mock:
                pending = service.import_document(source, ingest_after_import=False)
                ingest_mock.assert_not_called()
            self.assertEqual(pending["ingest_status"], "pending")

            source.write_text("# Document updated", encoding="utf-8")
            with patch(
                "core.knowledge_base.Ingestor.ingest",
                return_value=ingest_result,
            ) as ingest_mock:
                completed = service.import_document(source)
                ingest_mock.assert_called_once()
            self.assertEqual(completed["ingest_status"], "completed")
            self.assertEqual(completed["ingest"], ingest_result)

    def test_import_graph_tool_and_delete_actions_are_logged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "document.md"
            source.write_text("Alpha", encoding="utf-8")
            service = _service(root)
            imported = service.import_document(source, ingest_after_import=False)

            def finish_graph(system, user, tools, tool_handler, **kwargs):
                del system, user, tools, kwargs
                tool_handler("finish", {})
                return "done"

            with patch("core.ingestor.chat_with_tools", side_effect=finish_graph):
                service.ingest(paths=[imported["markdown_relative_path"]], mode="graph")
            service.delete_document(imported["source_id"])

            log_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "log").glob("*.tsv")
            )
            self.assertIn("document_import_start", log_text)
            self.assertIn("graph_tool_call", log_text)
            self.assertIn("delete_document", log_text)


class _ImportAPIService:
    def import_document(self, source_path, **kwargs):
        source = Path(source_path)
        if source.name == "broken.txt":
            raise DocumentConversionError("无法转换测试文件")
        if source.name == "ingest-fail.txt":
            return {
                "source_id": "source-2",
                "original_filename": source.name,
                "detected_format": "txt",
                "markdown_relative_path": "ingest-fail-stable.md",
                "conversion_status": "completed",
                "ingest_status": "failed",
                "ingest_error": "模拟整理失败",
                "size": source.stat().st_size,
            }
        return {
            "source_id": "source-1",
            "original_filename": source.name,
            "detected_format": source.suffix.removeprefix("."),
            "markdown_relative_path": "document-stable.md",
            "conversion_status": "completed",
            "ingest_status": "completed" if kwargs["ingest_after_import"] else "pending",
            "size": source.stat().st_size,
        }


class EntryPointTests(unittest.TestCase):
    def test_api_import_success_and_format_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            config_path.write_text(
                _settings(root).model_dump_json(indent=2),
                encoding="utf-8",
            )
            app = create_app(
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            app.dependency_overrides[get_service] = lambda: _ImportAPIService()
            client = TestClient(app)

            success = client.post(
                "/api/v1/import?ingest=false",
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
            self.assertEqual(success.status_code, 200)
            self.assertEqual(success.json()["data"]["ingest_status"], "pending")

            unsupported = client.post(
                "/api/v1/import",
                files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
            )
            self.assertEqual(unsupported.status_code, 415)
            self.assertEqual(unsupported.json()["error"]["code"], "UNSUPPORTED_FORMAT")

            conversion = client.post(
                "/api/v1/import",
                files={"file": ("broken.txt", b"broken", "text/plain")},
            )
            self.assertEqual(conversion.status_code, 422)
            self.assertEqual(conversion.json()["error"]["code"], "CONVERSION_FAILED")

            ingest_failure = client.post(
                "/api/v1/import",
                files={"file": ("ingest-fail.txt", b"text", "text/plain")},
            )
            self.assertEqual(ingest_failure.status_code, 502)
            self.assertEqual(ingest_failure.json()["error"]["code"], "INGEST_FAILED")

    def test_cli_import_outputs_structured_json(self) -> None:
        class FakeService:
            def import_document(self, path, *, ingest_after_import=True):
                return {
                    "source_id": "source-1",
                    "original_filename": Path(path).name,
                    "detected_format": "txt",
                    "markdown_relative_path": "notes-stable.md",
                    "conversion_status": "completed",
                    "ingest_status": "completed" if ingest_after_import else "pending",
                }

        output = io.StringIO()
        with (
            patch.object(start, "load_config", return_value=AppConfig()),
            patch.object(start, "KnowledgeBaseService", return_value=FakeService()),
            redirect_stdout(output),
        ):
            exit_code = start.main(["import", "notes.txt", "--no-ingest"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["ingest_status"], "pending")


class LoggerTests(unittest.TestCase):
    def test_daily_tsv_has_header_and_redacts_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            secret = "phase9e-super-secret"
            logger = DailyTSVLogger(Path(temporary_dir), "INFO")
            path = logger.log(
                "test",
                "llm_request",
                f"Authorization: Bearer {secret}; api_key={secret}",
                12,
            )
            self.assertIsNotNone(path)
            content = path.read_text(encoding="utf-8")
            self.assertEqual(
                content.splitlines()[0],
                "time\tlevel\tmodule\taction\tdetail\telapsed_ms",
            )
            self.assertNotIn(secret, content)
            self.assertIn("[REDACTED]", content)


if __name__ == "__main__":
    unittest.main()
