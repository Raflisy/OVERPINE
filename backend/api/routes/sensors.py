"""Sensor history and monitoring scheduler API handlers."""
from __future__ import annotations

import re
import secrets
from typing import Any

from backend.monitoring.models import SensorState
from backend.web.router import HTTPError, Request
from backend.web.validation import query_integer, query_sensor_state, query_value

_SENSOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def _validated_sensor_id(request: Request) -> str:
    """Validate the captured sensor identifier."""
    sensor_id = request.path_params["sensor_id"]
    if not _SENSOR_ID_PATTERN.fullmatch(sensor_id):
        raise HTTPError(422, "Invalid sensor id")
    return sensor_id


def _authorize(request: Request) -> None:
    """Check the application session token using a constant-time comparison."""
    supplied = request.headers.get("X-Overpine-Token")
    if not supplied or not secrets.compare_digest(
        supplied,
        request.app.session_token,
    ):
        raise HTTPError(401, "Valid X-Overpine-Token required")


def list_sensors(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Return discovered sensors with optional category and state filters."""
    category = query_value(request, "category")
    state = query_sensor_state(request)
    sensors = request.app.monitoring_scheduler.registry.list()
    if category is not None:
        sensors = [sensor for sensor in sensors if sensor.category == category]
    if state is not None:
        sensors = [sensor for sensor in sensors if sensor.state == state]
    return {"sensors": [sensor.as_dict() for sensor in sensors]}


def get_sensor(request: Request) -> dict[str, Any]:
    """Return one sensor or a 404 for an unknown id."""
    sensor_id = _validated_sensor_id(request)
    sensor = request.app.monitoring_scheduler.registry.get(sensor_id)
    if sensor is None:
        raise HTTPError(404, "Sensor not found")
    return sensor.as_dict()


def get_sensor_history(request: Request) -> dict[str, object]:
    """Return bounded history samples for an existing sensor."""
    sensor_id = _validated_sensor_id(request)
    limit = query_integer(request, "limit", 3600, 1, 3600)
    registry = request.app.monitoring_scheduler.registry
    if registry.get(sensor_id) is None:
        raise HTTPError(404, "Sensor not found")
    return {"sensor_id": sensor_id, "history": registry.history(sensor_id, limit)}


def monitoring_status(request: Request) -> dict[str, object]:
    """Return scheduler state, providers, and sensor-state counts."""
    return request.app.monitoring_scheduler.status()


def pause_monitoring(request: Request) -> dict[str, object]:
    """Pause monitoring after validating the app-local session token."""
    _authorize(request)
    return request.app.monitoring_scheduler.pause()


def resume_monitoring(request: Request) -> dict[str, object]:
    """Resume monitoring after validating the app-local session token."""
    _authorize(request)
    return request.app.monitoring_scheduler.resume()
