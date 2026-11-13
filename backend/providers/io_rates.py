"""Python module for the io rates portion of the OVERPINE project."""

import time
from collections import deque
from typing import Any

from backend.monitoring.models import SensorState
from backend.winapi.disk import disk_io_sample
from backend.winapi.network import network_rate_sample


class IoRateSampler:
    """Disk and network speed in bytes/second from Windows PDH counters."""

    def __init__(self, history: int = 60):
        self.history = deque(maxlen=history)  # last 60 samples = 60 s at 1 s polling
        self._sensor_states: dict[
            str, tuple[SensorState, str | None]
        ] = {}

    @staticmethod
    def _pdh_state(
        state: str,
        sensor_name: str,
    ) -> tuple[SensorState, str | None] | None:
        """Translate the documented PDH sample result into a sensor state."""
        if state == "valid":
            return None
        if state == "no data yet":
            return SensorState.NOT_INITIALIZED, "no data yet; waiting for two samples"
        if state == "object or counter does not exist":
            return SensorState.UNSUPPORTED, f"{sensor_name} counter is not exposed"
        return SensorState.ERROR, f"{sensor_name} counter state: {state}"

    def sample(
        self,
        inventory: dict[str, Any] | None = None,
    ) -> dict[str, float | None]:
        """Return rates and retain explicit first-sample or missing states."""
        disk = disk_io_sample()
        network = network_rate_sample(inventory)
        states: dict[str, tuple[SensorState, str | None]] = {}
        for sensor_id, sample in (
            ("storage.disk.read", disk.read),
            ("storage.disk.write", disk.write),
        ):
            state = self._pdh_state(sample.state, sensor_id)
            if state is not None:
                states[sensor_id] = state

        if network.state == "no data yet":
            network_state = (
                SensorState.NOT_INITIALIZED,
                "no data yet; waiting for two samples",
            )
        elif network.state == "valid":
            network_state = None
        elif network.state == "Windows IP Helper APIs are unavailable":
            network_state = (
                SensorState.UNSUPPORTED,
                network.state,
            )
        elif network.state.startswith("error:"):
            network_state = (SensorState.ERROR, network.state)
        else:
            network_state = (SensorState.UNAVAILABLE, network.state)
        for sensor_id in ("network.receive", "network.send"):
            if network_state is not None:
                states[sensor_id] = network_state
        self._sensor_states = states

        point = {
            "ts": time.time(),
            "disk_read_bps": disk.read.value,
            "disk_write_bps": disk.write.value,
            "net_down_bps": network.received_bytes_per_second,
            "net_up_bps": network.sent_bytes_per_second,
        }
        self.history.append(point)
        return point

    def sensor_states(
        self,
    ) -> dict[str, tuple[SensorState, str | None]]:
        """Return reasons for sensors whose first or current reading is missing."""
        return dict(self._sensor_states)