"""Best-effort process enumeration using the Windows Tool Help APIs."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from backend.winapi.bindings import (
    DWORD,
    FILETIME,
    HANDLE,
    PROCESS_MEMORY_COUNTERS_EX,
    PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESSENTRY32W,
    TH32CS_SNAPPROCESS,
    bind_function,
    load_library,
    windows_last_error,
)

log = logging.getLogger("overpine")
_ACCESS_DENIED = 5
_ERROR_NO_MORE_FILES = 18
_FILETIME_TICKS_PER_SECOND = 10_000_000
_lock = threading.RLock()
_previous_cpu_samples: dict[int, tuple[int, float]] = {}


@dataclass(frozen=True, slots=True)
class ProcessSample:
    """Contain a process's identity, CPU rate, memory, and availability state."""

    process_id: int
    name: str
    cpu_percent: float | None
    cpu_state: str
    working_set_bytes: int | None
    memory_state: str


def _filetime_value(value: Any) -> int:
    """Combine the high and low DWORD fields of a FILETIME value."""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _process_cpu_ticks(kernel32: Any, process: HANDLE) -> int:
    """Read cumulative process CPU time from GetProcessTimes."""
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
    """Read process working-set bytes from K32GetProcessMemoryInfo."""
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


def _read_process_metrics(
    kernel32: Any,
    process_id: int,
    name: str,
    now: float,
    logical_processor_count: int | None = None,
) -> ProcessSample:
    """Read metrics for one process while isolating access failures."""
    # Microsoft Learn: OpenProcess function (processthreadsapi.h).
    open_process = bind_function(
        kernel32,
        "OpenProcess",
        HANDLE,
        [DWORD, ctypes.c_int32, DWORD],
    )
    process = open_process(
        DWORD(PROCESS_QUERY_LIMITED_INFORMATION),
        ctypes.c_int32(0),
        DWORD(process_id),
    )
    if process is None:
        error = windows_last_error()
        state = (
            "access denied"
            if error == _ACCESS_DENIED
            else f"unavailable (WinError {error})"
        )
        with _lock:
            _previous_cpu_samples.pop(process_id, None)
        return ProcessSample(process_id, name, None, state, None, state)

    close_handle = bind_function(
        kernel32,
        "CloseHandle",
        ctypes.c_int32,
        [HANDLE],
    )
    try:
        cpu_percent: float | None = None
        cpu_state = "no data yet"
        try:
            cpu_ticks = _process_cpu_ticks(kernel32, process)
        except OSError as error:
            log.debug("Could not read CPU time for process %s: %s", process_id, error)
            cpu_state = (
                "access denied"
                if error.errno == _ACCESS_DENIED
                else f"unavailable (WinError {error.errno})"
            )
        else:
            with _lock:
                previous = _previous_cpu_samples.get(process_id)
                _previous_cpu_samples[process_id] = (cpu_ticks, now)
            if previous is not None and not logical_processor_count:
                logical_processor_count = os.cpu_count()
            if (
                previous is not None
                and logical_processor_count is not None
                and logical_processor_count > 0
            ):
                elapsed = now - previous[1]
                delta_ticks = cpu_ticks - previous[0]
                if elapsed > 0 and delta_ticks >= 0:
                    cpu_percent = min(
                        100.0,
                        delta_ticks
                        / _FILETIME_TICKS_PER_SECOND
                        / elapsed
                        / logical_processor_count
                        * 100.0,
                    )
                    cpu_state = "valid"
                else:
                    cpu_state = "no data yet"
            elif previous is not None:
                cpu_state = "unavailable (logical processor count)"

        try:
            working_set = _working_set_bytes(kernel32, process)
            memory_state = "valid"
        except OSError as error:
            log.debug(
                "Could not read working set for process %s: %s",
                process_id,
                error,
            )
            working_set = None
            memory_state = (
                "access denied"
                if error.errno == _ACCESS_DENIED
                else f"unavailable (WinError {error.errno})"
            )
        return ProcessSample(
            process_id,
            name,
            cpu_percent,
            cpu_state,
            working_set,
            memory_state,
        )
    finally:
        if not close_handle(process):
            log.debug("Could not close handle for process %s", process_id)


def list_processes() -> list[ProcessSample] | None:
    """Return process metrics; access failures affect only the affected process."""
    global _previous_cpu_samples
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None

    # Microsoft Learn: CreateToolhelp32Snapshot function (tlhelp32.h).
    create_snapshot = bind_function(
        kernel32,
        "CreateToolhelp32Snapshot",
        HANDLE,
        [DWORD, DWORD],
    )
    snapshot = create_snapshot(
        DWORD(TH32CS_SNAPPROCESS),
        DWORD(0),
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot is None or snapshot == invalid_handle:
        raise OSError(windows_last_error(), "CreateToolhelp32Snapshot failed")

    close_handle = bind_function(
        kernel32,
        "CloseHandle",
        ctypes.c_int32,
        [HANDLE],
    )
    try:
        # Microsoft Learn: Process32FirstW function (tlhelp32.h).
        first_process = bind_function(
            kernel32,
            "Process32FirstW",
            ctypes.c_int32,
            [HANDLE, ctypes.POINTER(PROCESSENTRY32W)],
        )
        # Microsoft Learn: Process32NextW function (tlhelp32.h).
        next_process = bind_function(
            kernel32,
            "Process32NextW",
            ctypes.c_int32,
            [HANDLE, ctypes.POINTER(PROCESSENTRY32W)],
        )
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not first_process(snapshot, ctypes.byref(entry)):
            error = windows_last_error()
            if error == _ERROR_NO_MORE_FILES:
                return []
            raise OSError(error, "Process32FirstW failed")

        now = time.monotonic()
        logical_processor_count = os.cpu_count()
        result: list[ProcessSample] = []
        seen_ids: set[int] = set()
        while True:
            process_id = int(entry.th32ProcessID)
            seen_ids.add(process_id)
            result.append(
                _read_process_metrics(
                    kernel32,
                    process_id,
                    str(entry.szExeFile),
                    now,
                    logical_processor_count,
                )
            )
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not next_process(snapshot, ctypes.byref(entry)):
                error = windows_last_error()
                if error != _ERROR_NO_MORE_FILES:
                    raise OSError(error, "Process32NextW failed")
                break
        with _lock:
            _previous_cpu_samples = {
                process_id: sample
                for process_id, sample in _previous_cpu_samples.items()
                if process_id in seen_ids
            }
        return result
    finally:
        if not close_handle(snapshot):
            log.debug("Could not close the process snapshot handle")
