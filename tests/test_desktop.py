"""Path, privileges, and desktop-server configuration tests."""
from __future__ import annotations

import os
import socket
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from backend.core import config, paths, privileges
from backend.core.config import Settings
from backend.monitoring.scheduler import MonitoringScheduler
from backend.web.server import LocalHTTPServer
import desktop
from desktop import (
    SingleInstanceLock,
    find_edge_executable,
    select_port,
    start_edge_app,
    wait_for_window_close,
)
from tests.http_client import LocalHTTPTestClient
from tests.test_support import create_isolated_app


class DesktopTests(unittest.TestCase):
    """Verify local desktop configuration and isolated path handling."""

    def test_development_paths_use_the_project_root(self) -> None:
        """Source runs keep resources and writable files beside the source."""
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.object(sys, "_MEIPASS", None, create=True),
        ):
            project_root = Path(paths.__file__).resolve().parents[2]
            self.assertFalse(paths.is_frozen())
            self.assertEqual(paths.resource_dir(), project_root)
            self.assertEqual(paths.user_data_dir(), project_root)

    def test_frozen_paths_separate_bundled_resources_and_user_data(self) -> None:
        """Frozen resources come from _MEIPASS and writes go to LOCALAPPDATA."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_root = root / "bundle"
            local_app_data = root / "local"
            bundle_root.mkdir()
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(bundle_root), create=True),
                patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
            ):
                self.assertTrue(paths.is_frozen())
                self.assertEqual(paths.resource_dir(), bundle_root)
                self.assertEqual(
                    paths.user_data_dir(),
                    local_app_data / "OVERPINE",
                )
                self.assertTrue((local_app_data / "OVERPINE").is_dir())
                paths.ensure_user_directories()

            for subdirectory in (
                "logs",
                "data",
                "data/exports",
                "data/stress",
            ):
                self.assertTrue(
                    (local_app_data / "OVERPINE" / subdirectory).is_dir()
                )

    def test_user_config_overrides_bundled_values(self) -> None:
        """User config values overlay bundled sections without replacing defaults."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundled_config = root / "default_config.toml"
            bundled_config.write_text(
                '[server]\nhost = "127.0.0.1"\nport = 8000\n'
                '[logging]\nlevel = "INFO"\nfile = "logs/overpine.log"\n',
                encoding="utf-8",
            )
            user_root = root / "user-data"
            user_root.mkdir()
            (user_root / "config.toml").write_text(
                '[server]\nport = 8123\n[logging]\nlevel = "WARNING"\n',
                encoding="utf-8",
            )
            with (
                patch.object(config, "CONFIG_FILE", bundled_config),
                patch.object(config, "user_data_dir", lambda: user_root),
            ):
                settings = config.load_settings()

        self.assertEqual(settings.port, 8123)
        self.assertEqual(settings.log_level, "WARNING")
        self.assertEqual(settings.log_file, "logs/overpine.log")

    def test_is_admin_returns_false_when_windows_api_raises(self) -> None:
        """Privilege detection safely handles unsupported or failing ctypes APIs."""
        with patch.object(privileges.ctypes, "windll", None, create=True):
            self.assertFalse(privileges.is_admin())

    def test_desktop_single_instance_lock_is_exclusive(self) -> None:
        """Only one launcher can hold the per-user app lock at a time."""
        with TemporaryDirectory() as directory:
            lock_path = Path(directory) / "overpine.lock"
            first = SingleInstanceLock(lock_path)
            second = SingleInstanceLock(lock_path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_edge_is_found_in_a_standard_program_files_location(self) -> None:
        """Locate Edge without relying on the Windows registry."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = (
                root
                / "Microsoft"
                / "Edge"
                / "Application"
                / "msedge.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict(
                os.environ,
                {
                    "ProgramFiles(x86)": "",
                    "ProgramFiles": directory,
                    "LOCALAPPDATA": "",
                },
            ):
                self.assertEqual(find_edge_executable(), executable)

    def test_edge_is_found_through_the_app_paths_registry(self) -> None:
        """Read the registered Edge executable when standard folders miss it."""
        with TemporaryDirectory() as directory:
            executable = Path(directory) / "msedge.exe"
            executable.touch()

            class RegistryKey:
                def __enter__(self):
                    return self

                def __exit__(self, *_exc_info):
                    return None

            registry = SimpleNamespace(
                HKEY_CURRENT_USER=1,
                HKEY_LOCAL_MACHINE=2,
                KEY_READ=0x20019,
                KEY_WOW64_64KEY=0x100,
                KEY_WOW64_32KEY=0x200,
                OpenKey=lambda *_args: RegistryKey(),
                QueryValueEx=lambda *_args: (str(executable), 1),
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "ProgramFiles(x86)": "",
                        "ProgramFiles": "",
                        "LOCALAPPDATA": "",
                    },
                ),
                patch.object(os, "name", "nt"),
                patch.dict(sys.modules, {"winreg": registry}),
            ):
                self.assertEqual(find_edge_executable(), executable)

    def test_edge_app_uses_fixed_app_mode_arguments_and_separate_profile(self) -> None:
        """Launch Edge without a shell and keep its profile in user data."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "msedge.exe"
            profile = root / "webview-profile"
            with patch("desktop.subprocess.Popen") as popen:
                process = start_edge_app(
                    executable,
                    "http://127.0.0.1:8123",
                    profile,
                )

        self.assertIs(process, popen.return_value)
        popen.assert_called_once_with(
            [
                str(executable),
                "--app=http://127.0.0.1:8123",
                f"--user-data-dir={profile}",
            ],
            close_fds=True,
        )

    def test_window_wait_ends_when_edge_exits_or_heartbeat_is_stale(self) -> None:
        """Accept either Edge process exit or the ten-second heartbeat timeout."""
        app = create_isolated_app(self, configure_logging=False)
        app.heartbeat_count = 1
        process = SimpleNamespace(poll=lambda: 0)
        wait_for_window_close(app, process)

        app.heartbeat_count = 0
        app.last_heartbeat_at = time.monotonic() - 11
        wait_for_window_close(app, None)

    def test_main_initializes_multiprocessing_before_desktop_start(self) -> None:
        """Call freeze_support before constructing desktop processes."""
        calls = []

        def freeze_support() -> None:
            calls.append("freeze")

        def run_desktop() -> int:
            calls.append("run")
            return 7

        with (
            patch.object(desktop.multiprocessing, "freeze_support", freeze_support),
            patch.object(desktop, "_run_desktop", run_desktop),
        ):
            self.assertEqual(desktop.main(), 7)
        self.assertEqual(calls, ["freeze", "run"])

    def test_heartbeat_requires_token_and_refreshes_timestamp(self) -> None:
        """Only authenticated local frontend requests keep the desktop alive."""
        app = create_isolated_app(self, configure_logging=False)
        app.last_heartbeat_at = time.monotonic() - 1
        with LocalHTTPTestClient(app) as client:
            before_rejected = app.last_heartbeat_at
            rejected = client.post("/api/v1/heartbeat")
            wrong_token = client.post(
                "/api/v1/heartbeat",
                headers={"X-Overpine-Token": "wrong-token"},
            )
            self.assertEqual(app.last_heartbeat_at, before_rejected)

            before = app.last_heartbeat_at
            t0 = time.monotonic()
            accepted = client.post(
                "/api/v1/heartbeat",
                headers={"X-Overpine-Token": app.session_token},
            )

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(wrong_token.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {"status": "ok"})
        self.assertEqual(app.heartbeat_count, 1)
        self.assertGreaterEqual(app.last_heartbeat_at, t0)
        self.assertGreater(app.last_heartbeat_at, before)

    def test_busy_preferred_port_selects_free_port_and_configures_local_origins(
        self,
    ) -> None:
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
            self.assertNotEqual(selected_port, 8000)
            app = create_isolated_app(
                self,
                Settings(port=selected_port),
                configure_logging=False,
            )
            server = LocalHTTPServer(app, port=selected_port)
            try:
                self.assertIn(f"127.0.0.1:{selected_port}", server.allowed_hosts)
                self.assertIn(f"localhost:{selected_port}", server.allowed_hosts)
                self.assertIn(
                    f"http://127.0.0.1:{selected_port}",
                    server.allowed_origins,
                )
                self.assertIn(
                    f"http://localhost:{selected_port}",
                    server.allowed_origins,
                )
            finally:
                server.server_close()
        finally:
            listener.close()

    def test_health_includes_admin_status(self) -> None:
        """Health API exposes a boolean rather than an inferred privilege state."""
        app = create_isolated_app(self, configure_logging=False)
        app.monitoring_scheduler = MonitoringScheduler(providers=[])
        with LocalHTTPTestClient(app) as client:
            response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["is_admin"], bool)

    def test_admin_restart_endpoint_requires_stress_session_token(self) -> None:
        """Elevation endpoint rejects missing tokens before attempting a restart."""
        app = create_isolated_app(self, configure_logging=False)
        app.monitoring_scheduler = MonitoringScheduler(providers=[])
        with LocalHTTPTestClient(app) as client:
            response = client.post("/api/v1/admin/restart-as-admin")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
