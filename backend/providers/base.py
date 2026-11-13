"""Contracts shared by sensor data providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.monitoring.models import Sensor, SensorState


class SensorProvider(ABC):
    """Discover and sample sensors from one local operating-system source."""

    name: str

    @abstractmethod
    def discover(self) -> list[Sensor]:
        """Return sensors currently exposed by this provider."""

    @abstractmethod
    def read(self) -> dict[str, float | None]:
        """Return latest values, leaving missing readings as None."""

    def capabilities(self) -> dict[str, Any]:
        """Describe provider availability and implementation notes."""
        return {"name": self.name, "state": "available"}

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        """Return explicit states for sensors whose availability is known."""
        return {}

    def diagnostics(self) -> dict[str, tuple[str | None, str | None]]:
        """Return the native API/counter path and error code for each sensor."""
        return {}

    def shutdown(self) -> None:
        """Release provider resources, if any."""
