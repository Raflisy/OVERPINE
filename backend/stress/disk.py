"""Temporary-file disk load generator."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from threading import Event, Thread
from typing import Any

from backend.stress.base import StressWorker

_WRITE_CHUNK_SIZE = 4 * 1024 * 1024


class DiskStressWorker(StressWorker):
    """Write and read a capped temporary file under the app data directory."""

    def __init__(
        self,
        data_dir: Path,
        max_file_bytes: int = 1024**3,
    ) -> None:
        self._stress_dir = data_dir / "stress"
        self._max_file_bytes = max_file_bytes
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        self._state = "idle"
        self._error: str | None = None
        self._bytes_written = 0
        self._bytes_read = 0
        self._file_path: Path | None = None

    def start(self, intensity: int, stop_event: Event) -> None:
        """Start the temporary-file workload."""
        if self._state != "idle":
            raise RuntimeError("Disk stress worker has already been started")
        self._stress_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event = stop_event
        self._state = "running"
        target_size = max(1, int(self._max_file_bytes * intensity / 100))
        self._thread = Thread(
            target=self._run,
            args=(target_size, stop_event),
            name="overpine-disk-stress",
            daemon=True,
        )
        self._thread.start()

    def _run(self, target_size: int, stop_event: Event) -> None:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="overpine-stress-",
                suffix=".tmp",
                dir=self._stress_dir,
                delete=False,
            ) as file:
                self._file_path = Path(file.name)
                block = b"\xA5" * min(_WRITE_CHUNK_SIZE, target_size)
                while self._bytes_written < target_size and not stop_event.is_set():
                    count = min(len(block), target_size - self._bytes_written)
                    file.write(block[:count])
                    self._bytes_written += count
                file.flush()
                os.fsync(file.fileno())
                if not stop_event.is_set():
                    file.seek(0)
                    while self._bytes_read < self._bytes_written and not stop_event.is_set():
                        data = file.read(
                            min(_WRITE_CHUNK_SIZE, self._bytes_written - self._bytes_read)
                        )
                        if not data:
                            raise OSError("Temporary stress file ended before its written size")
                        self._bytes_read += len(data)
                    file.flush()
                    os.fsync(file.fileno())
                    stop_event.wait()
        except OSError as exc:
            self._error = f"Disk workload failed: {exc}"
            self._state = "error"
        finally:
            if self._file_path is not None:
                try:
                    self._file_path.unlink(missing_ok=True)
                except OSError as exc:
                    self._error = f"Could not delete temporary stress file: {exc}"
                    self._state = "error"
            if self._state != "error":
                self._state = "stopped"

    def stop(self) -> None:
        """Signal the worker and wait for temporary-file cleanup."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)

    def status(self) -> dict[str, Any]:
        """Report I/O progress and current temporary-file path."""
        return {
            "state": self._state,
            "bytes_written": self._bytes_written,
            "bytes_read": self._bytes_read,
            "temporary_file_active": bool(
                self._file_path is not None and self._file_path.exists()
            ),
            "error": self._error,
        }

    def result_summary(self) -> dict[str, Any]:
        """Return bytes written and read before cleanup."""
        return {"bytes_written": self._bytes_written, "bytes_read": self._bytes_read}
