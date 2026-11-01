"""Safety and API tests for local stress workloads."""
from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

from backend.api.routes import stress as stress_routes
from backend.main import create_app
from backend.monitoring.scheduler import MonitoringScheduler
from backend.stress.base import StressWorker
from backend.stress.disk import DiskStressWorker
from backend.stress.gpu import GpuStressWorker
from backend.stress.manager import SUPPORTED_TARGETS, StressManager
from backend.stress.memory import MemoryStressWorker


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

    def __init__(self, tmp_path: Path, factories: dict | None = None) -> None:
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
        self.app = create_app()
        self.app.state.stress_manager = self.manager
        self.client = TestClient(
            self.app,
            base_url="http://127.0.0.1:8000",
            client=("127.0.0.1", 8000),
        )
        self.token = self.app.state.stress_token


@pytest.fixture
def context(tmp_path: Path) -> StressTestContext:
    """Return a test client whose workers never load actual hardware."""
    return StressTestContext(tmp_path)


def _start(context: StressTestContext, **overrides) -> dict:
    payload = {
        "targets": ["cpu"],
        "duration_s": 5,
        "intensity_percent": 10,
    }
    payload.update(overrides)
    response = context.client.post(
        "/api/v1/stress/start",
        json=payload,
        headers={"X-Overpine-Token": context.token},
    )
    return response.json()


@pytest.mark.parametrize(
    "payload",
    [
        {"targets": ["bogus"], "duration_s": 5, "intensity_percent": 10},
        {"targets": ["cpu"], "duration_s": 301, "intensity_percent": 10},
        {"targets": ["cpu"], "duration_s": 5, "intensity_percent": 101},
        {"targets": [], "duration_s": 5, "intensity_percent": 10},
        {"targets": ["all", "cpu"], "duration_s": 5, "intensity_percent": 10},
    ],
)
def test_stress_start_rejects_invalid_payload(context, payload):
    response = context.client.post(
        "/api/v1/stress/start",
        json=payload,
        headers={"X-Overpine-Token": context.token},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_state_changes_require_the_app_token(context, token):
    headers = {"X-Overpine-Token": token} if token else {}
    payload = {"targets": ["cpu"], "duration_s": 5, "intensity_percent": 10}
    assert context.client.post("/api/v1/stress/start", json=payload, headers=headers).status_code == 401
    assert context.client.post("/api/v1/stress/stop", headers=headers).status_code == 401


def test_start_token_is_available_to_loopback_frontend(context):
    response = context.client.get("/api/v1/stress/token")
    assert response.status_code == 200
    assert response.json() == {"token": context.token}


def test_admin_restart_endpoint_accepts_valid_token_and_schedules_restart(
    context,
    monkeypatch,
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

    monkeypatch.setattr(stress_routes, "is_admin", lambda: False)
    monkeypatch.setattr(stress_routes.threading, "Timer", CapturedTimer)

    response = context.client.post(
        "/api/v1/admin/restart-as-admin",
        headers={"X-Overpine-Token": context.token},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "restart_requested"}
    assert timer_state["started"] is True


def test_only_one_run_can_be_active(context):
    first = context.client.post(
        "/api/v1/stress/start",
        json={"targets": ["cpu"], "duration_s": 5, "intensity_percent": 10},
        headers={"X-Overpine-Token": context.token},
    )
    second = context.client.post(
        "/api/v1/stress/start",
        json={"targets": ["memory"], "duration_s": 5, "intensity_percent": 10},
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


def test_stop_cleans_disk_temp_file_and_releases_memory(tmp_path: Path):
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


def test_shutdown_waits_for_worker_cleanup(tmp_path: Path):
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


def test_live_metrics_and_result_ranges_use_only_sampled_values(
    tmp_path: Path,
    monkeypatch,
):
    manager = StressManager(data_dir=tmp_path)
    monkeypatch.setattr(
        "backend.stress.manager.psutil.cpu_percent",
        lambda **_: [20.0, 40.0],
    )
    monkeypatch.setattr(
        "backend.stress.manager.psutil.virtual_memory",
        lambda: SimpleNamespace(total=100, available=60),
    )
    monkeypatch.setattr(manager._process, "cpu_percent", lambda **_: 5.0)
    monkeypatch.setattr(
        manager._process,
        "memory_info",
        lambda: SimpleNamespace(rss=2 * 1024**2),
    )
    io_sample = {
        "disk_read_bps": 1_000_000,
        "disk_write_bps": 2_000_000,
        "net_down_bps": 3_000_000,
        "net_up_bps": 4_000_000,
    }
    monkeypatch.setattr(manager._io_sampler, "sample", lambda: io_sample)

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


def test_safety_watchdog_aborts_on_low_available_ram(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.stress.manager.psutil.virtual_memory",
        lambda: type("Memory", (), {"total": 100, "available": 14})(),
    )
    manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"cpu": FakeWorker},
        safety_check_interval=0.01,
        min_system_free_bytes=0,
    )
    manager._sample_metrics = lambda: None
    manager.start(["cpu"], duration_s=5, intensity=10)
    manager._run_thread.join(timeout=2)
    result = manager.results()
    assert result is not None
    assert result["state"] == "aborted"
    assert result["abort_reason"] == "Available RAM fell below 15% of total memory"


def test_safety_watchdog_aborts_on_low_unplugged_battery(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.stress.manager.psutil.virtual_memory",
        lambda: type("Memory", (), {"total": 100, "available": 50})(),
    )
    monkeypatch.setattr(
        "backend.stress.manager.psutil.disk_usage",
        lambda _: type("Disk", (), {"free": 4 * 1024**3})(),
    )
    monkeypatch.setattr(
        "backend.stress.manager.psutil.sensors_battery",
        lambda: type("Battery", (), {"percent": 29, "power_plugged": False})(),
    )
    monkeypatch.setattr(
        psutil,
        "sensors_temperatures",
        lambda **_: {},
        raising=False,
    )
    manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"cpu": FakeWorker},
        safety_check_interval=0.01,
        min_system_free_bytes=2 * 1024**3,
    )
    manager._sample_metrics = lambda: None
    manager.start(["cpu"], duration_s=5, intensity=10)
    manager._run_thread.join(timeout=2)
    result = manager.results()
    assert result is not None
    assert result["state"] == "aborted"
    assert result["abort_reason"] == "Battery is below 30% and the laptop is not plugged in"


def test_all_expands_to_supported_targets_and_gpu_is_unsupported(context):
    status = _start(context, targets=["all"])
    assert status["targets"] == list(SUPPORTED_TARGETS)
    context.manager.stop()

    gpu_status = _start(context, targets=["gpu"])
    assert gpu_status["target_states"]["gpu"]["state"] == "unsupported"
    assert gpu_status["state"] == "finished"


def test_specific_target_starts_only_that_worker(context):
    status = _start(context, targets=["network"])
    assert status["targets"] == ["network"]
    assert set(status["target_states"]) == {"network"}
    context.manager.stop()


def test_gpu_summary_never_reports_simulated_utilization(tmp_path: Path):
    app = create_app()
    app.state.monitoring_scheduler = MonitoringScheduler(providers=[])
    app.state.stress_manager = StressManager(
        data_dir=tmp_path,
        worker_factories={"gpu": GpuStressWorker},
        safety_check_interval=0.01,
    )
    app.state.stress_manager._check_safety = lambda: None
    app.state.stress_manager._sample_metrics = lambda: None
    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 8000),
    ) as client:
        token = app.state.stress_token
        response = client.post(
            "/api/v1/stress/start",
            json={"targets": ["gpu"], "duration_s": 5, "intensity_percent": 10},
            headers={"X-Overpine-Token": token},
        )
        assert response.status_code == 200
        result = client.get("/api/v1/stress/results").json()["result"]
        assert result["target_summaries"]["gpu"]["state"] == "unsupported"
        assert result["metrics"]["cpu_percent"] is None
