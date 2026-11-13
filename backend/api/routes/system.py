"""Local device discovery and system metrics API handlers."""
from __future__ import annotations

import secrets
from typing import Any, cast

from backend.core.user_settings import SettingsStorageError
from backend.services.device_notes import get_device_note
from backend.services.system_info import get_system_info
from backend.web.router import HTTPError, Request


def system_info(_request: Request) -> dict[str, object]:
    """Return computer details and current system-wide resource usage."""
    return get_system_info()


def device_note(_request: Request) -> dict[str, Any]:
    """Return a sourced public fact and an original motto for this device."""
    return get_device_note(
        get_system_info(),
        online_lookups_enabled=_request.app.online_lookups_enabled,
    )


def online_lookup_setting(request: Request) -> dict[str, bool]:
    """Return whether external device lookups are enabled for this session."""
    return {"enabled": request.app.online_lookups_enabled}


def set_online_lookup_setting(request: Request) -> dict[str, bool]:
    """Set the session's opt-in permission for Wikipedia and Wikimedia lookups."""
    supplied = request.headers.get("X-Overpine-Token")
    if not supplied or not secrets.compare_digest(
        supplied,
        request.app.session_token,
    ):
        raise HTTPError(401, "Valid X-Overpine-Token required")
    raw_body: object = request.body
    if (
        not isinstance(raw_body, dict)
    ):
        raise HTTPError(422, "Request body must contain a boolean enabled field")
    body = cast(dict[str, object], raw_body)
    if set(body) != {"enabled"} or type(body.get("enabled")) is not bool:
        raise HTTPError(422, "Request body must contain a boolean enabled field")
    values = request.app.settings_store.values()
    values["online_device_fact_lookup"] = body["enabled"]
    try:
        request.app.settings_store.save(values)
    except SettingsStorageError as error:
        raise HTTPError(503, str(error)) from error
    request.app.settings = request.app.settings_store.apply()
    request.app.online_lookups_enabled = request.app.settings.online_device_fact_lookup
    return {"enabled": request.app.online_lookups_enabled}
