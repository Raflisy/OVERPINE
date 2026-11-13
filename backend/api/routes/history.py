"""GET sensor history and token-protected history clearing."""
from __future__ import annotations

import math
import re
import secrets
from typing import Any

from backend.monitoring.models import Sensor
from backend.storage.history import HistoryStorageError, HistoryStore
from backend.web.router import HTTPError, Request

_SENSOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_RESOLUTIONS = frozenset({"auto", "raw", "minute", "5-minute"})


def _one_query_value(request: Request, key: str) -> str | None:
    """Return one query parameter and reject ambiguous duplicates."""
    values = request.query.get(key)
    if values is None:
        return None
    if len(values) != 1:
        raise HTTPError(422, f"Query parameter {key} must appear once")
    return values[0]


def _required_number(request: Request, key: str) -> float:
    """Parse one finite Unix timestamp query parameter."""
    raw = _one_query_value(request, key)
    if raw is None:
        raise HTTPError(422, f"Query parameter {key} is required")
    try:
        value = float(raw)
    except ValueError as error:
        raise HTTPError(422, f"Query parameter {key} must be a Unix timestamp") from error
    if not math.isfinite(value):
        raise HTTPError(422, f"Query parameter {key} must be finite")
    return value


def _authorize(request: Request) -> None:
    """Require the app-session token using constant-time comparison."""
    supplied = request.headers.get("X-Overpine-Token")
    if not supplied or not secrets.compare_digest(
        supplied,
        request.app.session_token,
    ):
        raise HTTPError(401, "Valid X-Overpine-Token required")


def _history_status(request: Request) -> dict[str, Any]:
    """Return configured history limits and current database health."""
    return request.app.history_store.status()


def get_history(request: Request) -> dict[str, Any]:
    """Return real raw or downsampled readings for a validated time range."""
    sensor_id = _one_query_value(request, "sensor")
    if sensor_id is None or not _SENSOR_ID_PATTERN.fullmatch(sensor_id):
        raise HTTPError(422, "A valid sensor query parameter is required")
    start = _required_number(request, "from")
    end = _required_number(request, "to")
    if start >= end:
        raise HTTPError(422, "from must be earlier than to")
    resolution = _one_query_value(request, "resolution") or "auto"
    if resolution not in _RESOLUTIONS:
        raise HTTPError(422, "resolution must be auto, raw, minute, or 5-minute")
    try:
        result = request.app.history_store.read(
            sensor_id,
            start,
            end,
            resolution,
        )
    except ValueError as error:
        raise HTTPError(422, str(error)) from error
    except HistoryStorageError as error:
        raise HTTPError(503, f"History storage is unavailable: {error}") from error
    return {**result, "status": _history_status(request)}


def list_history_sensors(request: Request) -> dict[str, Any]:
    """List stored sensor IDs with names from the current registry when known."""
    store: HistoryStore = request.app.history_store
    sensor_ids: list[str]
    try:
        sensor_ids = store.sensors()
    except HistoryStorageError:
        sensor_ids: list[str] = []
    sensors: list[Sensor] = request.app.monitoring_scheduler.registry.list()
    current: dict[str, Sensor] = {sensor.id: sensor for sensor in sensors}
    return {
        "sensors": [
            {
                "id": sensor_id,
                "name": current[sensor_id].name if sensor_id in current else sensor_id,
                "unit": current[sensor_id].unit if sensor_id in current else "",
                "category": (
                    current[sensor_id].category if sensor_id in current else ""
                ),
            }
            for sensor_id in sensor_ids
        ],
        "status": _history_status(request),
    }


def clear_history(request: Request) -> dict[str, Any]:
    """Clear the application's SQLite sensor history after token validation."""
    _authorize(request)
    try:
        request.app.history_store.clear()
    except HistoryStorageError as error:
        raise HTTPError(503, f"History storage is unavailable: {error}") from error
    return {"cleared": True, "status": _history_status(request)}
