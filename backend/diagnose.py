"""Command-line discovery of real OVERPINE sensor readings."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from backend.core.config import load_settings
from backend.monitoring.models import Sensor
from backend.monitoring.scheduler import MonitoringScheduler


def _missing_reasons(
    sensors: list[Sensor],
    status: dict[str, Any],
) -> list[str]:
    """Explain common unavailable readings using discovered provider status."""
    ids = {sensor.id for sensor in sensors}
    reasons: list[str] = []
    providers = status.get("providers", [])
    lhm = next(
        (
            provider
            for provider in providers
            if isinstance(provider, dict) and provider.get("name") == "lhm-web"
        ),
        {},
    )
    if "cpu.package.temperature" not in ids:
        if lhm.get("lhm_running") is False:
            reasons.append(
                "CPU package temperature is unavailable. To see CPU temperature "
                "and power: download LibreHardwareMonitor from its official GitHub "
                "releases, run it as Administrator, then enable Options -> Remote "
                "Web Server -> Run (port 8085)."
            )
        else:
            reasons.append(
                "No provider currently exposes a CPU package temperature sensor."
            )
    if not any(sensor.category == "fan" for sensor in sensors):
        reasons.append("Fan RPM is not exposed by the active sensor providers.")
    if not any(
        sensor.category == "storage" and sensor.unit == "C"
        for sensor in sensors
    ):
        reasons.append("Drive temperatures are not currently exposed.")
    return reasons


def _sensor_rows(sensors: list[Sensor]) -> list[dict[str, Any]]:
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
        }
        for sensor in sensors
    ]


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ("sensor id", "category", "source", "reliability", "state", "value")
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
                else "Unavailable"
            ),
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


def main(argv: list[str] | None = None) -> int:
    """Run providers briefly and print a real-data table or JSON document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    scheduler: MonitoringScheduler | None = None
    try:
        settings = load_settings()
        scheduler = MonitoringScheduler(
            intervals=settings.monitoring_intervals,
            history_size=settings.monitoring_history_size,
            simulation=False,
            lhm_enabled=settings.lhm_enabled,
            lhm_host=settings.lhm_host,
            lhm_port=settings.lhm_port,
            lhm_timeout_s=settings.lhm_timeout_s,
            smart_enabled=settings.smart_enabled,
        )
        scheduler.start()
        time.sleep(3.0)
        sensors = sorted(scheduler.registry.list(), key=lambda sensor: sensor.id)
        rows = _sensor_rows(sensors)
        status = scheduler.status()
        counts = status["sensor_counts"]
        missing = _missing_reasons(sensors, status)
        if args.json:
            print(
                json.dumps(
                    {
                        "sensors": rows,
                        "counts_by_state": counts,
                        "monitoring": status,
                        "missing": missing,
                    },
                    indent=2,
                )
            )
        else:
            _print_table(rows)
            print("\nSummary counts by state:")
            for state, count in counts.items():
                print(f"  {state}: {count}")
            print("\nWhat is missing and why:")
            for reason in missing or ["No common sensor gaps were detected."]:
                print(f"  - {reason}")
        return 0
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Sensor diagnosis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if scheduler is not None:
            scheduler.stop()


if __name__ == "__main__":
    raise SystemExit(main())
