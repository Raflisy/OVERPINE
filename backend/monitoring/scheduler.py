"""Bounded-time sensor polling, state tracking, and provider backoff."""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.monitoring.registry import SensorRegistry
from backend.providers import (
    BatteryProvider,
    IoProvider,
    PdhProvider,
    SensorProvider,
    StorageTemperatureProvider,
    WindowsCounterProvider,
    WindowsProvider,
    WmiThermalProvider,
)

log = logging.getLogger("overpine")
_MAX_BACKOFF_S = 60.0
_DISCOVERY_INTERVAL_S = 30.0


@dataclass(slots=True)
class _ProviderRuntime:
    provider: SensorProvider
    interval_s: float
    timeout_s: float
    next_due: float = 0.0
    next_discovery: float = 0.0
    active: bool = False
    timed_out: bool = False
    call_started: float = 0.0
    generation: int = 0
    failures: int = 0
    last_error: str | None = None
    repeated_error_count: int = 0
    state: str = "not_initialized"
    sensor_ids: set[str] = field(default_factory=set)
    next_category_due: dict[str, float] = field(default_factory=dict)


class MonitoringScheduler:
    """Poll providers on isolated daemon workers and bound retained history."""

    def __init__(
        self,
        providers: list[SensorProvider] | None = None,
        registry: SensorRegistry | None = None,
        intervals: dict[str, float] | None = None,
        history_size: int = 3600,
    ) -> None:
        self.registry = registry or SensorRegistry(history_size)
        self._intervals = intervals or {
            "cpu": 1.0,
            "memory": 2.0,
            "storage": 5.0,
            "battery": 5.0,
            "thermal": 2.0,
            "fan": 2.0,
            "power": 2.0,
            "gpu": 10.0,
            "network": 1.0,
            "windows": 10.0,
        }
        selected_providers = providers if providers is not None else [
            WindowsProvider(),
            BatteryProvider(),
            StorageTemperatureProvider(),
            PdhProvider(),
            IoProvider(),
            WindowsCounterProvider(),
            WmiThermalProvider(),
        ]
        self._runtimes = {
            provider.name: self._runtime_for(provider)
            for provider in selected_providers
        }
        self._results: queue.Queue[
            tuple[str, int, list[Sensor] | None, dict[str, float | None] | None, str | None]
        ] = queue.Queue()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._started_wall: str | None = None
        self._stopped = False

    def start(self) -> None:
        """Start the monitoring loop once for this scheduler instance."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stopped:
                raise RuntimeError("A stopped monitoring scheduler cannot be restarted")
            self._started_at = time.monotonic()
            self._started_wall = datetime.now(UTC).isoformat()
            self._thread = threading.Thread(
                target=self._run,
                name="overpine-monitoring-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop polling, shut providers down, and join the scheduler thread."""
        with self._lock:
            self._stopped = True
            self._stop_event.set()
            self._resume_event.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))

    def pause(self) -> dict[str, Any]:
        """Pause scheduled polling without discarding sensor values or history."""
        self._resume_event.clear()
        return self.status()

    def resume(self) -> dict[str, Any]:
        """Resume provider polling."""
        self._resume_event.set()
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return session and per-provider availability without raising."""
        now = time.monotonic()
        with self._lock:
            started_at = self._started_at
            runtimes = list(self._runtimes.values())
            running = self._thread is not None and self._thread.is_alive() and not self._stopped
            paused = not self._resume_event.is_set()
            provider_rows: list[dict[str, Any]] = []
            for runtime in runtimes:
                try:
                    capabilities = runtime.provider.capabilities()
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    capabilities = {"capabilities_error": str(exc)}
                row = {
                    "name": runtime.provider.name,
                    "state": runtime.state,
                    "last_error": runtime.last_error,
                    "backoff_s": max(0.0, runtime.next_due - now)
                    if runtime.failures
                    else 0.0,
                    "sensor_count": len(runtime.sensor_ids),
                    **capabilities,
                }
                provider_rows.append(row)
            sensors = self.registry.list()
            counts = {state.value: 0 for state in SensorState}
            for sensor in sensors:
                counts[sensor.state.value] += 1
            return {
                "state": "paused" if paused and running else "running" if running else "stopped",
                "running": running,
                "paused": paused and running,
                "session_started_at": self._started_wall,
                "session_duration_s": (
                    max(0.0, now - started_at) if started_at is not None else 0.0
                ),
                "providers": provider_rows,
                "sensor_counts": counts,
            }

    def _runtime_for(self, provider: SensorProvider) -> _ProviderRuntime:
        interval_key = getattr(provider, "poll_interval_category", None)
        if interval_key is not None:
            interval = float(self._intervals.get(interval_key, 10.0))
        else:
            sensor_categories = getattr(provider, "poll_categories", ())
            interval = min(
                (float(self._intervals.get(category, 10.0)) for category in sensor_categories),
                default=10.0,
            )
        timeout = float(getattr(provider, "timeout_s", 5.0))
        return _ProviderRuntime(
            provider=provider,
            interval_s=max(0.5, interval),
            timeout_s=max(3.0, timeout * 2 + 1),
        )

    def _run(self) -> None:
        """Dispatch due providers, enforce call deadlines, and apply results."""
        try:
            while not self._stop_event.is_set():
                self._collect_results()
                now = time.monotonic()
                if self._resume_event.is_set():
                    for runtime in self._runtimes.values():
                        self._dispatch_if_due(runtime, now)
                self._check_timeouts(now)
                self._stop_event.wait(0.05)
        finally:
            for runtime in self._runtimes.values():
                try:
                    runtime.provider.shutdown()
                except Exception:
                    log.exception(
                        "Sensor provider shutdown failed: %s",
                        runtime.provider.name,
                    )

    def _dispatch_if_due(self, runtime: _ProviderRuntime, now: float) -> None:
        if runtime.active or now < runtime.next_due:
            return
        runtime.active = True
        runtime.timed_out = False
        runtime.call_started = now
        runtime.generation += 1
        generation = runtime.generation
        discover = now >= runtime.next_discovery
        if discover:
            runtime.next_discovery = now + _DISCOVERY_INTERVAL_S
        threading.Thread(
            target=self._call_provider,
            args=(runtime, generation, discover),
            name=f"overpine-provider-{runtime.provider.name}",
            daemon=True,
        ).start()

    def _call_provider(
        self,
        runtime: _ProviderRuntime,
        generation: int,
        discover: bool,
    ) -> None:
        sensors: list[Sensor] | None = None
        try:
            if discover:
                sensors = runtime.provider.discover()
            values = runtime.provider.read()
            if not isinstance(values, dict):
                raise TypeError("Provider read() must return a sensor-value dictionary")
            self._results.put(
                (runtime.provider.name, generation, sensors, values, None)
            )
        except Exception as exc:  # noqa: BLE001 - provider failures are reported back to the scheduler loop
            self._results.put(
                (runtime.provider.name, generation, sensors, None, str(exc))
            )

    def _collect_results(self) -> None:
        while True:
            try:
                name, generation, sensors, values, error = self._results.get_nowait()
            except queue.Empty:
                return
            runtime = self._runtimes.get(name)
            if runtime is None or generation != runtime.generation:
                continue
            runtime.active = False
            if runtime.timed_out:
                continue
            if error is not None:
                self._record_failure(runtime, error)
                continue
            try:
                self._record_success(runtime, sensors, values or {})
            except Exception as exc:  # noqa: BLE001 - consumer errors should be converted into provider failures
                self._record_failure(
                    runtime,
                    f"Could not apply provider readings: {exc}",
                )

    def _record_success(
        self,
        runtime: _ProviderRuntime,
        discovered: list[Sensor] | None,
        values: dict[str, float | None],
    ) -> None:
        now = time.monotonic()
        runtime.failures = 0
        runtime.next_due = now + runtime.interval_s
        runtime.state = "available"
        if runtime.last_error:
            if runtime.repeated_error_count:
                log.info(
                    "Sensor provider %s error repeated %d times before recovery",
                    runtime.provider.name,
                    runtime.repeated_error_count + 1,
                )
            runtime.last_error = None
            runtime.repeated_error_count = 0
        if discovered is not None:
            discovered_ids: set[str] = set()
            for sensor in discovered:
                self.registry.register(sensor)
                discovered_ids.add(sensor.id)
            for missing_id in runtime.sensor_ids - discovered_ids:
                sensor = self.registry.get(missing_id)
                if sensor is not None:
                    self.registry.set_state(
                        missing_id,
                        SensorState.TEMPORARILY_DISCONNECTED,
                        "Sensor is no longer reported by its provider",
                    )
            runtime.sensor_ids.update(discovered_ids)
        sensor_states = runtime.provider.sensor_states()
        diagnostics = runtime.provider.diagnostics()
        for sensor_id, (api_path, error_code) in diagnostics.items():
            sensor = self.registry.get(sensor_id)
            if sensor is not None:
                sensor.api_path = api_path
                sensor.error_code = error_code
        category_due: dict[str, bool] = {}
        for sensor_id in runtime.sensor_ids:
            sensor = self.registry.get(sensor_id)
            if sensor is None:
                continue
            category = sensor.category
            due = category_due.setdefault(
                category,
                now >= runtime.next_category_due.get(category, 0.0),
            )
            if sensor_id in sensor_states:
                state, message = sensor_states[sensor_id]
                self.registry.set_state(sensor_id, state, message)
            elif sensor_id in values and due:
                self.registry.update(sensor_id, values[sensor_id])
            elif due and (
                discovered is None or any(item.id == sensor_id for item in discovered)
            ):
                self.registry.update(sensor_id, None)
        for category, due in category_due.items():
            if due:
                runtime.next_category_due[category] = (
                    now + float(self._intervals.get(category, runtime.interval_s))
                )
        try:
            capabilities = runtime.provider.capabilities()
            provider_state = capabilities.get("state")
            if isinstance(provider_state, str):
                runtime.state = provider_state.casefold()
        except Exception as exc:  # noqa: BLE001 - capability probes are best-effort and should not kill polling
            log.warning("Could not read capabilities for %s: %s", runtime.provider.name, exc)

    def _record_failure(self, runtime: _ProviderRuntime, error: str) -> None:
        runtime.failures += 1
        runtime.state = "error"
        runtime.next_due = time.monotonic() + min(
            _MAX_BACKOFF_S,
            2.0 ** min(runtime.failures - 1, 6),
        )
        if error == runtime.last_error:
            runtime.repeated_error_count += 1
        else:
            if runtime.last_error and runtime.repeated_error_count:
                log.info(
                    "Sensor provider %s repeated the previous error %d times",
                    runtime.provider.name,
                    runtime.repeated_error_count + 1,
                )
            runtime.last_error = error
            runtime.repeated_error_count = 0
            log.warning("Sensor provider %s failed: %s", runtime.provider.name, error)
        for sensor_id in runtime.sensor_ids:
            self.registry.set_state(sensor_id, SensorState.ERROR, error)

    def _check_timeouts(self, now: float) -> None:
        for runtime in self._runtimes.values():
            if (
                runtime.active
                and not runtime.timed_out
                and now - runtime.call_started > runtime.timeout_s
            ):
                runtime.timed_out = True
                self._record_failure(
                    runtime,
                    f"Provider call timed out after {runtime.timeout_s:g} seconds",
                )
