from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.config import AppConfig
from core.knowledge_base import KnowledgeBaseService
from start_web import create_app


class FrontendMountTests(unittest.TestCase):
    def test_spa_assets_fallback_and_api_are_served_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            dist = root / "dist"
            assets = dist / "assets"
            assets.mkdir(parents=True)
            (dist / "index.html").write_text(
                "<!doctype html><title>phase-8-spa</title>", encoding="utf-8"
            )
            (assets / "app.js").write_text("window.phase8 = true;", encoding="utf-8")
            logo_bytes = b"\x89PNG\r\n\x1a\nphase-8-logo"
            (dist / "kemo-graph-logo.png").write_bytes(logo_bytes)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")

            with patch("start_web.FRONTEND_DIST", dist):
                app = create_app(
                    config_path=config_path,
                    data_dir=root / "data",
                    external_dir=root / "markdown",
                )
            client = TestClient(app)

            for path in ("/", "/documents", "/graph", "/settings/deep-link"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("phase-8-spa", response.text)
                self.assertTrue(
                    response.headers["content-type"].startswith("text/html")
                )

            asset = client.get("/assets/app.js")
            self.assertEqual(asset.status_code, 200)
            self.assertIn("window.phase8", asset.text)
            self.assertTrue(
                asset.headers["content-type"].startswith("application/javascript")
            )
            logo = client.get("/kemo-graph-logo.png")
            self.assertEqual(logo.status_code, 200)
            self.assertEqual(logo.content, logo_bytes)
            self.assertTrue(logo.headers["content-type"].startswith("image/png"))
            self.assertEqual(client.get("/api/v1/status").status_code, 200)
            config_response = client.get("/api/v1/config")
            self.assertEqual(config_response.status_code, 200)
            config = config_response.json()["data"]
            config["chunk_size"] = 768
            saved_config = client.put("/api/v1/config", json=config)
            self.assertEqual(saved_config.status_code, 200)
            self.assertEqual(saved_config.json()["data"]["chunk_size"], 768)
            self.assertEqual(
                client.get("/api/v1/config").json()["data"]["chunk_size"], 768
            )
            missing_api = client.get("/api/v1/not-a-route")
            self.assertEqual(missing_api.status_code, 404)
            self.assertEqual(missing_api.json()["error"]["code"], "NOT_FOUND")

    def test_missing_dist_warns_but_api_still_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            with (
                patch("start_web.FRONTEND_DIST", root / "missing-dist"),
                self.assertLogs("start_web", level="WARNING") as captured,
            ):
                app = create_app(
                    config_path=config_path,
                    data_dir=root / "data",
                    external_dir=root / "markdown",
                )
            self.assertIn("前端构建目录不存在", "\n".join(captured.output))
            client = TestClient(app)
            self.assertEqual(client.get("/api/v1/status").status_code, 200)
            self.assertEqual(client.get("/").status_code, 404)


class ManagementAPITests(unittest.TestCase):
    def test_recycle_cleanup_keeps_fresh_files_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            external_dir = root / "external" / "markdown"
            recycle_dir = root / "external" / "recycle"
            external_dir.mkdir(parents=True)
            recycle_dir.mkdir(parents=True)
            service = KnowledgeBaseService(
                settings=AppConfig(),
                data_dir=root / "data",
                external_dir=external_dir,
                config_path=root / "config.json",
            )

            now = datetime.now(timezone.utc)
            expired = recycle_dir / "expired.md"
            expired.write_text("expired", encoding="utf-8")
            expired.with_name("expired.md.meta.json").write_text(
                json.dumps({"expires_at": (now - timedelta(days=1)).isoformat()}),
                encoding="utf-8",
            )
            fresh = recycle_dir / "nested" / "fresh.md"
            fresh.parent.mkdir()
            fresh.write_text("fresh", encoding="utf-8")
            fresh.with_name("fresh.md.meta.json").write_text(
                json.dumps({"expires_at": (now + timedelta(days=1)).isoformat()}),
                encoding="utf-8",
            )
            orphan = recycle_dir / "orphan.bin"
            orphan.write_bytes(b"orphan")

            self.assertEqual(
                service.cleanup_recycle(),
                {"deleted": 1, "forced": False},
            )
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(orphan.exists())

            self.assertEqual(
                service.cleanup_recycle(force=True),
                {"deleted": 2, "forced": True},
            )
            self.assertTrue(recycle_dir.is_dir())
            self.assertEqual(list(recycle_dir.iterdir()), [])

    def test_config_round_trip_uses_selected_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "custom-config.json"
            service = KnowledgeBaseService(
                settings=AppConfig(),
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )
            config = service.get_config()
            config["chunk_size"] = 768
            saved = service.save_config(config)

            self.assertEqual(saved["chunk_size"], 768)
            self.assertEqual(service.get_config(), saved)
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["chunk_size"], 768)
            self.assertEqual(persisted["kemo"]["api_key"], "")
            self.assertNotIn("api_key_source", persisted["kemo"])
            self.assertEqual(service.data_dir, (root / "data").resolve())

    def test_config_api_key_is_masked_and_can_be_replaced_or_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.json"
            settings = AppConfig(
                kemo={"api_key": "initial-secret", "api_key_env": "FALLBACK_KEY"}
            )
            service = KnowledgeBaseService(
                settings=settings,
                config_path=config_path,
                data_dir=root / "data",
                external_dir=root / "markdown",
            )

            with patch.dict(
                os.environ, {"FALLBACK_KEY": "environment-secret"}, clear=True
            ):
                visible = service.get_config()
                self.assertNotEqual(visible["kemo"]["api_key"], "initial-secret")
                self.assertEqual(visible["kemo"]["api_key_source"], "config")

                unchanged = service.save_config(visible)
                self.assertEqual(unchanged["kemo"]["api_key_source"], "config")
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["kemo"]["api_key"], "initial-secret")

                unchanged["kemo"]["api_key"] = "replacement-secret"
                replaced = service.save_config(unchanged)
                self.assertEqual(replaced["kemo"]["api_key_source"], "config")
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["kemo"]["api_key"], "replacement-secret")

                replaced["kemo"]["api_key"] = ""
                cleared = service.save_config(replaced)
                self.assertEqual(cleared["kemo"]["api_key"], "")
                self.assertEqual(cleared["kemo"]["api_key_source"], "environment")
                persisted = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["kemo"]["api_key"], "")

    def test_upload_registers_document_and_content_is_immediately_available(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            service = KnowledgeBaseService(
                settings=AppConfig(),
                data_dir=root / "data",
                external_dir=root / "markdown",
                config_path=root / "config.json",
            )
            uploaded = service.upload_file("# 新文档\n\n正文", "new-document.md")

            self.assertIsNotNone(uploaded["source_id"])
            document_page = service.list_documents(status="active")
            documents = document_page["documents"]
            self.assertEqual(document_page["pagination"]["total"], 1)
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["relative_path"], "new-document.md")
            content = service.get_document_content(documents[0]["source_id"])
            self.assertEqual(content["content"], "# 新文档\n\n正文")


if __name__ == "__main__":
    unittest.main()
