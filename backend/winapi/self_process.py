"""Current-process CPU and working-set metrics from Kernel32."""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from backend.winapi.bindings import (
    DWORD,
    FILETIME,
    HANDLE,
    PROCESS_MEMORY_COUNTERS_EX,
    bind_function,
    load_library,
    windows_last_error,
)

log = logging.getLogger("overpine")
_FILETIME_TICKS_PER_SECOND = 10_000_000
_lock = threading.RLock()
_previous_cpu_sample: tuple[int, float] | None = None


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Contain current-process CPU percent and working-set size."""

    cpu_percent: float | None
    working_set_bytes: int | None


def _filetime_value(value: FILETIME) -> int:
    """Combine the two documented DWORD halves without casting the pointer."""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _process_cpu_ticks(kernel32: Any, process: HANDLE) -> int:
    """Read cumulative process kernel and user CPU times."""
    # Microsoft Learn: GetProcessTimes function (processthreadsapi.h).
    function = bind_function(
        kernel32,
        "GetProcessTimes",
        ctypes.c_int32,
        [
            HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ],
    )
    creation = FILETIME()
    exit_time = FILETIME()
    kernel_time = FILETIME()
    user_time = FILETIME()
    if not function(
        process,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        raise OSError(windows_last_error(), "GetProcessTimes failed")
    return _filetime_value(kernel_time) + _filetime_value(user_time)


def _working_set_bytes(kernel32: Any, process: HANDLE) -> int:
    """Read process working-set bytes with K32GetProcessMemoryInfo."""
    # Microsoft Learn: K32GetProcessMemoryInfo function (psapi.h).
    function = bind_function(
        kernel32,
        "K32GetProcessMemoryInfo",
        ctypes.c_int32,
        [
            HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
            DWORD,
        ],
    )
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    if not function(
        process,
        ctypes.byref(counters),
        DWORD(counters.cb),
    ):
        raise OSError(windows_last_error(), "K32GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize)


def current_process_metrics() -> ProcessMetrics:
    """Read this process's memory and CPU rate, or unavailable values off Windows."""
    global _previous_cpu_sample
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return ProcessMetrics(None, None)

    # Microsoft Learn: GetCurrentProcess function (processthreadsapi.h).
    get_current_process = bind_function(
        kernel32,
        "GetCurrentProcess",
        HANDLE,
        [],
    )
    process = get_current_process()
    if process is None:
        raise OSError(windows_last_error(), "GetCurrentProcess returned a null handle")

    cpu_percent: float | None = None
    now = time.monotonic()
    try:
        cpu_ticks = _process_cpu_ticks(kernel32, process)
    except OSError:
        log.exception("Could not read current-process CPU time")
    else:
        with _lock:
            previous = _previous_cpu_sample
            _previous_cpu_sample = (cpu_ticks, now)
        if previous is not None:
            elapsed = now - previous[1]
            delta_ticks = cpu_ticks - previous[0]
            if elapsed > 0 and delta_ticks >= 0:
                cpu_percent = (
                    delta_ticks / _FILETIME_TICKS_PER_SECOND / elapsed * 100.0
                )

    try:
        working_set = _working_set_bytes(kernel32, process)
    except OSError:
        log.exception("Could not read current-process working set")
        working_set = None
    return ProcessMetrics(cpu_percent, working_set)
