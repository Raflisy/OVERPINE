"""CPU, memory, battery, and optional psutil thermal sensor provider."""
from __future__ import annotations

import math
import re
from typing import Any

import psutil

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider


def _key(value: str) -> str:
    """Convert provider labels to stable identifier fragments."""
    normalized = re.sub(r"[^a-z0-9]+", ".", value.casefold()).strip(".")
    return normalized or "sensor"


def _valid(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


class PsutilProvider(SensorProvider):
    """Expose real values available through psutil on this operating system."""

    name = "psutil"
    poll_categories = ("cpu", "memory", "battery", "thermal", "fan")

    def __init__(self) -> None:
        self._sensors: list[Sensor] = []
        self._capabilities: dict[str, Any] = {}

    def discover(self) -> list[Sensor]:
        """Discover common counters and any temperature/fan sensors exposed."""
        sensors: list[Sensor] = [
            Sensor("cpu.total", "CPU utilization", "cpu", "%", self.name),
            Sensor("memory.used", "Memory utilization", "memory", "%", self.name),
        ]
        logical_count = psutil.cpu_count(logical=True) or 0
        sensors.extend(
            Sensor(
                f"cpu.core.{index}",
                f"CPU logical core {index + 1} utilization",
                "cpu",
                "%",
                self.name,
            )
            for index in range(logical_count)
        )
        sensors.extend(
            [
                Sensor(
                    "cpu.frequency.current",
                    "CPU current frequency",
                    "cpu",
                    "MHz",
                    self.name,
                    reliability="reported-by-firmware",
                ),
                Sensor(
                    "cpu.frequency.max",
                    "CPU maximum frequency",
                    "cpu",
                    "MHz",
                    self.name,
                    reliability="reported-by-firmware",
                ),
            ]
        )

        battery = None
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError, OSError):
            pass
        if battery is not None:
            sensors.extend(
                [
                    Sensor("battery.charge", "Battery charge", "battery", "%", self.name),
                    Sensor(
                        "battery.power_plugged",
                        "AC power connected",
                        "battery",
                        "bool",
                        self.name,
                    ),
                    Sensor(
                        "battery.seconds_left",
                        "Estimated battery time remaining",
                        "battery",
                        "s",
                        self.name,
                    ),
                ]
            )
        else:
            sensors.append(
                Sensor(
                    "battery.charge",
                    "Battery charge",
                    "battery",
                    "%",
                    self.name,
                    state=SensorState.UNAVAILABLE,
                    error_message="No battery reading is exposed by psutil",
                )
            )

        temperature_sensors, temperature_note = self._discover_temperatures()
        fan_sensors, fan_note = self._discover_fans()
        sensors.extend(temperature_sensors)
        sensors.extend(fan_sensors)
        self._sensors = sensors
        self._capabilities = {
            "temperatures": temperature_note,
            "fans": fan_note,
        }
        return list(sensors)

    def read(self) -> dict[str, float | None]:
        """Sample values without replacing unavailable readings with zero."""
        if not self._sensors:
            self.discover()
        result: dict[str, float | None] = {}
        result["cpu.total"] = _valid(psutil.cpu_percent(interval=None))
        try:
            core_values = psutil.cpu_percent(interval=None, percpu=True)
        except (AttributeError, NotImplementedError, OSError):
            core_values = []
        for index, value in enumerate(core_values):
            result[f"cpu.core.{index}"] = _valid(value)

        memory = psutil.virtual_memory()
        result["memory.used"] = _valid(memory.percent)
        try:
            frequency = psutil.cpu_freq()
        except (AttributeError, NotImplementedError, OSError):
            frequency = None
        result["cpu.frequency.current"] = (
            _valid(frequency.current) if frequency is not None else None
        )
        result["cpu.frequency.max"] = _valid(frequency.max) if frequency is not None else None

        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError, OSError):
            battery = None
        if battery is not None:
            result["battery.charge"] = _valid(battery.percent)
            result["battery.power_plugged"] = (
                float(battery.power_plugged)
                if battery.power_plugged is not None
                else None
            )
            seconds_left = battery.secsleft
            result["battery.seconds_left"] = (
                float(seconds_left) if seconds_left >= 0 else None
            )

        result.update(self._read_optional_sensors())
        return result

    def capabilities(self) -> dict[str, Any]:
        """Report thermal/fan API availability without implying a sensor exists."""
        return {"name": self.name, **self._capabilities}

    def _discover_temperatures(self) -> tuple[list[Sensor], str]:
        try:
            groups = psutil.sensors_temperatures()
        except (AttributeError, NotImplementedError, OSError):
            return [], "Temperature sensors are not exposed by psutil on this platform"
        found: list[Sensor] = []
        for group, entries in groups.items():
            for index, entry in enumerate(entries):
                label = str(entry.label or f"{group} {index + 1}")
                sensor_id = (
                    "cpu.package.temperature"
                    if not any(s.id == "cpu.package.temperature" for s in found)
                    and ("cpu" in group.casefold() or "package" in label.casefold())
                    else f"thermal.{_key(group)}.{_key(label)}"
                )
                found.append(
                    Sensor(
                        sensor_id,
                        label,
                        "cpu" if sensor_id.startswith("cpu.") else "thermal",
                        "C",
                        self.name,
                        state=SensorState.NOT_INITIALIZED,
                    )
                )
        return found, (
            "Temperature sensors are exposed by psutil"
            if found
            else "Temperature sensors are not exposed by psutil on this platform"
        )

    def _discover_fans(self) -> tuple[list[Sensor], str]:
        try:
            groups = psutil.sensors_fans()
        except (AttributeError, NotImplementedError, OSError):
            return [], "Fan sensors are not exposed by psutil on this platform"
        found = [
            Sensor(
                f"fan.{_key(group)}.{_key(str(entry.label or index + 1))}",
                str(entry.label or f"{group} fan {index + 1}"),
                "fan",
                "RPM",
                self.name,
            )
            for group, entries in groups.items()
            for index, entry in enumerate(entries)
        ]
        return found, (
            "Fan sensors are exposed by psutil"
            if found
            else "Fan sensors are not exposed by psutil on this platform"
        )

    def _read_optional_sensors(self) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        try:
            groups = psutil.sensors_temperatures()
        except (AttributeError, NotImplementedError, OSError):
            groups = {}
        for group, entries in groups.items():
            for index, entry in enumerate(entries):
                label = str(entry.label or f"{group} {index + 1}")
                sensor_id = (
                    "cpu.package.temperature"
                    if any(
                        sensor.id == "cpu.package.temperature"
                        and not any(
                            key == "cpu.package.temperature" for key in result
                        )
                        for sensor in self._sensors
                    )
                    and ("cpu" in group.casefold() or "package" in label.casefold())
                    else f"thermal.{_key(group)}.{_key(label)}"
                )
                result[sensor_id] = _valid(entry.current)
        try:
            fan_groups = psutil.sensors_fans()
        except (AttributeError, NotImplementedError, OSError):
            fan_groups = {}
        for group, entries in fan_groups.items():
            for index, entry in enumerate(entries):
                result[
                    f"fan.{_key(group)}.{_key(str(entry.label or index + 1))}"
                ] = _valid(entry.current)
        return result
