"""Persistent, language-neutral PDH queries for Windows performance counters."""
from __future__ import annotations

import atexit
import ctypes
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Literal
from typing import Any

from backend.winapi.bindings import (
    DWORD,
    HANDLE,
    PDH_COUNTER_INFO_W,
    PDH_FMT_COUNTERVALUE,
    SIZE_T,
    bind_function,
    load_library,
)

log = logging.getLogger("overpine")
_ERROR_SUCCESS = 0
_PDH_MORE_DATA = 0x800007D2
# Microsoft Learn: PDH Error Codes.
_PDH_CSTATUS_VALID_DATA = 0x00000000
_PDH_CSTATUS_NEW_DATA = 0x00000001
_PDH_CSTATUS_NO_INSTANCE = 0x800007D1
_PDH_CSTATUS_ITEM_NOT_VALIDATED = 0x800007D3
_PDH_NO_DATA = 0x800007D5
_PDH_CSTATUS_NO_OBJECT = 0xC0000BB8
_PDH_CSTATUS_NO_COUNTER = 0xC0000BB9
_PDH_FMT_DOUBLE = 0x00000200
_sessions: set[PdhSession] = set()
_sessions_lock = threading.RLock()
_INSTANCE_PATTERN = re.compile(r"\\[^\\()]+\((.*)\)\\[^\\]+$")


PdhCounterState = Literal[
    "valid",
    "no data yet",
    "object or counter does not exist",
    "other error",
]


@dataclass(frozen=True, slots=True)
class PdhCounterSample:
    """Contain a formatted counter value and its documented PDH state."""

    value: float | None
    state: PdhCounterState


class PdhStatusError(OSError):
    """Represent a failed PDH call with its mapped availability state."""

    def __init__(self, status: int, function_name: str) -> None:
        """Create a status error using the documented PDH status mapping."""
        self.state = _counter_state(status)
        super().__init__(
            status,
            f"{function_name} failed with PDH status 0x{status & 0xFFFFFFFF:08X} "
            f"({self.state})",
        )


def _counter_state(status: int) -> PdhCounterState:
    """Map documented PDH status codes to clear monitoring states."""
    normalized = status & 0xFFFFFFFF
    if normalized in (_PDH_CSTATUS_VALID_DATA, _PDH_CSTATUS_NEW_DATA):
        return "valid"
    if normalized in (_PDH_CSTATUS_ITEM_NOT_VALIDATED, _PDH_NO_DATA):
        return "no data yet"
    if normalized in (
        _PDH_CSTATUS_NO_INSTANCE,
        _PDH_CSTATUS_NO_OBJECT,
        _PDH_CSTATUS_NO_COUNTER,
    ):
        return "object or counter does not exist"
    return "other error"


def _check_status(status: int, function_name: str) -> None:
    """Raise an explicit error for a failed PDH function return code."""
    if status != _ERROR_SUCCESS:
        raise PdhStatusError(status, function_name)


def _counter_info(
    library: Any,
    counter: HANDLE,
) -> tuple[str | None, str | None]:
    """Read the localized counter path and provider explanation from PDH."""
    get_counter_info = bind_function(
        library,
        "PdhGetCounterInfoW",
        DWORD,
        [HANDLE, ctypes.c_ubyte, ctypes.POINTER(DWORD), ctypes.c_void_p],
    )
    buffer_size = DWORD(0)
    status = int(
        get_counter_info(
            counter,
            ctypes.c_ubyte(1),
            ctypes.byref(buffer_size),
            None,
        )
    )
    if status != _PDH_MORE_DATA or buffer_size.value <= 0:
        raise OSError(
            status,
            "PdhGetCounterInfoW did not return the required counter-info size",
        )
    buffer = ctypes.create_string_buffer(buffer_size.value)
    _check_status(
        int(
            get_counter_info(
                counter,
                ctypes.c_ubyte(1),
                ctypes.byref(buffer_size),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
        ),
        "PdhGetCounterInfoW",
    )
    info = ctypes.cast(buffer, ctypes.POINTER(PDH_COUNTER_INFO_W)).contents
    full_path = info.szFullPath
    explanation = info.szExplainText
    return (
        str(full_path) if full_path else None,
        str(explanation) if explanation else None,
    )


def _counter_info_path(library: Any, query: HANDLE, path: str) -> str:
    """Localize an English wildcard path through the documented PDH query flow."""
    counter = HANDLE()
    add_english = bind_function(
        library,
        "PdhAddEnglishCounterW",
        DWORD,
        [HANDLE, ctypes.c_wchar_p, SIZE_T, ctypes.POINTER(HANDLE)],
    )
    _check_status(
        int(add_english(query, path, SIZE_T(0), ctypes.byref(counter))),
        "PdhAddEnglishCounterW",
    )

    localized_path, _ = _counter_info(library, counter)
    if not localized_path:
        raise OSError("PdhGetCounterInfoW returned no counter path")
    return localized_path


def _expanded_localized_paths(library: Any, path: str) -> list[str]:
    """Expand a localized wildcard path with PdhExpandWildCardPathW."""
    expand = bind_function(
        library,
        "PdhExpandWildCardPathW",
        DWORD,
        [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(DWORD),
            DWORD,
        ],
    )
    buffer_size = DWORD(0)
    status = int(
        expand(
            None,
            path,
            None,
            ctypes.byref(buffer_size),
            DWORD(0),
        )
    )
    if status != _PDH_MORE_DATA or buffer_size.value <= 0:
        raise OSError(
            status,
            "PdhExpandWildCardPathW did not return the required path-list size",
        )

    buffer = ctypes.create_unicode_buffer(buffer_size.value)
    _check_status(
        int(
            expand(
                None,
                path,
                buffer,
                ctypes.byref(buffer_size),
                DWORD(0),
            )
        ),
        "PdhExpandWildCardPathW",
    )
    return [value for value in buffer[: buffer_size.value].split("\0") if value]


class PdhSession:
    """Own a PDH query and retain its counter handles between samples."""

    def __init__(self) -> None:
        """Create a real-time query and register it for process-exit cleanup."""
        library = load_library("pdh")
        if library is None:
            raise OSError("PDH performance counters are available only on Windows")
        self._library = library
        self._query = HANDLE()
        self._sample_count = 0
        self._last_collect_at: float | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._counter_explanations: dict[int, str | None] = {}

        # Microsoft Learn: PdhOpenQueryW function (pdh.h).
        open_query = bind_function(
            library,
            "PdhOpenQueryW",
            DWORD,
            [ctypes.c_wchar_p, SIZE_T, ctypes.POINTER(HANDLE)],
        )
        _check_status(
            int(open_query(None, SIZE_T(0), ctypes.byref(self._query))),
            "PdhOpenQueryW",
        )
        with _sessions_lock:
            _sessions.add(self)

    @staticmethod
    def expand_english_counter(path: str) -> list[tuple[str, str]]:
        """Return instance names and localized paths matching an English wildcard."""
        library = load_library("pdh")
        if library is None:
            raise OSError("PDH performance counters are available only on Windows")

        # Microsoft Learn: PdhOpenQueryW function (pdh.h).
        open_query = bind_function(
            library,
            "PdhOpenQueryW",
            DWORD,
            [ctypes.c_wchar_p, SIZE_T, ctypes.POINTER(HANDLE)],
        )
        query = HANDLE()
        _check_status(
            int(open_query(None, SIZE_T(0), ctypes.byref(query))),
            "PdhOpenQueryW",
        )
        try:
            localized_path = _counter_info_path(library, query, path)
        finally:
            # Microsoft Learn: PdhCloseQuery function (pdh.h).
            close_query = bind_function(
                library,
                "PdhCloseQuery",
                DWORD,
                [HANDLE],
            )
            _check_status(int(close_query(query)), "PdhCloseQuery")

        expanded = _expanded_localized_paths(library, localized_path)
        instances: list[tuple[str, str]] = []
        for expanded_path in expanded:
            match = _INSTANCE_PATTERN.search(expanded_path)
            if match is None:
                raise OSError(
                    f"Expanded PDH path does not contain a counter instance: {expanded_path}"
                )
            instances.append((match.group(1), expanded_path))
        return instances

    def add_english_counter(self, path: str) -> HANDLE:
        """Add one language-neutral, non-wildcard counter to this query."""
        with self._lock:
            self._ensure_open()
            counter = HANDLE()
            # Microsoft Learn: PdhAddEnglishCounterW function (pdh.h).
            add_counter = bind_function(
                self._library,
                "PdhAddEnglishCounterW",
                DWORD,
                [HANDLE, ctypes.c_wchar_p, SIZE_T, ctypes.POINTER(HANDLE)],
            )
            _check_status(
                int(
                    add_counter(
                        self._query,
                        path,
                        SIZE_T(0),
                        ctypes.byref(counter),
                    )
                ),
                "PdhAddEnglishCounterW",
            )
            _, explanation = _counter_info(self._library, counter)
            self._counter_explanations[int(counter.value or 0)] = explanation
            return counter

    def add_expanded_counter(self, path: str) -> list[tuple[str, HANDLE]]:
        """Expand an English wildcard and add each localized path to this query."""
        with self._lock:
            self._ensure_open()
            expanded = self.expand_english_counter(path)
            result: list[tuple[str, HANDLE]] = []
            # Microsoft Learn: PdhAddCounterW function (pdh.h).
            add_counter = bind_function(
                self._library,
                "PdhAddCounterW",
                DWORD,
                [HANDLE, ctypes.c_wchar_p, SIZE_T, ctypes.POINTER(HANDLE)],
            )
            for instance, localized_path in expanded:
                counter = HANDLE()
                _check_status(
                    int(
                        add_counter(
                            self._query,
                            localized_path,
                            SIZE_T(0),
                            ctypes.byref(counter),
                        )
                    ),
                    "PdhAddCounterW",
                )
                _, explanation = _counter_info(self._library, counter)
                self._counter_explanations[int(counter.value or 0)] = explanation
                result.append((instance, counter))
            return result

    def collect(self, minimum_interval_s: float = 1.0) -> bool:
        """Collect at least one second apart and require two rate samples."""
        if minimum_interval_s < 0:
            raise ValueError("minimum_interval_s cannot be negative")
        with self._lock:
            self._ensure_open()
            now = time.monotonic()
            last_collect_at = getattr(self, "_last_collect_at", None)
            if (
                last_collect_at is not None
                and now - last_collect_at < minimum_interval_s
            ):
                return False
            self._last_collect_at = now
            # Microsoft Learn: PdhCollectQueryData function (pdh.h).
            collect = bind_function(
                self._library,
                "PdhCollectQueryData",
                DWORD,
                [HANDLE],
            )
            status = int(collect(self._query)) & 0xFFFFFFFF
            if status == _PDH_NO_DATA:
                return False
            _check_status(status, "PdhCollectQueryData")
            self._sample_count += 1
            return self._sample_count >= 2

    def counter_sample(self, counter: HANDLE) -> PdhCounterSample:
        """Read a counter value and retain no-data/missing/error states."""
        with self._lock:
            self._ensure_open()
            if self._sample_count < 2:
                return PdhCounterSample(None, "no data yet")
            value = PDH_FMT_COUNTERVALUE()
            # Microsoft Learn: PdhGetFormattedCounterValue function (pdh.h).
            get_value = bind_function(
                self._library,
                "PdhGetFormattedCounterValue",
                DWORD,
                [
                    HANDLE,
                    DWORD,
                    ctypes.POINTER(DWORD),
                    ctypes.POINTER(PDH_FMT_COUNTERVALUE),
                ],
            )
            api_status = int(
                get_value(
                    counter,
                    DWORD(_PDH_FMT_DOUBLE),
                    None,
                    ctypes.byref(value),
                )
            )
            if api_status != _ERROR_SUCCESS:
                state = _counter_state(api_status)
                if state != "other error":
                    return PdhCounterSample(None, state)
                raise OSError(
                    api_status,
                    "PdhGetFormattedCounterValue failed with PDH status "
                    f"0x{api_status & 0xFFFFFFFF:08X}",
                )
            state = _counter_state(int(value.CStatus))
            if state != "valid":
                return PdhCounterSample(None, state)
            result = float(value.value.doubleValue)
            if not math.isfinite(result):
                return PdhCounterSample(None, "other error")
            return PdhCounterSample(result, "valid")

    def value(self, counter: HANDLE) -> float | None:
        """Read a valid formatted double value from a previously collected query."""
        return self.counter_sample(counter).value

    def counter_explanation(self, counter: HANDLE) -> str | None:
        """Return the unit/meaning explanation supplied by the PDH provider."""
        with self._lock:
            self._ensure_open()
            return self._counter_explanations.get(int(counter.value or 0))

    def close(self) -> None:
        """Close the query and all handles it owns."""
        with self._lock:
            if self._closed:
                return
            # Microsoft Learn: PdhCloseQuery function (pdh.h).
            close_query = bind_function(
                self._library,
                "PdhCloseQuery",
                DWORD,
                [HANDLE],
            )
            _check_status(int(close_query(self._query)), "PdhCloseQuery")
            self._closed = True
        with _sessions_lock:
            _sessions.discard(self)

    def _ensure_open(self) -> None:
        """Reject use after the query has been closed."""
        if self._closed:
            raise RuntimeError("PDH query is closed")


def close_all_sessions() -> None:
    """Close retained PDH queries during normal interpreter shutdown."""
    with _sessions_lock:
        sessions = list(_sessions)
    for session in sessions:
        try:
            session.close()
        except OSError:
            log.exception("Could not close a PDH query during shutdown")


atexit.register(close_all_sessions)
