"""Optional local SMART readings through smartctl with fixed command forms."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider

log = logging.getLogger("overpine")


class SmartProvider(SensorProvider):
    """Read SMART drive data only when explicitly enabled in configuration."""

    name = "smartctl"
    poll_categories = ("windows",)

    def __init__(self, enabled: bool = False, timeout_s: float = 8.0) -> None:
        self.enabled = enabled
        self.timeout_s = timeout_s
        self._executable: str | None = None
        self._devices: list[str] = []
        self._sensors: list[Sensor] = []
        self._health: dict[str, str] = {}
        self._error: str | None = None

    def discover(self) -> list[Sensor]:
        """Find smartctl and local drives, without accepting user-supplied paths."""
        self._sensors = []
        if not self.enabled:
            return []
        self._executable = shutil.which("smartctl")
        if self._executable is None:
            self._error = "smartctl is not installed or is not on PATH"
            self._sensors.append(
                Sensor(
                    "storage.smart.status",
                    "SMART provider",
                    "storage",
                    "status",
                    self.name,
                    state=SensorState.UNSUPPORTED,
                    error_message=self._error,
                )
            )
            return list(self._sensors)
        try:
            result = subprocess.run(
                [self._executable, "--scan"],
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._error = str(exc)
            return []
        if result.returncode != 0:
            self._error = result.stderr.strip() or "smartctl scan failed"
            return []
        self._devices = [
            line.split()[0]
            for line in result.stdout.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for device in self._devices:
            key = re.sub(r"[^a-zA-Z0-9]+", ".", device).strip(".").casefold()
            self._sensors.extend(
                [
                    Sensor(
                        f"storage.smart.{key}.temperature",
                        f"{device} SMART temperature",
                        "storage",
                        "C",
                        self.name,
                    ),
                    Sensor(
                        f"storage.smart.{key}.power-on-hours",
                        f"{device} power-on hours",
                        "storage",
                        "h",
                        self.name,
                    ),
                    Sensor(
                        f"storage.smart.{key}.percentage-used",
                        f"{device} percentage used",
                        "storage",
                        "%",
                        self.name,
                    ),
                ]
            )
        return list(self._sensors)

    def read(self) -> dict[str, float | None]:
        """Query SMART JSON using only the discovered local device identifiers."""
        values: dict[str, float | None] = {}
        if not self.enabled or self._executable is None:
            return values
        for device in self._devices:
            try:
                result = subprocess.run(
                    [self._executable, "--all", "--json", device],
                    capture_output=True,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_s,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                payload = json.loads(result.stdout) if result.stdout else {}
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                log.debug("SMART query failed for discovered device %s: %s", device, exc)
                continue
            self._record_drive(device, payload, values)
        return values

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "state": (
                "unsupported"
                if self.enabled and self._executable is None
                else "available"
                if self.enabled
                else "disabled"
            ),
            "health_results": dict(self._health),
            "message": self._error,
        }

    def _record_drive(
        self,
        device: str,
        payload: dict[str, Any],
        values: dict[str, float | None],
    ) -> None:
        key = re.sub(r"[^a-zA-Z0-9]+", ".", device).strip(".").casefold()
        temperature = payload.get("temperature")
        if not isinstance(temperature, dict):
            temperature = payload.get("nvme_smart_health_information_log", {})
        attributes = payload.get("ata_smart_attributes", {}).get("table", [])
        hours: object = payload.get("power_on_time", {}).get("hours")
        percentage_used: object = payload.get("nvme_smart_health_information_log", {}).get(
            "percentage_used"
        )
        if isinstance(attributes, list):
            for entry in attributes:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).casefold()
                raw_value = entry.get("raw", {}).get("value")
                if "temperature" in name:
                    temperature = {"current": raw_value}
                elif "power_on_hours" in name:
                    hours = raw_value
                elif "percentage_used" in name:
                    percentage_used = raw_value
        if isinstance(temperature, dict):
            values[f"storage.smart.{key}.temperature"] = _number(
                temperature.get("current", temperature.get("value"))
            )
        values[f"storage.smart.{key}.power-on-hours"] = _number(hours)
        values[f"storage.smart.{key}.percentage-used"] = _number(percentage_used)
        smart_status = payload.get("smart_status")
        passed = smart_status.get("passed") if isinstance(smart_status, dict) else None
        if isinstance(passed, bool):
            self._health[device] = "PASSED" if passed else "FAILED"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
