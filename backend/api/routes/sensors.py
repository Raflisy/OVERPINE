"""Sensor history and monitoring scheduler control API."""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Header, HTTPException, Query, Request

from backend.monitoring.models import SensorState

router = APIRouter(tags=["sensors"])
_SENSOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def _validated_sensor_id(sensor_id: str) -> str:
    if not _SENSOR_ID_PATTERN.fullmatch(sensor_id):
        raise HTTPException(status_code=422, detail="Invalid sensor id")
    return sensor_id


def _authorize(request: Request, token: str | None) -> None:
    expected = request.app.state.stress_token
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Valid X-Overpine-Token required")


@router.get("/sensors")
def list_sensors(
    request: Request,
    category: str | None = None,
    state: SensorState | None = None,
) -> dict[str, list[dict[str, str | float | None]]]:
    """Return discovered sensors with optional category and state filters."""
    sensors = request.app.state.monitoring_scheduler.registry.list()
    if category is not None:
        sensors = [sensor for sensor in sensors if sensor.category == category]
    if state is not None:
        sensors = [sensor for sensor in sensors if sensor.state == state]
    return {"sensors": [sensor.as_dict() for sensor in sensors]}


@router.get("/sensors/{sensor_id}")
def get_sensor(sensor_id: str, request: Request) -> dict[str, str | float | None]:
    """Return one sensor or a 404 for an unknown id."""
    sensor_id = _validated_sensor_id(sensor_id)
    sensor = request.app.state.monitoring_scheduler.registry.get(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return sensor.as_dict()


@router.get("/sensors/{sensor_id}/history")
def get_sensor_history(
    sensor_id: str,
    request: Request,
    limit: int = Query(default=3600, ge=1, le=3600),
) -> dict[str, object]:
    """Return bounded history samples for an existing sensor."""
    sensor_id = _validated_sensor_id(sensor_id)
    registry = request.app.state.monitoring_scheduler.registry
    if registry.get(sensor_id) is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return {"sensor_id": sensor_id, "history": registry.history(sensor_id, limit)}


@router.get("/monitoring/status")
def monitoring_status(request: Request) -> dict[str, object]:
    """Return scheduler state, providers, and sensor-state counts."""
    return request.app.state.monitoring_scheduler.status()


@router.post("/monitoring/pause")
def pause_monitoring(
    request: Request,
    x_overpine_token: str | None = Header(default=None, alias="X-Overpine-Token"),
) -> dict[str, object]:
    """Pause monitoring after validating the app-local session token."""
    _authorize(request, x_overpine_token)
    return request.app.state.monitoring_scheduler.pause()


@router.post("/monitoring/resume")
def resume_monitoring(
    request: Request,
    x_overpine_token: str | None = Header(default=None, alias="X-Overpine-Token"),
) -> dict[str, object]:
    """Resume monitoring after validating the app-local session token."""
    _authorize(request, x_overpine_token)
    return request.app.state.monitoring_scheduler.resume()
