"""OVERPINE backend entry point.

Run (from the project root):
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import disk_network, health, sensors, stress, system
from backend.core.config import (
    APP_NAME,
    APP_VERSION,
    FRONTEND_DIR,
    Settings,
    load_settings,
)
from backend.core.logging_setup import setup_logging
from backend.core.paths import ensure_user_directories
from backend.monitoring.scheduler import MonitoringScheduler
from backend.stress.manager import StressManager

log = logging.getLogger(__name__)


def trusted_hosts_for_port(port: int) -> list[str]:
    """Return local host allowlist entries for a selected server port."""
    return [
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{port}",
        f"localhost:{port}",
    ]


def cors_origins_for_port(port: int) -> list[str]:
    """Return browser origins for the selected local server port."""
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def create_app(
    settings: Settings | None = None,
    release_instance_lock: Callable[[], None] | None = None,
) -> FastAPI:
    """Create the local monitoring API and its frozen-aware resource mounts."""
    settings = settings or load_settings()
    ensure_user_directories()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start monitoring and stop workers/providers during application shutdown."""
        app.state.monitoring_scheduler.start()
        try:
            yield
        finally:
            app.state.stress_manager.shutdown()
            app.state.monitoring_scheduler.stop()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    scheduler = MonitoringScheduler(
        intervals=settings.monitoring_intervals,
        history_size=settings.monitoring_history_size,
        simulation=settings.monitoring_simulation,
        lhm_enabled=settings.lhm_enabled,
        lhm_host=settings.lhm_host,
        lhm_port=settings.lhm_port,
        lhm_timeout_s=settings.lhm_timeout_s,
        smart_enabled=settings.smart_enabled,
    )
    app.state.monitoring_scheduler = scheduler
    app.state.stress_manager = StressManager(sensor_registry=scheduler.registry)
    app.state.stress_token = secrets.token_urlsafe(32)
    app.state.release_instance_lock = release_instance_lock

    # Security: only accept requests addressed to localhost (blocks DNS rebinding).
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts_for_port(settings.port),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_for_port(settings.port),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Overpine-Token"],
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(disk_network.router, prefix="/api/v1")
    app.include_router(sensors.router, prefix="/api/v1")
    app.include_router(stress.router, prefix="/api/v1")
    app.include_router(stress.admin_router, prefix="/api/v1")
    # Frontend is mounted last so /api and /docs take priority.
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    log.info("%s %s ready", APP_NAME, APP_VERSION)
    return app


app = create_app()
