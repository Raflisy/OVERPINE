"""Typed sensor metadata and status values."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SensorState(str, Enum):
    """Current availability state of a discovered sensor."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    PERMISSION_RESTRICTED = "PERMISSION_RESTRICTED"
    TEMPORARILY_DISCONNECTED = "TEMPORARILY_DISCONNECTED"
    ERROR = "ERROR"
    NOT_INITIALIZED = "NOT_INITIALIZED"


@dataclass(slots=True)
class Sensor:
    """Describe a discovered sensor and its latest real measurement."""

    id: str
    name: str
    category: str
    unit: str
    source: str
    reliability: str = "measured"
    state: SensorState = SensorState.NOT_INITIALIZED
    value: float | None = None
    last_update: float | None = None
    min: float | None = None
    max: float | None = None
    avg: float | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, str | float | None]:
        """Return JSON-compatible metadata without losing unavailable values."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "unit": self.unit,
            "source": self.source,
            "reliability": self.reliability,
            "state": self.state.value,
            "value": self.value,
            "last_update": self.last_update,
            "min": self.min,
            "max": self.max,
            "avg": self.avg,
            "error_message": self.error_message,
        }
