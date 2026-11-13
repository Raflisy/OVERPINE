"""Import-safe wrappers for built-in Windows APIs."""

from backend.winapi.battery import BatteryStatus, get_battery_status
from backend.winapi.cpu import (
    CpuFrequency,
    active_processor_count,
    cpu_frequency,
    system_cpu_percent,
    system_uptime_seconds,
)
from backend.winapi.memory import MemoryStatus, get_memory_status
from backend.winapi.process import ProcessMetrics, current_process_metrics
from backend.winapi.cpu import CpuCache, ProcessorTopology, processor_topology
from backend.winapi.disk import DiskIoSample, VolumeInfo, disk_io_sample, logical_volumes
from backend.winapi.network import (
    NetworkInterface,
    NetworkRates,
    network_interfaces,
    network_rate_sample,
)
from backend.winapi.processes import ProcessSample, list_processes

__all__ = [
    "BatteryStatus",
    "CpuCache",
    "CpuFrequency",
    "DiskIoSample",
    "MemoryStatus",
    "NetworkInterface",
    "NetworkRates",
    "ProcessMetrics",
    "ProcessSample",
    "ProcessorTopology",
    "VolumeInfo",
    "active_processor_count",
    "cpu_frequency",
    "current_process_metrics",
    "disk_io_sample",
    "get_battery_status",
    "get_memory_status",
    "list_processes",
    "logical_volumes",
    "network_interfaces",
    "network_rate_sample",
    "processor_topology",
    "system_cpu_percent",
    "system_uptime_seconds",
]
