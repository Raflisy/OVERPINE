"""Mocked monitoring registry, provider, scheduler, and API tests."""
from __future__ import annotations

import io
import json
import logging
import subprocess
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings, load_settings
from backend.monitoring.models import Sensor, SensorState
from backend.monitoring.registry import SensorRegistry
from backend.monitoring.scheduler import MonitoringScheduler
from backend.providers.base import SensorProvider
from backend.providers.lhm_web_provider import (
    LhmWebProvider,
    parse_lhm_number,
    parse_lhm_tree,
)
from backend.providers.simulated_provider import SimulatedProvider
from backend.providers.wmi_thermal_provider import (
    WmiThermalProvider,
    tenths_kelvin_to_celsius,
)
from backend.stress.manager import StressManager


class StaticProvider(SensorProvider):
    """Deterministic provider for scheduler tests."""

    name = "test-provider"
    poll_categories = ("cpu",)

    def __init__(self, value: float | None = 12.5) -> None:
        self.value = value
        self.read_count = 0

    def discover(self) -> list[Sensor]:
        return [Sensor("cpu.test", "Test CPU sensor", "cpu", "%", self.name)]

    def read(self) -> dict[str, float | None]:
        self.read_count += 1
        return {"cpu.test": self.value}


class FailingProvider(SensorProvider):
    """Provider whose failures must not terminate the scheduler loop."""

    name = "failing-provider"
    poll_categories = ("cpu",)

    def discover(self) -> list[Sensor]:
        raise RuntimeError("deliberate test failure")

    def read(self) -> dict[str, float | None]:
        raise AssertionError("read should not run after discover fails")


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_registry_running_statistics_and_bounded_history():
    registry = SensorRegistry(history_size=2)
    registry.register(Sensor("cpu.temp", "CPU temperature", "cpu", "C", "test"))

    registry.update("cpu.temp", 20.0)
    registry.update("cpu.temp", 30.0)
    registry.update("cpu.temp", 10.0)
    sensor = registry.get("cpu.temp")

    assert sensor is not None
    assert (sensor.min, sensor.max, sensor.avg) == (10.0, 30.0, 20.0)
    assert [sample["value"] for sample in registry.history("cpu.temp")] == [30.0, 10.0]
    assert len(registry.history("cpu.temp")) <= 2


def test_registry_preserves_missing_values_as_null():
    registry = SensorRegistry()
    registry.register(Sensor("cpu.temp", "CPU temperature", "cpu", "C", "test"))

    sensor = registry.update("cpu.temp", None)

    assert sensor.value is None
    assert sensor.state == SensorState.UNAVAILABLE
    assert registry.history("cpu.temp")[-1]["value"] is None
    assert sensor.min is None
    assert sensor.max is None
    assert sensor.avg is None


def test_registry_thread_safety_smoke():
    registry = SensorRegistry(history_size=100)
    registry.register(Sensor("cpu.load", "CPU load", "cpu", "%", "test"))
    errors: list[Exception] = []

    def update_range(start: int) -> None:
        try:
            for value in range(start, start + 100):
                registry.update("cpu.load", float(value))
        except Exception as exc:  # noqa: BLE001 - the test intentionally tracks any worker failure
            errors.append(exc)

    threads = [threading.Thread(target=update_range, args=(base,)) for base in (0, 100, 200, 300)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    sensor = registry.get("cpu.load")
    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sensor is not None and sensor.min == 0.0 and sensor.max == 399.0
    assert len(registry.history("cpu.load")) == 100


def test_lhm_parser_handles_comma_decimals_missing_values_and_tree_nodes():
    fixture = {
        "Text": "Computer",
        "Children": [
            {
                "Text": "CPU",
                "Children": [
                    {
                        "Text": "CPU Package",
                        "Type": "Temperature",
                        "Value": "52,0 °C",
                        "SensorId": "/intelcpu/0/temperature/1",
                    },
                    {
                        "Text": "CPU Package",
                        "Type": "Power",
                        "Value": "12.5 W",
                        "SensorId": "/intelcpu/0/power/0",
                    },
                    {
                        "Text": "Core #1",
                        "Type": "Temperature",
                        "Value": "-",
                        "SensorId": "/intelcpu/0/temperature/2",
                    },
                    {"Text": "Node without a sensor value", "Children": []},
                ],
            }
        ],
    }

    sensors, values = parse_lhm_tree(fixture)

    assert parse_lhm_number("52,0 °C") == 52.0
    assert parse_lhm_number("-") is None
    assert parse_lhm_number(None) is None
    assert values["cpu.package.temperature"] == 52.0
    assert values["cpu.package.power"] == 12.5
    assert any(sensor.value is None for sensor in sensors)
    assert all(sensor.source == "lhm-web" for sensor in sensors)


def test_lhm_provider_rejects_non_loopback_hosts():
    with pytest.raises(ValueError, match="127.0.0.1"):
        LhmWebProvider(host="example.com")
    with pytest.raises(ValueError, match="127.0.0.1"):
        LhmWebProvider(host="192.168.1.10")


def test_lhm_unreachable_server_marks_sensors_unavailable_and_logs_once(
    monkeypatch,
    caplog,
):
    payload = {
        "Text": "Computer",
        "Children": [{
            "Text": "CPU",
            "Children": [{
                "Text": "CPU Package",
                "Type": "Temperature",
                "Value": "45.0 °C",
                "SensorId": "/intelcpu/0/temperature/1",
            }],
        }],
    }
    monkeypatch.setattr(
        "backend.providers.lhm_web_provider.urllib.request.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(json.dumps(payload).encode()),
    )
    provider = LhmWebProvider()
    discovered = provider.discover()
    assert discovered

    def unreachable(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "backend.providers.lhm_web_provider.urllib.request.urlopen",
        unreachable,
    )
    caplog.set_level(logging.INFO, logger="overpine")

    assert provider.read()["cpu.package.temperature"] is None
    assert provider.read()["cpu.package.temperature"] is None
    assert provider.capabilities()["lhm_running"] is False
    state, message = provider.sensor_states()["cpu.package.temperature"]
    assert state == SensorState.UNAVAILABLE
    assert message == "LibreHardwareMonitor web server not reachable on 127.0.0.1:8085"
    assert sum("web server is not reachable" in record.message for record in caplog.records) == 1


def test_wmi_temperature_conversion_and_missing_class_state(monkeypatch):
    assert tenths_kelvin_to_celsius(2981.5) == pytest.approx(25.0)
    monkeypatch.setattr(
        "backend.providers.wmi_thermal_provider.platform.system",
        lambda: "Windows",
    )
    monkeypatch.setattr(
        "backend.providers.wmi_thermal_provider.shutil.which",
        lambda _: "powershell.exe",
    )
    monkeypatch.setattr(
        "backend.providers.wmi_thermal_provider.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["powershell.exe"],
            returncode=0,
            stdout=json.dumps(
                {"state": "error", "message": "Invalid class MSAcpi_ThermalZoneTemperature"}
            ),
            stderr="",
        ),
    )
    provider = WmiThermalProvider()

    sensors = provider.discover()

    assert sensors[0].state == SensorState.UNSUPPORTED
    assert "not exposed" in (sensors[0].error_message or "")


def test_scheduler_survives_provider_exceptions_and_caps_backoff():
    scheduler = MonitoringScheduler(
        providers=[FailingProvider()],
        intervals={"cpu": 0.5},
    )
    runtime = scheduler._runtimes["failing-provider"]
    scheduler.start()
    try:
        assert _wait_for(lambda: runtime.failures > 0)
        assert scheduler._thread is not None and scheduler._thread.is_alive()
        observed_delays = []
        for index in range(8):
            scheduler._record_failure(runtime, f"failure-{index}")
            observed_delays.append(runtime.next_due - time.monotonic())
        assert observed_delays[-1] <= 60.0
        assert observed_delays[-1] > 59.0
    finally:
        scheduler.stop()


def test_scheduler_pause_resume_and_null_provider_values():
    provider = StaticProvider(value=None)
    scheduler = MonitoringScheduler(
        providers=[provider],
        intervals={"cpu": 0.5},
    )
    scheduler.start()
    try:
        assert _wait_for(
            lambda: provider.read_count >= 1
            and scheduler.registry.get("cpu.test") is not None
        )
        sensor = scheduler.registry.get("cpu.test")
        assert sensor is not None
        assert sensor.value is None
        assert sensor.state == SensorState.UNAVAILABLE
        scheduler.pause()
        paused_count = provider.read_count
        time.sleep(0.65)
        assert provider.read_count == paused_count
        scheduler.resume()
        assert _wait_for(lambda: provider.read_count > paused_count)
    finally:
        scheduler.stop()


def test_simulated_provider_is_opt_in_and_explicitly_labelled():
    assert SimulatedProvider(enabled=False).discover() == []
    provider = SimulatedProvider(enabled=True)

    assert provider.discover()
    assert all(sensor.id.startswith("sim.") for sensor in provider.discover())
    assert all(sensor.source == "SIMULATED" for sensor in provider.discover())
    assert all(sensor_id.startswith("sim.") for sensor_id in provider.read())


def _api_client(monkeypatch):
    import backend.main as main_module

    monkeypatch.setattr(main_module, "setup_logging", lambda _settings: None)
    monkeypatch.setattr(main_module, "ensure_user_directories", lambda: None)
    app = main_module.create_app(Settings())
    scheduler = MonitoringScheduler(providers=[])
    scheduler.registry.register(
        Sensor("cpu.package.temperature", "CPU package", "cpu", "C", "lhm-web")
    )
    scheduler.registry.update("cpu.package.temperature", 52.0)
    app.state.monitoring_scheduler = scheduler
    return app, scheduler, TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 8000),
    )


def test_sensor_api_filters_history_404_and_invalid_ids(monkeypatch):
    app, scheduler, client = _api_client(monkeypatch)
    try:
        response = client.get("/api/v1/sensors?category=cpu&state=AVAILABLE")
        assert response.status_code == 200
        assert len(response.json()["sensors"]) == 1
        assert client.get("/api/v1/sensors?category=fan").json()["sensors"] == []
        assert client.get("/api/v1/sensors/cpu.package.temperature").status_code == 200
        history = client.get(
            "/api/v1/sensors/cpu.package.temperature/history?limit=1"
        )
        assert history.status_code == 200
        assert len(history.json()["history"]) == 1
        assert client.get("/api/v1/sensors/no.such.sensor").status_code == 404
        assert client.get("/api/v1/sensors/%40outside").status_code == 422
    finally:
        scheduler.stop()
        app.state.stress_manager.shutdown()


def test_monitoring_pause_resume_requires_token(monkeypatch):
    app, scheduler, client = _api_client(monkeypatch)
    token = app.state.stress_token
    scheduler.start()
    try:
        assert client.post("/api/v1/monitoring/pause").status_code == 401
        assert client.post(
            "/api/v1/monitoring/pause",
            headers={"X-Overpine-Token": token},
        ).status_code == 200
        assert client.post(
            "/api/v1/monitoring/resume",
            headers={"X-Overpine-Token": "invalid"},
        ).status_code == 401
        response = client.post(
            "/api/v1/monitoring/resume",
            headers={"X-Overpine-Token": token},
        )
        assert response.status_code == 200
        assert response.json()["paused"] is False
    finally:
        scheduler.stop()
        app.state.stress_manager.shutdown()


def test_stress_safety_uses_only_available_registry_cpu_package_temperature(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        "backend.stress.manager.psutil.virtual_memory",
        lambda: SimpleNamespace(total=100, available=50),
    )
    monkeypatch.setattr(
        "backend.stress.manager.psutil.disk_usage",
        lambda _: SimpleNamespace(free=4 * 1024**3),
    )
    monkeypatch.setattr(
        "backend.stress.manager.psutil.sensors_battery",
        lambda: None,
    )
    registry = SensorRegistry()
    registry.register(Sensor("cpu.package.temperature", "CPU package", "cpu", "C", "lhm-web"))
    registry.set_state(
        "cpu.package.temperature",
        SensorState.UNAVAILABLE,
        "Not reported",
    )
    manager = StressManager(
        data_dir=tmp_path,
        min_system_free_bytes=0,
        sensor_registry=registry,
    )

    assert manager._check_safety() is None
    assert manager._temperature_status == "temperature not monitored"
    assert manager._cpu_temperature_c is None

    registry.update("cpu.package.temperature", 95.0)
    assert manager._check_safety() == "CPU temperature reached 95 C"


def test_monitoring_config_validation_and_version(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "default_config.toml"
    config_path.write_text(
        '[server]\nhost = "127.0.0.1"\n'
        '[monitoring]\nhistory_size = 9000\nsimulation = true\n'
        '[monitoring.intervals]\ncpu = 0.25\nmemory = 3.5\n'
        '[lhm]\nenabled = true\nhost = "example.com"\nport = 99999\n'
        '[smart]\nenabled = true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.core.config.CONFIG_FILE", config_path)
    monkeypatch.setattr("backend.core.config.user_data_dir", lambda: tmp_path / "data")

    settings = load_settings()

    assert settings.monitoring_history_size == 3600
    assert settings.monitoring_simulation is True
    assert settings.monitoring_intervals["cpu"] == 1.0
    assert settings.monitoring_intervals["memory"] == 3.5
    assert settings.lhm_host == "127.0.0.1"
    assert settings.lhm_port == 8085
    assert settings.smart_enabled is True
    from backend.core.config import APP_VERSION

    assert APP_VERSION == "0.4.0"
