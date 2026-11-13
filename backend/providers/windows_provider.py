"""CPU, memory, and battery sensor provider backed by Windows APIs."""
from __future__ import annotations

import logging
import math
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider
from backend.winapi import (
    active_processor_count,
    cpu_frequency,
    get_battery_status,
    get_memory_status,
    system_cpu_percent,
)
from backend.winapi.cpu import per_core_cpu_percent

log = logging.getLogger("overpine")


def _valid(value: object) -> float | None:
    """Convert finite numeric readings while preserving unavailable values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


class WindowsProvider(SensorProvider):
    """Expose processor, memory, and system-power values reported by Windows."""

    name = "windows"
    poll_categories = ("cpu", "memory", "battery")

    def __init__(self) -> None:
        """Create an empty sensor inventory until discover is called."""
        self._sensors: list[Sensor] = []
        self._battery_available = False
        self._sensor_states: dict[
            str, tuple[SensorState, str | None]
        ] = {}
        self._frequency_source = (
            "CallNtPowerInformation (reported; not guaranteed live)"
        )

    def discover(self) -> list[Sensor]:
        """Discover Windows counters and report whether a system battery exists."""
        sensors = [
            Sensor("cpu.total", "CPU utilization", "cpu", "%", self.name),
            Sensor("memory.used", "Memory utilization", "memory", "%", self.name),
            Sensor(
                "cpu.frequency.current",
                "CPU reported frequency (PDH preferred; fallback not guaranteed live)",
                "cpu",
                "MHz",
                "windows-frequency",
                reliability="reported-by-windows",
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
        logical_count = active_processor_count()
        if logical_count is not None:
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

        try:
            battery = get_battery_status()
        except OSError:
            log.exception("Could not discover Windows battery status")
            battery = None
        self._battery_available = battery is not None
        battery_state = (
            SensorState.NOT_INITIALIZED
            if self._battery_available
            else SensorState.UNAVAILABLE
        )
        battery_error = (
            None
            if self._battery_available
            else "Windows does not report a system battery"
        )
        sensors.extend(
            [
                Sensor(
                    "battery.charge",
                    "Battery charge",
                    "battery",
                    "%",
                    self.name,
                    api_path="GetSystemPowerStatus (SYSTEM_POWER_STATUS.BatteryLifePercent)",
                    state=battery_state,
                    error_message=battery_error,
                ),
                Sensor(
                    "battery.power_plugged",
                    "AC power connected",
                    "battery",
                    "bool",
                    self.name,
                    api_path="GetSystemPowerStatus (SYSTEM_POWER_STATUS.ACLineStatus)",
                    state=battery_state,
                    error_message=battery_error,
                ),
                Sensor(
                    "battery.seconds_left",
                    "Estimated battery time remaining",
                    "battery",
                    "s",
                    self.name,
                    api_path="GetSystemPowerStatus (SYSTEM_POWER_STATUS.BatteryLifeTime)",
                    state=battery_state,
                    error_message=battery_error,
                ),
            ]
        )
        self._sensors = sensors
        return list(sensors)

    def read(self) -> dict[str, float | None]:
        """Sample supported values without substituting zero for missing data."""
        if not self._sensors:
            self.discover()

        result: dict[str, float | None] = {}
        total_cpu = _valid(system_cpu_percent())
        result["cpu.total"] = total_cpu
        sensor_states: dict[str, tuple[SensorState, str | None]] = {}
        if total_cpu is None:
            sensor_states["cpu.total"] = (
                SensorState.NOT_INITIALIZED,
                "no data yet; CPU utilization requires two samples at least 1 s apart",
            )
        per_core = per_core_cpu_percent()
        if per_core is not None:
            result.update(
                {
                    f"cpu.core.{index}": _valid(value)
                    for index, value in enumerate(per_core)
                }
            )
            for index, value in enumerate(per_core):
                if value is None:
                    sensor_states[f"cpu.core.{index}"] = (
                        SensorState.NOT_INITIALIZED,
                        "no data yet; PDH requires two samples at least 1 s apart",
                    )
        else:
            for sensor in self._sensors:
                if sensor.id.startswith("cpu.core."):
                    sensor_states[sensor.id] = (
                        SensorState.NOT_INITIALIZED,
                        "no data yet; PDH requires two samples at least 1 s apart",
                    )
        self._sensor_states = sensor_states

        memory = get_memory_status()
        result["memory.used"] = (
            _valid(memory.used_percent) if memory is not None else None
        )
        frequency = cpu_frequency()
        self._frequency_source = frequency.current_source
        result["cpu.frequency.current"] = _valid(frequency.current_mhz)
        result["cpu.frequency.max"] = _valid(frequency.maximum_mhz)

        battery = get_battery_status()
        if battery is not None:
            result["battery.charge"] = _valid(battery.percent)
            result["battery.power_plugged"] = (
                float(battery.power_plugged)
                if battery.power_plugged is not None
                else None
            )
            result["battery.seconds_left"] = (
                float(battery.seconds_left)
                if battery.seconds_left is not None
                else None
            )
            if battery.seconds_left is None:
                self._sensor_states["battery.seconds_left"] = (
                    SensorState.UNAVAILABLE,
                    "GetSystemPowerStatus returned the unknown BatteryLifeTime "
                    "sentinel; Windows has no remaining-time estimate",
                )
        return result

    def sensor_states(
        self,
    ) -> dict[str, tuple[SensorState, str | None]]:
        """Report initial CPU sampling and absent system batteries."""
        states = dict(self._sensor_states)
        if not self._battery_available:
            message = "Windows does not report a system battery"
            states.update(
                {
                    sensor_id: (SensorState.UNAVAILABLE, message)
                    for sensor_id in (
                        "battery.charge",
                        "battery.power_plugged",
                        "battery.seconds_left",
                    )
                }
            )
        return states

    def diagnostics(self) -> dict[str, tuple[str | None, str | None]]:
        """Report the exact power-status field used for remaining battery time."""
        return {
            "battery.seconds_left": (
                "GetSystemPowerStatus (SYSTEM_POWER_STATUS.BatteryLifeTime)",
                None,
            )
        }

    def capabilities(self) -> dict[str, Any]:
        """Report whether a battery is available without implying sensor support."""
        return {
            "name": self.name,
            "battery": "available" if self._battery_available else "unavailable",
            "thermal_and_fan": (
                "provided by other Windows sensor providers"
            ),
            "frequency_source": (
                self._frequency_source
            ),
        }
