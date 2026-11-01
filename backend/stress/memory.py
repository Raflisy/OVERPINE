"""Memory load generator with a strict available-RAM ceiling."""
from __future__ import annotations

import threading
from threading import Event, Thread
from typing import Any

import psutil

from backend.stress.base import StressWorker

_PAGE_SIZE = 4096
_CHUNK_SIZE = 8 * 1024 * 1024


class MemoryStressWorker(StressWorker):
    """Touch allocated pages without exceeding 60% of currently free RAM."""

    def __init__(self, max_allocation_bytes: int | None = None) -> None:
        self._max_allocation_bytes = max_allocation_bytes
        self._thread: Thread | None = None
        self._state = "idle"
        self._error: str | None = None
        self._allocated_bytes = 0
        self._stop_event: Event | None = None

    def start(self, intensity: int, stop_event: Event) -> None:
        """Allocate and touch a capped fraction of currently available RAM."""
        if self._state != "idle":
            raise RuntimeError("Memory stress worker has already been started")
        self._stop_event = stop_event
        memory = psutil.virtual_memory()
        available = memory.available
        total = memory.total
        allocation_limit = min(
            int(available * intensity / 100),
            int(available * 0.60),
            max(0, available - int(total * 0.15)),
        )
        if self._max_allocation_bytes is not None:
            allocation_limit = min(allocation_limit, self._max_allocation_bytes)
        self._state = "running"
        self._thread = Thread(
            target=self._allocate,
            args=(allocation_limit, stop_event),
            name="overpine-memory-stress",
            daemon=True,
        )
        self._thread.start()

    def _allocate(self, allocation_limit: int, stop_event: Event) -> None:
        chunks: list[bytearray] = []
        try:
            while self._allocated_bytes < allocation_limit and not stop_event.is_set():
                chunk_size = min(_CHUNK_SIZE, allocation_limit - self._allocated_bytes)
                chunk = bytearray(chunk_size)
                for offset in range(0, chunk_size, _PAGE_SIZE):
                    chunk[offset] = 1
                chunks.append(chunk)
                self._allocated_bytes += chunk_size
            stop_event.wait()
        except (MemoryError, OSError) as exc:
            self._error = f"Memory allocation failed: {exc}"
            self._state = "error"
        finally:
            chunks.clear()
            self._allocated_bytes = 0
            if self._state != "error":
                self._state = "stopped"

    def stop(self) -> None:
        """Release allocated pages and wait for the worker thread to exit."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def status(self) -> dict[str, Any]:
        """Report worker state and current allocation."""
        return {
            "state": self._state,
            "allocated_bytes": self._allocated_bytes,
            "error": self._error,
        }

    def result_summary(self) -> dict[str, Any]:
        """Return the worker's final allocation state."""
        return {"allocated_bytes_at_stop": self._allocated_bytes}
