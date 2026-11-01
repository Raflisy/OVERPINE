"""Loopback-only LibreHardwareMonitor Remote Web Server provider."""
from __future__ import annotations

import json
import logging
import math
import re
import urllib.error
import urllib.request
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider

log = logging.getLogger("overpine")
_VALUE_PATTERN = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_TYPE_INFO = {
    "temperature": ("thermal", "C"),
    "power": ("power", "W"),
    "fan": ("fan", "RPM"),
    "clock": ("cpu", "MHz"),
    "load": ("gpu", "%"),
    "voltage": ("power", "V"),
}


def parse_lhm_number(value: object) -> float | None:
    """Parse LHM numeric strings, including comma decimals and missing markers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if not isinstance(value, str) or value.strip() in {"", "-"}:
        return None
    match = _VALUE_PATTERN.search(value.replace("\u00a0", " "))
    if match is None:
        return None
    try:
        numeric = float(match.group().replace(",", "."))
    except ValueError:
        return None
    return numeric if math.isfinite(numeric) else None


def parse_lhm_tree(payload: object) -> tuple[list[Sensor], dict[str, float | None]]:
    """Extract supported measurements from a nested LHM JSON tree."""
    sensors: list[Sensor] = []
    values: dict[str, float | None] = {}

    def visit(node: object, path: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        text = str(node.get("Text") or "")
        current_path = (*path, text) if text else path
        sensor_type = str(node.get("Type") or "").casefold()
        if sensor_type in _TYPE_INFO and "Value" in node:
            category, unit = _TYPE_INFO[sensor_type]
            context = " ".join(current_path).casefold()
            name = text or str(node.get("Name") or "Hardware sensor")
            sensor_id = _stable_sensor_id(
                sensor_type,
                name,
                str(node.get("SensorId") or "/".join(current_path)),
                context,
            )
            reading = parse_lhm_number(node.get("Value"))
            sensors.append(
                Sensor(
                    sensor_id,
                    name,
                    _sensor_category(sensor_type, context, category),
                    unit,
                    "lhm-web",
                    state=(
                        SensorState.AVAILABLE
                        if reading is not None
                        else SensorState.UNAVAILABLE
                    ),
                    value=reading,
                    error_message=(
                        None
                        if reading is not None
                        else "LibreHardwareMonitor has no current value for this sensor"
                    ),
                )
            )
            values[sensor_id] = reading
        children = node.get("Children")
        if isinstance(children, list):
            for child in children:
                visit(child, current_path)

    visit(payload, ())
    return sensors, values


def _sensor_category(sensor_type: str, context: str, fallback: str) -> str:
    if "storage" in context or any(term in context for term in ("ssd", "nvme", "drive", "disk")):
        return "storage" if sensor_type == "temperature" else fallback
    if "gpu" in context:
        return "gpu"
    if sensor_type == "temperature" and "cpu" in context:
        return "cpu"
    if sensor_type == "clock" and "gpu" in context:
        return "gpu"
    if sensor_type == "load" and "cpu" in context:
        return "cpu"
    return fallback


def _stable_sensor_id(
    sensor_type: str,
    name: str,
    source_path: str,
    context: str,
) -> str:
    if sensor_type == "temperature" and "cpu" in context and "package" in context:
        return "cpu.package.temperature"
    if sensor_type == "power" and "cpu" in context and "package" in context:
        return "cpu.package.power"
    if sensor_type == "temperature" and any(
        term in context for term in ("drive", "disk", "ssd", "nvme")
    ):
        prefix = "storage"
    elif "gpu" in context:
        prefix = "gpu"
    elif sensor_type == "temperature" and "cpu" in context:
        prefix = "cpu"
    else:
        prefix = _TYPE_INFO[sensor_type][0]
    fragment = re.sub(
        r"[^a-z0-9]+",
        ".",
        f"{source_path} {name}".casefold(),
    ).strip(".")
    return f"{prefix}.{sensor_type}.{fragment or 'sensor'}"[:256]


class LhmWebProvider(SensorProvider):
    """Read LHM data.json without permitting non-loopback connections."""

    name = "lhm-web"
    poll_categories = ("thermal", "power", "fan", "gpu", "storage")

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8085,
        timeout_s: float = 2.0,
    ) -> None:
        normalized_host = host.casefold().strip("[]")
        if normalized_host not in {"127.0.0.1", "localhost"}:
            raise ValueError("LibreHardwareMonitor host must be 127.0.0.1 or localhost")
        if not 1 <= port <= 65535:
            raise ValueError("LibreHardwareMonitor port must be between 1 and 65535")
        if timeout_s <= 0:
            raise ValueError("LibreHardwareMonitor timeout must be positive")
        self.host = normalized_host
        self.port = port
        self.timeout_s = timeout_s
        self._url = f"http://{self.host}:{self.port}/data.json"
        self._sensors: dict[str, Sensor] = {}
        self._values: dict[str, float | None] = {}
        self._error: str | None = None
        self._last_logged_error = False
        self._lhm_running = False

    def discover(self) -> list[Sensor]:
        """Discover LHM sensors and cache their first reading."""
        self._fetch()
        return list(self._sensors.values())

    def read(self) -> dict[str, float | None]:
        """Fetch current readings; an unavailable server never raises outward."""
        self._fetch()
        return dict(self._values)

    def capabilities(self) -> dict[str, Any]:
        sensor_ids = set(self._sensors)
        notes: list[str] = []
        if self._lhm_running:
            if "cpu.package.temperature" not in sensor_ids:
                notes.append("LibreHardwareMonitor does not expose a CPU package temperature")
            if not any(sensor_id.startswith("fan.") for sensor_id in sensor_ids):
                notes.append("LibreHardwareMonitor does not expose fan RPM readings")
            if not any(
                sensor.category == "gpu" and sensor.unit == "C"
                for sensor in self._sensors.values()
            ):
                notes.append("LibreHardwareMonitor does not expose a GPU temperature")
            if not any(
                sensor.category == "storage" and sensor.unit == "C"
                for sensor in self._sensors.values()
            ):
                notes.append("LibreHardwareMonitor does not expose drive temperatures")
        return {
            "name": self.name,
            "lhm_running": self._lhm_running,
            "state": "available" if self._lhm_running else "unavailable",
            "last_error": self._error,
            "url": self._url,
            "notes": notes,
        }

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        if self._lhm_running:
            return {}
        message = (
            f"LibreHardwareMonitor web server not reachable on "
            f"{self.host}:{self.port}"
        )
        return {
            sensor_id: (SensorState.UNAVAILABLE, message)
            for sensor_id in self._sensors
        }

    def shutdown(self) -> None:
        self._values.clear()

    def _fetch(self) -> None:
        try:
            request = urllib.request.Request(
                self._url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            sensors, values = parse_lhm_tree(payload)
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            self._lhm_running = False
            self._error = str(exc)
            if not self._last_logged_error:
                log.info(
                    "LibreHardwareMonitor web server is not reachable on %s:%s",
                    self.host,
                    self.port,
                )
                self._last_logged_error = True
            for sensor_id in self._sensors:
                self._values[sensor_id] = None
            return
        self._lhm_running = True
        self._error = None
        self._last_logged_error = False
        for sensor in sensors:
            self._sensors[sensor.id] = sensor
        self._values = values
