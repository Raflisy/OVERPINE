"""Disk and network throughput API handler."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.providers.io_rates import IoRateSampler
from backend.web.router import Request

log = logging.getLogger("overpine.io")
sampler = IoRateSampler()
_lock = threading.Lock()
_last_sample = 0.0
_failing = False
MIN_INTERVAL_S = 0.5


def get_io(_request: Request) -> dict[str, Any]:
    """Return disk and network throughput history in bytes per second."""
    global _last_sample, _failing
    error = None
    with _lock:
        now = time.monotonic()
        if now - _last_sample >= MIN_INTERVAL_S:
            _last_sample = now
            try:
                sampler.sample()
                _failing = False
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                if not _failing:
                    log.warning("Disk/network sampling failed: %s", error)
                _failing = True
        points = list(sampler.history)
    return {"points": points, "error": error}
