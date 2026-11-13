"""Health, system information, device notes, and static asset tests."""
from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.core import config
from backend.core.config import Settings
from backend.monitoring.scheduler import MonitoringScheduler
from backend.services import device_notes
from backend.services.device_notes import _device_motto, _lookup_wikipedia_fact
from tests.http_client import LocalHTTPTestClient
from tests.test_support import create_isolated_app
from backend.winapi.cpu import CpuCache, CpuFrequency, ProcessorTopology


def _client(test_case: unittest.TestCase) -> LocalHTTPTestClient:
    """Create an isolated real-HTTP client on an ephemeral loopback port."""
    app = create_isolated_app(
        test_case,
        Settings(port=0),
        configure_logging=False,
    )
    app.monitoring_scheduler = MonitoringScheduler(providers=[])
    return LocalHTTPTestClient(app)


class HealthTests(unittest.TestCase):
    """Exercise the system API over a real local HTTP connection."""

    def test_health_ok(self) -> None:
        with _client(self) as client:
            response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("ram_mb", body["self"])

    def test_system_info_detects_host_and_resources(self) -> None:
        inventory = {
            "computer": {"Manufacturer": "Example", "Model": "Model"},
            "board": {"Manufacturer": "Example", "Product": "Board"},
            "bios": {"Manufacturer": "Example", "SMBIOSBIOSVersion": "1.0"},
            "processors": [{"Name": "Example CPU", "NumberOfCores": 4}],
            "memory": [],
            "graphics": [],
            "disks": [],
            "controllers": [],
            "firmwareType": None,
            "secureBoot": None,
        }
        with (
            patch(
                "backend.services.hardware_inventory._cached_inventory",
                return_value=inventory,
            ),
            patch(
                "backend.services.system_info.get_live_windows_sensors",
                return_value={"gpuUsagePercent": 12.5},
            ),
            patch(
                "backend.services.system_info.processor_topology",
                return_value=ProcessorTopology(
                    4,
                    8,
                    (CpuCache(2, 1024**2, 0), CpuCache(3, 8 * 1024**2, 0)),
                ),
            ),
            patch(
                "backend.services.system_info.cpu_frequency",
                return_value=CpuFrequency(1200.0, 2800.0),
            ),
            patch(
                "backend.services.system_info.read_network_interfaces",
                return_value=[],
            ),
            _client(self) as client,
        ):
            response = client.get("/api/v1/system")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            "device_name",
            "operating_system",
            "architecture",
            "processor",
            "device_model",
            "manufacturer",
        ):
            self.assertTrue(body[key])
        self.assertIsInstance(body["graphics_adapters"], list)
        self.assertTrue(body["hardware_sections"])
        self.assertGreaterEqual(
            {section["title"] for section in body["hardware_sections"]},
            {
                "Computer & motherboard",
                "Processor",
                "Memory",
                "Graphics",
                "Displays",
                "Audio",
                "Network",
                "Power & battery",
                "Storage",
                "System & security",
                "Live readings",
            },
        )
        self.assertTrue(body["hardware_inventory_status"])
        self.assertTrue(
            body["battery"] is None
            or 0 <= body["battery"]["percent"] <= 100
        )
        self.assertTrue(
            body["physical_cores"] is None or body["physical_cores"] > 0
        )
        self.assertTrue(
            body["logical_processors"] is None
            or body["logical_processors"] > 0
        )
        self.assertGreater(body["memory_total_gib"], 0)
        self.assertGreaterEqual(body["memory_used_percent"], 0)
        self.assertLessEqual(body["memory_used_percent"], 100)
        self.assertEqual(body["gpu_usage_percent"], 12.5)
        self.assertTrue(
            body["cpu_frequency_mhz"] is None or body["cpu_frequency_mhz"] > 0
        )
        self.assertTrue(
            body["disk_read_bytes_per_sec"] is None
            or body["disk_read_bytes_per_sec"] >= 0
        )
        self.assertTrue(
            body["network_receive_bytes_per_sec"] is None
            or body["network_receive_bytes_per_sec"] >= 0
        )
        self.assertTrue(
            body["system_drive"] is None
            or body["system_drive"]["total_gib"] > 0
        )

    def test_device_fact_searches_public_source_using_model_only(self) -> None:
        search_result = b'{"query":{"search":[{"title":"ThinkPad X1"}]}}'
        summary_result = (
            b'{"extract":"The ThinkPad X1 is a family of premium business laptops."}'
        )
        with patch(
            "backend.services.device_notes.urlopen",
            side_effect=[
                BytesIO(search_result),
                BytesIO(summary_result),
            ],
        ) as open_url:
            fact = _lookup_wikipedia_fact(
                "Lenovo",
                "Lenovo ThinkPad X1 Carbon Gen 11",
            )

        self.assertIn("premium business laptops", fact["fact"])
        self.assertEqual(fact["source"], "Wikipedia · ThinkPad X1")
        self.assertEqual(
            fact["source_url"],
            "https://en.wikipedia.org/wiki/ThinkPad_X1",
        )
        search_url = open_url.call_args_list[0].args[0].full_url
        self.assertIn("ThinkPad", search_url)
        self.assertNotIn("DESKTOP-", search_url)
        self.assertNotIn("serial", search_url.casefold())

    def test_device_fact_requires_specific_model_identifier_match(self) -> None:
        response = (
            b'{"query":{"search":['
            b'{"title":"ThinkPad Yoga","snippet":"A 2013 convertible laptop."},'
            b'{"title":"ThinkPad L series","snippet":"Specifications for ThinkPad L13 Yoga Gen 2."}'
            b']}}'
        )
        summary = b'{"extract":"The ThinkPad L series includes business laptops."}'
        with patch(
            "backend.services.device_notes.urlopen",
            side_effect=[BytesIO(response), BytesIO(summary)],
        ) as open_url:
            fact = _lookup_wikipedia_fact("Lenovo", "ThinkPad L13 Yoga Gen 2")

        self.assertEqual(fact["source"], "Wikipedia · ThinkPad L series")
        self.assertIn("ThinkPad L series", fact["fact"])
        self.assertEqual(open_url.call_count, 2)

    def test_wikipedia_lookup_collects_multiple_sourced_facts(self) -> None:
        search_result = (
            b'{"query":{"search":['
            b'{"title":"ThinkPad X1","snippet":"Lenovo ThinkPad X1 computer."},'
            b'{"title":"ThinkPad X1 Carbon","snippet":"Lenovo ThinkPad X1 Carbon laptop."}'
            b']}}'
        )               
        first_summary = (
            b'{"extract":"The ThinkPad X1 is a laptop family. '
            b'Its models have included convertible designs."}'
        )
        second_summary = (
            b'{"extract":"The ThinkPad X1 Carbon is a lightweight laptop. '
            b'It has been released in multiple generations."}'
        )
        with patch(
            "backend.services.device_notes.urlopen",
            side_effect=[
                BytesIO(search_result),
                BytesIO(first_summary),
                BytesIO(second_summary),
            ],
        ):
            facts = device_notes._lookup_wikipedia_facts("Lenovo", "ThinkPad X1")

        self.assertEqual(len(facts), 4)
        self.assertEqual(
            {fact["source"] for fact in facts},
            {"Wikipedia · ThinkPad X1", "Wikipedia · ThinkPad X1 Carbon"},
        )
        self.assertTrue(all(fact["fact"] and fact["source_url"] for fact in facts))

    def test_device_fact_and_motto_do_not_repeat_on_successive_refreshes(self) -> None:
        cache_key = "lenovo|thinkpad refresh test"
        with (
            patch.dict(
                device_notes._fact_cache,
                {
                    cache_key: (
                        device_notes.time.monotonic(),
                        [
                            {
                                "fact": "A first sourced device fact.",
                                "source": "Wikipedia · ThinkPad",
                                "source_url": "https://en.wikipedia.org/wiki/ThinkPad",
                                "message": None,
                            },
                            {
                                "fact": "A second sourced device fact.",
                                "source": "Wikipedia · ThinkPad",
                                "source_url": "https://en.wikipedia.org/wiki/ThinkPad",
                                "message": None,
                            },
                        ],
                    )
                },
            ),
            patch.dict(
                device_notes._last_fact_by_device,
                {cache_key: "A first sourced device fact."},
            ),
            patch.dict(
                device_notes._last_motto_by_device,
                {
                    cache_key: _device_motto(
                        "Lenovo",
                        "ThinkPad refresh test",
                    )["text"]
                },
            ),
        ):
            first_fact = device_notes._device_fact(
                "Lenovo",
                "ThinkPad refresh test",
            )
            second_fact = device_notes._device_fact(
                "Lenovo",
                "ThinkPad refresh test",
            )
            first_motto = _device_motto(
                "Lenovo",
                "ThinkPad refresh test",
            )["text"]
            second_motto = _device_motto(
                "Lenovo",
                "ThinkPad refresh test",
            )["text"]

        self.assertNotEqual(first_fact["fact"], second_fact["fact"])
        self.assertNotEqual(first_motto, second_motto)

    def test_unmatched_model_does_not_get_a_brand_only_fact(self) -> None:
        response = b'{"query":{"search":[{"title":"Lenovo"}]}}'
        with patch(
            "backend.services.device_notes.urlopen",
            return_value=BytesIO(response),
        ) as open_url:
            fact = _lookup_wikipedia_fact("Lenovo", "Lenovo 20VLS23T00")

        self.assertIsNone(fact["fact"])
        self.assertIsNone(fact["source_url"])
        open_url.assert_called_once()

    def test_device_motto_is_original_and_matches_manufacturer(self) -> None:
        motto = _device_motto(
            "LENOVO",
            "LENOVO ThinkPad L13 Yoga Gen 2",
        )
        self.assertTrue(motto["text"])
        self.assertIn("thinkpad", motto["attribution"].casefold())
        self.assertIn("overpine", motto["attribution"].casefold())

    def test_invalid_config_settings_fall_back_to_safe_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "default_config.toml"
            config_path.write_text(
                """
[server]
host = "example.com"
port = "not-a-port"

[logging]
level = "not-a-level"
file = ""
""".strip(),
                encoding="utf-8",
            )
            with (
                patch.object(config, "CONFIG_FILE", config_path),
                patch.object(
                    config,
                    "user_data_dir",
                    return_value=Path(directory),
                ),
            ):
                settings = config.load_settings()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8000)
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.log_file, "logs/overpine.log")

    def test_foreign_host_rejected(self) -> None:
        with _client(self) as client:
            response = client.get(
                "/api/v1/health",
                headers={"Host": "evil.example"},
            )
        self.assertEqual(response.status_code, 400)

    def test_dashboard_and_assets_are_served(self) -> None:
        with _client(self) as client:
            dashboard = client.get("/")
            self.assertEqual(dashboard.status_code, 200)
            for content in (
                'id="theme-toggle"',
                'id="page-title">',
                'id="processor-value"',
                'id="graphics-value"',
                'id="system-cpu-value"',
                'id="battery-value"',
                'id="device-fact"',
                'id="cpu-monitor-chart"',
                'id="memory-monitor-chart"',
                'id="disk-monitor-chart"',
                'id="network-monitor-chart"',
                'id="gpu-monitor-chart"',
                'id="online-device-lookups"',
                'id="sensor-history-chart"',
                'id="stress-chart"',
                'id="memory-composition-used"',
                'id="hardware-details-grid"',
                'id="hardware-inventory-status"',
                'id="device-motto"',
                "/css/tokens.css",
                "/js/app.js",
            ):
                self.assertIn(content, dashboard.text)
            self.assertNotIn('id="device-motto-attribution"', dashboard.text)
            self.assertEqual(client.get("/css/tokens.css").status_code, 200)
            self.assertEqual(client.get("/css/components.css").status_code, 200)
            self.assertEqual(client.get("/js/app.js").status_code, 200)
            self.assertEqual(
                client.get("/js/charts/line-chart.js").status_code,
                200,
            )
            self.assertEqual(
                client.get("/js/pages/sensors.js").status_code,
                200,
            )
            self.assertEqual(
                client.get("/js/pages/stress.js").status_code,
                200,
            )
            self.assertEqual(
                client.get("/vendor/chart.umd.min.js").status_code,
                404,
            )
            self.assertEqual(
                client.get("/images/overpine-mark.png").headers["content-type"],
                "image/png",
            )

    def test_device_note_endpoint_returns_sourced_fact_and_attribution(self) -> None:
        note = {
            "fact": "A verified device fact.",
            "source": "Wikipedia · Example",
            "source_url": "https://en.wikipedia.org/wiki/Example",
            "message": None,
            "motto": "Small machine, big plans.",
            "motto_attribution": "OVERPINE · original device wit",
        }
        with (
            patch(
                "backend.api.routes.system.get_system_info",
                return_value={"manufacturer": "Example", "device_model": "Model"},
            ),
            patch("backend.api.routes.system.get_device_note", return_value=note),
            _client(self) as client,
        ):
            response = client.get("/api/v1/device-note")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), note)

    def test_device_note_is_offline_by_default(self) -> None:
        with (
            patch(
                "backend.services.device_notes.urlopen",
                side_effect=AssertionError("unexpected external request"),
            ) as open_url,
            patch(
                "backend.api.routes.system.get_system_info",
                return_value={"manufacturer": "Example", "device_model": "Model"},
            ),
            _client(self) as client,
        ):
            setting = client.get("/api/v1/settings/online-lookups")
            response = client.get("/api/v1/device-note")
            note = device_notes.get_device_note(
                {"manufacturer": "Example", "device_model": "Model"},
            )

        self.assertEqual(setting.json(), {"enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["fact"])
        self.assertIsNone(response.json()["source_url"])
        self.assertIn("disabled", response.json()["message"])
        self.assertIsNone(note["fact"])
        self.assertIsNone(note["source_url"])
        self.assertIn("disabled", note["message"])
        self.assertTrue(note["motto"])
        open_url.assert_not_called()

    def test_online_lookup_setting_requires_token_and_boolean(self) -> None:
        with _client(self) as client:
            forbidden = client.post(
                "/api/v1/settings/online-lookups",
                json_body={"enabled": True},
            )
            token = client.get("/api/v1/stress/token").json()["token"]
            invalid = client.post(
                "/api/v1/settings/online-lookups",
                headers={"X-Overpine-Token": token},
                json_body={"enabled": "true"},
            )
            enabled = client.post(
                "/api/v1/settings/online-lookups",
                headers={"X-Overpine-Token": token},
                json_body={"enabled": True},
            )
            current = client.get("/api/v1/settings/online-lookups")

        self.assertEqual(forbidden.status_code, 401)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.json(), {"enabled": True})
        self.assertEqual(current.json(), {"enabled": True})


if __name__ == "__main__":
    unittest.main()
