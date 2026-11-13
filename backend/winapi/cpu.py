"""System processor metrics from Kernel32, PowrProf, and PDH."""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from statistics import mean
from typing import Any

from backend.winapi.bindings import (
    CACHE_RELATIONSHIP,
    DWORD,
    FILETIME,
    GROUP_AFFINITY,
    HANDLE,
    PROCESSOR_RELATIONSHIP,
    PROCESSOR_POWER_INFORMATION,
    RELATION_CACHE,
    RELATION_PROCESSOR_CORE,
    SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX,
    SYSTEM_INFO,
    bind_function,
    load_library,
    windows_last_error,
)
from backend.winapi.pdh import PdhSession

log = logging.getLogger("overpine")
_FILETIME_TICKS_PER_SECOND = 10_000_000
_MIN_CPU_SAMPLE_INTERVAL_S = 1.0
# Microsoft Learn: POWER_INFORMATION_LEVEL, ProcessorInformation value.
_POWER_INFORMATION_PROCESSOR = 11
# Microsoft Learn: GetLogicalProcessorInformationEx function (sysinfoapi.h).
_ERROR_INSUFFICIENT_BUFFER = 122
_cpu_lock = threading.RLock()
_previous_system_times: tuple[int, int, int, float] | None = None
_processor_query: PdhSession | None = None
_processor_values: list[tuple[str, HANDLE]] = []
_frequency_query: PdhSession | None = None
_frequency_counters: tuple[HANDLE, HANDLE] | None = None
_frequency_query_error: str | None = None


@dataclass(frozen=True, slots=True)
class CpuFrequency:
    """Contain Windows-reported frequency values and their current source."""

    current_mhz: float | None
    maximum_mhz: float | None
    current_source: str = "CallNtPowerInformation (reported; not guaranteed live)"
    percent_of_maximum: float | None = None


@dataclass(frozen=True, slots=True)
class CpuCache:
    """Describe a cache entry returned by processor-topology APIs."""

    level: int
    size_bytes: int
    cache_type: int


_CACHE_TYPE_UNIFIED = 0
_CACHE_TYPE_INSTRUCTION = 1
_CACHE_TYPE_DATA = 2
_CACHE_TYPE_TRACE = 3
_CACHE_TYPE_NAMES = {
    _CACHE_TYPE_UNIFIED: "unified",
    _CACHE_TYPE_INSTRUCTION: "instruction",
    _CACHE_TYPE_DATA: "data",
    _CACHE_TYPE_TRACE: "trace",
}


def aggregate_cache_sizes(
    caches: tuple[CpuCache, ...],
) -> dict[tuple[int, int], int]:
    """Sum reported cache sizes by level and documented cache type."""
    totals: dict[tuple[int, int], int] = {}
    for cache in caches:
        key = (cache.level, cache.cache_type)
        totals[key] = totals.get(key, 0) + cache.size_bytes
    return totals


def cache_type_name(cache_type: int) -> str:
    """Return a readable name for a Windows CACHE_TYPE value."""
    return _CACHE_TYPE_NAMES.get(cache_type, "other")


@dataclass(frozen=True, slots=True)
class ProcessorTopology:
    """Contain active physical/logical processor counts and cache entries."""

    physical_core_count: int
    logical_processor_count: int
    caches: tuple[CpuCache, ...]


def _filetime_value(value: FILETIME) -> int:
    """Combine the two documented DWORD halves without pointer casting."""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _get_system_times(kernel32: Any) -> tuple[int, int, int]:
    """Read idle, kernel, and user time using GetSystemTimes."""
    # Microsoft Learn: GetSystemTimes function (processthreadsapi.h).
    function = bind_function(
        kernel32,
        "GetSystemTimes",
        ctypes.c_int32,
        [
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ],
    )
    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not function(
        ctypes.byref(idle),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError(windows_last_error(), "GetSystemTimes failed")
    return (
        _filetime_value(idle),
        _filetime_value(kernel),
        _filetime_value(user),
    )


def system_cpu_percent() -> float | None:
    """Return total busy time percent over an interval of at least one second."""
    global _previous_system_times
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None
    current = _get_system_times(kernel32)
    sampled_at = time.monotonic()
    with _cpu_lock:
        previous = _previous_system_times
        if previous is None:
            _previous_system_times = (*current, sampled_at)
            return None
        previous_idle, previous_kernel, previous_user, previous_at = previous
        elapsed = sampled_at - previous_at
        if elapsed < _MIN_CPU_SAMPLE_INTERVAL_S:
            return None
        _previous_system_times = (*current, sampled_at)
    idle_delta = current[0] - previous_idle
    total_delta = current[1] - previous_kernel + current[2] - previous_user
    if idle_delta < 0 or total_delta <= 0 or idle_delta > total_delta:
        return None
    # GetSystemTimes kernel time includes idle; subtract idle once from total.
    return 100.0 * (total_delta - idle_delta) / total_delta


def active_processor_count() -> int | None:
    """Return active logical processors counted from core affinity masks."""
    topology = processor_topology()
    return topology.logical_processor_count if topology is not None else None


def _processor_information_buffer(kernel32: Any, relationship: int) -> Any:
    """Read one variable-sized relationship list from GetLogicalProcessorInformationEx."""
    # Microsoft Learn: GetLogicalProcessorInformationEx function (sysinfoapi.h).
    function = bind_function(
        kernel32,
        "GetLogicalProcessorInformationEx",
        ctypes.c_int32,
        [DWORD, ctypes.c_void_p, ctypes.POINTER(DWORD)],
    )
    required_size = DWORD(0)
    if function(
        DWORD(relationship),
        None,
        ctypes.byref(required_size),
    ):
        if required_size.value == 0:
            return ctypes.create_string_buffer(0), 0
    elif (
        windows_last_error() != _ERROR_INSUFFICIENT_BUFFER
        or required_size.value == 0
    ):
        raise OSError(
            windows_last_error(),
            "GetLogicalProcessorInformationEx could not size its result",
        )

    buffer = ctypes.create_string_buffer(required_size.value)
    returned_size = DWORD(required_size.value)
    if not function(
        DWORD(relationship),
        ctypes.cast(buffer, ctypes.c_void_p),
        ctypes.byref(returned_size),
    ):
        raise OSError(
            windows_last_error(),
            "GetLogicalProcessorInformationEx failed",
        )
    if returned_size.value > required_size.value:
        raise OSError("GetLogicalProcessorInformationEx returned an invalid size")
    return buffer, int(returned_size.value)


def _processor_topology_from_buffers(
    processor_buffer: Any,
    processor_size: int,
    cache_buffer: Any,
    cache_size: int,
) -> ProcessorTopology:
    """Parse documented variable-size processor-core and cache records."""
    physical_cores = 0
    logical_processors = 0
    caches: list[CpuCache] = []
    header_size = ctypes.sizeof(DWORD) * 2

    for buffer, size, target in (
        (processor_buffer, processor_size, "processor"),
        (cache_buffer, cache_size, "cache"),
    ):
        base_address = ctypes.addressof(buffer)
        offset = 0
        while offset < size:
            remaining = size - offset
            if remaining < header_size:
                raise OSError("Processor topology contains a truncated record")
            record_address = base_address + offset
            record = ctypes.cast(
                record_address,
                ctypes.POINTER(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX),
            ).contents
            record_size = int(record.Size)
            if record_size < header_size or record_size > remaining:
                raise OSError("Processor topology contains an invalid record size")

            relation_address = (
                record_address
                + SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset
            )
            if target == "processor" and int(record.Relationship) == RELATION_PROCESSOR_CORE:
                relation = ctypes.cast(
                    relation_address,
                    ctypes.POINTER(PROCESSOR_RELATIONSHIP),
                ).contents
                group_count = int(relation.GroupCount)
                if group_count <= 0:
                    raise OSError("A processor core has no affinity group")
                relation_size = (
                    SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset
                    + PROCESSOR_RELATIONSHIP.GroupMask.offset
                    + group_count * ctypes.sizeof(GROUP_AFFINITY)
                )
                if relation_size > record_size:
                    raise OSError("A processor core record is truncated")
                group_masks_address = (
                    relation_address + PROCESSOR_RELATIONSHIP.GroupMask.offset
                )
                physical_cores += 1
                for group_index in range(group_count):
                    affinity = ctypes.cast(
                        group_masks_address
                        + group_index * ctypes.sizeof(GROUP_AFFINITY),
                        ctypes.POINTER(GROUP_AFFINITY),
                    ).contents
                    logical_processors += int(affinity.Mask).bit_count()
            elif target == "cache" and int(record.Relationship) == RELATION_CACHE:
                relation = ctypes.cast(
                    relation_address,
                    ctypes.POINTER(CACHE_RELATIONSHIP),
                ).contents
                group_count = int(relation.GroupCount)
                relation_size = (
                    SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset
                    + CACHE_RELATIONSHIP.GroupMasks.offset
                    + group_count * ctypes.sizeof(GROUP_AFFINITY)
                )
                if group_count <= 0 or relation_size > record_size:
                    raise OSError("A processor cache record is truncated")
                if relation.Level > 0 and relation.CacheSize > 0:
                    caches.append(
                        CpuCache(
                            level=int(relation.Level),
                            size_bytes=int(relation.CacheSize),
                            cache_type=int(relation.Type),
                        )
                    )
            offset += record_size
        if offset != size:
            raise OSError("Processor topology records do not fill their buffer")

    return ProcessorTopology(
        physical_core_count=physical_cores,
        logical_processor_count=logical_processors,
        caches=tuple(caches),
    )


def processor_topology() -> ProcessorTopology | None:
    """Return active processor counts and cache records, or None off Windows."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None
    processor_buffer, processor_size = _processor_information_buffer(
        kernel32,
        RELATION_PROCESSOR_CORE,
    )
    cache_buffer, cache_size = _processor_information_buffer(
        kernel32,
        RELATION_CACHE,
    )
    return _processor_topology_from_buffers(
        processor_buffer,
        processor_size,
        cache_buffer,
        cache_size,
    )


def _is_total_processor_instance(instance: str) -> bool:
    """Identify overall or processor-group totals, not individual cores."""
    return any(part.strip().casefold() == "_total" for part in instance.split(","))


def _powrprof_frequency() -> CpuFrequency:
    """Read reported frequencies from CallNtPowerInformation."""
    kernel32 = load_library("kernel32")
    powrprof = load_library("powrprof")
    if kernel32 is None or powrprof is None:
        return CpuFrequency(None, None)

    # Microsoft Learn: GetSystemInfo function (sysinfoapi.h).
    get_system_info = bind_function(
        kernel32,
        "GetSystemInfo",
        None,
        [ctypes.POINTER(SYSTEM_INFO)],
    )
    system_info = SYSTEM_INFO()
    get_system_info(ctypes.byref(system_info))
    processor_count = int(system_info.dwNumberOfProcessors)
    if processor_count <= 0:
        return CpuFrequency(None, None)

    # Microsoft Learn: CallNtPowerInformation function (powerbase.h).
    function = bind_function(
        powrprof,
        "CallNtPowerInformation",
        ctypes.c_int32,
        [
            ctypes.c_int32,
            ctypes.c_void_p,
            DWORD,
            ctypes.c_void_p,
            DWORD,
        ],
    )
    processors = (PROCESSOR_POWER_INFORMATION * processor_count)()
    output_size = ctypes.sizeof(processors)
    result = function(
        ctypes.c_int32(_POWER_INFORMATION_PROCESSOR),
        None,
        DWORD(0),
        ctypes.cast(processors, ctypes.c_void_p),
        DWORD(output_size),
    )
    if result != 0:
        log.warning("CallNtPowerInformation failed with NTSTATUS %s", result)
        return CpuFrequency(None, None)

    current = [
        float(item.CurrentMhz)
        for item in processors
        if item.CurrentMhz > 0
    ]
    maximum = [
        float(item.MaxMhz)
        for item in processors
        if item.MaxMhz > 0
    ]
    return CpuFrequency(
        current_mhz=mean(current) if current else None,
        maximum_mhz=mean(maximum) if maximum else None,
    )


def _pdh_frequency_sample() -> tuple[float | None, float | None]:
    """Read current MHz and percent after verifying the PDH explanation units."""
    global _frequency_query, _frequency_counters, _frequency_query_error
    if load_library("pdh") is None:
        return None, None
    with _cpu_lock:
        if _frequency_query is None:
            query: PdhSession | None = None
            try:
                query = PdhSession()
                frequency = query.add_english_counter(
                    r"\Processor Information(_Total)\Processor Frequency"
                )
                maximum_percent = query.add_english_counter(
                    r"\Processor Information(_Total)\% of Maximum Frequency"
                )
                frequency_explanation = (
                    query.counter_explanation(frequency) or ""
                ).casefold()
                percent_explanation = (
                    query.counter_explanation(maximum_percent) or ""
                ).casefold()
                if "megahertz" not in frequency_explanation:
                    query.close()
                    raise ValueError(
                        "Processor Frequency PDH explanation does not confirm MHz"
                    )
                if "percentage" not in percent_explanation:
                    query.close()
                    raise ValueError(
                        "% of Maximum Frequency PDH explanation does not confirm percent"
                    )
                _frequency_query = query
                _frequency_counters = (frequency, maximum_percent)
                _frequency_query_error = None
            except (OSError, ValueError) as error:
                if query is not None:
                    try:
                        query.close()
                    except OSError as close_error:
                        log.warning(
                            "Could not close the failed PDH frequency query: %s",
                            close_error,
                        )
                error_text = str(error)
                if _frequency_query_error != error_text:
                    log.warning(
                        "PDH processor frequency is unavailable: %s",
                        error_text,
                    )
                _frequency_query_error = error_text
                return None, None

        assert _frequency_counters is not None
        try:
            if not _frequency_query.collect():
                return None, None
            frequency_sample = _frequency_query.counter_sample(
                _frequency_counters[0]
            )
            percent_sample = _frequency_query.counter_sample(
                _frequency_counters[1]
            )
        except OSError as error:
            log.warning("Could not read PDH processor frequency: %s", error)
            return None, None
        return (
            frequency_sample.value if frequency_sample.state == "valid" else None,
            percent_sample.value if percent_sample.state == "valid" else None,
        )


def cpu_frequency() -> CpuFrequency:
    """Prefer unit-verified PDH frequency and label the PowrProf fallback."""
    fallback = _powrprof_frequency()
    current_mhz, percent_of_maximum = _pdh_frequency_sample()
    if current_mhz is None:
        return fallback
    return CpuFrequency(
        current_mhz=current_mhz,
        maximum_mhz=fallback.maximum_mhz,
        current_source="Processor Frequency PDH (MHz per counter explanation)",
        percent_of_maximum=percent_of_maximum,
    )


def per_core_cpu_percent() -> list[float | None] | None:
    """Return per-logical-processor percentages from Processor Information PDH."""
    global _processor_query, _processor_values
    if load_library("pdh") is None:
        return None
    with _cpu_lock:
        if _processor_query is None:
            try:
                _processor_query = PdhSession()
                expanded = _processor_query.add_expanded_counter(
                    r"\Processor Information(*)\% Processor Time"
                )
                _processor_values = [
                    (instance, handle)
                    for instance, handle in expanded
                    if not _is_total_processor_instance(instance)
                ]
            except OSError:
                _processor_query = None
                _processor_values = []
                log.exception("Could not initialize processor performance counters")
                return None
        try:
            has_rate_sample = _processor_query.collect()
            if not has_rate_sample:
                return None
            return [
                _processor_query.value(handle)
                for _, handle in _processor_values
            ]
        except OSError:
            log.exception("Could not read processor performance counters")
            return None


def system_uptime_seconds() -> float | None:
    """Return Windows system uptime from GetTickCount64."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None
    # Microsoft Learn: GetTickCount64 function (sysinfoapi.h).
    function = bind_function(kernel32, "GetTickCount64", ctypes.c_uint64, [])
    return int(function()) / 1000.0
