"""Command-line discovery of real OVERPINE sensor readings."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from backend.core.config import load_settings
from backend.monitoring.models import Sensor, SensorState
from backend.monitoring.scheduler import MonitoringScheduler

_RATE_SENSOR_PREFIXES = ("cpu.core.",)
_RATE_SENSOR_IDS = {
    "cpu.total",
    "network.receive",
    "network.send",
    "storage.disk.read",
    "storage.disk.write",
}
_HIGH_CARDINALITY_COUNTER_PREFIXES = ("gpu.gpu-utilization.",)
_MIN_DIAGNOSIS_RUNTIME_S = 3.0


def _missing_reasons(
    sensors: list[Sensor],
    status: dict[str, Any],
) -> list[str]:
    """Explain common unavailable readings using discovered provider status."""
    ids = {sensor.id for sensor in sensors}
    reasons: list[str] = []
    if "cpu.package.temperature" not in ids and not any(
        sensor.category == "thermal" and sensor.state == SensorState.AVAILABLE
        for sensor in sensors
    ):
        reasons.append(
            "No available thermal-zone or CPU-package temperature sensor is exposed."
        )
    if not any(
        sensor.category == "fan" and sensor.state == SensorState.AVAILABLE
        for sensor in sensors
    ):
        reasons.append("Fan RPM is unsupported by the active sensor providers.")
    if not any(
        sensor.category == "storage"
        and sensor.unit == "C"
        and sensor.state == SensorState.AVAILABLE
        for sensor in sensors
    ):
        reasons.append("Drive temperatures are not currently exposed.")
    return reasons


def _sensor_rows(
    sensors: list[Sensor],
) -> list[dict[str, Any]]:
    return [
        {
            "id": sensor.id,
            "category": sensor.category,
            "source": sensor.source,
            "reliability": sensor.reliability,
            "state": sensor.state.value,
            "value": sensor.value,
            "unit": sensor.unit,
            "message": sensor.error_message,
            "note": sensor.note,
            "api_path": sensor.api_path or sensor.source,
            "error_code": sensor.error_code,
        }
        for sensor in sensors
    ]


def _summary_counts(sensors: list[Sensor]) -> dict[str, int]:
    """Count ordinary sensors without letting per-engine counters dominate."""
    counts = {state.value: 0 for state in SensorState}
    for sensor in sensors:
        if sensor.id.startswith(_HIGH_CARDINALITY_COUNTER_PREFIXES):
            continue
        counts[sensor.state.value] += 1
    return counts


def _not_sampled_yet(sensors: list[Sensor]) -> dict[str, int]:
    """Count per-process/per-counter rows still awaiting a usable first sample."""
    counts: dict[str, int] = {}
    for sensor in sensors:
        if (
            sensor.state == SensorState.NOT_INITIALIZED
            and sensor.id.startswith(_HIGH_CARDINALITY_COUNTER_PREFIXES)
        ):
            prefix = next(
                item.rstrip("*")
                for item in _HIGH_CARDINALITY_COUNTER_PREFIXES
                if sensor.id.startswith(item)
            )
            group = prefix + "*"
            counts[group] = counts.get(group, 0) + 1
    return counts


def _counter_group_counts(sensors: list[Sensor]) -> dict[str, dict[str, int]]:
    """Count all excluded per-process/per-counter rows and their pending samples."""
    counts: dict[str, dict[str, int]] = {}
    for sensor in sensors:
        prefix = next(
            (
                item.rstrip("*")
                for item in _HIGH_CARDINALITY_COUNTER_PREFIXES
                if sensor.id.startswith(item)
            ),
            None,
        )
        if prefix is None:
            continue
        group = prefix + "*"
        group_counts = counts.setdefault(
            group,
            {"total": 0, "not_sampled_yet": 0},
        )
        group_counts["total"] += 1
        if sensor.state == SensorState.NOT_INITIALIZED:
            group_counts["not_sampled_yet"] += 1
    return counts


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = (
        "sensor id",
        "category",
        "source",
        "reliability",
        "state",
        "value",
        "reason",
    )
    rendered = [
        (
            str(row["id"]),
            str(row["category"]),
            str(row["source"]),
            str(row["reliability"]),
            str(row["state"]),
            (
                f"{row['value']} {row['unit']}"
                if row["value"] is not None
                else (
                    "no data yet"
                    if row["state"] == SensorState.NOT_INITIALIZED.value
                    or "no data yet" in str(row["message"]).casefold()
                    else "Unavailable"
                )
            ),
            str(row["message"] or row["note"] or ""),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rendered))
        for index in range(len(headers))
    ]
    print(" | ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(" | ".join(row[index].ljust(widths[index]) for index in range(len(headers))))


def _print_verbose_diagnostics(rows: list[dict[str, Any]]) -> None:
    """Print the API/counter path and recorded error code for unavailable sensors."""
    failures = [
        row for row in rows if row["state"] != SensorState.AVAILABLE.value
    ]
    if not failures:
        print("\nVerbose sensor diagnostics: no failed sensors.")
        return
    print("\nVerbose sensor diagnostics:")
    for row in failures:
        print(
            f"  {row['id']}: API/counter={row.get('api_path') or row['source']}; "
            f"Windows error code={row.get('error_code') or 'not reported'}; "
            f"reason={row['message'] or row['note'] or 'not available'}"
        )


def _print_unavailable_details(rows: list[dict[str, Any]]) -> None:
    """Print the source, native error/result, and reason for unavailable rows."""
    unavailable = [
        row for row in rows if row["state"] == SensorState.UNAVAILABLE.value
    ]
    if not unavailable:
        return
    print("\nUnavailable sensor details:")
    for row in unavailable:
        error_code = row.get("error_code")
        outcome = (
            f"Windows error code {error_code}"
            if error_code
            else "no Windows error code reported; see result/reason"
        )
        print(
            f"  {row['id']}: API/counter={row.get('api_path') or row['source']}; "
            f"{outcome}; result/reason="
            f"{row['message'] or row['note'] or 'no usable value was returned'}"
        )


def _wait_for_rate_sensors(
    scheduler: MonitoringScheduler,
    timeout_s: float = 10.0,
) -> None:
    """Wait for rate values and allow providers at least three seconds to sample."""
    started_at = time.monotonic()
    deadline = started_at + timeout_s
    while True:
        sensors = scheduler.registry.list()
        rate_sensors = [
            sensor
            for sensor in sensors
            if sensor.id in _RATE_SENSOR_IDS
            or sensor.id.startswith(_RATE_SENSOR_PREFIXES)
        ]
        found_ids = {sensor.id for sensor in rate_sensors}
        required_ids = {
            "cpu.total",
            "network.receive",
            "network.send",
            "storage.disk.read",
            "storage.disk.write",
        }
        ready = (
            required_ids.issubset(found_ids)
            and all(
                sensor.state != SensorState.NOT_INITIALIZED
                for sensor in rate_sensors
            )
        )
        if (
            ready
            and time.monotonic() - started_at >= _MIN_DIAGNOSIS_RUNTIME_S
        ):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    """Run providers briefly and print a real-data table or JSON document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include native API/counter paths and Windows error codes",
    )
    args = parser.parse_args(argv)
    scheduler: MonitoringScheduler | None = None
    try:
        settings = load_settings()
        scheduler = MonitoringScheduler(
            intervals=settings.monitoring_intervals,
            history_size=settings.monitoring_history_size,
        )
        scheduler.start()
        _wait_for_rate_sensors(scheduler)
        sensors = sorted(scheduler.registry.list(), key=lambda sensor: sensor.id)
        rows = _sensor_rows(sensors)
        status = scheduler.status()
        counts = _summary_counts(sensors)
        not_sampled_yet = _not_sampled_yet(sensors)
        counter_groups = _counter_group_counts(sensors)
        missing = _missing_reasons(sensors, status)
        if args.json:
            print(
                json.dumps(
                    {
                        "sensors": rows,
                        "counts_by_state": counts,
                        "not_sampled_yet": not_sampled_yet,
                        "excluded_counter_groups": counter_groups,
                        "monitoring": status,
                        "missing": missing,
                    },
                    indent=2,
                )
            )
        else:
            _print_table(rows)
            _print_unavailable_details(rows)
            if args.verbose:
                _print_verbose_diagnostics(rows)
            print("\nSummary counts by state:")
            for state, count in counts.items():
                print(f"  {state}: {count}")
            print("\nNot sampled yet (excluded per-process/per-counter rows):")
            for group, values in counter_groups.items():
                print(
                    f"  {group}: {values['not_sampled_yet']} not sampled yet "
                    f"of {values['total']} rows"
                )
            if not counter_groups:
                print("  None")
            print("\nWhat is missing and why:")
            for reason in missing or ["No common sensor gaps were detected."]:
                print(f"  - {reason}")
            for provider in status["providers"]:
                if provider.get("name") == "windows":
                    frequency_source = provider.get("frequency_source")
                    if frequency_source:
                        print(f"\nCPU frequency source: {frequency_source}")
                    break
        return 0
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Sensor diagnosis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if scheduler is not None:
            scheduler.stop()


if __name__ == "__main__":
    raise SystemExit(main())
