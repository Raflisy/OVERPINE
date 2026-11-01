import logging
import threading
import time

from fastapi import APIRouter

from backend.providers.io_rates import IoRateSampler

log = logging.getLogger("overpine.io")

router = APIRouter(tags=["io"])  # /api/v1 is added in backend/main.py

sampler = IoRateSampler()
_lock = threading.Lock()
_last_sample = 0.0
_failing = False
MIN_INTERVAL_S = 0.5  # fast polling or several tabs won't create noisy points


@router.get("/io")
def get_io() -> dict:
    """Disk and network speed history (bytes/second). Empty on the very first call."""
    global _last_sample, _failing
    error = None
    with _lock:
        now = time.monotonic()
        if now - _last_sample >= MIN_INTERVAL_S:
            _last_sample = now
            try:
                sampler.sample()
                _failing = False
            except (OSError, RuntimeError, TypeError, ValueError) as exc:  # a failing counter must not become HTTP 500
                error = f"{type(exc).__name__}: {exc}"
                if not _failing:      # log once, not every second
                    log.warning("Disk/network sampling failed: %s", error)
                _failing = True
        points = list(sampler.history)
    return {"points": points, "error": error}