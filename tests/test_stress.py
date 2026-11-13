"""Safety and API tests for local stress workloads."""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from backend.api.routes import stress as stress_routes
from backend.monitoring.scheduler import MonitoringScheduler
from backend.stress.base import StressWorker
from backend.stress.disk import DiskStressWorker
from backend.stress.gpu import GpuStressWorker
from backend.stress.manager import SUPPORTED_TARGETS, StressManager
from backend.stress.memory import MemoryStressWorker
from tests.http_client import LocalHTTPTestClient
from tests.test_support import create_isolated_app


class FakeWorker(StressWorker):
    """Low-cost worker used to test manager and API control flow."""
    
    def __init__(self) -> None:
        self.state = "idle"
        self.started = Event()
        self.stopped = Event()

    def start(self, intensity: int, stop_event: Event) -> None:
        self.state = "running"
        self.started.set()

    def stop(self) -> None:
        self.state = "stopped"
        self.stopped.set()

    def status(self) -> dict:
        return {"state": self.state}

    def result_summary(self) -> dict:
        return {"state": self.state}


class StressTestContext:
    """Build an isolated app, manager, token, and HTTP test client."""

    def __init__(
        self,
        test_case: unittest.TestCase,
        tmp_path: Path,
        factories: dict | None = None,
    ) -> None:
        self.manager = StressManager(
            data_dir=tmp_path,
            disk_max_bytes=4096,
            worker_factories=factories or {
                **{target: FakeWorker for target in SUPPORTED_TARGETS},
                "gpu": GpuStressWorker,
            },
            safety_check_interval=0.01,
        )
        self.manager._check_safety = lambda: None
        self.manager._sample_metrics = lambda: None
        self.app = create_isolated_app(test_case, configure_logging=False)
        self.app.monitoring_scheduler = MonitoringScheduler(providers=[])
        self.app.stress_manager = self.manager
        self.client = LocalHTTPTestClient(self.app)
        self.token = self.app.session_token

    def close(self) -> None:
        """Stop the HTTP server and shut down all app services."""
        self.client.close()


def _start(context: StressTestContext, **overrides) -> dict:
    payload = {
        "targets": ["cpu"],
        "duration_s": 5,
        "intensity_percent": 10,
    }
    payload.update(overrides)
    response = context.client.post(
        "/api/v1/stress/start",
        json_body=payload,
        headers={"X-Overpine-Token": context.token},
    )
    return response.json()


def _check_stress_start_rejects_invalid_payload(context, payload):
    response = context.client.post(
        "/api/v1/stress/start",
        json_body=payload,
        headers={"X-Overpine-Token": context.token},
    )
    assert response.status_code == 422


def _check_state_changes_require_the_app_token(context, token):
    headers = {"X-Overpine-Token": token} if token else {}
    payload = {"targets": ["cpu"], "duration_s": 5, "intensity_percent": 10}
    assert context.client.post(
        "/api/v1/stress/start",
        json_body=payload,
        headers=headers,
    ).status_code == 401
    assert context.client.post("/api/v1/stress/stop", headers=headers).status_code == 401


def _check_start_token_is_available_to_loopback_frontend(context):
    response = context.client.get("/api/v1/stress/token")
    assert response.status_code == 200
    assert response.json() == {"token": context.token}


def _check_admin_restart_endpoint_accepts_valid_token_and_schedules_restart(
    context,
):
    """Authorized restart requests are scheduled without running in the test."""
    timer_state = {"started": False}

    class CapturedTimer:
        def __init__(self, interval, function, args=()):
            self.interval = interval
            self.function = function
            self.args = args

        def start(self):
            timer_state["started"] = True

    with (
        patch.object(stress_routes, "is_admin", return_value=False),
        patch.object(stress_routes.threading, "Timer", CapturedTimer),
    ):
        response = context.client.post(
            "/api/v1/admin/restart-as-admin",
            headers={"X-Overpine-Token": context.token},
        )

    assert response.status_code == 202
    assert response.json() == {"status": "restart_requested"}
    assert timer_state["started"] is True


def _check_only_one_run_can_be_active(context):
    first = context.client.post(
        "/api/v1/stress/start",
        json_body={"targets": ["cpu"], "duration_s": 5, "intensity_percent": 10},
        headers={"X-Overpine-Token": context.token},
    )
    second = context.client.post(
        "/api/v1/stress/start",
        json_body={"targets": ["memory"], "duration_s": 5, "intensity_percent": 10},
        headers={"X-Overpine-Token": context.token},
    )
    assert first.status_code == 200
    assert second.status_code == 409
    stopped = context.client.post(
        "/api/v1/stress/stop",
        headers={"X-Overpine-Token": context.token},
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "aborted"


def _check_stop_cleans_disk_temp_file_and_releases_memory(tmp_path: Path):
    disk = DiskStressWorker(tmp_path, max_file_bytes=4096)
    memory = MemoryStressWorker(max_allocation_bytes=128)
    manager = StressManager(
        data_dir=tmp_path,
        disk_max_bytes=4096,
        worker_factories={"disk": lambda: disk, "memory": lambda: memory},
        safety_check_interval=0.01,
    )
    manager._check_safety = lambda: None
    manager._sample_metrics = lambda: None
    manager.start(["disk", "memory"], duration_s=5, intensity=100)
    deadline = time.monotonic() + 2
    while memory.status()["allocated_bytes"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.stop()["state"] == "aborted"
    assert not list((tmp_path / "stress").glob("overpine-stress-*.tmp"))
    assert memory.status()["allocated_bytes"] == 0
    assert disk.status()["temporary_file_active"] is False


def _check_shutdown_waits_for_worker_cleanup(tmp_path: Path):
    worker = FakeWorker()
    manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"cpu": lambda: worker},
        safety_check_interval=0.01,
    )
    manager._check_safety = lambda: None
    manager._sample_metrics = lambda: None
    manager.start(["cpu"], duration_s=5, intensity=10)
    manager.shutdown()
    assert worker.stopped.is_set()
    assert manager.status()["state"] == "aborted"
    assert not manager._run_thread.is_alive()


def _check_live_metrics_and_result_ranges_use_only_sampled_values(
    tmp_path: Path,
):
    manager = StressManager(data_dir=tmp_path)
    io_sample = {
        "disk_read_bps": 1_000_000,
        "disk_write_bps": 2_000_000,
        "net_down_bps": 3_000_000,
        "net_up_bps": 4_000_000,
    }
    with (
        patch(
            "backend.stress.manager.per_core_cpu_percent",
            return_value=[20.0, 40.0],
        ),
        patch(
            "backend.stress.manager.get_memory_status",
            return_value=SimpleNamespace(used_percent=40.0),
        ),
        patch(
            "backend.stress.manager.current_process_metrics",
            return_value=SimpleNamespace(
                cpu_percent=5.0,
                working_set_bytes=2 * 1024**2,
            ),
        ),
        patch.object(manager._io_sampler, "sample", return_value=io_sample),
    ):
        manager._sample_metrics()
        manager._sample_metrics()
        status_metrics = manager.status()["live_metrics"]
        result_metrics = manager._build_result()["metrics"]

    assert status_metrics["cpu_percent_per_core"] == [20.0, 40.0]
    assert status_metrics["memory_percent"] == 40.0
    assert status_metrics["disk_mb_per_second"] == 3.0
    assert status_metrics["network_mb_per_second"] == 7.0
    assert result_metrics["cpu_percent"] == {"min": 30.0, "avg": 30.0, "max": 30.0}
    assert result_metrics["cpu_percent_per_core"] == [
        {"min": 20.0, "avg": 20.0, "max": 20.0},
        {"min": 40.0, "avg": 40.0, "max": 40.0},
    ]


def _check_safety_watchdog_aborts_on_low_available_ram(tmp_path: Path):
    manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"cpu": FakeWorker},
        safety_check_interval=0.01,
        min_system_free_bytes=0,
    )
    manager._sample_metrics = lambda: None
    with patch(
        "backend.stress.manager.get_memory_status",
        return_value=SimpleNamespace(total_bytes=100, available_bytes=14),
    ):
        manager.start(["cpu"], duration_s=5, intensity=10)
        manager._run_thread.join(timeout=2)
        result = manager.results()
    assert result is not None
    assert result["state"] == "aborted"
    assert result["abort_reason"] == "Available RAM fell below 15% of total memory"


def _check_safety_watchdog_aborts_on_low_unplugged_battery(tmp_path: Path):
    manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"cpu": FakeWorker},
        safety_check_interval=0.01,
        min_system_free_bytes=2 * 1024**3,
    )
    manager._sample_metrics = lambda: None
    with (
        patch(
            "backend.stress.manager.get_memory_status",
            return_value=SimpleNamespace(total_bytes=100, available_bytes=50),
        ),
        patch(
            "backend.stress.manager.disk_usage",
            return_value=type("Disk", (), {"free": 4 * 1024**3})(),
        ),
        patch(
            "backend.stress.manager.get_battery_status",
            return_value=SimpleNamespace(percent=29, power_plugged=False),
        ),
    ):
        manager.start(["cpu"], duration_s=5, intensity=10)
        manager._run_thread.join(timeout=2)
        result = manager.results()
    assert result is not None
    assert result["state"] == "aborted"
    assert result["abort_reason"] == "Battery is below 30% and the laptop is not plugged in"


def _check_all_expands_to_supported_targets_and_gpu_is_unsupported(context):
    status = _start(context, targets=["all"])
    assert status["targets"] == list(SUPPORTED_TARGETS)
    context.manager.stop()

    gpu_status = _start(context, targets=["gpu"])
    assert gpu_status["target_states"]["gpu"]["state"] == "unsupported"
    assert gpu_status["state"] == "finished"


def _check_specific_target_starts_only_that_worker(context):
    status = _start(context, targets=["network"])
    assert status["targets"] == ["network"]
    assert set(status["target_states"]) == {"network"}
    context.manager.stop()


def _check_gpu_summary_never_reports_simulated_utilization(
    test_case: unittest.TestCase,
    tmp_path: Path,
):
    app = create_isolated_app(test_case, configure_logging=False)
    app.monitoring_scheduler = MonitoringScheduler(providers=[])
    app.stress_manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"gpu": GpuStressWorker},
        safety_check_interval=0.01,
    )
    app.stress_manager._check_safety = lambda: None
    app.stress_manager._sample_metrics = lambda: None
    with LocalHTTPTestClient(app) as client:
        token = app.session_token
        response = client.post(
            "/api/v1/stress/start",
            json_body={"targets": ["gpu"], "duration_s": 5, "intensity_percent": 10},
            headers={"X-Overpine-Token": token},
        )
        assert response.status_code == 200
        result = client.get("/api/v1/stress/results").json()["result"]
        assert result["target_summaries"]["gpu"]["state"] == "unsupported"
        assert result["metrics"]["cpu_percent"] is None


class StressTests(unittest.TestCase):
    """Run isolated manager and API scenarios with standard unittest."""

    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.tmp_path = Path(self._temporary_directory.name)
        self.context = StressTestContext(self, self.tmp_path)
        self.addCleanup(self.context.close)

    def test_stress_start_rejects_invalid_payload(self) -> None:
        payloads = (
            {"targets": ["bogus"], "duration_s": 5, "intensity_percent": 10},
            {"targets": ["cpu"], "duration_s": 301, "intensity_percent": 10},
            {"targets": ["cpu"], "duration_s": 5, "intensity_percent": 101},
            {"targets": [], "duration_s": 5, "intensity_percent": 10},
            {"targets": ["all", "cpu"], "duration_s": 5, "intensity_percent": 10},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                _check_stress_start_rejects_invalid_payload(self.context, payload)

    def test_state_changes_require_the_app_token(self) -> None:
        for token in (None, "wrong-token"):
            with self.subTest(token=token):
                _check_state_changes_require_the_app_token(self.context, token)

    def test_start_token_is_available_to_loopback_frontend(self) -> None:
        _check_start_token_is_available_to_loopback_frontend(self.context)

    def test_admin_restart_endpoint_accepts_valid_token_and_schedules_restart(
        self,
    ) -> None:
        _check_admin_restart_endpoint_accepts_valid_token_and_schedules_restart(
            self.context
        )

    def test_only_one_run_can_be_active(self) -> None:
        _check_only_one_run_can_be_active(self.context)

    def test_stop_cleans_disk_temp_file_and_releases_memory(self) -> None:
        _check_stop_cleans_disk_temp_file_and_releases_memory(self.tmp_path)

    def test_shutdown_waits_for_worker_cleanup(self) -> None:
        _check_shutdown_waits_for_worker_cleanup(self.tmp_path)

    def test_live_metrics_and_result_ranges_use_only_sampled_values(self) -> None:
        _check_live_metrics_and_result_ranges_use_only_sampled_values(self.tmp_path)

    def test_safety_watchdog_aborts_on_low_available_ram(self) -> None:
        _check_safety_watchdog_aborts_on_low_available_ram(self.tmp_path)

    def test_safety_watchdog_aborts_on_low_unplugged_battery(self) -> None:
        _check_safety_watchdog_aborts_on_low_unplugged_battery(self.tmp_path)

    def test_all_expands_to_supported_targets_and_gpu_is_unsupported(self) -> None:
        _check_all_expands_to_supported_targets_and_gpu_is_unsupported(self.context)

    def test_specific_target_starts_only_that_worker(self) -> None:
        _check_specific_target_starts_only_that_worker(self.context)

    def test_gpu_summary_never_reports_simulated_utilization(self) -> None:
        _check_gpu_summary_never_reports_simulated_utilization(
            self,
            self.tmp_path,
        )


if __name__ == "__main__":
    unittest.main()
