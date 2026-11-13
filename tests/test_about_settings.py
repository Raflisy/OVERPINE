"""Tests for About, Settings, and live sensor-support information."""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from backend.core.config import Settings
from backend.core.user_settings import SettingsStore, settings_values
from backend.monitoring.scheduler import MonitoringScheduler
from tests.http_client import LocalHTTPTestClient
from tests.test_support import create_isolated_app


class AboutSettingsTests(unittest.TestCase):
    """Exercise information and settings APIs with app-data paths isolated."""

    def setUp(self) -> None:
        self.app = create_isolated_app(
            self,
            Settings(
                about_credits="Credits go here",
                about_course_university="Course / University",
            ),
            configure_logging=False,
        )
        self.app.monitoring_scheduler = MonitoringScheduler(providers=[])
        self.client = LocalHTTPTestClient(self.app)
        self.addCleanup(self.client.close)

    def _valid_settings(self) -> dict[str, object]:
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        return cast(dict[str, object], response.json()["settings"])

    def _post_settings(
        self,
        payload: dict[str, object],
        *,
        token: bool = True,
    ):
        headers = (
            {"X-Overpine-Token": self.app.session_token}
            if token
            else {}
        )
        return self.client.post(
            "/api/settings",
            json_body=payload,
            headers=headers,
        )

    def test_about_response_has_live_import_check_and_local_identity(self) -> None:
        with patch(
            "backend.api.routes.about.get_system_info",
            return_value={"device_model": "Test Model"},
        ):
            response = self.client.get("/api/about")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "app_name",
                "app_version",
                "python_version",
                "windows_version",
                "machine_model",
                "data_directory",
                "database_size_bytes",
                "first_sample_timestamp",
                "first_sample_date",
                "history_warning",
                "credits",
                "course_university",
                "standard_library_statement",
                "import_check",
            },
        )
        self.assertEqual(body["machine_model"], "Test Model")
        self.assertEqual(body["credits"], "Credits go here")
        self.assertEqual(body["course_university"], "Course / University")
        self.assertEqual(
            body["standard_library_statement"],
            "Built with the Python standard library only",
        )
        self.assertEqual(body["data_directory"], str(self.app.data_dir.resolve()))
        self.assertTrue(body["import_check"]["standard_library_only"])
        self.assertEqual(body["import_check"]["imports_found"], [])
        self.assertEqual(body["import_check"]["external_frontend_urls"], [])

    def test_about_reports_first_retained_sample_date(self) -> None:
        timestamp = time.time() - 2
        self.app.history_store.record(
            timestamp,
            "cpu.total",
            12.5,
            "AVAILABLE",
        )
        self.app.history_store._flush_pending()

        with patch(
            "backend.api.routes.about.get_system_info",
            return_value={"device_model": "Test Model"},
        ):
            response = self.client.get("/api/about")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertAlmostEqual(body["first_sample_timestamp"], timestamp)
        self.assertEqual(body["first_sample_date"][-6:], "+00:00")

    def test_navigation_exposes_all_three_new_pages_and_modules(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        for route in ("about", "settings", "help"):
            self.assertIn(f'href="#{route}"', page.text)
            self.assertIn(f'id="{route}-page"', page.text)
            self.assertEqual(
                self.client.get(f"/js/pages/{route}.js").status_code,
                200,
            )

    def test_settings_validation_rejects_bad_types_ranges_and_unknown_keys(
        self,
    ) -> None:
        invalid_payloads: tuple[tuple[str, object, str], ...] = (
            ("history_enabled", "yes", "history_enabled must be true or false"),
            ("retention_days", 0, "retention_days must be an integer"),
            ("max_mb", 10241, "max_mb must be an integer"),
            (
                "refresh_interval_seconds",
                0,
                "refresh_interval_seconds must be an integer",
            ),
            ("theme", [], "theme must be either dark or light"),
            (
                "online_device_fact_lookup",
                1,
                "online_device_fact_lookup must be true or false",
            ),
        )
        for key, invalid_value, message in invalid_payloads:
            with self.subTest(key=key):
                payload = self._valid_settings()
                payload[key] = invalid_value
                response = self._post_settings(payload)
                self.assertEqual(response.status_code, 422)
                self.assertIn(message, response.json()["error"]["message"])

        payload = self._valid_settings()
        payload["unknown_setting"] = True
        response = self._post_settings(payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Unknown settings", response.json()["error"]["message"])

        payload = self._valid_settings()
        payload["recorded_sensors"] = ["valid.sensor", "bad sensor"]
        response = self._post_settings(payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("invalid sensor ID", response.json()["error"]["message"])

    def test_settings_save_and_reset_require_the_session_token(self) -> None:
        self.assertEqual(
            self._post_settings(self._valid_settings(), token=False).status_code,
            401,
        )
        self.assertEqual(
            self._post_settings({"reset": True}, token=False).status_code,
            401,
        )

        response = self._post_settings({"reset": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Path(response.json()["settings_file"]).exists())
        self.assertFalse(response.json()["settings"]["online_device_fact_lookup"])

    def test_settings_file_stays_in_data_folder_and_survives_store_restart(
        self,
    ) -> None:
        values = self._valid_settings()
        values.update({
            "history_enabled": False,
            "retention_days": 14,
            "max_mb": 256,
            "recorded_sensors": ["cpu.total", "battery.charge"],
            "refresh_interval_seconds": 9,
            "theme": "light",
            "online_device_fact_lookup": False,
        })
        response = self._post_settings(values)

        self.assertEqual(response.status_code, 200)
        settings_path = Path(response.json()["settings_file"]).resolve()
        settings_path.relative_to(self.app.data_dir.resolve())
        self.assertTrue(settings_path.is_file())

        restarted_store = SettingsStore(
            settings_path,
            self.app.settings_store.base_settings,
        )
        self.assertEqual(restarted_store.values(), values)
        self.assertEqual(
            settings_values(restarted_store.apply())["recorded_sensors"],
            ["cpu.total", "battery.charge"],
        )
        self.assertEqual(
            response.json()["restart_required"],
            [
                "History recording",
                "History retention",
                "History size limit",
                "Recorded sensor selection",
            ],
        )


if __name__ == "__main__":
    unittest.main()
