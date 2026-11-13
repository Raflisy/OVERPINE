"""Compatibility capacity result backed by Windows volume APIs."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path

from backend.winapi.bindings import (
    GUID_DEVINTERFACE_DISK,
    IOCTL_STORAGE_GET_DEVICE_NUMBER,
    IOCTL_STORAGE_QUERY_PROPERTY,
    PROPERTY_STANDARD_QUERY,
    STORAGE_DEVICE_NUMBER,
    STORAGE_DEVICE_TEMPERATURE_PROPERTY,
    STORAGE_PROPERTY_QUERY,
    STORAGE_TEMPERATURE_DATA_DESCRIPTOR,
    STORAGE_TEMPERATURE_INFO,
)
from backend.winapi.device import (
    close_device,
    device_io_control,
    enumerate_device_paths,
    open_storage_device,
)
from backend.winapi.disk import volume_info


@dataclass(frozen=True, slots=True)
class DiskUsage:
    """Contain total, used, and free capacity values in bytes."""

    total: int
    used: int
    free: int
    available_to_caller: int


@dataclass(frozen=True, slots=True)
class StorageTemperature:
    """Contain a temperature from one physical storage-device sensor."""

    disk_number: int
    sensor_index: int
    temperature_c: int


def disk_device_paths() -> list[str]:
    """Enumerate present disk interfaces using the documented SetupAPI class."""
    return enumerate_device_paths(GUID_DEVINTERFACE_DISK)


def get_storage_device_number(interface_path: str) -> int:
    """Resolve a disk-interface path to its stable current PhysicalDrive number."""
    handle = open_storage_device(interface_path)
    try:
        device_number = STORAGE_DEVICE_NUMBER()
        device_io_control(
            handle,
            IOCTL_STORAGE_GET_DEVICE_NUMBER,
            None,
            device_number,
        )
        return int(device_number.DeviceNumber)
    finally:
        close_device(handle)


def query_physical_drive_temperatures(
    disk_number: int,
) -> list[StorageTemperature]:
    """Read temperature entries from one PhysicalDrive with zero access rights."""
    path = rf"\\.\PhysicalDrive{disk_number}"
    handle = open_storage_device(path)
    try:
        query = STORAGE_PROPERTY_QUERY(
            STORAGE_DEVICE_TEMPERATURE_PROPERTY,
            PROPERTY_STANDARD_QUERY,
            (ctypes.c_ubyte * 1)(0),
        )
        output = ctypes.create_string_buffer(4096)
        bytes_returned = device_io_control(
            handle,
            IOCTL_STORAGE_QUERY_PROPERTY,
            query,
            output,
        )
        descriptor = STORAGE_TEMPERATURE_DATA_DESCRIPTOR.from_buffer_copy(output)
        info_offset = STORAGE_TEMPERATURE_DATA_DESCRIPTOR.TemperatureInfo.offset
        info_size = ctypes.sizeof(STORAGE_TEMPERATURE_INFO)
        required_size = info_offset + descriptor.InfoCount * info_size
        if (
            bytes_returned < info_offset
            or descriptor.Size < required_size
            or bytes_returned < required_size
        ):
            raise OSError(
                "StorageDeviceTemperatureProperty returned an incomplete descriptor"
            )
        readings: list[StorageTemperature] = []
        for index in range(descriptor.InfoCount):
            start = info_offset + index * info_size
            info = STORAGE_TEMPERATURE_INFO.from_buffer_copy(
                output.raw[start : start + info_size]
            )
            readings.append(
                StorageTemperature(
                    disk_number=disk_number,
                    sensor_index=int(info.Index),
                    temperature_c=int(info.Temperature),
                )
            )
        return readings
    finally:
        close_device(handle)


def disk_usage(path: str | Path) -> DiskUsage:
    """Read volume capacity using GetDiskFreeSpaceExW."""
    volume = volume_info(path)
    if (
        volume.total_bytes is None
        or volume.free_bytes is None
        or volume.available_bytes is None
    ):
        raise OSError(volume.state)
    if volume.free_bytes > volume.total_bytes:
        raise OSError("GetDiskFreeSpaceExW returned more free bytes than total capacity")
    return DiskUsage(
        total=volume.total_bytes,
        used=volume.total_bytes - volume.free_bytes,
        free=volume.free_bytes,
        available_to_caller=volume.available_bytes,
    )
