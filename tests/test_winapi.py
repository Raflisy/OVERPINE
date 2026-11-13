"""Standard-library tests for documented Windows API wrappers."""
from __future__ import annotations

import ctypes
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.winapi import (
    bindings,
    cpu,
    memory,
    network,
    processes,
    self_process,
    selftest,
)
from backend.winapi import pdh
from backend.services import system_info


class FakeFunction:
    """Provide a callable fake that accepts ctypes signature attributes."""

    def __init__(self, callback) -> None:
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class WindowsBindingsTests(unittest.TestCase):
    """Verify native structure layouts and import-safe API behavior."""

    def test_documented_structure_sizes_match_pointer_width(self) -> None:
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        expected_system_info = 48 if pointer_size == 8 else 36
        self.assertEqual(ctypes.sizeof(bindings.SYSTEM_INFO), expected_system_info)
        self.assertEqual(ctypes.sizeof(bindings.MEMORYSTATUSEX), 64)

    def test_memory_status_survives_optional_performance_info_failure(self) -> None:
        def fill_memory_status(pointer) -> int:
            status = ctypes.cast(
                pointer,
                ctypes.POINTER(bindings.MEMORYSTATUSEX),
            ).contents
            status.ullTotalPhys = 16_000
            status.ullAvailPhys = 6_000
            status.ullTotalPageFile = 24_000
            status.ullAvailPageFile = 8_000
            status.dwMemoryLoad = 62
            return 1

        def fail_performance_info(_pointer, _size) -> int:
            return 0

        libraries = {
            "kernel32": SimpleNamespace(
                GlobalMemoryStatusEx=FakeFunction(fill_memory_status)
            ),
            "psapi": SimpleNamespace(
                GetPerformanceInfo=FakeFunction(fail_performance_info)
            ),
        }
        with (
            patch.object(
                memory,
                "load_library",
                side_effect=lambda name: libraries[name],
            ),
            patch.object(memory, "windows_last_error", return_value=5),
            self.assertLogs("overpine", level="ERROR"),
        ):
            result = memory.get_memory_status()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.total_bytes, 16_000)
        self.assertEqual(result.available_bytes, 6_000)
        self.assertEqual(result.used_percent, 62.0)
        self.assertIsNone(result.committed_bytes)
        self.assertIsNone(result.commit_limit_bytes)
        self.assertIsNone(result.cache_bytes)

    def test_cpu_rate_requires_two_samples(self) -> None:
        samples = iter(
            [
                (100, 200, 100),
                (150, 280, 120),
            ]
        )

        def write_filetime(pointer, ticks: int) -> None:
            value = ctypes.cast(
                pointer,
                ctypes.POINTER(bindings.FILETIME),
            ).contents
            value.dwLowDateTime = ticks & 0xFFFFFFFF
            value.dwHighDateTime = ticks >> 32

        def get_system_times(idle_pointer, kernel_pointer, user_pointer) -> int:
            idle, kernel, user = next(samples)
            write_filetime(idle_pointer, idle)
            write_filetime(kernel_pointer, kernel)
            write_filetime(user_pointer, user)
            return 1

        kernel32 = SimpleNamespace(
            GetSystemTimes=FakeFunction(get_system_times)
        )
        with (
            patch.object(cpu, "load_library", return_value=kernel32),
            patch.object(cpu, "_previous_system_times", None),
            patch.object(cpu.time, "monotonic", side_effect=(0.0, 1.0)),
        ):
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertAlmostEqual(cpu.system_cpu_percent(), 50.0)

    def test_cpu_total_rejects_short_intervals_and_keeps_baseline(self) -> None:
        samples = iter(
            [
                (10, 100, 100),
                (20, 150, 120),
                (30, 200, 130),
            ]
        )

        def write_filetime(pointer, ticks: int) -> None:
            value = ctypes.cast(
                pointer,
                ctypes.POINTER(bindings.FILETIME),
            ).contents
            value.dwLowDateTime = ticks
            value.dwHighDateTime = 0

        def get_system_times(idle_pointer, kernel_pointer, user_pointer) -> int:
            idle, kernel, user = next(samples)
            for pointer, ticks in (
                (idle_pointer, idle),
                (kernel_pointer, kernel),
                (user_pointer, user),
            ):
                write_filetime(pointer, ticks)
            return 1

        kernel32 = SimpleNamespace(
            GetSystemTimes=FakeFunction(get_system_times)
        )
        with (
            patch.object(cpu, "load_library", return_value=kernel32),
            patch.object(cpu, "_previous_system_times", None),
            patch.object(cpu.time, "monotonic", side_effect=(0.0, 0.5, 1.0)),
        ):
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertIsNone(cpu.system_cpu_percent())
            self.assertAlmostEqual(
                cpu.system_cpu_percent(),
                100.0 * (130 - 20) / 130,
            )

    def test_cpu_frequency_prefers_pdh_and_labels_powrprof_fallback(self) -> None:
        powrprof = cpu.CpuFrequency(1201.0, 2803.0)
        with (
            patch.object(cpu, "_powrprof_frequency", return_value=powrprof),
            patch.object(cpu, "_pdh_frequency_sample", return_value=(1400.0, 50.0)),
        ):
            preferred = cpu.cpu_frequency()
        self.assertEqual(preferred.current_mhz, 1400.0)
        self.assertEqual(preferred.maximum_mhz, 2803.0)
        self.assertEqual(preferred.percent_of_maximum, 50.0)
        self.assertIn("PDH", preferred.current_source)

        with (
            patch.object(cpu, "_powrprof_frequency", return_value=powrprof),
            patch.object(cpu, "_pdh_frequency_sample", return_value=(None, None)),
        ):
            fallback = cpu.cpu_frequency()
        self.assertEqual(fallback.current_mhz, 1201.0)
        self.assertIn("not guaranteed live", fallback.current_source)

    def test_cpu_total_instances_are_not_reported_as_cores(self) -> None:
        self.assertTrue(cpu._is_total_processor_instance("_Total"))
        self.assertTrue(cpu._is_total_processor_instance("0,_Total"))
        self.assertFalse(cpu._is_total_processor_instance("0,7"))

    def test_system_drive_used_percentage_uses_standard_library_fields(self) -> None:
        usage = SimpleNamespace(total=100, used=25, free=75)
        with (
            patch.dict(system_info.os.environ, {"SystemDrive": "X:\\"}),
            patch.object(system_info, "disk_usage", return_value=usage),
        ):
            result = system_info._system_drive()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["used_percent"], 25.0)

    def test_processor_topology_parses_core_masks_and_cache_bytes(self) -> None:
        processor_size = (
            bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset
            + bindings.PROCESSOR_RELATIONSHIP.GroupMask.offset
            + ctypes.sizeof(bindings.GROUP_AFFINITY)
        )
        processor_buffer = ctypes.create_string_buffer(processor_size)
        processor_record = ctypes.cast(
            processor_buffer,
            ctypes.POINTER(bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX),
        ).contents
        processor_record.Relationship = bindings.RELATION_PROCESSOR_CORE
        processor_record.Size = processor_size
        processor = ctypes.cast(
            ctypes.addressof(processor_buffer)
            + bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset,
            ctypes.POINTER(bindings.PROCESSOR_RELATIONSHIP),
        ).contents
        processor.GroupCount = 1
        processor.GroupMask[0].Mask = 0b1011

        cache_size = (
            bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset
            + bindings.CACHE_RELATIONSHIP.GroupMasks.offset
            + ctypes.sizeof(bindings.GROUP_AFFINITY)
        )
        cache_buffer = ctypes.create_string_buffer(cache_size)
        cache_record = ctypes.cast(
            cache_buffer,
            ctypes.POINTER(bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX),
        ).contents
        cache_record.Relationship = bindings.RELATION_CACHE
        cache_record.Size = cache_size
        cache = ctypes.cast(
            ctypes.addressof(cache_buffer)
            + bindings.SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX.RelationshipData.offset,
            ctypes.POINTER(bindings.CACHE_RELATIONSHIP),
        ).contents
        cache.Level = 3
        cache.CacheSize = 8 * 1024**2
        cache.Type = 0
        cache.GroupCount = 1
        cache.GroupMasks[0].Mask = 0b1011

        topology = cpu._processor_topology_from_buffers(
            processor_buffer,
            processor_size,
            cache_buffer,
            cache_size,
        )

        self.assertEqual(topology.physical_core_count, 1)
        self.assertEqual(topology.logical_processor_count, 3)
        self.assertEqual(topology.caches[0].level, 3)
        self.assertEqual(topology.caches[0].size_bytes, 8 * 1024**2)

    def test_cache_sizes_are_aggregated_by_level_and_type(self) -> None:
        caches = (
            cpu.CpuCache(1, 32 * 1024, 2),
            cpu.CpuCache(1, 32 * 1024, 2),
            cpu.CpuCache(1, 32 * 1024, 1),
            cpu.CpuCache(2, 256 * 1024, 0),
            cpu.CpuCache(2, 256 * 1024, 0),
            cpu.CpuCache(3, 8 * 1024**2, 0),
        )

        self.assertEqual(
            cpu.aggregate_cache_sizes(caches),
            {
                (1, 2): 64 * 1024,
                (1, 1): 32 * 1024,
                (2, 0): 512 * 1024,
                (3, 0): 8 * 1024**2,
            },
        )
        self.assertEqual(cpu.cache_type_name(2), "data")

    def test_network_counter_wraparound_uses_unsigned_64_bit_delta(self) -> None:
        self.assertEqual(network._counter_delta(10, (1 << 64) - 11), 21)

    def test_filter_interface_is_not_a_real_adapter(self) -> None:
        filter_row = bindings.MIB_IF_ROW2()
        filter_row.InterfaceIndex = 1
        filter_row.Type = 6
        filter_row.InterfaceAndOperStatusFlags.HardwareInterface = 1
        filter_row.InterfaceAndOperStatusFlags.FilterInterface = 1
        physical_row = bindings.MIB_IF_ROW2()
        physical_row.InterfaceIndex = 2
        physical_row.Type = 6
        physical_row.InterfaceAndOperStatusFlags.HardwareInterface = 1

        with (
            patch.object(network, "load_library", return_value=object()),
            patch.object(
                network,
                "_interface_table",
                return_value=[filter_row, physical_row],
            ),
            patch.object(network, "_adapter_addresses", return_value={}),
        ):
            interfaces = network.network_interfaces()

        self.assertIsNotNone(interfaces)
        assert interfaces is not None
        self.assertEqual([item.interface_index for item in interfaces], [2])

    def test_network_rates_require_two_real_samples_and_handle_wraparound(self) -> None:
        first = network.NetworkInterface(
            4, "Wi-Fi", "Wi-Fi", 6, True, 1, True, 1_000_000, 1_000_000,
            (1 << 64) - 11, (1 << 64) - 21, None, (),
        )
        second = network.NetworkInterface(
            4, "Wi-Fi", "Wi-Fi", 6, True, 1, True, 1_000_000, 1_000_000,
            10, 5, None, (),
        )
        with (
            patch.object(network, "_previous_counters", {}),
            patch.object(network, "network_interfaces", side_effect=[[first], [second]]),
            patch.object(network.time, "monotonic", side_effect=[1.0, 2.0]),
        ):
            initial = network.network_rate_sample()
            measured = network.network_rate_sample()

        self.assertIsNone(initial.received_bytes_per_second)
        self.assertEqual(initial.state, "no data yet")
        self.assertEqual(measured.received_bytes_per_second, 21.0)
        self.assertEqual(measured.sent_bytes_per_second, 26.0)

    def test_network_rate_rejects_short_interval_without_moving_baseline(self) -> None:
        samples = [
            network.NetworkInterface(
                4, "Wi-Fi", "Wi-Fi", 6, True, 1, True, 1_000_000, 1_000_000,
                100, 200, None, (),
            ),
            network.NetworkInterface(
                4, "Wi-Fi", "Wi-Fi", 6, True, 1, True, 1_000_000, 1_000_000,
                150, 300, None, (),
            ),
            network.NetworkInterface(
                4, "Wi-Fi", "Wi-Fi", 6, True, 1, True, 1_000_000, 1_000_000,
                210, 420, None, (),
            ),
        ]
        with (
            patch.object(network, "_previous_counters", {}),
            patch.object(network, "network_interfaces", side_effect=[[item] for item in samples]),
            patch.object(network.time, "monotonic", side_effect=(1.0, 1.5, 2.1)),
        ):
            initial = network.network_rate_sample()
            short = network.network_rate_sample()
            measured = network.network_rate_sample()

        self.assertEqual(initial.state, "no data yet")
        self.assertEqual(short.state, "no data yet")
        self.assertAlmostEqual(measured.received_bytes_per_second or 0, 100.0)
        self.assertAlmostEqual(measured.sent_bytes_per_second or 0, 200.0)

    def test_network_rates_do_not_count_duplicate_interface_rows_twice(self) -> None:
        sample = network.NetworkInterface(
            4, "Ethernet", "Ethernet", 6, True, 1, True, 1_000_000, 1_000_000,
            100, 200, None, (),
        )
        updated = network.NetworkInterface(
            4, "Ethernet", "Ethernet", 6, True, 1, True, 1_000_000, 1_000_000,
            200, 400, None, (),
        )
        with (
            patch.object(network, "_previous_counters", {}),
            patch.object(
                network,
                "network_interfaces",
                side_effect=[[sample], [updated, updated]],
            ),
            patch.object(network.time, "monotonic", side_effect=[1.0, 2.0]),
        ):
            network.network_rate_sample()
            measured = network.network_rate_sample()

        self.assertEqual(measured.received_bytes_per_second, 100.0)
        self.assertEqual(measured.sent_bytes_per_second, 200.0)

    def test_pdh_status_mapping_distinguishes_no_data_missing_and_valid(self) -> None:
        self.assertEqual(pdh._counter_state(0), "valid")
        self.assertEqual(pdh._counter_state(0x800007D3), "no data yet")
        self.assertEqual(pdh._counter_state(0xC0000BB9), "object or counter does not exist")
        self.assertEqual(pdh._counter_state(0x80000001), "other error")

    def test_pdh_rate_samples_are_at_least_one_second_apart(self) -> None:
        calls = 0

        def collect(_query) -> int:
            nonlocal calls
            calls += 1
            return 0

        session = object.__new__(pdh.PdhSession)
        session._closed = False
        session._lock = threading.RLock()
        session._query = ctypes.c_void_p(1)
        session._sample_count = 0
        session._last_collect_at = None
        session._library = SimpleNamespace(
            PdhCollectQueryData=FakeFunction(collect)
        )
        with patch.object(pdh.time, "monotonic", side_effect=(0.0, 0.5, 1.0)):
            self.assertFalse(session.collect())
            self.assertFalse(session.collect())
            self.assertTrue(session.collect())

        self.assertEqual(calls, 2)

    def test_pdh_counter_sample_reports_missing_counter_without_zero(self) -> None:
        def missing_counter(_counter, _format, _type_pointer, value_pointer) -> int:
            value = ctypes.cast(
                value_pointer,
                ctypes.POINTER(bindings.PDH_FMT_COUNTERVALUE),
            ).contents
            value.CStatus = 0xC0000BB9
            return 0

        session = object.__new__(pdh.PdhSession)
        session._lock = threading.RLock()
        session._closed = False
        session._sample_count = 2
        session._library = SimpleNamespace(
            PdhGetFormattedCounterValue=FakeFunction(missing_counter)
        )
        session._counter_explanations = {}

        sample = session.counter_sample(ctypes.c_void_p(1))

        self.assertIsNone(sample.value)
        self.assertEqual(sample.state, "object or counter does not exist")

    def test_pdh_counter_sample_keeps_first_read_null_then_returns_measured_value(self) -> None:
        calls = 0

        def valid_counter(_counter, _format, _type_pointer, value_pointer) -> int:
            nonlocal calls
            calls += 1
            value = ctypes.cast(
                value_pointer,
                ctypes.POINTER(bindings.PDH_FMT_COUNTERVALUE),
            ).contents
            value.CStatus = 0
            value.value.doubleValue = 42.5
            return 0

        session = object.__new__(pdh.PdhSession)
        session._lock = threading.RLock()
        session._closed = False
        session._sample_count = 1
        session._library = SimpleNamespace(
            PdhGetFormattedCounterValue=FakeFunction(valid_counter)
        )
        session._counter_explanations = {}

        initial = session.counter_sample(ctypes.c_void_p(1))
        session._sample_count = 2
        measured = session.counter_sample(ctypes.c_void_p(1))

        self.assertIsNone(initial.value)
        self.assertEqual(initial.state, "no data yet")
        self.assertEqual(measured.value, 42.5)
        self.assertEqual(measured.state, "valid")
        self.assertEqual(calls, 1)

    def test_self_process_cpu_requires_two_samples_and_memory_is_measured(self) -> None:
        cpu_samples = iter([(0, 0), (0, 10_000_000)])

        def get_process_times(
            _process,
            _creation,
            _exit_time,
            kernel_pointer,
            user_pointer,
        ) -> int:
            kernel_ticks, user_ticks = next(cpu_samples)
            for pointer, ticks in (
                (kernel_pointer, kernel_ticks),
                (user_pointer, user_ticks),
            ):
                value = ctypes.cast(
                    pointer,
                    ctypes.POINTER(bindings.FILETIME),
                ).contents
                value.dwLowDateTime = ticks & 0xFFFFFFFF
                value.dwHighDateTime = ticks >> 32
            return 1

        def get_memory(_process, counters_pointer, _size) -> int:
            counters = ctypes.cast(
                counters_pointer,
                ctypes.POINTER(bindings.PROCESS_MEMORY_COUNTERS_EX),
            ).contents
            counters.WorkingSetSize = 4096
            return 1

        kernel32 = SimpleNamespace(
            GetCurrentProcess=FakeFunction(lambda: ctypes.c_void_p(1)),
            GetProcessTimes=FakeFunction(get_process_times),
            K32GetProcessMemoryInfo=FakeFunction(get_memory),
        )
        with (
            patch.object(self_process, "load_library", return_value=kernel32),
            patch.object(self_process, "_previous_cpu_sample", None),
            patch.object(self_process.time, "monotonic", side_effect=[10.0, 11.0]),
        ):
            first = self_process.current_process_metrics()
            second = self_process.current_process_metrics()

        self.assertIsNone(first.cpu_percent)
        self.assertEqual(first.working_set_bytes, 4096)
        self.assertEqual(second.cpu_percent, 100.0)
        self.assertEqual(second.working_set_bytes, 4096)

    def test_process_access_denied_returns_null_metrics(self) -> None:
        kernel32 = SimpleNamespace(
            OpenProcess=FakeFunction(lambda *_args: None)
        )
        with patch.object(processes, "windows_last_error", return_value=5):
            sample = processes._read_process_metrics(
                kernel32,
                123,
                "protected.exe",
                1.0,
            )

        self.assertIsNone(sample.cpu_percent)
        self.assertIsNone(sample.working_set_bytes)
        self.assertEqual(sample.cpu_state, "access denied")
        self.assertEqual(sample.memory_state, "access denied")

    def test_new_process_cpu_sample_is_no_data_yet(self) -> None:
        kernel32 = SimpleNamespace(
            OpenProcess=FakeFunction(lambda *_args: ctypes.c_void_p(1)),
            CloseHandle=FakeFunction(lambda *_args: 1),
        )
        with (
            patch.object(processes, "_previous_cpu_samples", {}),
            patch.object(processes, "_process_cpu_ticks", return_value=10_000_000),
            patch.object(processes, "_working_set_bytes", return_value=4096),
            patch.object(processes.os, "cpu_count", return_value=4),
        ):
            sample = processes._read_process_metrics(
                kernel32,
                123,
                "new.exe",
                1.0,
                4,
            )

        self.assertIsNone(sample.cpu_percent)
        self.assertEqual(sample.cpu_state, "no data yet")

    def test_process_cpu_is_normalized_and_capped_at_total_cpu(self) -> None:
        kernel32 = SimpleNamespace(
            OpenProcess=FakeFunction(lambda *_args: ctypes.c_void_p(1)),
            CloseHandle=FakeFunction(lambda *_args: 1),
        )
        with (
            patch.object(processes, "_previous_cpu_samples", {}),
            patch.object(
                processes,
                "_process_cpu_ticks",
                side_effect=[0, 20_000_000, 70_000_000],
            ),
            patch.object(processes, "_working_set_bytes", return_value=4096),
        ):
            processes._read_process_metrics(kernel32, 123, "cpu.exe", 1.0, 4)
            normalized = processes._read_process_metrics(
                kernel32, 123, "cpu.exe", 2.0, 4
            )
            capped = processes._read_process_metrics(
                kernel32, 123, "cpu.exe", 3.0, 4
            )

        self.assertEqual(normalized.cpu_percent, 50.0)
        self.assertEqual(normalized.cpu_state, "valid")
        self.assertEqual(capped.cpu_percent, 100.0)
        self.assertEqual(capped.cpu_state, "valid")

    def test_battery_remaining_wording_distinguishes_ac_from_unknown(self) -> None:
        self.assertEqual(
            selftest._battery_remaining_text(
                SimpleNamespace(power_plugged=True, seconds_left=None)
            ),
            "not applicable (plugged in)",
        )
        self.assertEqual(
            selftest._battery_remaining_text(
                SimpleNamespace(power_plugged=False, seconds_left=None)
            ),
            "unavailable (Windows reports unknown)",
        )
        self.assertEqual(
            selftest._battery_remaining_text(
                SimpleNamespace(power_plugged=False, seconds_left=300)
            ),
            "300 seconds",
        )


if __name__ == "__main__":
    unittest.main()
