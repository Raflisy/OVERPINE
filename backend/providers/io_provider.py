"""Disk and network throughput sensors backed by IoRateSampler."""
from __future__ import annotations

from typing import ClassVar

from backend.monitoring.models import Sensor
from backend.providers.base import SensorProvider
from backend.providers.io_rates import IoRateSampler


class IoProvider(SensorProvider):
    """Expose sampled system-wide disk and network transfer rates."""

    name = "io-rates"
    poll_categories = ("storage", "network")
    _SENSORS: ClassVar[dict[str, tuple[str, str]]] = {
        "storage.disk.read": ("Disk read throughput", "storage"),
        "storage.disk.write": ("Disk write throughput", "storage"),
        "network.receive": ("Network receive throughput", "network"),
        "network.send": ("Network send throughput", "network"),
    }

    def __init__(self) -> None:
        self._sampler = IoRateSampler()

    def discover(self) -> list[Sensor]:
        """Register disk/network byte-rate counters as MB/s."""
        return [
            Sensor(sensor_id, name, category, "MB/s", self.name)
            for sensor_id, (name, category) in self._SENSORS.items()
        ]

    def read(self) -> dict[str, float | None]:
        """Return available rates; the first sample remains unavailable."""
        sample = self._sampler.sample()
        if sample is None:
            return {sensor_id: None for sensor_id in self._SENSORS}
        keys = {
            "storage.disk.read": "disk_read_bps",
            "storage.disk.write": "disk_write_bps",
            "network.receive": "net_down_bps",
            "network.send": "net_up_bps",
        }
        return {
            sensor_id: (
                float(sample[key]) / (1024**2)
                if sample.get(key) is not None
                else None
            )
            for sensor_id, key in keys.items()
        }
