"""Multiprocess CPU load generator."""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import threading
from threading import Event, Thread
from typing import Any

from backend.stress.base import StressWorker


def _cpu_load(stop_event: Any, completed_batches: Any, index: int) -> None:
    """Run a bounded hashing loop until the process stop event is set."""
    payload = b"OVERPINE local CPU stress workload"
    local_batches = 0
    while not stop_event.is_set():
        for _ in range(256):
            payload = hashlib.sha256(payload).digest()
        local_batches += 1
        if local_batches % 32 == 0:
            with completed_batches.get_lock():
                completed_batches[index] += 32


class CpuStressWorker(StressWorker):
    """CPU worker using separate processes so the workload is not GIL-bound."""

    def __init__(self) -> None:
        self._processes: list[multiprocessing.Process] = []
        self._stop_event: Any = None
        self._counts: Any = None
        self._external_stop = Event()
        self._watcher: Thread | None = None
        self._state = "idle"
        self._error: str | None = None
        self._worker_count = 0

    def start(self, intensity: int, stop_event: Event) -> None:
        """Start one worker per requested logical-core fraction."""
        if self._state != "idle":
            raise RuntimeError("CPU stress worker has already been started")
        logical_cores = os.cpu_count() or 1
        self._worker_count = max(1, round(logical_cores * intensity / 100))
        context = multiprocessing.get_context("spawn")
        self._stop_event = context.Event()
        self._counts = context.Array("Q", self._worker_count, lock=True)
        try:
            for index in range(self._worker_count):
                process = context.Process(
                    target=_cpu_load,
                    args=(self._stop_event, self._counts, index),
                    name=f"overpine-cpu-stress-{index}",
                )
                process.start()
                self._processes.append(process)
        except Exception:
            self.stop()
            raise

        self._state = "running"

        def watch_stop() -> None:
            while not self._external_stop.wait(0.1):
                if stop_event.is_set():
                    self._stop_event.set()
                    return

        self._watcher = Thread(target=watch_stop, name="overpine-cpu-stop", daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        """Signal child processes, then terminate any that do not exit promptly."""
        self._external_stop.set()
        if self._stop_event is not None:
            self._stop_event.set()
        for process in self._processes:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1)
        if self._watcher is not None and self._watcher is not threading.current_thread():
            self._watcher.join(timeout=1)
        self._state = "stopped"

    def status(self) -> dict[str, Any]:
        """Report child process health and completed workload batches."""
        if self._state == "running" and any(
            process.exitcode is not None for process in self._processes
        ):
            self._state = "error"
            self._error = "CPU worker processes exited unexpectedly"
        completed = (
            sum(self._counts[index] for index in range(self._worker_count))
            if self._counts is not None
            else 0
        )
        return {
            "state": self._state,
            "workers": self._worker_count,
            "completed_batches": completed,
            "error": self._error,
        }

    def result_summary(self) -> dict[str, Any]:
        """Return process count and completed hashing batches."""
        details = self.status()
        return {
            "workers": details["workers"],
            "completed_batches": details["completed_batches"],
        }
