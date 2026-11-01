import time
from collections import deque

import psutil


class IoRateSampler:
    """Disk and network speed in bytes/second, from successive psutil counters."""

    def __init__(self, history: int = 60):
        self._prev = None
        self.history = deque(maxlen=history)  # last 60 samples = 60 s at 1 s polling

    def sample(self) -> dict | None:
        now = time.monotonic()
        disk = psutil.disk_io_counters()      # None if the OS exposes no disk counters
        net = psutil.net_io_counters()        # all adapters combined
        cur = (now, disk, net)

        prev, self._prev = self._prev, cur
        if prev is None:
            return None                       # first reading has no rate yet

        dt = now - prev[0]
        if dt <= 0:
            return None

        def rate(new: int, old: int) -> float:
            return max(0, new - old) / dt     # max() guards against counter reset

        point = {
            "ts": time.time(),
            "disk_read_bps": rate(disk.read_bytes, prev[1].read_bytes) if disk and prev[1] else None,
            "disk_write_bps": rate(disk.write_bytes, prev[1].write_bytes) if disk and prev[1] else None,
            "net_down_bps": rate(net.bytes_recv, prev[2].bytes_recv) if net and prev[2] else None,
            "net_up_bps": rate(net.bytes_sent, prev[2].bytes_sent) if net and prev[2] else None,
        }
        self.history.append(point)
        return point