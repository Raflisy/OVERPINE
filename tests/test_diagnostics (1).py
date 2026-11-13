"""Tests for the read-only logs and diagnostics API."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core.config import Settings
from backend.monitoring.models import Sensor, SensorState
from backend.monitoring.scheduler import MonitoringScheduler
from tests.http_client import LocalHTTPTestClient
from tests.test_support import create_isolated_app

_LOG = (
    "2026-11-13 10:00:00,001 | INFO     | overpine | Started\n"
    "2026-11-13 10:00:01,002 | WARNING  | overpine | Sensor provider x failed: boom\n"
    "2026-11-13 10:00:02,003 | ERROR    | overpine | Crash\n"
    "Traceback (most recent call last):\n"
    "  File \"x.py\", line 1\n"
    "2026-11-13 10:00:03,004 | INFO     | overpine | Done\n"
)


class DiagnosticsTests(unittest.TestCase):
    """Exercise the diagnostics endpoints with temporary files only."""

    def setUp(self) -> None:
        self.app = create_isolated_app(self, Settings(), configure_logging=False)
        self.app.monitoring_scheduler = MonitoringScheduler(providers=[])
        self.client = LocalHTTPTestClient(self.app)
        self.addCleanup(self.client.close)
        directory = tempfile.TemporaryDirectory(prefix="overpine-log-")
        self.addCleanup(directory.cleanup)
        self.log_file = Path(directory.name) / "overpine.log"

    def _get_logs(self, query: str = ""):
        with patch(
            "backend.api.routes.diagnostics._log_path",
            return_value=self.log_file,
        ):
            return self.client.get(f"/api/logs{query}")

    def test_missing_log_file_is_reported_not_crashed(self) -> None:
        response = self._get_logs()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["entries"], [])
        self.assertIn("does not exist", body["message"])

    def test_log_entries_group_traceback_lines_with_their_entry(self) -> None:
        self.log_file.write_text(_LOG, encoding="utf-8")
        body = self._get_logs().json()
        self.assertTrue(body["available"])
        self.assertEqual(len(body["entries"]), 4)
        crash = body["entries"][2]
        self.assertEqual(crash["level"], "ERROR")
        self.assertIn("Traceback", crash["message"])

    def test_level_filter_is_a_minimum_severity(self) -> None:
        self.log_file.write_text(_LOG, encoding="utf-8")
        body = self._get_logs("?level=WARNING").json()
        self.assertEqual(
            [entry["level"] for entry in body["entries"]],
            ["WARNING", "ERROR"],
        )

    def test_search_is_case_insensitive_and_limit_keeps_newest(self) -> None:
        self.log_file.write_text(_LOG, encoding="utf-8")
        body = self._get_logs("?q=BOOM").json()
        self.assertEqual(body["total_matching"], 1)
        body = self._get_logs("?limit=1").json()
        self.assertEqual(body["entries"][0]["message"], "Done")
        self.assertEqual(body["total_matching"], 4)

    def test_bad_query_values_are_rejected(self) -> None:
        for query in ("?level=LOUD", "?limit=0", "?limit=9999", "?limit=abc"):
            with self.subTest(query=query):
                self.assertEqual(self._get_logs(query).status_code, 422)
        self.assertEqual(self._get_logs("?q=" + "a" * 101).status_code, 422)

    def test_log_path_outside_data_root_is_refused(self) -> None:
        with patch(
            "backend.api.routes.diagnostics.user_data_dir",
            return_value=Path(tempfile.gettempdir()) / "overpine-root-that-does-not-exist",
        ):
            self.app.settings = dataclasses.replace(
                self.app.settings,
                log_file=str(self.log_file),
            )
            response = self.client.get("/api/logs")
        body = response.json()
        self.assertFalse(body["available"])
        self.assertIn("outside", body["message"])

    def test_diagnostics_counts_exclude_detail_sensors_and_list_problems(self) -> None:
        registry = self.app.monitoring_scheduler.registry
        registry.register(Sensor("cpu.total", "CPU utilization", "cpu", "%", "windows"))
        registry.update("cpu.total", 12.0)
        registry.register(Sensor("fan.rpm", "Fan speed", "fan", "rpm", "windows"))
        registry.set_state("fan.rpm", SensorState.UNSUPPORTED, "not exposed")
        registry.register(
            Sensor(
                "gpu.gpu-utilization.pid-1-luid-x",
                "GPU engine",
                "gpu",
                "%",
                "windows",
            )
        )
        registry.set_state(
            "gpu.gpu-utilization.pid-1-luid-x",
            SensorState.ERROR,
            "gone",
        )
        body = self.client.get("/api/diagnostics").json()
        self.assertEqual(body["sensor_counts"]["AVAILABLE"], 1)
        self.assertEqual(body["sensor_counts"]["UNSUPPORTED"], 1)
        self.assertEqual(body["sensor_counts"]["ERROR"], 0)
        self.assertEqual(body["detail_sensor_count"], 1)
        self.assertEqual([item["id"] for item in body["problems"]], ["fan.rpm"])
        self.assertIn("history", body)
        self.assertIn("providers", body)


if __name__ == "__main__":
    unittest.main()
