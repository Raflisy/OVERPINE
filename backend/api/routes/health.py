"""Health endpoint: proves the backend is alive and reports its own cost."""
from __future__ import annotations

import time

import psutil
from fastapi import APIRouter

from backend.core.config import APP_NAME, APP_VERSION
from backend.core.privileges import is_admin

router = APIRouter(tags=["health"])

_started_at = time.time()
_process = psutil.Process()
_process.cpu_percent(interval=None)  # prime the counter (first call returns 0.0)


@router.get("/health")
def health() -> dict:
    """Return app status plus OVERPINE's own CPU/RAM usage."""
    mem = _process.memory_info()
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "is_admin": is_admin(),
        "uptime_seconds": round(time.time() - _started_at, 1),
        "self": {
            "cpu_percent": _process.cpu_percent(interval=None),
            "ram_mb": round(mem.rss / (1024 * 1024), 1),
        },
    }
