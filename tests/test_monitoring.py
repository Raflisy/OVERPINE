"""Mocked monitoring registry, provider, scheduler, and API tests."""
from __future__ import annotations

import json
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from backend.core.config import Settings, load_settings
from backend.main import create_app
from backend.monitoring.models import Sensor, SensorState
from backend.monitoring.registry import SensorRegistry
from backend.monitoring.scheduler import MonitoringScheduler
from backend.providers.base import SensorProvider
from backend.providers.io_provider import IoProvider
from backend.providers.wmi_thermal_provider import (
    WmiThermalProvider,
    tenths_kelvin_to_celsius,
)
from backend.stress.manager import StressManager
from tests.http_client import LocalHTTPTestClient
from backend.winapi.disk import DiskIoSample
from backend.winapi.network import NetworkRates
from backend.winapi.pdh import PdhCounterSample


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


def _check_registry_running_statistics_and_bounded_history():
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


def _check_registry_preserves_missing_values_as_null():
    registry = SensorRegistry()
    registry.register(Sensor("cpu.temp", "CPU temperature", "cpu", "C", "test"))

    sensor = registry.update("cpu.temp", None)

    assert sensor.value is None
    assert sensor.state == SensorState.UNAVAILABLE
    assert registry.history("cpu.temp")[-1]["value"] is None
    assert sensor.min is None
    assert sensor.max is None
    assert sensor.avg is None


def _check_registry_thread_safety_smoke():
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


def _check_wmi_temperature_conversion_and_missing_class_state():
    assert abs(tenths_kelvin_to_celsius(2981.5) - 25.0) < 1e-9
    with (
        patch(
            "backend.providers.wmi_thermal_provider.platform.system",
            return_value="Windows",
        ),
        patch(
            "backend.providers.wmi_thermal_provider.load_settings",
            return_value=Settings(allow_external_programs=True),
        ),
        patch(
            "backend.providers.wmi_thermal_provider.shutil.which",
            return_value="powershell.exe",
        ),
        patch(
            "backend.providers.wmi_thermal_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["powershell.exe"],
                returncode=0,
                stdout=json.dumps(
                    {
                        "state": "error",
                        "message": "Invalid class MSAcpi_ThermalZoneTemperature",
                    }
                ),
                stderr="",
            ),
        ),
    ):
        provider = WmiThermalProvider()
        sensors = provider.discover()

    assert sensors[0].state == SensorState.UNSUPPORTED
    assert "not exposed" in (sensors[0].error_message or "")


def _check_scheduler_survives_provider_exceptions_and_caps_backoff():
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


def _check_scheduler_pause_resume_and_null_provider_values():
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


def _api_client():
    test_data_dir = TemporaryDirectory()
    root = Path(test_data_dir.name).resolve()
    try:
        with (
            patch("backend.main.DATA_DIR", root / "data"),
            patch(
                "backend.core.config.CONFIG_FILE",
                root / "config" / "default_config.toml",
            ),
            patch("backend.core.config.user_data_dir", return_value=root),
            patch("backend.stress.manager.DATA_DIR", root / "data" / "stress"),
        ):
            app = create_app(Settings(), configure_logging=False)
    except Exception:
        test_data_dir.cleanup()
        raise
    scheduler = MonitoringScheduler(providers=[])
    scheduler.registry.register(
        Sensor("cpu.package.temperature", "CPU package", "cpu", "C", "windows")
    )
    scheduler.registry.update("cpu.package.temperature", 52.0)
    app.monitoring_scheduler = scheduler
    app.stress_manager = StressManager(sensor_registry=scheduler.registry)
    return app, scheduler, LocalHTTPTestClient(app), test_data_dir


def _close_api_client(client, test_data_dir: TemporaryDirectory) -> None:
    try:
        client.close()
    finally:
        test_data_dir.cleanup()


def _check_sensor_api_filters_history_404_and_invalid_ids():
    app, scheduler, client, test_data_dir = _api_client()
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
        _close_api_client(client, test_data_dir)


def _check_sensor_api_preserves_unsupported_reasons():
    app, scheduler, client, test_data_dir = _api_client()
    scheduler.registry.register(
        Sensor(
            "fan.rpm",
            "Fan speed",
            "fan",
            "RPM",
            "pdh",
            state=SensorState.UNSUPPORTED,
            error_message="Windows does not expose fan speed on this computer",
        )
    )
    scheduler.registry.register(
        Sensor(
            "thermal.wmi.acpi-zone.0",
            "ACPI thermal zone",
            "thermal",
            "C",
            "wmi-thermal",
            state=SensorState.UNSUPPORTED,
            error_message=(
                "External Windows programs are disabled by "
                "[windows_tools] allow_external_programs"
            ),
        )
    )
    try:
        rows = client.get("/api/v1/sensors").json()["sensors"]
        details = {row["id"]: row for row in rows}
        assert details["fan.rpm"]["state"] == SensorState.UNSUPPORTED.value
        assert "does not expose fan speed" in details["fan.rpm"]["error_message"]
        assert details["thermal.wmi.acpi-zone.0"]["state"] == SensorState.UNSUPPORTED.value
        assert "allow_external_programs" in details["thermal.wmi.acpi-zone.0"]["error_message"]
    finally:
        _close_api_client(client, test_data_dir)


def _check_monitoring_pause_resume_requires_token():
    app, scheduler, client, test_data_dir = _api_client()
    token = app.session_token
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
        _close_api_client(client, test_data_dir)


def _check_stress_safety_uses_only_available_registry_cpu_package_temperature(
    tmp_path: Path,
):
    registry = SensorRegistry()
    registry.register(Sensor("cpu.package.temperature", "CPU package", "cpu", "C", "windows"))
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

    with (
        patch(
            "backend.stress.manager.get_memory_status",
            return_value=SimpleNamespace(total_bytes=100, available_bytes=50),
        ),
        patch(
            "backend.stress.manager.disk_usage",
            return_value=SimpleNamespace(free=4 * 1024**3),
        ),
        patch("backend.stress.manager.get_battery_status", return_value=None),
    ):
        assert manager._check_safety() is None
        assert manager._temperature_status == "temperature not monitored"
        assert manager._temperature_c is None

        registry.update("cpu.package.temperature", 95.0)
        assert manager._check_safety() == "Reported temperature reached 95 C"


def _check_monitoring_config_validation_and_version(tmp_path: Path):
    config_path = tmp_path / "default_config.toml"
    config_path.write_text(
        '[server]\nhost = "127.0.0.1"\n'
        '[monitoring]\nhistory_size = 9000\n'
        '[monitoring.intervals]\ncpu = 0.25\nmemory = 3.5\n'
        '[history]\nrecord_sensors = ["cpu.total", "memory.used"]\n'
        '[windows_tools]\nallow_external_programs = true\n',
        encoding="utf-8",
    )
    with (
        patch("backend.core.config.CONFIG_FILE", config_path),
        patch("backend.core.config.user_data_dir", return_value=tmp_path / "data"),
    ):
        settings = load_settings()

    assert settings.monitoring_history_size == 3600
    assert settings.monitoring_intervals["cpu"] == 1.0
    assert settings.monitoring_intervals["memory"] == 3.5
    assert settings.allow_external_programs is True
    assert settings.history_record_sensors == ("cpu.total", "memory.used")
    from backend.core.config import Settings

    assert Settings().allow_external_programs is False
    assert 15 <= len(Settings().history_record_sensors) <= 20
    from backend.core.config import APP_VERSION

    assert APP_VERSION == "0.5.0"


class MonitoringTests(unittest.TestCase):
    """Run provider, scheduler, and API tests under unittest."""

    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.tmp_path = Path(self._temporary_directory.name)

    def test_registry_running_statistics_and_bounded_history(self) -> None:
        _check_registry_running_statistics_and_bounded_history()

    def test_registry_preserves_missing_values_as_null(self) -> None:
        _check_registry_preserves_missing_values_as_null()

    def test_registry_thread_safety_smoke(self) -> None:
        _check_registry_thread_safety_smoke()

    def test_wmi_temperature_conversion_and_missing_class_state(self) -> None:
        _check_wmi_temperature_conversion_and_missing_class_state()

    def test_io_provider_reports_first_sample_as_no_data_yet(self) -> None:
        no_data = PdhCounterSample(None, "no data yet")
        disk = DiskIoSample(no_data, no_data, no_data, None, None, None)
        network = NetworkRates(None, None, "no data yet")
        provider = IoProvider()
        provider.discover()
        with (
            patch("backend.providers.io_rates.disk_io_sample", return_value=disk),
            patch(
                "backend.providers.io_rates.network_rate_sample",
                return_value=network,
            ),
        ):
            readings = provider.read()

        self.assertTrue(all(value is None for value in readings.values()))
        states = provider.sensor_states()
        self.assertEqual(len(states), 4)
        self.assertTrue(
            all(state == SensorState.NOT_INITIALIZED for state, _ in states.values())
        )
        self.assertTrue(
            all("no data yet" in (message or "") for _, message in states.values())
        )

    def test_scheduler_survives_provider_exceptions_and_caps_backoff(self) -> None:
        _check_scheduler_survives_provider_exceptions_and_caps_backoff()

    def test_scheduler_pause_resume_and_null_provider_values(self) -> None:
        _check_scheduler_pause_resume_and_null_provider_values()

    def test_sensor_api_filters_history_404_and_invalid_ids(self) -> None:
        _check_sensor_api_filters_history_404_and_invalid_ids()

    def test_sensor_api_preserves_unsupported_reasons(self) -> None:
        _check_sensor_api_preserves_unsupported_reasons()

    def test_monitoring_pause_resume_requires_token(self) -> None:
        _check_monitoring_pause_resume_requires_token()

    def test_stress_safety_uses_only_available_registry_cpu_package_temperature(
        self,
    ) -> None:
        _check_stress_safety_uses_only_available_registry_cpu_package_temperature(
            self.tmp_path
        )

    def test_monitoring_config_validation_and_version(self) -> None:
        _check_monitoring_config_validation_and_version(self.tmp_path)


if __name__ == "__main__":
    unittest.main()
