"""Launch OVERPINE in a native WebView2 window on Windows."""
from __future__ import annotations

import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from backend.core.config import load_settings
from backend.core.paths import ensure_user_directories, user_data_dir
from backend.main import create_app

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


def window_geometry(
    screen_width: int,
    screen_height: int,
) -> tuple[int, int, tuple[int, int]]:
    """Choose a large window that remains within the available screen size."""
    if screen_width <= 0 or screen_height <= 0:
        raise ValueError("Screen dimensions must be positive")

    width = max(1, round(screen_width * 0.92))
    height = max(1, round(screen_height * 0.92))
    min_size = (min(900, width), min(600, height))
    return width, height, min_size


def _screen_dimensions() -> tuple[int, int]:
    """Read the primary screen size, using the existing default off Windows."""
    if os.name != "nt":
        return 1280, 800

    import ctypes

    user32 = ctypes.windll.user32
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


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

    port = select_port()
    url = f"http://127.0.0.1:{port}"
    app_holder = {}

    def app_factory():
        from dataclasses import replace

        app = create_app(
            replace(load_settings(), port=port),
            release_instance_lock=instance_lock.release,
        )
        app_holder["app"] = app
        return app

    config = uvicorn.Config(
        app_factory,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
        access_log=False,
        factory=True,
    )
    server = uvicorn.Server(config)

    def run_server() -> None:
        try:
            server.run()
        except Exception:
            log.exception("The OVERPINE local server failed")

    server_thread = threading.Thread(
        target=run_server,
        name="overpine-local-server",
        daemon=True,
    )
    server_thread.start()

    exit_code = 0
    try:
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

        try:
            import webview

            width, height, min_size = window_geometry(*_screen_dimensions())
            webview.create_window(
                "OVERPINE",
                url,
                width=width,
                height=height,
                min_size=min_size,
            )
            webview.start(gui="edgechromium")
        except Exception:
            log.exception("The OVERPINE WebView2 window could not be opened")
            webbrowser.open(url)
            _show_message(
                "OVERPINE browser fallback",
                "The native WebView2 window could not be opened. OVERPINE has been "
                f"opened in your default browser at {url}. Closing this message stops "
                "the local server.",
                error=True,
            )
    except KeyboardInterrupt:
        log.info("OVERPINE desktop shutdown requested")
    finally:
        server.should_exit = True
        server_thread.join(timeout=3)
        if server_thread.is_alive():
            server.force_exit = True
            server_thread.join(timeout=2)
        app = app_holder.get("app")
        if app is not None:
            app.state.stress_manager.shutdown()
        instance_lock.release()
    return exit_code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(_run_desktop())
