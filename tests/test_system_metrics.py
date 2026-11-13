"""Python module for the test system metrics portion of the OVERPINE project."""

import unittest
from unittest.mock import patch

from backend.services import system_info


class SystemMetricsTests(unittest.TestCase):
    """Verify live I/O rates retain missing initial samples."""

    def test_io_throughput_uses_live_windows_counter_rates(self) -> None:
        samples = iter(
            [
                None,
                {
                    "disk_read_bps": 100.0,
                    "disk_write_bps": 100.0,
                    "net_down_bps": 200.0,
                    "net_up_bps": 100.0,
                },
            ]
        )
        with patch.object(
            system_info._io_sampler,
            "sample",
            side_effect=lambda _inventory: next(samples),
        ):
            self.assertEqual(
                system_info._live_io_rates(None),
                (None, None, None, None),
            )
            self.assertEqual(
                system_info._live_io_rates(None),
                (100.0, 100.0, 200.0, 100.0),
            )
