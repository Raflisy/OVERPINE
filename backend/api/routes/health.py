"""Health endpoint with best-effort current-process metrics."""
from __future__ import annotations

import time
from typing import Any

from backend.core.config import APP_NAME, APP_VERSION
from backend.core.privileges import is_admin
from backend.web.router import Request
from backend.winapi import current_process_metrics, system_uptime_seconds


def health(_request: Request) -> dict[str, Any]:
    """Return health with measured current-process metrics when Windows exposes them."""
    metrics = current_process_metrics()
    uptime = system_uptime_seconds()
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "is_admin": is_admin(),
        "uptime_seconds": round(uptime, 1) if uptime is not None else None,
        "self": {
            "cpu_percent": metrics.cpu_percent,
            "ram_mb": (
                round(metrics.working_set_bytes / (1024**2), 1)
                if metrics.working_set_bytes is not None
                else None
            ),
        },
    }


def heartbeat(request: Request) -> dict[str, str]:
    """Record an authenticated frontend heartbeat for desktop lifecycle checks."""
    request.app.last_heartbeat_at = time.monotonic()
    request.app.heartbeat_count += 1
    return {"status": "ok"}
