"""Thread-safe registry and bounded measurement history for sensors."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from backend.monitoring.models import Sensor, SensorState


@dataclass(slots=True)
class _RunningStats:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)


class SensorRegistry:
    """Store discovered sensors with bounded history and session statistics."""

    def __init__(self, history_size: int = 3600) -> None:
        if history_size < 1:
            raise ValueError("history_size must be at least one")
        self._history_size = history_size
        self._lock = threading.RLock()
        self._sensors: dict[str, Sensor] = {}
        self._history: dict[str, deque[dict[str, float | None]]] = {}
        self._stats: dict[str, _RunningStats] = {}

    def register(self, sensor: Sensor) -> Sensor:
        """Add a new sensor or refresh metadata without discarding its history."""
        with self._lock:
            existing = self._sensors.get(sensor.id)
            if existing is None:
                self._sensors[sensor.id] = sensor
                self._history[sensor.id] = deque(maxlen=self._history_size)
                self._stats[sensor.id] = _RunningStats()
                return sensor
            existing.name = sensor.name
            existing.category = sensor.category
            existing.unit = sensor.unit
            existing.source = sensor.source
            existing.reliability = sensor.reliability
            if sensor.state != SensorState.NOT_INITIALIZED:
                existing.state = sensor.state
                existing.error_message = sensor.error_message
            return existing

    def update(self, sensor_id: str, value: float | None) -> Sensor:
        """Record a real value, or preserve null and mark it unavailable."""
        with self._lock:
            sensor = self._require_sensor(sensor_id)
            timestamp = time.time()
            normalized = (
                float(value)
                if value is not None
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                else None
            )
            sensor.value = normalized
            sensor.last_update = timestamp
            self._history[sensor_id].append(
                {"timestamp": timestamp, "value": normalized}
            )
            if normalized is None:
                sensor.state = SensorState.UNAVAILABLE
                sensor.error_message = "No current reading is available"
                return sensor

            sensor.state = SensorState.AVAILABLE
            sensor.error_message = None
            stats = self._stats[sensor_id]
            stats.add(normalized)
            sensor.min = stats.minimum
            sensor.max = stats.maximum
            sensor.avg = stats.total / stats.count
            return sensor

    def set_state(
        self,
        sensor_id: str,
        state: SensorState,
        message: str | None = None,
    ) -> Sensor:
        """Set a sensor state and explanation without fabricating a reading."""
        with self._lock:
            sensor = self._require_sensor(sensor_id)
            sensor.state = state
            sensor.error_message = message
            if state != SensorState.AVAILABLE:
                sensor.value = None
            sensor.last_update = time.time()
            return sensor

    def get(self, sensor_id: str) -> Sensor | None:
        """Return one registered sensor."""
        with self._lock:
            return self._sensors.get(sensor_id)

    def list(self) -> list[Sensor]:
        """Return a stable snapshot of all discovered sensors."""
        with self._lock:
            return list(self._sensors.values())

    def history(self, sensor_id: str, limit: int | None = None) -> list[dict[str, float | None]]:
        """Return the newest bounded samples for a sensor."""
        with self._lock:
            if sensor_id not in self._history:
                raise KeyError(sensor_id)
            samples = list(self._history[sensor_id])
            if limit is not None:
                samples = samples[-limit:]
            return samples

    def _require_sensor(self, sensor_id: str) -> Sensor:
        try:
            return self._sensors[sensor_id]
        except KeyError as exc:
            raise KeyError(sensor_id) from exc
