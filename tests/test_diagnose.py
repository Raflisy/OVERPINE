"""Tests for sensor-diagnosis sampling, counts, and failure details."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from backend.diagnose import (
    _not_sampled_yet,
    _counter_group_counts,
    _print_unavailable_details,
    _sensor_rows,
    _summary_counts,
    _wait_for_rate_sensors,
)
from backend.monitoring.models import Sensor, SensorState


class _StaticRegistry:
    """Provide ready rate sensors to the diagnosis wait helper."""

    def list(self) -> list[Sensor]:
        return [
            Sensor(sensor_id, sensor_id, category, "%", "test", state=SensorState.AVAILABLE)
            for sensor_id, category in (
                ("cpu.total", "cpu"),
                ("network.receive", "network"),
                ("network.send", "network"),
                ("storage.disk.read", "storage"),
                ("storage.disk.write", "storage"),
            )
        ]


class _StaticScheduler:
    """Expose the static sensor registry used by diagnosis wait tests."""

    registry = _StaticRegistry()


class DiagnoseTests(unittest.TestCase):
    """Verify dynamic counters and unavailable reasons stay explicit."""

    def test_per_engine_no_data_rows_are_separate_from_summary_counts(self) -> None:
        sensors = [
            Sensor(
                f"gpu.gpu-utilization.pid-{index}-engine",
                "GPU engine",
                "gpu",
                "%",
                "pdh",
                state=SensorState.NOT_INITIALIZED,
            )
            for index in range(498)
        ]
        sensors.append(
            Sensor(
                "cpu.total",
                "CPU utilization",
                "cpu",
                "%",
                "windows",
                state=SensorState.NOT_INITIALIZED,
            )
        )

        counts = _summary_counts(sensors)

        self.assertEqual(counts[SensorState.NOT_INITIALIZED.value], 1)
        self.assertEqual(
            _not_sampled_yet(sensors),
            {"gpu.gpu-utilization.*": 498},
        )
        self.assertEqual(
            _counter_group_counts(sensors),
            {
                "gpu.gpu-utilization.*": {
                    "total": 498,
                    "not_sampled_yet": 498,
                }
            },
        )

    def test_diagnosis_waits_at_least_three_seconds(self) -> None:
        elapsed = [0.0]

        def sleep(seconds: float) -> None:
            elapsed[0] += seconds

        with (
            patch("backend.diagnose.time.monotonic", side_effect=lambda: elapsed[0]),
            patch("backend.diagnose.time.sleep", side_effect=sleep),
        ):
            _wait_for_rate_sensors(_StaticScheduler())

        self.assertGreaterEqual(elapsed[0], 3.0)

    def test_unavailable_details_include_api_result_and_plain_reason(self) -> None:
        sensor = Sensor(
            "battery.voltage",
            "Battery voltage",
            "battery",
            "V",
            "windows-counter",
            state=SensorState.UNAVAILABLE,
            error_message="CIM query returned no Voltage value",
            api_path="Get-CimInstance -Namespace root\\wmi -ClassName BatteryStatus",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            _print_unavailable_details(_sensor_rows([sensor]))

        rendered = output.getvalue()
        self.assertIn("battery.voltage", rendered)
        self.assertIn("Get-CimInstance -Namespace root\\wmi -ClassName BatteryStatus", rendered)
        self.assertIn("no Windows error code reported", rendered)
        self.assertIn("CIM query returned no Voltage value", rendered)


if __name__ == "__main__":
    unittest.main()
