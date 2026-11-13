"""Memory metrics from documented Kernel32 and PSAPI functions."""
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from typing import Any

from backend.winapi.bindings import (
    DWORD,
    MEMORYSTATUSEX,
    PERFORMANCE_INFORMATION,
    bind_function,
    load_library,
    windows_last_error,
)

log = logging.getLogger("overpine")


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    """Contain system memory values in bytes and percent."""

    total_bytes: int
    available_bytes: int
    used_percent: float
    page_file_total_bytes: int
    page_file_available_bytes: int
    committed_bytes: int | None
    commit_limit_bytes: int | None
    cache_bytes: int | None


def _call_memory_status(kernel32: Any) -> MEMORYSTATUSEX:
    """Call GlobalMemoryStatusEx with its documented structure size."""
    # Microsoft Learn: GlobalMemoryStatusEx function (sysinfoapi.h).
    function = bind_function(
        kernel32,
        "GlobalMemoryStatusEx",
        ctypes.c_int32,
        [ctypes.POINTER(MEMORYSTATUSEX)],
    )
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not function(ctypes.byref(status)):
        raise OSError(windows_last_error(), "GlobalMemoryStatusEx failed")
    return status


def _call_performance_info(psapi: Any) -> PERFORMANCE_INFORMATION:
    """Call GetPerformanceInfo using the documented byte-size argument."""
    # Microsoft Learn: GetPerformanceInfo function (psapi.h).
    function = bind_function(
        psapi,
        "GetPerformanceInfo",
        ctypes.c_int32,
        [ctypes.POINTER(PERFORMANCE_INFORMATION), DWORD],
    )
    info = PERFORMANCE_INFORMATION()
    info.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
    if not function(ctypes.byref(info), DWORD(info.cb)):
        raise OSError(windows_last_error(), "GetPerformanceInfo failed")
    return info


def get_memory_status() -> MemoryStatus | None:
    """Return system memory data, or None when running outside Windows."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None
    native = _call_memory_status(kernel32)
    total = int(native.ullTotalPhys)
    available = int(native.ullAvailPhys)
    used_percent = float(native.dwMemoryLoad)

    committed_bytes: int | None = None
    commit_limit_bytes: int | None = None
    cache_bytes: int | None = None
    try:
        psapi = load_library("psapi")
        if psapi is not None:
            info = _call_performance_info(psapi)
            page_size = int(info.PageSize)
            committed_bytes = int(info.CommitTotal) * page_size
            commit_limit_bytes = int(info.CommitLimit) * page_size
            cache_bytes = int(info.SystemCache) * page_size
    except OSError:
        log.exception("Could not read Windows performance memory counters")

    return MemoryStatus(
        total_bytes=total,
        available_bytes=available,
        used_percent=used_percent,
        page_file_total_bytes=int(native.ullTotalPageFile),
        page_file_available_bytes=int(native.ullAvailPageFile),
        committed_bytes=committed_bytes,
        commit_limit_bytes=commit_limit_bytes,
        cache_bytes=cache_bytes,
    )
