"""Explicitly opt-in sample sensors for UI and integration development."""
from __future__ import annotations

from backend.monitoring.models import Sensor
from backend.providers.base import SensorProvider


class SimulatedProvider(SensorProvider):
    """Expose clearly labelled values only when simulation is enabled."""

    name = "simulation"
    poll_categories = ("cpu",)

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self._values = {
            "sim.cpu.total": 37.0,
            "sim.cpu.package.temperature": 49.0,
            "sim.cpu.package.power": 12.5,
            "sim.fan.system.rpm": 1800.0,
            "sim.storage.drive.temperature": 34.0,
        }
        self._sensors = [
            Sensor(
                sensor_id,
                name,
                category,
                unit,
                "SIMULATED",
                reliability="calculated-estimate",
            )
            for sensor_id, name, category, unit in (
                ("sim.cpu.total", "Simulated CPU utilization", "cpu", "%"),
                (
                    "sim.cpu.package.temperature",
                    "Simulated CPU package temperature",
                    "cpu",
                    "C",
                ),
                ("sim.cpu.package.power", "Simulated CPU package power", "power", "W"),
                ("sim.fan.system.rpm", "Simulated system fan speed", "fan", "RPM"),
                (
                    "sim.storage.drive.temperature",
                    "Simulated drive temperature",
                    "storage",
                    "C",
                ),
            )
        ]

    def discover(self) -> list[Sensor]:
        """Return no sensors unless simulation mode was explicitly configured."""
        return list(self._sensors) if self.enabled else []

    def read(self) -> dict[str, float | None]:
        """Return visibly synthetic sample values only in simulation mode."""
        return dict(self._values) if self.enabled else {}

    def capabilities(self) -> dict[str, object]:
        """Keep simulated status explicit for clients and diagnostics."""
        return {
            "name": self.name,
            "state": "simulated" if self.enabled else "disabled",
            "source": "SIMULATED",
        }
