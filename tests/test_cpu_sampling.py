"""Tests for sharing the system CPU measurement between callers."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.winapi import cpu


class CpuSampleSharingTests(unittest.TestCase):
    """The CPU baseline is shared, so a quick second caller must not get nothing."""

    def _patches(self, times, monotonic_values):
        return (
            patch.object(cpu, "load_library", return_value=object()),
            patch.object(cpu, "_get_system_times", side_effect=times),
            patch.object(cpu, "_previous_system_times", None),
            patch.object(cpu, "_last_cpu_percent", None),
            patch.object(cpu.time, "monotonic", side_effect=monotonic_values),
        )

    def test_second_caller_within_one_second_gets_the_last_real_value(self) -> None:
        times = [(10, 100, 100), (20, 150, 120), (25, 160, 125)]
        a, b, c, d, e = self._patches(times, (0.0, 1.0, 1.4))
        with a, b, c, d, e:
            self.assertIsNone(cpu.system_cpu_percent())
            first = cpu.system_cpu_percent()
            second = cpu.system_cpu_percent()
        self.assertAlmostEqual(first, 100.0 * (70 - 10) / 70)
        self.assertEqual(second, first)

    def test_a_measurement_is_not_reused_after_five_seconds(self) -> None:
        # The third sample is invalid (idle time went backwards), so the baseline
        # moves to 8.0 s without a new value; the old value is then too old.
        times = [(10, 100, 100), (20, 150, 120), (5, 300, 300), (6, 301, 301)]
        a, b, c, d, e = self._patches(times, (0.0, 1.0, 8.0, 8.5))
        with a, b, c, d, e:
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertIsNotNone(cpu.system_cpu_percent())
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertIsNone(cpu.system_cpu_percent())

    def test_no_value_is_invented_before_the_first_real_measurement(self) -> None:
        times = [(10, 100, 100), (11, 101, 101)]
        a, b, c, d, e = self._patches(times, (0.0, 0.4))
        with a, b, c, d, e:
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertIsNone(cpu.system_cpu_percent())


if __name__ == "__main__":
    unittest.main()
