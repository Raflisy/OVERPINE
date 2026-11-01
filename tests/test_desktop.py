"""Path, privileges, and desktop-server configuration tests."""
from __future__ import annotations

import socket
import sys
from pathlib import Path

from backend.core import config, paths, privileges
from backend.core.config import Settings
from backend.main import create_app
from backend.monitoring.scheduler import MonitoringScheduler
from desktop import SingleInstanceLock, select_port, window_geometry


def test_development_paths_use_the_project_root(monkeypatch):
    """Source runs keep resources and writable files beside the source."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    project_root = Path(paths.__file__).resolve().parents[2]

    assert paths.is_frozen() is False
    assert paths.resource_dir() == project_root
    assert paths.user_data_dir() == project_root


def test_frozen_paths_separate_bundled_resources_and_user_data(
    tmp_path: Path,
    monkeypatch,
):
    """Frozen resources come from _MEIPASS and writes go to LOCALAPPDATA."""
    bundle_root = tmp_path / "bundle"
    local_app_data = tmp_path / "local"
    bundle_root.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert paths.is_frozen() is True
    assert paths.resource_dir() == bundle_root
    assert paths.user_data_dir() == local_app_data / "OVERPINE"
    assert (local_app_data / "OVERPINE").is_dir()

    paths.ensure_user_directories()
    assert (local_app_data / "OVERPINE" / "logs").is_dir()
    assert (local_app_data / "OVERPINE" / "data").is_dir()
    assert (local_app_data / "OVERPINE" / "data" / "exports").is_dir()
    assert (local_app_data / "OVERPINE" / "data" / "stress").is_dir()


def test_user_config_overrides_bundled_values(tmp_path: Path, monkeypatch):
    """User config values overlay bundled sections without replacing defaults."""
    bundled_config = tmp_path / "default_config.toml"
    bundled_config.write_text(
        '[server]\nhost = "127.0.0.1"\nport = 8000\n'
        '[logging]\nlevel = "INFO"\nfile = "logs/overpine.log"\n',
        encoding="utf-8",
    )
    user_root = tmp_path / "user-data"
    user_root.mkdir()
    (user_root / "config.toml").write_text(
        '[server]\nport = 8123\n[logging]\nlevel = "WARNING"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", bundled_config)
    monkeypatch.setattr(config, "user_data_dir", lambda: user_root)

    settings = config.load_settings()

    assert settings.port == 8123
    assert settings.log_level == "WARNING"
    assert settings.log_file == "logs/overpine.log"


def test_is_admin_returns_false_when_windows_api_raises(monkeypatch):
    """Privilege detection safely handles unsupported or failing ctypes APIs."""
    monkeypatch.setattr(privileges.ctypes, "windll", None, raising=False)
    assert privileges.is_admin() is False


def test_desktop_single_instance_lock_is_exclusive(tmp_path: Path):
    """Only one launcher can hold the per-user app lock at a time."""
    lock_path = tmp_path / "overpine.lock"
    first = SingleInstanceLock(lock_path)
    second = SingleInstanceLock(lock_path)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_window_geometry_scales_to_various_screen_aspect_ratios():
    """The native window stays within landscape, portrait, and small displays."""
    for screen_width, screen_height in (
        (1920, 1080),
        (1366, 768),
        (900, 1600),
        (800, 600),
    ):
        width, height, min_size = window_geometry(screen_width, screen_height)

        assert width <= screen_width
        assert height <= screen_height
        assert min_size[0] <= width
        assert min_size[1] <= height
        assert width == round(screen_width * 0.92)
        assert height == round(screen_height * 0.92)


def test_busy_preferred_port_selects_free_port_and_configures_local_origins():
    """A busy 8000 falls back to a free port shared by host and CORS settings."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        try:
            listener.bind(("127.0.0.1", 8000))
            listener.listen()
        except OSError:
            pass

        selected_port = select_port(preferred_port=8000)
        assert selected_port != 8000

        app = create_app(Settings(port=selected_port))
        trusted = next(
            middleware.kwargs["allowed_hosts"]
            for middleware in app.user_middleware
            if middleware.cls.__name__ == "TrustedHostMiddleware"
        )
        cors = next(
            middleware.kwargs["allow_origins"]
            for middleware in app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        assert f"127.0.0.1:{selected_port}" in trusted
        assert f"localhost:{selected_port}" in trusted
        assert f"http://127.0.0.1:{selected_port}" in cors
        assert f"http://localhost:{selected_port}" in cors
    finally:
        listener.close()


def test_health_includes_admin_status():
    """Health API exposes a boolean rather than an inferred privilege state."""
    from fastapi.testclient import TestClient

    app = create_app()
    app.state.monitoring_scheduler = MonitoringScheduler(providers=[])
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert isinstance(response.json()["is_admin"], bool)


def test_admin_restart_endpoint_requires_stress_session_token(tmp_path: Path):
    """Elevation endpoint rejects missing tokens before attempting a restart."""
    from fastapi.testclient import TestClient

    app = create_app()
    app.state.monitoring_scheduler = MonitoringScheduler(providers=[])
    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 8000),
    ) as client:
        response = client.post("/api/v1/admin/restart-as-admin")
    assert response.status_code == 401
