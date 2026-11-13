"""Launch OVERPINE in Microsoft Edge app mode or the default browser."""
from __future__ import annotations

import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from backend.core.config import load_settings
from backend.core.paths import ensure_user_directories, user_data_dir
from backend.main import Application, create_app
from backend.web.server import LocalHTTPServer

log = logging.getLogger("overpine")


class SingleInstanceLock:
    """Hold an operating-system file lock for the lifetime of the app."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> bool:
        """Return false if another OVERPINE process already holds the lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"\0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self._file.close()
            self._file = None
            return False
        return True

    def release(self) -> None:
        """Release the lock descriptor without removing its persistent file."""
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            log.exception("Could not release the OVERPINE instance lock")
        finally:
            self._file.close()
            self._file = None


def select_port(host: str = "127.0.0.1", preferred_port: int = 8000) -> int:
    """Use the preferred port when free; otherwise ask the OS for a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, preferred_port))
            return preferred_port
        except OSError:
            listener.bind((host, 0))
            return int(listener.getsockname()[1])


def wait_for_health(url: str, server_thread: threading.Thread, timeout: float = 10.0) -> bool:
    """Wait for the local API health endpoint for at most ten seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.1)
    return False


def find_edge_executable() -> Path | None:
    """Find Edge from standard install locations or the App Paths registry key."""
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidate = (
                Path(root)
                / "Microsoft"
                / "Edge"
                / "Application"
                / "msedge.exe"
            )
            if candidate.is_file():
                return candidate

    if os.name != "nt":
        return None

    try:
        import winreg
    except ImportError:
        log.exception("Windows registry support is unavailable")
        return None

    app_paths = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    views = (
        winreg.KEY_WOW64_64KEY,
        winreg.KEY_WOW64_32KEY,
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(
                    hive,
                    app_paths,
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    value, _value_type = winreg.QueryValueEx(key, "")
            except FileNotFoundError:
                continue
            except OSError:
                log.debug(
                    "Could not read the Edge App Paths registry entry",
                    exc_info=True,
                )
                continue
            candidate = Path(str(value).strip('"'))
            if candidate.is_file():
                return candidate
    return None


def start_edge_app(
    executable: Path,
    url: str,
    profile_dir: Path,
) -> subprocess.Popen[bytes]:
    """Launch Edge with fixed arguments and an isolated OVERPINE profile."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            str(executable),
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
        ],
        close_fds=True,
    )


def wait_for_window_close(
    application: Application,
    process: subprocess.Popen[bytes] | None,
    heartbeat_timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> None:
    """Wait for Edge exit or for the frontend heartbeat to stop."""
    if heartbeat_timeout <= 0 or poll_interval <= 0:
        raise ValueError("Heartbeat and polling intervals must be positive")
    while True:
        if (
            process is not None
            and application.heartbeat_count
            and process.poll() is not None
        ):
            return
        if time.monotonic() - application.last_heartbeat_at >= heartbeat_timeout:
            return
        time.sleep(poll_interval)


def _show_message(title: str, message: str, error: bool = False) -> None:
    """Show fallback/error information in a console or native dialog."""
    if sys.stdout is not None:
        print(f"{title}: {message}", file=sys.stderr if error else sys.stdout)
    try:
        if os.name == "nt":
            import ctypes

            flags = 0x10 if error else 0x40
            ctypes.windll.user32.MessageBoxW(None, message, title, flags)
        else:
            log.error("%s: %s", title, message)
    except Exception:
        log.exception("Could not display the OVERPINE desktop message")


def _run_desktop() -> int:
    """Start the local server, then open a native window or browser fallback."""
    logging.basicConfig(level=logging.WARNING)
    try:
        ensure_user_directories()
        instance_lock = SingleInstanceLock(user_data_dir() / "overpine.lock")
        if not instance_lock.acquire():
            _show_message(
                "OVERPINE is already running",
                "Only one OVERPINE desktop instance can run at a time.",
            )
            return 0
    except OSError:
        log.exception("Could not initialize OVERPINE writable storage")
        _show_message(
            "OVERPINE could not start",
            "Writable application folders could not be created. Check the log or console.",
            error=True,
        )
        return 1

    exit_code = 0
    server: LocalHTTPServer | None = None
    server_thread: threading.Thread | None = None
    try:
        from dataclasses import replace

        settings = load_settings()
        port = select_port(preferred_port=settings.port)
        app = create_app(
            replace(settings, port=port),
            release_instance_lock=instance_lock.release,
        )
        try:
            server = LocalHTTPServer(app, host="127.0.0.1", port=port)
        except OSError as exc:
            log.warning(
                "Could not bind to 127.0.0.1:%d (%s); selecting a free local port",
                port,
                exc,
            )
            server = LocalHTTPServer(app, host="127.0.0.1", port=0)
        actual_port = int(server.server_address[1])
        url = f"http://127.0.0.1:{actual_port}"
        server_thread = server.start_in_thread(timeout=10.0)
        if not wait_for_health(
            f"{url}/api/v1/health",
            server_thread,
            timeout=10.0,
        ):
            log.error(
                "OVERPINE server did not become healthy at %s within 10 seconds",
                url,
            )
            _show_message(
                "OVERPINE could not start",
                "The local server did not become ready within 10 seconds.",
                error=True,
            )
            return 1

        app.last_heartbeat_at = time.monotonic()
        app.heartbeat_count = 0
        edge = find_edge_executable()
        edge_process: subprocess.Popen[bytes] | None = None
        if edge is not None:
            try:
                edge_process = start_edge_app(
                    edge,
                    url,
                    user_data_dir() / "webview-profile",
                )
            except OSError:
                log.exception("Could not start Microsoft Edge in app mode")

        if edge_process is None:
            if edge is None:
                message = (
                    "Microsoft Edge was not found. Opening OVERPINE in the default "
                    f"browser at {url}."
                )
            else:
                message = (
                    "Microsoft Edge could not be started. Opening OVERPINE in the "
                    f"default browser at {url}."
                )
            print(message)
            if not webbrowser.open(url):
                _show_message(
                    "OVERPINE could not open a browser",
                    f"Open this local address manually: {url}",
                    error=True,
                )
                exit_code = 1
            else:
                wait_for_window_close(app, None)
        else:
            wait_for_window_close(app, edge_process)
    except KeyboardInterrupt:
        log.info("OVERPINE desktop shutdown requested")
    except Exception:
        log.exception("The OVERPINE local server failed")
        exit_code = 1
    finally:
        if server is not None:
            try:
                server.stop(timeout=5)
            except (OSError, RuntimeError, TimeoutError):
                log.exception("Could not stop the OVERPINE local server cleanly")
        instance_lock.release()
    return exit_code


def main() -> int:
    """Run the desktop launcher after initializing multiprocessing support."""
    multiprocessing.freeze_support()
    return _run_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
