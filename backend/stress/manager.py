"""Stress-run coordination, safety watchdog, and live metric summaries."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from statistics import mean
from threading import Event, Thread
from typing import Any

from backend.core.constants import DATA_DIR
from backend.monitoring.models import SensorState
from backend.monitoring.registry import SensorRegistry
from backend.providers.io_rates import IoRateSampler
from backend.stress.base import StressWorker
from backend.stress.cpu import CpuStressWorker
from backend.stress.disk import DiskStressWorker
from backend.stress.gpu import GpuStressWorker
from backend.stress.memory import MemoryStressWorker
from backend.stress.network import NetworkStressWorker
from backend.winapi import current_process_metrics, get_battery_status, get_memory_status
from backend.winapi.cpu import per_core_cpu_percent
from backend.winapi.storage import disk_usage

log = logging.getLogger("overpine")

SUPPORTED_TARGETS = ("cpu", "memory", "disk", "network")
ALLOWED_TARGETS = (*SUPPORTED_TARGETS, "gpu")
_METRIC_KEYS = (
    "cpu_percent",
    "memory_percent",
    "app_cpu_percent",
    "app_memory_mb",
    "disk_mb_per_second",
    "network_mb_per_second",
)


class StressRunConflictError(RuntimeError):
    """Raised when a new run conflicts with the manager's current state."""


class StressWorkerStartError(RuntimeError):
    """Raised when a selected worker cannot be started."""


class StressManager:
    """Run at most one stress test and enforce laptop safety limits."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        disk_max_bytes: int = 1024**3,
        worker_factories: dict[str, Callable[[], StressWorker]] | None = None,
        safety_check_interval: float = 1.0,
        min_available_ram_ratio: float = 0.15,
        min_system_free_bytes: int = 2 * 1024**3,
        low_battery_percent: int = 30,
        sensor_registry: SensorRegistry | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._disk_max_bytes = disk_max_bytes
        self._worker_factories = worker_factories or {
            "cpu": CpuStressWorker,
            "memory": MemoryStressWorker,
            "disk": lambda: DiskStressWorker(data_dir, disk_max_bytes),
            "network": NetworkStressWorker,
            "gpu": GpuStressWorker,
        }
        self._safety_check_interval = safety_check_interval
        self._min_available_ram_ratio = min_available_ram_ratio
        self._min_system_free_bytes = min_system_free_bytes
        self._low_battery_percent = low_battery_percent
        self._sensor_registry = sensor_registry
        self._lock = threading.RLock()
        self._state = "idle"
        self._targets: list[str] = []
        self._target_workers: dict[str, StressWorker] = {}
        self._target_states: dict[str, dict[str, Any]] = {}
        self._stop_event: Event | None = None
        self._run_thread: Thread | None = None
        self._started_at: float | None = None
        self._deadline: float | None = None
        self._duration_s = 0
        self._intensity = 0
        self._abort_reason: str | None = None
        self._temperature_status = "temperature not monitored"
        self._temperature_c: float | None = None
        self._metrics: dict[str, Any] = self._empty_metrics()
        self._metric_samples: dict[str, list[float]] = {
            key: [] for key in _METRIC_KEYS
        }
        self._cpu_core_samples: dict[int, list[float]] = {}
        self._result: dict[str, Any] | None = None
        self._io_sampler = IoRateSampler()
        self._shutdown = False

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        """Return null-valued metrics until a real OS sample is available."""
        return {
            "cpu_percent_per_core": None,
            "cpu_percent": None,
            "memory_percent": None,
            "app_cpu_percent": None,
            "app_memory_mb": None,
            "disk_read_mb_per_second": None,
            "disk_write_mb_per_second": None,
            "disk_mb_per_second": None,
            "network_receive_mb_per_second": None,
            "network_send_mb_per_second": None,
            "network_mb_per_second": None,
        }

    def start(self, targets: list[str], duration_s: int, intensity: int) -> dict[str, Any]:
        """Create and start selected workers, rejecting concurrent runs."""
        if not targets:
            raise ValueError("Select at least one stress target")
        if "all" in targets:
            if len(targets) != 1:
                raise ValueError("'all' cannot be combined with individual targets")
            selected = list(SUPPORTED_TARGETS)
        else:
            selected = list(dict.fromkeys(targets))

        with self._lock:
            if self._shutdown:
                raise StressRunConflictError("Stress manager is shutting down")
            if self._state in ("running", "stopping"):
                raise StressRunConflictError("A stress test is already running")
            self._state = "running"
            self._targets = selected
            self._duration_s = duration_s
            self._intensity = intensity
            self._started_at = time.monotonic()
            self._deadline = self._started_at + duration_s
            self._abort_reason = None
            self._temperature_status = "temperature not monitored"
            self._temperature_c = None
            self._metrics = self._empty_metrics()
            self._metric_samples = {key: [] for key in _METRIC_KEYS}
            self._cpu_core_samples = {}
            self._stop_event = Event()
            self._target_workers = {}
            self._target_states = {}
            self._io_sampler = IoRateSampler()

            try:
                for target in selected:
                    factory = self._worker_factories.get(target)
                    if factory is None:
                        raise ValueError(f"Unsupported stress target: {target}")
                    worker = factory()
                    self._target_workers[target] = worker
                    worker.start(intensity, self._stop_event)
                    self._target_states[target] = worker.status()
            except Exception as exc:
                self._abort_reason = f"Could not start stress worker: {exc}"
                self._state = "aborted"
                for worker in self._target_workers.values():
                    try:
                        worker.stop()
                    except Exception:
                        log.exception("Stress worker cleanup failed during startup")
                log.error("Stress test aborted: %s", self._abort_reason)
                log.info("Stress test stopped")
                raise StressWorkerStartError(self._abort_reason) from exc

            if self._target_workers and all(
                worker.status().get("state") == "unsupported"
                for worker in self._target_workers.values()
            ):
                self._state = "finished"
                self._target_states = {
                    target: worker.status()
                    for target, worker in self._target_workers.items()
                }
                self._result = self._build_result()
                log.info(
                    "Stress test started: targets=%s duration_s=%s intensity_percent=%s",
                    ",".join(selected),
                    duration_s,
                    intensity,
                )
                log.info("Stress test stopped")
                return self.status()

            self._run_thread = Thread(
                target=self._run_watchdog,
                name="overpine-stress-watchdog",
                daemon=True,
            )
            self._run_thread.start()
            log.info(
                "Stress test started: targets=%s duration_s=%s intensity_percent=%s",
                ",".join(selected),
                duration_s,
                intensity,
            )
        return self.status()

    def stop(self) -> dict[str, Any]:
        """Stop an active run and synchronously wait for worker cleanup."""
        with self._lock:
            if self._state not in ("running", "stopping"):
                return self.status()
            if self._state == "running":
                self._state = "stopping"
                self._abort_reason = "Stopped by user"
            stop_event = self._stop_event
            run_thread = self._run_thread
            if stop_event is not None:
                stop_event.set()
        if run_thread is not None and run_thread is not threading.current_thread():
            run_thread.join(timeout=20)
            if run_thread.is_alive():
                for worker in self._target_workers.values():
                    worker.stop()
                run_thread.join(timeout=5)
        return self.status()

    def shutdown(self) -> None:
        """Prevent new runs and stop any children during application shutdown."""
        with self._lock:
            self._shutdown = True
        self.stop()

    def status(self) -> dict[str, Any]:
        """Return run state, per-target details, elapsed time, and live metrics."""
        with self._lock:
            now = time.monotonic()
            elapsed = (
                max(0.0, now - self._started_at)
                if self._started_at is not None
                else 0.0
            )
            remaining = (
                max(0.0, self._deadline - now)
                if self._deadline is not None and self._state in ("running", "stopping")
                else 0.0
            )
            target_states = {
                target: worker.status()
                for target, worker in self._target_workers.items()
            }
            self._target_states.update(target_states)
            return {
                "state": self._state,
                "targets": list(self._targets),
                "target_states": dict(self._target_states),
                "duration_s": self._duration_s,
                "intensity_percent": self._intensity,
                "elapsed": round(elapsed, 2),
                "remaining": round(remaining, 2),
                "abort_reason": self._abort_reason,
                "temperature_status": self._temperature_status,
                "temperature_c": self._temperature_c,
                "live_metrics": dict(self._metrics),
            }

    def results(self) -> dict[str, Any] | None:
        """Return the most recently completed run summary, if one exists."""
        with self._lock:
            return self._result

    def _run_watchdog(self) -> None:
        """Sample live metrics and abort on the first failed safety check."""
        stop_event = self._stop_event
        deadline = self._deadline
        if stop_event is None or deadline is None:
            return
        abort_reason: str | None = None
        try:
            while not stop_event.is_set():
                abort_reason = self._check_safety()
                if abort_reason:
                    should_log_abort = False
                    with self._lock:
                        if self._state == "running":
                            self._state = "stopping"
                            self._abort_reason = abort_reason
                            should_log_abort = True
                    if should_log_abort:
                        log.error("Stress test aborted: %s", abort_reason)
                    stop_event.set()
                    break

                self._sample_metrics()
                for target, worker in self._target_workers.items():
                    worker_status = worker.status()
                    if worker_status.get("state") == "error":
                        abort_reason = (
                            worker_status.get("error")
                            or f"{target} stress worker failed"
                        )
                        should_log_abort = False
                        with self._lock:
                            if self._state == "running":
                                self._state = "stopping"
                                self._abort_reason = abort_reason
                                should_log_abort = True
                        if should_log_abort:
                            log.error("Stress test aborted: %s", abort_reason)
                        stop_event.set()
                        break
                if stop_event.is_set():
                    break
                if time.monotonic() >= deadline:
                    break
                stop_event.wait(
                    min(self._safety_check_interval, max(0.0, deadline - time.monotonic()))
                )
        except Exception as exc:
            abort_reason = f"Stress watchdog failed: {exc}"
            should_log_abort = False
            with self._lock:
                if self._state == "running":
                    self._state = "stopping"
                    self._abort_reason = abort_reason
                    should_log_abort = True
            if should_log_abort:
                log.exception("Stress test aborted: %s", abort_reason)
            stop_event.set()
        finally:
            cleanup_errors: dict[str, str] = {}
            for target, worker in self._target_workers.items():
                try:
                    worker.stop()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    cleanup_errors[target] = str(exc)
            with self._lock:
                if cleanup_errors:
                    cleanup_reason = "; ".join(
                        f"{target}: {error}"
                        for target, error in cleanup_errors.items()
                    )
                    if self._abort_reason is None:
                        self._abort_reason = f"Worker cleanup failed: {cleanup_reason}"
                        log.error("Stress test aborted: %s", self._abort_reason)
                    else:
                        self._abort_reason = (
                            f"{self._abort_reason}; worker cleanup failed: {cleanup_reason}"
                        )
                        log.error("Stress worker cleanup failed: %s", cleanup_reason)
                if self._abort_reason:
                    self._state = "aborted"
                else:
                    self._state = "finished"
                self._target_states = {
                    target: worker.status()
                    for target, worker in self._target_workers.items()
                }
                for target, error in cleanup_errors.items():
                    self._target_states[target] = {
                        "state": "error",
                        "error": f"Cleanup failed: {error}",
                    }
                self._result = self._build_result()
            log.info("Stress test stopped")

    def _check_safety(self) -> str | None:
        """Return an abort reason when a measurable laptop safety limit is hit."""
        try:
            memory = get_memory_status()
            if (
                memory is None
                or memory.total_bytes <= 0
                or memory.available_bytes / memory.total_bytes
                < self._min_available_ram_ratio
            ):
                return "Available RAM fell below 15% of total memory"

            system_drive = os.environ.get("SystemDrive") or Path.home().anchor or os.sep
            if disk_usage(system_drive).free < self._min_system_free_bytes:
                return "The system drive has less than 2 GB free"
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Could not verify a required safety limit: {exc}"

        try:
            battery = get_battery_status()
        except OSError:
            log.exception("Could not verify the system battery state")
            battery = None
        if (
            battery is not None
            and battery.power_plugged is False
            and battery.percent is not None
            and battery.percent < self._low_battery_percent
        ):
            return "Battery is below 30% and the laptop is not plugged in"

        temperature_sensors = (
            [
                sensor
                for sensor in self._sensor_registry.list()
                if sensor.category in ("cpu", "thermal", "storage")
                and sensor.unit == "C"
                and sensor.state == SensorState.AVAILABLE
                and sensor.value is not None
            ]
            if self._sensor_registry is not None
            else []
        )
        if temperature_sensors:
            sensor = max(temperature_sensors, key=lambda item: float(item.value or 0))
            self._temperature_c = float(sensor.value)
            self._temperature_status = f"monitored: {sensor.name}"
            if self._temperature_c >= 95:
                return "Reported temperature reached 95 C"
        else:
            self._temperature_c = None
            self._temperature_status = "temperature not monitored"
        return None

    def _sample_metrics(self) -> None:
        """Capture real system/app readings and retain only available values."""
        metrics = self._empty_metrics()
        errors: dict[str, str] = {}
        try:
            per_core = per_core_cpu_percent()
            valid_core_values = (
                [
                    float(value)
                    for value in per_core
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ]
                if per_core is not None
                else []
            )
            metrics["cpu_percent_per_core"] = (
                per_core if valid_core_values else None
            )
            if valid_core_values:
                metrics["cpu_percent"] = mean(valid_core_values)
                for index, value in enumerate(per_core or []):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self._cpu_core_samples.setdefault(index, []).append(
                            float(value)
                        )
        except (OSError, RuntimeError) as exc:
            errors["cpu"] = str(exc)
        try:
            memory = get_memory_status()
            metrics["memory_percent"] = (
                memory.used_percent if memory is not None else None
            )
        except (OSError, RuntimeError, ValueError) as exc:
            errors["memory"] = str(exc)
        try:
            process = current_process_metrics()
            metrics["app_cpu_percent"] = process.cpu_percent
            metrics["app_memory_mb"] = (
                process.working_set_bytes / (1024 * 1024)
                if process.working_set_bytes is not None
                else None
            )
        except OSError as exc:
            errors["app"] = str(exc)
        try:
            io_sample = self._io_sampler.sample()
            if io_sample is not None:
                disk_read = io_sample["disk_read_bps"]
                disk_write = io_sample["disk_write_bps"]
                net_receive = io_sample["net_down_bps"]
                net_send = io_sample["net_up_bps"]
                metrics["disk_read_mb_per_second"] = (
                    disk_read / 1_000_000 if disk_read is not None else None
                )
                metrics["disk_write_mb_per_second"] = (
                    disk_write / 1_000_000 if disk_write is not None else None
                )
                if disk_read is not None and disk_write is not None:
                    metrics["disk_mb_per_second"] = (disk_read + disk_write) / 1_000_000
                metrics["network_receive_mb_per_second"] = (
                    net_receive / 1_000_000 if net_receive is not None else None
                )
                metrics["network_send_mb_per_second"] = (
                    net_send / 1_000_000 if net_send is not None else None
                )
                if net_receive is not None and net_send is not None:
                    metrics["network_mb_per_second"] = (
                        net_receive + net_send
                    ) / 1_000_000
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            errors["io"] = str(exc)
        metrics["errors"] = errors or None
        with self._lock:
            self._metrics = metrics
            for key in _METRIC_KEYS:
                value = metrics.get(key)
                if isinstance(value, (int, float)):
                    self._metric_samples[key].append(float(value))

    def _build_result(self) -> dict[str, Any]:
        """Build the final per-target and min/average/max metric summary."""
        summaries = {}
        for key in _METRIC_KEYS:
            values = self._metric_samples[key]
            summaries[key] = (
                {
                    "min": min(values),
                    "avg": mean(values),
                    "max": max(values),
                }
                if values
                else None
            )
        summaries["cpu_percent_per_core"] = [
            {
                "min": min(values),
                "avg": mean(values),
                "max": max(values),
            }
            for _, values in sorted(self._cpu_core_samples.items())
        ] or None
        return {
            "state": self._state,
            "targets": list(self._targets),
            "duration_s": self._duration_s,
            "intensity_percent": self._intensity,
            "elapsed": round(
                max(0.0, time.monotonic() - self._started_at)
                if self._started_at is not None
                else 0.0,
                2,
            ),
            "abort_reason": self._abort_reason,
            "temperature_status": self._temperature_status,
            "temperature_c": self._temperature_c,
            "metrics": summaries,
            "target_summaries": {
                target: worker.result_summary()
                for target, worker in self._target_workers.items()
            },
        }
