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
        self._states: dict[str, tuple[SensorState, str | None]] = {}
        self._diagnostics: dict[str, tuple[str | None, str | None]] = {}

    _API_PATHS = {
        "gpu.3d.utilization": (
            "Get-CimInstance -ClassName "
            "Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
            "(engtype_3D)"
        ),
        "battery.voltage": (
            "Get-CimInstance -Namespace root\\wmi -ClassName BatteryStatus "
            "(Voltage)"
        ),
    }

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
                api_path=self._API_PATHS["gpu.3d.utilization"],
            ),
            Sensor(
                "battery.voltage",
                "Battery voltage",
                "battery",
                "V",
                self.name,
                api_path=self._API_PATHS["battery.voltage"],
            ),
        ]

    def read(self) -> dict[str, float | None]:
        """Use the asynchronously cached Windows query and retain good readings."""
        if not self._windows:
            return {}
        data: dict[str, Any] = get_live_windows_sensors()
        self._states = {}
        self._diagnostics = {
            sensor_id: (api_path, None)
            for sensor_id, api_path in self._API_PATHS.items()
        }
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
            value = self._last_values.get(sensor_id)
            result[sensor_id] = value
            if value is not None:
                continue
            if not self._sampled_once:
                self._states[sensor_id] = (
                    SensorState.NOT_INITIALIZED,
                    "The asynchronous Windows sensor query has not returned a sample yet",
                )
                continue
            query_error = self._query_error(data, sensor_id)
            message = query_error or (
                "The Windows sensor query returned no usable value "
                "(empty result; no Windows error code was reported)"
            )
            self._states[sensor_id] = (SensorState.UNAVAILABLE, message)
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
        return dict(self._states)

    def diagnostics(self) -> dict[str, tuple[str | None, str | None]]:
        """Return the exact Windows query used and its reported error code."""
        return dict(self._diagnostics)

    @staticmethod
    def _query_error(data: dict[str, Any], sensor_id: str) -> str | None:
        """Return a matching CIM query error when Windows supplied one."""
        errors = data.get("errors")
        if not isinstance(errors, list):
            return None
        prefix = "GPU usage:" if sensor_id == "gpu.3d.utilization" else "Battery sensors:"
        for error in errors:
            if isinstance(error, str) and error.startswith(prefix):
                return f"{error} (no Windows error code was reported)"
        return None
