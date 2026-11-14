"""轻量后台维护调度器验收测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.settings import AppConfig
from core.maintenance import MaintenanceScheduler
from start_web import create_app


class _FakeMaintenanceService:
    def __init__(self, *, fail_summary: bool = False) -> None:
        self.settings = AppConfig(
            summary_trigger_time="03:00",
        )
        self.fail_summary = fail_summary
        self.summary_calls = 0

    def generate_group_summaries(self):
        self.summary_calls += 1
        if self.fail_summary:
            raise RuntimeError("summary failed")
        return {"generated": 2}



class MaintenanceSchedulerTests(unittest.TestCase):
    def test_runs_summary_once_per_day_at_configured_time(self) -> None:
        service = _FakeMaintenanceService()
        scheduler = MaintenanceScheduler(lambda: service)

        self.assertIsNone(scheduler.run_pending(datetime(2026, 7, 31, 2, 59)))
        first = scheduler.run_pending(datetime(2026, 7, 31, 3, 0))
        duplicate = scheduler.run_pending(datetime(2026, 7, 31, 3, 0, 45))
        next_day = scheduler.run_pending(datetime(2026, 8, 1, 3, 0))

        self.assertEqual(first["summary"], {"generated": 2})
        self.assertIsNone(duplicate)
        self.assertIsNotNone(next_day)
        self.assertEqual(service.summary_calls, 2)

    def test_summary_failure_is_reported_without_retrying_the_same_day(self) -> None:
        service = _FakeMaintenanceService(fail_summary=True)
        scheduler = MaintenanceScheduler(lambda: service)

        with self.assertLogs("core.maintenance.scheduler", level="ERROR"):
            result = scheduler.run_pending(datetime(2026, 7, 31, 3, 0))

        self.assertEqual(result["errors"]["summary"], "summary failed")
        self.assertIsNone(scheduler.run_pending(datetime(2026, 7, 31, 3, 0, 30)))

    def test_start_is_idempotent_and_creates_daemon_thread(self) -> None:
        service = _FakeMaintenanceService()
        scheduler = MaintenanceScheduler(
            lambda: service,
            poll_interval_seconds=60,
            clock=lambda: datetime(2026, 7, 31, 2, 0),
        )

        with self.assertLogs("core.maintenance.scheduler", level="INFO") as captured:
            self.assertTrue(scheduler.start())
            thread = scheduler._thread
            self.assertFalse(scheduler.start())
            self.assertTrue(scheduler.stop())

        self.assertIsNotNone(thread)
        self.assertTrue(thread.daemon)
        self.assertIn("每日 03:00", "\n".join(captured.output))
        self.assertFalse(scheduler.is_running)


class WebSchedulerIntegrationTests(unittest.TestCase):
    def test_web_lifespan_starts_and_stops_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config_path = root / "config.yaml"
            config_path.write_text("{}", encoding="utf-8")
            scheduler = unittest.mock.MagicMock()

            with (
                patch("start_web.MaintenanceScheduler", return_value=scheduler),
                patch("start_web.FRONTEND_DIST", root / "missing-dist"),
            ):
                application = create_app(
                    config_path=config_path,
                    data_dir=root / "data",
                    external_dir=root / "markdown",
                )

            with TestClient(application) as client:
                self.assertEqual(client.get("/api/v1/status").status_code, 200)
                scheduler.start.assert_called_once_with()

            scheduler.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
