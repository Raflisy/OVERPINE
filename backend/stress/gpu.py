"""Honest GPU stress-test availability reporting."""
from __future__ import annotations

from threading import Event
from typing import Any

from backend.stress.base import StressWorker


class GpuStressWorker(StressWorker):
    """Report GPU stress testing as unsupported instead of fabricating load."""

    def start(self, intensity: int, stop_event: Event) -> None:
        """Expose the unsupported state; this worker never generates fake load."""
        return

    def stop(self) -> None:
        """There is no GPU workload to stop."""
        return

    def status(self) -> dict[str, Any]:
        """Explain why no GPU stress workload is available."""
        return {
            "state": "unsupported",
            "message": "GPU stress testing is unsupported; no reliable local Python API is available.",
        }

    def result_summary(self) -> dict[str, Any]:
        """Return the unsupported reason for the run summary."""
        return {"state": "unsupported", "message": self.status()["message"]}
