"""Best-effort local computer identification and resource metrics."""
from __future__ import annotations

import os
import platform
from math import isfinite
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, TypedDict, cast

import psutil

from backend.services.hardware_inventory import (
    HardwareRow,
    get_hardware_inventory,
    get_hardware_sections,
    get_live_windows_sensors,
)

psutil.cpu_percent(interval=None)

_io_lock = Lock()
_last_io_sample: tuple[float, int, int, int, int] | None = None


class LiveBattery(TypedDict):
    percent: float | None
    power_plugged: bool | None
    seconds_left: int | None
    current_capacity_mwh: int | None
    voltage_v: float | None
    charge_rate_mw: int | None
    discharge_rate_mw: int | None
    charging: bool | None
    discharging: bool | None


def _windows_registry_value(key_path: str, value_name: str) -> str | None:
    if platform.system() != "Windows":
        return None

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
    except OSError:
        return None

    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.casefold() in {
        "to be filled by o.e.m.",
        "system product name",
        "default string",
    }:
        return None
    return value


def _processor_name() -> str:
    name = _windows_registry_value(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        "ProcessorNameString",
    )
    return name or platform.processor() or "Not detected"


def _computer_model() -> str:
    bios_key = r"HARDWARE\DESCRIPTION\System\BIOS"
    manufacturer = _windows_registry_value(bios_key, "SystemManufacturer")
    family = _windows_registry_value(bios_key, "SystemFamily")
    version = _windows_registry_value(bios_key, "SystemVersion")
    model = _windows_registry_value(bios_key, "SystemProductName")
    marketing_name = family or version
    if marketing_name:
        return " ".join(part for part in (manufacturer, marketing_name) if part)
    return " ".join(part for part in (manufacturer, model) if part) or "Not detected"


def _graphics_adapters() -> list[str]:
    key_path = (
        r"SYSTEM\CurrentControlSet\Control\Class"
        r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
    )
    if platform.system() != "Windows":
        return []

    import winreg

    adapters: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, subkey_name) as adapter_key:
                        adapter_name, _ = winreg.QueryValueEx(
                            adapter_key, "DriverDesc"
                        )
                except OSError:
                    continue
                if isinstance(adapter_name, str) and adapter_name.strip():
                    adapters.append(adapter_name.strip())
    except OSError:
        return []

    return list(dict.fromkeys(adapters))


def _system_drive() -> dict[str, int | float | str] | None:
    drive = os.environ.get("SystemDrive") or Path.home().anchor
    if not drive:
        return None
    try:
        usage = psutil.disk_usage(drive)
    except OSError:
        return None

    gib = 1024**3
    return {
        "mountpoint": drive,
        "total_gib": round(usage.total / gib, 1),
        "free_gib": round(usage.free / gib, 1),
        "used_percent": usage.percent,
    }


def _counter_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _interface_counters(inventory: dict[str, Any] | None) -> tuple[int, int]:
    counters = psutil.net_io_counters(pernic=True) or {}
    adapter_values = inventory.get("network") if inventory else None
    adapters = (
        [
            cast(dict[str, Any], adapter)
            for adapter in cast(list[Any], adapter_values)
            if isinstance(adapter, dict)
        ]
        if isinstance(adapter_values, list)
        else []
    )
    physical_names = {
        str(adapter.get("Name", "")).casefold()
        for adapter in adapters
    }
    stats = psutil.net_if_stats()
    active_names = {
        name.casefold()
        for name, interface_stats in stats.items()
        if interface_stats.isup and name.casefold() in physical_names
    }
    if physical_names and not active_names:
        active_names = {
            str(adapter.get("Name", "")).casefold()
            for adapter in adapters
            if str(adapter.get("Status", "")).casefold() == "up"
        }
        if not active_names:
            return 0, 0
    elif not physical_names:
        active_names = {
            name.casefold()
            for name, interface_stats in stats.items()
            if interface_stats.isup and name.casefold() != "loopback"
        }
    selected = [
        counter
        for name, counter in counters.items()
        if name.casefold() in active_names
    ]
    if not selected:
        aggregate = psutil.net_io_counters()
        return aggregate.bytes_recv, aggregate.bytes_sent
    return (
        sum(counter.bytes_recv for counter in selected),
        sum(counter.bytes_sent for counter in selected),
    )


def _live_io_rates(
    inventory: dict[str, Any] | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    global _last_io_sample
    disk = psutil.disk_io_counters()
    network_received, network_sent = _interface_counters(inventory)
    if disk is None:
        return None, None, None, None

    now = monotonic()
    current = (
        now,
        disk.read_bytes,
        disk.write_bytes,
        network_received,
        network_sent,
    )
    with _io_lock:
        previous = _last_io_sample
        _last_io_sample = current

    if previous is None:
        return None, None, None, None
    elapsed = now - previous[0]
    if elapsed <= 0:
        return None, None, None, None

    def rate(current_value: int, previous_value: int) -> float | None:
        difference = current_value - previous_value
        return difference / elapsed if difference >= 0 else None

    return (
        rate(disk.read_bytes, previous[1]),
        rate(disk.write_bytes, previous[2]),
        rate(network_received, previous[3]),
        rate(network_sent, previous[4]),
    )


def _valid_percentage(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    percentage = float(value)
    return min(100.0, max(0.0, percentage)) if isfinite(percentage) else None


def _format_rate(value: float | None) -> str:
    if value is None:
        return "Not exposed by Windows"
    if value >= 1024**2:
        return f"{value / (1024**2):.2f} MiB/s"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB/s"
    return f"{value:.0f} B/s"


def get_system_info() -> dict[str, object]:
    """Return detected device specifications and current system-wide usage."""
    memory = psutil.virtual_memory()
    physical_cores = psutil.cpu_count(logical=False)
    logical_cores = psutil.cpu_count(logical=True)
    battery = psutil.sensors_battery()
    bios_key = r"HARDWARE\DESCRIPTION\System\BIOS"
    operating_system = f"{platform.system()} {platform.release()}"
    architecture = platform.machine() or "Not detected"
    memory_total_gib = round(memory.total / (1024**3), 1)
    inventory = get_hardware_inventory()
    hardware_sections, hardware_inventory_status = get_hardware_sections(
        memory_total_gib,
        operating_system,
        platform.version(),
        architecture,
    )
    cpu_frequency = psutil.cpu_freq()
    cpu_frequency_mhz = (
        round(cpu_frequency.current, 1)
        if isfinite(cpu_frequency.current)
        else None
    )
    cpu_usage_percent = psutil.cpu_percent(interval=None)
    disk_read_rate, disk_write_rate, network_receive_rate, network_send_rate = (
        _live_io_rates(inventory)
    )
    windows_sensors = get_live_windows_sensors()
    sensor_sampled_at = windows_sensors.get("sampled_at")
    sensor_sampled = isinstance(sensor_sampled_at, str)
    gpu_usage_percent = _valid_percentage(windows_sensors.get("gpuUsagePercent"))
    battery_sensor_value = windows_sensors.get("battery")
    battery_sensors: dict[str, Any] = (
        cast(dict[str, Any], battery_sensor_value)
        if isinstance(battery_sensor_value, dict)
        else {}
    )
    battery_percent = _valid_percentage(battery.percent) if battery is not None else None
    battery_powered: bool | None = (
        battery.power_plugged if battery is not None else None
    )
    if isinstance(battery_sensors.get("PowerOnline"), bool):
        battery_powered = battery_sensors["PowerOnline"]
    battery_charge_rate = _counter_value(battery_sensors.get("ChargeRate"))
    battery_discharge_rate = _counter_value(battery_sensors.get("DischargeRate"))
    battery_voltage = _counter_value(battery_sensors.get("Voltage"))
    live_battery: LiveBattery | None = (
        {
            "percent": battery_percent,
            "power_plugged": battery_powered,
            "seconds_left": (
                battery.secsleft
                if battery is not None and battery.secsleft >= 0
                else None
            ),
            "current_capacity_mwh": _counter_value(
                battery_sensors.get("RemainingCapacity")
            ),
            "voltage_v": (
                round(battery_voltage / 1000, 3)
                if battery_voltage is not None
                else None
            ),
            "charge_rate_mw": battery_charge_rate,
            "discharge_rate_mw": battery_discharge_rate,
            "charging": (
                battery_sensors["Charging"]
                if isinstance(battery_sensors.get("Charging"), bool)
                else None
            ),
            "discharging": (
                battery_sensors["Discharging"]
                if isinstance(battery_sensors.get("Discharging"), bool)
                else None
            ),
        }
        if battery is not None or battery_sensors
        else None
    )
    memory_used_percent = memory.percent
    if hardware_sections:
        live_rows: list[HardwareRow] = [
            {
                "label": "CPU utilization",
                "value": f"{cpu_usage_percent:.1f}%",
            },
            {
                "label": "Current averaged CPU clock",
                "value": (
                    f"{cpu_frequency_mhz:.1f} MHz"
                    if cpu_frequency_mhz is not None
                    else "Not exposed by Windows"
                ),
            },
            {"label": "Memory in use", "value": f"{memory_used_percent:.1f}%"},
            {
                "label": "Disk read / write",
                "value": f"{_format_rate(disk_read_rate)} / {_format_rate(disk_write_rate)}",
            },
            {
                "label": "Network receive / send",
                "value": f"{_format_rate(network_receive_rate)} / {_format_rate(network_send_rate)}",
            },
            {
                "label": "GPU 3D engine utilization",
                "value": (
                    f"{gpu_usage_percent:.1f}%"
                    if gpu_usage_percent is not None
                    else "Not exposed by Windows"
                    if sensor_sampled
                    else "Waiting for first Windows sensor sample"
                ),
            },
        ]
        if live_battery is not None:
            live_rows.extend(
                [
                    {
                        "label": "Current battery charge",
                        "value": (
                            f"{live_battery['current_capacity_mwh']} mWh"
                            if live_battery["current_capacity_mwh"] is not None
                            else "Not exposed by Windows"
                            if sensor_sampled
                            else "Waiting for first Windows sensor sample"
                        ),
                    },
                    {
                        "label": "Battery voltage",
                        "value": (
                            f"{live_battery['voltage_v']:.3f} V"
                            if live_battery["voltage_v"] is not None
                            else "Not exposed by Windows"
                            if sensor_sampled
                            else "Waiting for first Windows sensor sample"
                        ),
                    },
                    {
                        "label": "Battery charge / discharge rate",
                        "value": (
                            f"{live_battery['charge_rate_mw']} mW charging"
                            if live_battery["charge_rate_mw"] is not None
                            else f"{live_battery['discharge_rate_mw']} mW discharging"
                            if live_battery["discharge_rate_mw"] is not None
                            else "Not exposed by Windows"
                            if sensor_sampled
                            else "Waiting for first Windows sensor sample"
                        ),
                    },
                ]
            )
        hardware_sections.append({"title": "Live readings", "rows": live_rows})

    return {
        "device_name": platform.node() or "Not detected",
        "device_model": _computer_model(),
        "manufacturer": _windows_registry_value(bios_key, "SystemManufacturer")
        or "Not detected",
        "product_code": _windows_registry_value(bios_key, "SystemProductName")
        or "Not detected",
        "bios_version": _windows_registry_value(bios_key, "BIOSVersion")
        or "Not detected",
        "operating_system": operating_system,
        "os_version": platform.version(),
        "architecture": architecture,
        "processor": _processor_name(),
        "physical_cores": physical_cores if physical_cores is not None else "Not detected",
        "logical_processors": logical_cores if logical_cores is not None else "Not detected",
        "graphics_adapters": _graphics_adapters(),
        "cpu_usage_percent": cpu_usage_percent,
        "cpu_frequency_mhz": cpu_frequency_mhz,
        "memory_total_gib": memory_total_gib,
        "memory_available_gib": round(memory.available / (1024**3), 1),
        "memory_used_percent": memory_used_percent,
        "disk_read_bytes_per_sec": disk_read_rate,
        "disk_write_bytes_per_sec": disk_write_rate,
        "network_receive_bytes_per_sec": network_receive_rate,
        "network_send_bytes_per_sec": network_send_rate,
        "gpu_usage_percent": gpu_usage_percent,
        "sensor_sampled_at": (
            sensor_sampled_at if sensor_sampled else None
        ),
        "hardware_sections": hardware_sections,
        "hardware_inventory_status": hardware_inventory_status,
        "system_drive": _system_drive(),
        "battery": live_battery,
    }
