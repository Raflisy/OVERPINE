"""Build the standard-library HTTP application and its background services."""
from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from backend.api.routes import (
    about,
    disk_network,
    health,
    history,
    sensors,
    settings as app_settings,
    stress,
    system,
)
from backend.core.config import (
    APP_NAME,
    APP_VERSION,
    DATA_DIR,
    FRONTEND_DIR,
    Settings,
    load_settings,
)
from backend.api.routes import (
    about,
    disk_network,
    health,
    diagnostics,
)
from backend.api.routes import (
    about,
    diagnostics,
    disk_network,
    health,
)

from backend.core.logging_setup import setup_logging
from backend.core.paths import ensure_user_directories
from backend.core.user_settings import SettingsStore
from backend.monitoring.scheduler import MonitoringScheduler
from backend.storage.history import HistoryStore
from backend.stress.manager import StressManager
from backend.web.router import Router

log = logging.getLogger(__name__)


def trusted_hosts_for_port(port: int) -> list[str]:
    """Return allowed local Host header values for a selected port."""
    return [
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{port}",
        f"localhost:{port}",
    ]


def cors_origins_for_port(port: int) -> list[str]:
    """Return allowed browser origins for a selected local port."""
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


class Application:
    """Own route handlers, monitoring services, and application lifecycle."""

    def __init__(
        self,
        settings: Settings,
        frontend_dir: Path = FRONTEND_DIR,
        release_instance_lock: Callable[[], None] | None = None,
    ) -> None:
        """Create services and register the full local monitoring API."""
        self.data_dir = Path(DATA_DIR)
        self.settings_store = SettingsStore(
            self.data_dir / "settings.json",
            settings,
        )
        self.settings = self.settings_store.apply()
        self.frontend_dir = frontend_dir.resolve()
        self.session_token = secrets.token_urlsafe(32)
        self.last_heartbeat_at = time.monotonic()
        self.heartbeat_count = 0
        self.online_lookups_enabled = self.settings.online_device_fact_lookup
        self.release_instance_lock = release_instance_lock
        self.router = Router()
        self.history_store = HistoryStore(
            self.data_dir / "history.db",
            enabled=self.settings.history_enabled,
            retention_days=self.settings.history_retention_days,
            max_mb=self.settings.history_max_mb,
            recorded_sensor_ids=self.settings.history_record_sensors,
        )
        self.monitoring_scheduler = MonitoringScheduler(
            intervals=self.settings.monitoring_intervals,
            history_size=self.settings.monitoring_history_size,
        )
        self.monitoring_scheduler.registry.set_sample_callback(
            self.history_store.record
        )
        self.stress_manager = StressManager(
            sensor_registry=self.monitoring_scheduler.registry,
        )
        self._register_routes()

    def start(self) -> None:
        """Start monitoring before the HTTP server begins accepting requests."""
        self.monitoring_scheduler.start()

    def stop(self) -> None:
        """Stop stress workers and monitoring during HTTP server shutdown."""
        try:
            self.stress_manager.shutdown()
        finally:
            try:
                self.monitoring_scheduler.stop()
            finally:
                self.history_store.close()

    def _register_routes(self) -> None:
        """Register API methods and strict parameter patterns."""
        routes = (
            ("GET", "/api/about", about.get_about, None),
            ("GET", "/api/settings", app_settings.get_settings, None),
            ("POST", "/api/settings", app_settings.update_settings, None),
            ("GET", "/api/history", history.get_history, None),
            ("GET", "/api/history/sensors", history.list_history_sensors, None),
            ("POST", "/api/history/clear", history.clear_history, None),
            ("GET", "/api/v1/health", health.health, None),
            ("POST", "/api/v1/heartbeat", health.heartbeat, None),
            ("GET", "/api/v1/system", system.system_info, None),
            ("GET", "/api/v1/device-note", system.device_note, None),
            ("GET", "/api/logs", diagnostics.get_logs, None),
            ("GET", "/api/diagnostics", diagnostics.get_diagnostics, None),
            (
                "GET",
                "/api/v1/settings/online-lookups",
                system.online_lookup_setting,
                None,
            ),
            (
                "POST",
                "/api/v1/settings/online-lookups",
                system.set_online_lookup_setting,
                None,
            ),
            ("GET", "/api/v1/io", disk_network.get_io, None),
            ("GET", "/api/v1/sensors", sensors.list_sensors, None),
            (
                "GET",
                "/api/v1/sensors/{sensor_id}/history",
                sensors.get_sensor_history,
                {"sensor_id": r"[^/]{1,257}"},
            ),
            (
                "GET",
                "/api/v1/sensors/{sensor_id}",
                sensors.get_sensor,
                {"sensor_id": r"[^/]{1,257}"},
            ),
            ("GET", "/api/v1/monitoring/status", sensors.monitoring_status, None),
            ("POST", "/api/v1/monitoring/pause", sensors.pause_monitoring, None),
            ("POST", "/api/v1/monitoring/resume", sensors.resume_monitoring, None),
            ("GET", "/api/v1/stress/token", stress.get_session_token, None),
            ("POST", "/api/v1/stress/start", stress.start_stress, None),
            ("POST", "/api/v1/stress/stop", stress.stop_stress, None),
            ("GET", "/api/v1/stress/status", stress.stress_status, None),
            ("GET", "/api/v1/stress/results", stress.stress_results, None),
            (
                "POST",
                "/api/v1/admin/restart-as-admin",
                stress.request_admin_restart,
                None,
            ),
        )
        for method, path, handler, patterns in routes:
            self.router.add(method, path, handler, patterns)


def create_app(
    settings: Settings | None = None,
    release_instance_lock: Callable[[], None] | None = None,
    configure_logging: bool = True,
    frontend_dir: Path = FRONTEND_DIR,
) -> Application:
    """Create the local application with configured services and logging."""
    selected_settings = settings or load_settings()
    if configure_logging:
        ensure_user_directories()
        setup_logging(selected_settings)
    application = Application(
        settings=selected_settings,
        frontend_dir=frontend_dir,
        release_instance_lock=release_instance_lock,
    )
    log.info("%s %s ready", APP_NAME, APP_VERSION)
    return application
