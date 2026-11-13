"""Disk capacity and throughput wrappers for documented Windows APIs."""
from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.winapi.bindings import (
    DWORD,
    MAX_PATH,
    bind_function,
    load_library,
    windows_last_error,
)
from backend.winapi.pdh import PdhCounterSample, PdhSession

log = logging.getLogger("overpine")
_lock = threading.RLock()
_query: PdhSession | None = None
_read_counter = None
_write_counter = None
_active_counter = None


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    """Contain a volume's drive identity and capacities in bytes."""

    path: str
    drive_type: int | None
    drive_type_name: str
    label: str | None
    filesystem: str | None
    total_bytes: int | None
    free_bytes: int | None
    available_bytes: int | None
    state: str


@dataclass(frozen=True, slots=True)
class DiskIoSample:
    """Contain disk rate values, PDH states, and provider explanations."""

    read: PdhCounterSample
    write: PdhCounterSample
    active_time: PdhCounterSample
    read_explanation: str | None
    write_explanation: str | None
    active_time_explanation: str | None


def _drive_type_name(drive_type: int) -> str:
    """Translate documented GetDriveTypeW values to readable names."""
    return {
        0: "unknown",
        1: "no root directory",
        2: "removable",
        3: "fixed",
        4: "network",
        5: "optical",
        6: "RAM disk",
    }.get(drive_type, "unknown")


def _volume_capacity(path: str) -> tuple[int, int, int]:
    """Read caller-available, total, and total-free volume bytes."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        raise OSError("Windows volume APIs are unavailable on this platform")
    # Microsoft Learn: GetDiskFreeSpaceExW function (fileapi.h).
    function = bind_function(
        kernel32,
        "GetDiskFreeSpaceExW",
        ctypes.c_int32,
        [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
        ],
    )
    available = ctypes.c_uint64()
    total = ctypes.c_uint64()
    total_free = ctypes.c_uint64()
    if not function(
        path,
        ctypes.byref(available),
        ctypes.byref(total),
        ctypes.byref(total_free),
    ):
        raise OSError(windows_last_error(), "GetDiskFreeSpaceExW failed")
    return int(available.value), int(total.value), int(total_free.value)


def _volume_details(path: str) -> tuple[str | None, str | None]:
    """Read volume label and file-system name when Windows exposes them."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        raise OSError("Windows volume APIs are unavailable on this platform")
    # Microsoft Learn: GetVolumeInformationW function (fileapi.h).
    function = bind_function(
        kernel32,
        "GetVolumeInformationW",
        ctypes.c_int32,
        [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            DWORD,
            ctypes.POINTER(DWORD),
            ctypes.POINTER(DWORD),
            ctypes.POINTER(DWORD),
            ctypes.c_wchar_p,
            DWORD,
        ],
    )
    label = ctypes.create_unicode_buffer(MAX_PATH)
    filesystem = ctypes.create_unicode_buffer(MAX_PATH)
    serial_number = DWORD(0)
    maximum_component_length = DWORD(0)
    filesystem_flags = DWORD(0)
    if not function(
        path,
        label,
        DWORD(len(label)),
        ctypes.byref(serial_number),
        ctypes.byref(maximum_component_length),
        ctypes.byref(filesystem_flags),
        filesystem,
        DWORD(len(filesystem)),
    ):
        raise OSError(windows_last_error(), "GetVolumeInformationW failed")
    return label.value or None, filesystem.value or None


def volume_info(path: str | Path) -> VolumeInfo:
    """Return one volume's type, file-system details, and capacity."""
    volume_path = str(path)
    if len(volume_path) == 2 and volume_path[1] == ":":
        volume_path += "\\"
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return VolumeInfo(
            volume_path, None, "unknown", None, None, None, None, None,
            "Windows volume APIs are unavailable",
        )
    # Microsoft Learn: GetDriveTypeW function (fileapi.h).
    get_drive_type = bind_function(
        kernel32,
        "GetDriveTypeW",
        DWORD,
        [ctypes.c_wchar_p],
    )
    drive_type_value = int(get_drive_type(volume_path))
    drive_type = drive_type_value or None
    try:
        available, total, free = _volume_capacity(volume_path)
    except OSError as error:
        return VolumeInfo(
            volume_path,
            drive_type,
            _drive_type_name(drive_type_value),
            None,
            None,
            None,
            None,
            None,
            f"Capacity unavailable: {error}",
        )
    try:
        label, filesystem = _volume_details(volume_path)
    except OSError as error:
        log.warning("Could not read details for volume %s: %s", volume_path, error)
        label, filesystem = None, None
    return VolumeInfo(
        path=volume_path,
        drive_type=drive_type,
        drive_type_name=_drive_type_name(drive_type_value),
        label=label,
        filesystem=filesystem,
        total_bytes=total,
        free_bytes=free,
        available_bytes=available,
        state="available",
    )


def logical_volumes() -> list[VolumeInfo] | None:
    """Enumerate mounted drive letters and return their readable volume data."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None
    # Microsoft Learn: GetLogicalDrives function (fileapi.h).
    get_logical_drives = bind_function(kernel32, "GetLogicalDrives", DWORD, [])
    drive_mask = int(get_logical_drives())
    if drive_mask == 0:
        raise OSError(windows_last_error(), "GetLogicalDrives failed")
    volumes: list[VolumeInfo] = []
    for drive_number in range(26):
        if drive_mask & (1 << drive_number):
            drive_path = f"{chr(ord('A') + drive_number)}:\\"
            volumes.append(volume_info(drive_path))
    return volumes


def _unavailable_sample() -> PdhCounterSample:
    """Represent a failed counter read without manufacturing a value."""
    return PdhCounterSample(None, "other error")


def disk_io_sample() -> DiskIoSample:
    """Read disk rates and active time from persistent PDH counters."""
    global _query, _read_counter, _write_counter, _active_counter
    with _lock:
        if _query is None:
            query: PdhSession | None = None
            try:
                query = PdhSession()
                read_counter = query.add_english_counter(
                    r"\PhysicalDisk(_Total)\Disk Read Bytes/sec"
                )
                write_counter = query.add_english_counter(
                    r"\PhysicalDisk(_Total)\Disk Write Bytes/sec"
                )
                active_counter = query.add_english_counter(
                    r"\PhysicalDisk(_Total)\% Disk Time"
                )
            except OSError:
                if query is not None:
                    query.close()
                log.exception("Could not initialize disk performance counters")
                return DiskIoSample(
                    _unavailable_sample(),
                    _unavailable_sample(),
                    _unavailable_sample(),
                    None,
                    None,
                    None,
                )
            _query = query
            _read_counter = read_counter
            _write_counter = write_counter
            _active_counter = active_counter
        try:
            _query.collect()
            return DiskIoSample(
                _query.counter_sample(_read_counter),
                _query.counter_sample(_write_counter),
                _query.counter_sample(_active_counter),
                _query.counter_explanation(_read_counter),
                _query.counter_explanation(_write_counter),
                _query.counter_explanation(_active_counter),
            )
        except OSError:
            log.exception("Could not read disk performance counters")
            return DiskIoSample(
                _unavailable_sample(),
                _unavailable_sample(),
                _unavailable_sample(),
                None,
                None,
                None,
            )


def disk_io_rates() -> tuple[float | None, float | None]:
    """Return disk read/write bytes per second for existing consumers."""
    sample = disk_io_sample()
    return sample.read.value, sample.write.value
