"""Token-protected user settings API."""
from __future__ import annotations
from backend.storage.history import is_detail_sensor

import secrets
from typing import cast

from backend.core.user_settings import (
    RESTART_REQUIRED_KEYS,
    SettingsStorageError,
    UserSettings,
)
from backend.web.router import HTTPError, Request

_RESTART_LABELS = {
    "history_enabled": "History recording",
    "retention_days": "History retention",
    "max_mb": "History size limit",
    "recorded_sensors": "Recorded sensor selection",
}


def _authorize(request: Request) -> None:
    """Require the current app-session token for settings changes."""
    supplied = request.headers.get("X-Overpine-Token")
    if not supplied or not secrets.compare_digest(
        supplied,
        request.app.session_token,
    ):
        raise HTTPError(401, "Valid X-Overpine-Token required")


def _restart_required(request: Request, values: UserSettings) -> list[str]:
    """List saved history settings that differ from the active history store."""
    required: list[str] = []
    active: dict[str, object] = {
        "history_enabled": request.app.history_store.enabled,
        "retention_days": request.app.history_store.retention_days,
        "max_mb": request.app.history_store.max_mb,
        "recorded_sensors": sorted(
            request.app.history_store.recorded_sensor_ids
        ),
    }
    for key in RESTART_REQUIRED_KEYS:
        if key == "recorded_sensors":
            saved_sensors = values["recorded_sensors"]
            differs = sorted(saved_sensors) != active[key]
        else:
            differs = values[key] != active[key]
        if differs:
            required.append(_RESTART_LABELS[key])
    return required


def _response(
    request: Request,
    values: UserSettings,
) -> dict[str, object]:
    """Build the public settings shape and current sensor choices."""
    selected = set(values["recorded_sensors"])
    sensors = [
        sensor  
        for sensor in request.app.monitoring_scheduler.registry.list()
        if not is_detail_sensor(sensor.id) or sensor.id in selected
    ]
    available_sensors: list[dict[str, str]] = [
        {
            "id": sensor.id,
            "name": sensor.name,
            "unit": sensor.unit,
            "state": sensor.state.value,
        }
        for sensor in sensors
    ]
    return {
        "settings": values,
        "settings_file": str(request.app.settings_store.path.resolve()),
        "data_directory": str(request.app.data_dir.resolve()),
        "available_sensors": available_sensors,
        "restart_required": _restart_required(request, values),
    }


def get_settings(request: Request) -> dict[str, object]:
    """Return user settings and discovered sensors available for selection."""
    values: UserSettings = request.app.settings_store.values()
    return _response(request, values)


def update_settings(request: Request) -> dict[str, object]:
    """Save a complete validated settings object or reset to config defaults."""
    _authorize(request)
    payload: object = request.body
    try:
        if isinstance(payload, dict):
            payload = cast(dict[str, object], payload)
        if isinstance(payload, dict) and set(payload) == {"reset"}:
            if payload["reset"] is not True:
                raise ValueError("reset must be true when resetting settings")
            values: UserSettings = request.app.settings_store.reset()
        else:
            values = request.app.settings_store.save(payload)
    except ValueError as error:
        raise HTTPError(422, str(error)) from error
    except SettingsStorageError as error:
        raise HTTPError(503, str(error)) from error

    request.app.settings = request.app.settings_store.apply()
    request.app.online_lookups_enabled = (
        request.app.settings.online_device_fact_lookup
    )
    return _response(request, values)
