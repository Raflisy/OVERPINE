"""Windows GPU and battery sensor provider using the existing CIM cache."""
from __future__ import annotations

import platform
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider
from backend.services.hardware_inventory import get_live_windows_sensors


class WindowsCounterProvider(SensorProvider):
    """Expose Windows GPU/battery counters sampled by the existing service."""

    name = "windows-counter"
    poll_interval_category = "windows"

    def __init__(self) -> None:
        self._windows = platform.system() == "Windows"
        self._last_values: dict[str, float] = {}
        self._sampled_once = False
        self._gpu_exposed = False

    def discover(self) -> list[Sensor]:
        """Register counters available through the Windows sensor query."""
        if not self._windows:
            return []
        return [
            Sensor(
                "gpu.3d.utilization",
                "GPU 3D engine utilization",
                "gpu",
                "%",
                self.name,
            ),
            Sensor(
                "battery.voltage",
                "Battery voltage",
                "battery",
                "V",
                self.name,
            ),
        ]

    def read(self) -> dict[str, float | None]:
        """Use the asynchronously cached Windows query and retain good readings."""
        if not self._windows:
            return {}
        data: dict[str, Any] = get_live_windows_sensors()
        sampled_at = data.get("sampled_at")
        result: dict[str, float | None] = {}
        gpu_value = data.get("gpuUsagePercent")
        if isinstance(gpu_value, (int, float)) and not isinstance(gpu_value, bool):
            self._last_values["gpu.3d.utilization"] = float(gpu_value)
            self._gpu_exposed = True
        battery = data.get("battery")
        if isinstance(battery, dict):
            voltage = battery.get("Voltage")
            if isinstance(voltage, (int, float)) and not isinstance(voltage, bool):
                self._last_values["battery.voltage"] = float(voltage) / 1000

        self._sampled_once = isinstance(sampled_at, str)
        for sensor_id in ("gpu.3d.utilization", "battery.voltage"):
            result[sensor_id] = self._last_values.get(sensor_id)
        return result

    def capabilities(self) -> dict[str, Any]:
        """Explain that Windows counters are populated asynchronously."""
        return {
            "name": self.name,
            "state": "available" if self._windows else "unsupported",
            "sampled_once": self._sampled_once,
            "note": (
                "The first Windows performance-counter sample may take several seconds"
                if self._windows
                else "Windows performance counters are unavailable on this platform"
            ),
            "gpu_note": (
                "GPU utilization is not exposed by Windows counters"
                if self._sampled_once and not self._gpu_exposed
                else None
            ),
        }

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        if not self._windows:
            return {
                "gpu.3d.utilization": (
                    SensorState.UNSUPPORTED,
                    "Windows performance counters are unavailable on this platform",
                ),
                "battery.voltage": (
                    SensorState.UNSUPPORTED,
                    "Windows battery counters are unavailable on this platform",
                ),
            }
        return {}
