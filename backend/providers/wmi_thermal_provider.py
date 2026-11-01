"""ACPI thermal-zone readings from the Windows root/wmi namespace."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider

_WMI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  $zones = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop)
  if ($zones.Count -eq 0) {
    [ordered]@{ state = 'unsupported'; message = 'No ACPI thermal zones are exposed' } | ConvertTo-Json -Compress
  } else {
    [ordered]@{ state = 'available'; values = @($zones | ForEach-Object { [double]$_.CurrentTemperature }) } | ConvertTo-Json -Compress
  }
} catch {
  [ordered]@{ state = 'error'; message = $_.Exception.Message } | ConvertTo-Json -Compress
}
"""


def tenths_kelvin_to_celsius(value: float) -> float:
    """Convert the MSAcpi tenths-of-Kelvin representation to Celsius."""
    return value / 10.0 - 273.15


class WmiThermalProvider(SensorProvider):
    """Report ACPI thermal-zone values with a firmware-reported caveat."""

    name = "wmi-thermal"
    poll_interval_category = "windows"

    def __init__(self, timeout_s: float = 5.0) -> None:
        self.timeout_s = timeout_s
        self._state: SensorState = SensorState.NOT_INITIALIZED
        self._message: str | None = None
        self._sensor_ids: list[str] = []
        self._values: dict[str, float | None] = {}

    def discover(self) -> list[Sensor]:
        """Register the Windows ACPI thermal-zone sensors."""
        if platform.system() != "Windows":
            self._state = SensorState.UNSUPPORTED
            self._message = "ACPI thermal-zone sensors require Windows"
            return []
        self._query()
        if not self._sensor_ids:
            self._sensor_ids = ["thermal.wmi.acpi-zone.0"]
            self._values = {self._sensor_ids[0]: None}
        return [
            Sensor(
                sensor_id,
                f"ACPI thermal zone {index + 1}",
                "thermal",
                "C",
                self.name,
                reliability="reported-by-firmware",
                state=self._state,
                value=self._values.get(sensor_id),
                error_message=self._message
                or "May not match the real CPU temperature",
            )
            for index, sensor_id in enumerate(self._sensor_ids)
        ]

    def read(self) -> dict[str, float | None]:
        """Read zones with a bounded PowerShell subprocess timeout."""
        if platform.system() != "Windows":
            return {}
        self._query()
        return dict(self._values)

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value.casefold(),
            "message": self._message
            or "ACPI values may not match the real CPU temperature",
        }

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        return {
            sensor_id: (self._state, self._message)
            for sensor_id in self._sensor_ids
            if self._state != SensorState.AVAILABLE
        }

    def _query(self) -> None:
        executable = shutil.which("powershell.exe")
        if executable is None:
            self._state = SensorState.UNSUPPORTED
            self._message = "Windows PowerShell was not found"
            return
        try:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", _WMI_SCRIPT],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            self._state = SensorState.ERROR
            self._message = f"ACPI thermal query timed out after {self.timeout_s:g} seconds"
            return
        except OSError as exc:
            self._state = SensorState.ERROR
            self._message = str(exc)
            return
        if result.returncode != 0:
            self._set_command_error(result.stderr.strip())
            return
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._state = SensorState.ERROR
            self._message = "Windows returned invalid ACPI thermal data"
            return
        if not isinstance(payload, dict):
            self._state = SensorState.ERROR
            self._message = "Windows returned an unexpected ACPI thermal response"
            return
        state = payload.get("state")
        if state == "unsupported":
            self._state = SensorState.UNSUPPORTED
            self._message = str(payload.get("message") or "No ACPI thermal zones are exposed")
            return
        if state == "error":
            self._set_command_error(str(payload.get("message") or "ACPI thermal query failed"))
            return
        values = payload.get("values")
        if not isinstance(values, list):
            self._state = SensorState.ERROR
            self._message = "Windows returned no ACPI thermal values"
            self._sensor_ids = ["thermal.wmi.acpi-zone.0"]
            self._values = {self._sensor_ids[0]: None}
            return
        if not values:
            self._state = SensorState.UNSUPPORTED
            self._message = "No ACPI thermal zones are exposed"
            self._sensor_ids = ["thermal.wmi.acpi-zone.0"]
            self._values = {self._sensor_ids[0]: None}
            return
        self._sensor_ids = [
            f"thermal.wmi.acpi-zone.{index}" for index in range(len(values))
        ]
        self._values = {}
        for sensor_id, value in zip(self._sensor_ids, values):
            try:
                temperature = (
                    tenths_kelvin_to_celsius(float(value))
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    else None
                )
            except (TypeError, ValueError, OverflowError):
                temperature = None
            self._values[sensor_id] = temperature
        self._state = SensorState.AVAILABLE
        self._message = "May not match the real CPU temperature"

    def _set_command_error(self, message: str) -> None:
        normalized = message.casefold()
        if any(term in normalized for term in ("access denied", "unauthorized", "permission")):
            self._state = SensorState.PERMISSION_RESTRICTED
            self._message = "Access to Windows ACPI thermal data is restricted; try administrator mode"
        elif any(term in normalized for term in ("invalid class", "not found", "does not exist")):
            self._state = SensorState.UNSUPPORTED
            self._message = "MSAcpi_ThermalZoneTemperature is not exposed by this system"
        else:
            self._state = SensorState.ERROR
            self._message = message or "Windows ACPI thermal query failed"
        if not self._sensor_ids:
            self._sensor_ids = ["thermal.wmi.acpi-zone.0"]
            self._values = {self._sensor_ids[0]: None}
