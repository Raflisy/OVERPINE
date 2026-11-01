from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import config
from backend.main import create_app
from backend.services import device_notes
from backend.services.device_notes import _device_motto, _lookup_wikipedia_fact


def _client() -> TestClient:
    # Host must be 127.0.0.1 or localhost (TrustedHostMiddleware).
    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


def test_health_ok():
    r = _client().get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ram_mb" in body["self"]


def test_system_info_detects_host_and_resources():
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
    with patch(
        "backend.services.hardware_inventory._cached_inventory",
        return_value=inventory,
    ), patch(
        "backend.services.system_info.get_live_windows_sensors",
        return_value={"gpuUsagePercent": 12.5},
    ):
        response = _client().get("/api/v1/system")
    assert response.status_code == 200
    body = response.json()
    assert body["device_name"]
    assert body["operating_system"]
    assert body["architecture"]
    assert body["processor"]
    assert body["device_model"]
    assert body["manufacturer"]
    assert isinstance(body["graphics_adapters"], list)
    assert body["hardware_sections"]
    assert {
        section["title"] for section in body["hardware_sections"]
    } >= {
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
    }
    assert body["hardware_inventory_status"]
    assert body["battery"] is None or 0 <= body["battery"]["percent"] <= 100
    assert body["physical_cores"] is None or body["physical_cores"] > 0
    assert body["logical_processors"] is None or body["logical_processors"] > 0
    assert body["memory_total_gib"] > 0
    assert 0 <= body["memory_used_percent"] <= 100
    assert body["gpu_usage_percent"] == 12.5
    assert body["cpu_frequency_mhz"] is None or body["cpu_frequency_mhz"] > 0
    assert body["disk_read_bytes_per_sec"] is None or body["disk_read_bytes_per_sec"] >= 0
    assert body["network_receive_bytes_per_sec"] is None or body["network_receive_bytes_per_sec"] >= 0
    assert body["system_drive"] is None or body["system_drive"]["total_gib"] > 0


def test_device_fact_searches_public_source_using_model_only():
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

    assert "premium business laptops" in fact["fact"]
    assert fact["source"] == "Wikipedia · ThinkPad X1"
    assert fact["source_url"] == "https://en.wikipedia.org/wiki/ThinkPad_X1"
    search_url = open_url.call_args_list[0].args[0].full_url
    assert "ThinkPad" in search_url
    assert "DESKTOP-" not in search_url
    assert "serial" not in search_url.casefold()


def test_device_fact_requires_specific_model_identifier_match():
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

    assert fact["source"] == "Wikipedia · ThinkPad L series"
    assert "ThinkPad L series" in fact["fact"]
    assert open_url.call_count == 2


def test_wikipedia_lookup_collects_multiple_sourced_facts():
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

    assert len(facts) == 4
    assert {fact["source"] for fact in facts} == {
        "Wikipedia · ThinkPad X1",
        "Wikipedia · ThinkPad X1 Carbon",
    }
    assert all(fact["fact"] and fact["source_url"] for fact in facts)


def test_device_fact_and_motto_do_not_repeat_on_successive_refreshes(monkeypatch):
    cache_key = "lenovo|thinkpad refresh test"
    monkeypatch.setitem(
        device_notes._fact_cache,
        cache_key,
        (
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
        ),
    )
    monkeypatch.setitem(
        device_notes._last_fact_by_device,
        cache_key,
        "A first sourced device fact.",
    )
    monkeypatch.setitem(
        device_notes._last_motto_by_device,
        cache_key,
        _device_motto("Lenovo", "ThinkPad refresh test")["text"],
    )

    first_fact = device_notes._device_fact("Lenovo", "ThinkPad refresh test")
    second_fact = device_notes._device_fact("Lenovo", "ThinkPad refresh test")
    first_motto = _device_motto("Lenovo", "ThinkPad refresh test")["text"]
    second_motto = _device_motto("Lenovo", "ThinkPad refresh test")["text"]

    assert first_fact["fact"] != second_fact["fact"]
    assert first_motto != second_motto


def test_unmatched_model_does_not_get_a_brand_only_fact():
    response = b'{"query":{"search":[{"title":"Lenovo"}]}}'
    with patch(
        "backend.services.device_notes.urlopen",
        return_value=BytesIO(response),
    ) as open_url:
        fact = _lookup_wikipedia_fact("Lenovo", "Lenovo 20VLS23T00")

    assert fact["fact"] is None
    assert fact["source_url"] is None
    open_url.assert_called_once()


def test_device_motto_is_original_and_matches_manufacturer():
    motto = _device_motto(
        "LENOVO",
        "LENOVO ThinkPad L13 Yoga Gen 2",
    )
    assert motto["text"]
    assert "thinkpad" in motto["attribution"].casefold()
    assert "overpine" in motto["attribution"].casefold()


def test_invalid_config_settings_fall_back_to_safe_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "default_config.toml"
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
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    settings = config.load_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert settings.log_file == "logs/overpine.log"


def test_foreign_host_rejected():
    r = TestClient(create_app(), base_url="http://evil.example").get("/api/v1/health")
    assert r.status_code == 400


def test_dashboard_and_assets_are_served():
    client = _client()
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'id="theme-toggle"' in dashboard.text
    assert 'id="page-title">' in dashboard.text
    assert 'id="processor-value"' in dashboard.text
    assert 'id="graphics-value"' in dashboard.text
    assert 'id="system-cpu-value"' in dashboard.text
    assert 'id="battery-value"' in dashboard.text
    assert 'id="device-fact"' in dashboard.text
    assert 'id="device-brand-logo"' in dashboard.text
    assert 'id="device-family-logo"' in dashboard.text
    assert 'id="cpu-chart-line"' in dashboard.text
    assert 'id="memory-chart-line"' in dashboard.text
    assert 'id="disk-read-line"' in dashboard.text
    assert 'id="network-receive-line"' in dashboard.text
    assert 'id="gpu-chart-line"' in dashboard.text
    assert 'id="memory-composition-used"' in dashboard.text
    assert 'id="hardware-details-grid"' in dashboard.text
    assert 'id="hardware-inventory-status"' in dashboard.text
    assert 'id="device-motto"' in dashboard.text
    assert 'id="device-motto-attribution"' not in dashboard.text
    assert "/css/tokens.css" in dashboard.text
    assert "/js/app.js" in dashboard.text

    assert client.get("/css/tokens.css").status_code == 200
    assert client.get("/css/components.css").status_code == 200
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/images/overpine-mark.png").headers["content-type"] == "image/png"


def test_device_note_endpoint_returns_sourced_fact_and_attribution():
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
    ):
        response = _client().get("/api/v1/device-note")

    assert response.status_code == 200
    assert response.json() == note
