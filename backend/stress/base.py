"""Common interface for stress-test workers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Event
from typing import Any


class StressWorker(ABC):
    """A single resource load generator with observable status and results."""

    @abstractmethod
    def start(self, intensity: int, stop_event: Event) -> None:
        """Start generating load until the shared stop event is set."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the worker and release resources it owns."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return the worker's current state and live details."""

    @abstractmethod
    def result_summary(self) -> dict[str, Any]:
        """Return the worker's final workload summary."""
