"""Local device discovery and system metrics API."""
from __future__ import annotations

from fastapi import APIRouter

from backend.services.device_notes import get_device_note
from backend.services.system_info import get_system_info

router = APIRouter(tags=["system"])


@router.get("/system")
def system_info() -> dict[str, object]:
    """Return computer details and current system-wide resource usage."""
    return get_system_info()


@router.get("/device-note")
def device_note() -> dict[str, str | None]:
    """Return a sourced public fact and an original motto for this device."""
    return get_device_note(get_system_info())
