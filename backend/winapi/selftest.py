"""Print live Windows API values for manual comparison with system tools."""
from __future__ import annotations

import time
from typing import Any, Callable

from backend.winapi import (
    cpu_frequency,
    get_battery_status,
    get_memory_status,
    list_processes,
    logical_volumes,
    network_interfaces,
    network_rate_sample,
    processor_topology,
    system_cpu_percent,
    system_uptime_seconds,
)
from backend.winapi.bindings import (
    IF_OPER_STATUS_DOWN,
    IF_OPER_STATUS_DORMANT,
    IF_OPER_STATUS_LOWER_LAYER_DOWN,
    IF_OPER_STATUS_NOT_PRESENT,
    IF_OPER_STATUS_TESTING,
    IF_OPER_STATUS_UNKNOWN,
    IF_OPER_STATUS_UP,
)
from backend.winapi.cpu import (
    aggregate_cache_sizes,
    cache_type_name,
    per_core_cpu_percent,
)
from backend.winapi.disk import DiskIoSample, disk_io_sample
from backend.winapi.network import NetworkRates


def _read(label: str, reader: Callable[[], Any]) -> Any:
    """Run one self-test read and show an explicit error on failure."""
    try:
        return reader()
    except OSError as error:
        print(f"{label}: unavailable ({error})")
        return None


def _display(value: object, unit: str, state: str = "valid") -> str:
    """Render a measured value or its explicit no-data state."""
    if value is None:
        return f"unavailable ({state})"
    return f"{value} {unit}"


def _operational_status_name(status: int | None) -> str:
    """Render the documented IF_OPER_STATUS value as a readable word."""
    return {
        IF_OPER_STATUS_UP: "up",
        IF_OPER_STATUS_DOWN: "down",
        IF_OPER_STATUS_TESTING: "testing",
        IF_OPER_STATUS_UNKNOWN: "unknown",
        IF_OPER_STATUS_DORMANT: "dormant",
        IF_OPER_STATUS_NOT_PRESENT: "not present",
        IF_OPER_STATUS_LOWER_LAYER_DOWN: "lower layer down",
    }.get(status, "unknown")


def _battery_remaining_text(battery: Any) -> str:
    """Distinguish AC-powered systems from an unknown battery-life estimate."""
    if battery.power_plugged is True:
        return "not applicable (plugged in)"
    if battery.seconds_left is None:
        return "unavailable (Windows reports unknown)"
    return f"{battery.seconds_left} seconds"


def _print_cache_summary(caches: tuple[Any, ...]) -> None:
    """Print cache totals by level and type, followed by each level total."""
    cache_totals = aggregate_cache_sizes(caches)
    level_totals: dict[int, int] = {}
    for (level, cache_type), size_bytes in sorted(cache_totals.items()):
        level_totals[level] = level_totals.get(level, 0) + size_bytes
        type_name = cache_type_name(cache_type)
        label = f"L{level}"
        if level == 1 or type_name != "unified":
            label = f"{label} {type_name}"
        print(f"CPU cache {label}: {size_bytes} bytes")
    for level, size_bytes in sorted(level_totals.items()):
        print(f"CPU cache L{level} total: {size_bytes} bytes")


def _print_process_summary(processes: list[Any]) -> None:
    """Print process counts and the ten highest memory and CPU readings."""
    readable = sum(
        process.cpu_percent is not None or process.working_set_bytes is not None
        for process in processes
    )
    access_denied = sum(
        process.cpu_state == "access denied"
        or process.memory_state == "access denied"
        for process in processes
    )
    no_data_yet = sum(
        process.cpu_state == "no data yet" for process in processes
    )
    print(
        f"Processes: total {len(processes)}, readable {readable}, "
        f"access denied {access_denied}, no data yet {no_data_yet}"
    )
    print("Access denied for protected processes is expected without administrator rights.")
    print("Top 10 processes by memory:")
    for process in sorted(
        (item for item in processes if item.working_set_bytes is not None),
        key=lambda item: item.working_set_bytes,
        reverse=True,
    )[:10]:
        print(
            f"  {process.name} (PID {process.process_id}): "
            f"{process.working_set_bytes} bytes"
        )
    print("Top 10 processes by CPU:")
    for process in sorted(
        (item for item in processes if item.cpu_percent is not None),
        key=lambda item: item.cpu_percent,
        reverse=True,
    )[:10]:
        print(
            f"  {process.name} (PID {process.process_id}): "
            f"{process.cpu_percent:.1f}%"
        )


def _print_disk_sample(sample: DiskIoSample, phase: str) -> None:
    """Print disk counters and the units/explanations supplied by PDH."""
    for label, counter, explanation in (
        ("read", sample.read, sample.read_explanation),
        ("write", sample.write, sample.write_explanation),
        ("active time", sample.active_time, sample.active_time_explanation),
    ):
        unit = "%" if label == "active time" else "bytes/s"
        detail = f"; PDH explanation: {explanation}" if explanation else ""
        print(
            f"Disk {label} {phase}: "
            f"{_display(counter.value, unit, counter.state)} "
            f"[{counter.state}]{detail} | compare with Task Manager > Performance > Disk"
        )


def _print_network_sample(sample: NetworkRates, phase: str) -> None:
    """Print aggregate network rate values and their current status."""
    print(
        f"Network received {phase}: "
        f"{_display(sample.received_bytes_per_second, 'bytes/s', sample.state)} "
        f"[{sample.state}] | compare with Task Manager > Performance > Ethernet/Wi-Fi"
    )
    print(
        f"Network sent {phase}: "
        f"{_display(sample.sent_bytes_per_second, 'bytes/s', sample.state)} "
        f"[{sample.state}] | compare with Task Manager > Performance > Ethernet/Wi-Fi"
    )


def main() -> int:
    """Sample real Windows metrics twice and print units and comparison locations."""
    print("OVERPINE Windows API self-test (all readings are live; no simulation)")

    topology = _read("Processor topology", processor_topology)
    if topology is None:
        print("Physical cores: unavailable (not exposed)")
        print("Logical processors: unavailable (not exposed)")
        print("Processor caches: unavailable (not exposed)")
    else:
        print(
            f"Physical cores: {topology.physical_core_count} cores | "
            "compare with Task Manager > Performance > CPU"
        )
        print(
            f"Logical processors: {topology.logical_processor_count} processors | "
            "compare with Task Manager > Performance > CPU"
        )
        _print_cache_summary(topology.caches)

    frequency = _read("Processor frequency", cpu_frequency)
    if frequency is not None:
        print(
            "Windows-reported processor frequency: "
            f"{_display(frequency.current_mhz, 'MHz')} from "
            f"{frequency.current_source}; "
            f"{_display(frequency.maximum_mhz, 'MHz')} maximum "
            "compare with Task Manager > Performance > CPU"
        )

    memory = _read("Memory status", get_memory_status)
    if memory is not None:
        print(
            f"Physical memory: {memory.total_bytes} bytes total, "
            f"{memory.available_bytes} bytes available, "
            f"{memory.used_percent:.1f}% used | "
            "compare with Task Manager > Performance > Memory"
        )
        print(
            f"Page file: {memory.page_file_total_bytes} bytes total, "
            f"{memory.page_file_available_bytes} bytes available | "
            "compare with Task Manager > Performance > Memory"
        )
        print(
            "Commit: "
            f"{_display(memory.committed_bytes, 'bytes')} of "
            f"{_display(memory.commit_limit_bytes, 'bytes')}; "
            f"system cache {_display(memory.cache_bytes, 'bytes')} | "
            "compare with Task Manager > Performance > Memory"
        )

    volumes = _read("Logical volumes", logical_volumes)
    if volumes is None:
        print("Disk volumes: unavailable (not exposed)")
    else:
        for volume in volumes:
            print(
                f"Volume {volume.path} ({volume.drive_type_name}): "
                f"total {_display(volume.total_bytes, 'bytes', volume.state)}, "
                f"free {_display(volume.free_bytes, 'bytes', volume.state)}, "
                f"available to caller "
                f"{_display(volume.available_bytes, 'bytes', volume.state)}, "
                f"file system {volume.filesystem or 'unavailable'} "
                f"[{volume.state}] | compare with File Explorer > This PC"
            )

    interfaces = _read("Network interfaces", network_interfaces)
    if interfaces is None:
        print("Network interfaces: unavailable (not exposed)")
    else:
        for interface in interfaces:
            print(
                f"Network interface {interface.name}: status "
                f"{_operational_status_name(interface.operational_status)}, media connected "
                f"{interface.media_connected}, link "
                f"transmit {_display(interface.transmit_link_speed_bps, 'bits/s')}, "
                f"receive {_display(interface.receive_link_speed_bps, 'bits/s')}, "
                f"received {_display(interface.received_octets, 'bytes')}, "
                f"sent {_display(interface.sent_octets, 'bytes')}, "
                f"IPs {', '.join(interface.addresses) or 'unavailable'} | "
                "compare with Task Manager > Performance > Ethernet/Wi-Fi"
            )

    cpu_first = _read("CPU usage sample 1", system_cpu_percent)
    cores_first = _read("Per-core CPU sample 1", per_core_cpu_percent)
    disk_first = _read("Disk rate sample 1", disk_io_sample)
    network_first = _read("Network rate sample 1", network_rate_sample)
    _read("Process sample 1", list_processes)
    time.sleep(1.0)
    cpu_second = _read("CPU usage sample 2", system_cpu_percent)
    cores_second = _read("Per-core CPU sample 2", per_core_cpu_percent)
    disk_second = _read("Disk rate sample 2", disk_io_sample)
    network_second = _read("Network rate sample 2", network_rate_sample)
    process_second = _read("Process sample 2", list_processes)

    print(
        f"Total CPU sample 1: {_display(cpu_first, '%', 'no data yet')} | "
        "compare with Task Manager > Performance > CPU"
    )
    print(
        f"Total CPU sample 2: {_display(cpu_second, '%', 'no data yet')} | "
        "compare with Task Manager > Performance > CPU"
    )
    for index, value in enumerate(cores_first or []):
        print(
            f"Logical processor {index + 1} sample 1: "
            f"{_display(value, '%', 'no data yet')} | "
            "compare with Task Manager > Performance > CPU > Logical processors"
        )
    for index, value in enumerate(cores_second or []):
        print(
            f"Logical processor {index + 1} sample 2: "
            f"{_display(value, '%', 'no data yet')} | "
            "compare with Task Manager > Performance > CPU > Logical processors"
        )
    if cores_first is None and cores_second is None:
        print("Per-core CPU: unavailable (no data yet or not exposed)")

    if disk_first is not None:
        _print_disk_sample(disk_first, "sample 1")
    if disk_second is not None:
        _print_disk_sample(disk_second, "sample 2")
    if network_first is not None:
        _print_network_sample(network_first, "sample 1")
    if network_second is not None:
        _print_network_sample(network_second, "sample 2")

    if process_second is None:
        print("Process metrics: unavailable (not exposed)")
    else:
        _print_process_summary(process_second)

    uptime = _read("Windows uptime", system_uptime_seconds)
    print(
        f"Windows uptime: {_display(uptime, 'seconds')} | "
        "compare with Task Manager > Performance > CPU > Up time"
    )
    battery = _read("Battery and AC status", get_battery_status)
    if battery is None:
        print("Battery/AC status: unavailable (not exposed)")
    else:
        print(
            f"Battery charge: {_display(battery.percent, '%')}; "
            f"AC connected: {_display(battery.power_plugged, 'bool')}; "
            f"remaining: {_battery_remaining_text(battery)} | "
            "compare with Windows Settings > System > Power & battery"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
