"""Standard-library tests for native Windows sensor providers."""
from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.monitoring.models import SensorState
from backend.providers.battery_provider import BatteryProvider
from backend.providers.pdh_provider import (
    PdhProvider,
    _CounterBinding,
    _CounterDefinition,
)
from backend.providers.storage_temperature_provider import StorageTemperatureProvider
from backend.providers.windows_counter_provider import WindowsCounterProvider
from backend.providers.windows_provider import WindowsProvider
from backend.providers.wmi_thermal_provider import WmiThermalProvider
from backend.winapi import bindings
from backend.winapi.battery import BatteryDeviceReading
from backend.winapi.cpu import CpuFrequency
from backend.winapi.pdh import PdhCounterSample, PdhStatusError


def _battery_reading(
    *,
    design: int = 50_000,
    full: int = 40_000,
    cycle_count: int = 200,
    power_state: int | None = 2,
    capacity: int | None = 20_000,
    voltage: int | None = 11_500,
    rate: int | None = -10_000,
    capabilities: int = 0,
    status_error: OSError | None = None,
) -> BatteryDeviceReading:
    return BatteryDeviceReading(
        path="battery-device",
        capabilities=capabilities,
        design_capacity=design,
        full_charge_capacity=full,
        cycle_count=cycle_count,
        power_state=power_state,
        capacity=capacity,
        voltage_mv=voltage,
        rate=rate,
        status_error=status_error,
    )


class NativeWindowsBindingsTests(unittest.TestCase):
    """Check layouts and IOCTL/property values against Microsoft Learn pages."""

    def test_battery_and_storage_structures_match_documented_layouts(self) -> None:
        self.assertEqual(ctypes.sizeof(bindings.BATTERY_INFORMATION), 36)
        self.assertEqual(ctypes.sizeof(bindings.BATTERY_QUERY_INFORMATION), 12)
        self.assertEqual(ctypes.sizeof(bindings.BATTERY_WAIT_STATUS), 20)
        self.assertEqual(ctypes.sizeof(bindings.BATTERY_STATUS), 16)
        self.assertEqual(ctypes.sizeof(bindings.STORAGE_PROPERTY_QUERY), 12)
        self.assertEqual(ctypes.sizeof(bindings.STORAGE_DEVICE_NUMBER), 12)
        self.assertEqual(ctypes.sizeof(bindings.STORAGE_TEMPERATURE_INFO), 16)
        self.assertEqual(ctypes.sizeof(bindings.STORAGE_TEMPERATURE_DATA_DESCRIPTOR), 40)

    def test_documented_storage_and_battery_ioctl_values(self) -> None:
        self.assertEqual(bindings.IOCTL_BATTERY_QUERY_TAG, 0x00294040)
        self.assertEqual(bindings.IOCTL_BATTERY_QUERY_INFORMATION, 0x00294044)
        self.assertEqual(bindings.IOCTL_BATTERY_QUERY_STATUS, 0x0029404C)
        self.assertEqual(bindings.IOCTL_STORAGE_GET_DEVICE_NUMBER, 0x002D1080)
        self.assertEqual(bindings.IOCTL_STORAGE_QUERY_PROPERTY, 0x002D1400)
        self.assertEqual(bindings.STORAGE_DEVICE_TEMPERATURE_PROPERTY, 52)


class BatteryProviderTests(unittest.TestCase):
    """Verify battery sentinels, relative units, wear, and access restrictions."""

    def test_unknown_battery_values_are_unavailable(self) -> None:
        reading = _battery_reading(
            design=bindings.BATTERY_UNKNOWN_CAPACITY,
            full=bindings.BATTERY_UNKNOWN_CAPACITY,
            cycle_count=0,
            capacity=bindings.BATTERY_UNKNOWN_CAPACITY,
            voltage=bindings.BATTERY_UNKNOWN_VOLTAGE,
            rate=bindings.BATTERY_UNKNOWN_RATE,
        )
        provider = BatteryProvider()
        with (
            patch("backend.providers.battery_provider.platform.system", return_value="Windows"),
            patch("backend.providers.battery_provider.battery_device_paths", return_value=["battery-device"]),
            patch("backend.providers.battery_provider.read_battery_device", return_value=reading),
        ):
            provider.discover()

        states = provider.sensor_states()
        self.assertEqual(
            states["battery.device.0.design_capacity"][0],
            SensorState.UNAVAILABLE,
        )
        self.assertEqual(
            states["battery.device.0.voltage"][0],
            SensorState.UNAVAILABLE,
        )
        self.assertEqual(
            states["battery.device.0.rate"][0],
            SensorState.UNAVAILABLE,
        )
        self.assertEqual(
            states["battery.device.0.cycle_count"][0],
            SensorState.UNSUPPORTED,
        )
        self.assertEqual(
            states["battery.device.0.wear"][0],
            SensorState.UNAVAILABLE,
        )

    def test_wear_is_calculated_only_from_two_valid_capacities(self) -> None:
        provider = BatteryProvider()
        with (
            patch("backend.providers.battery_provider.platform.system", return_value="Windows"),
            patch("backend.providers.battery_provider.battery_device_paths", return_value=["battery-device"]),
            patch(
                "backend.providers.battery_provider.read_battery_device",
                return_value=_battery_reading(),
            ),
        ):
            provider.discover()
            values = provider.read()
            self.assertAlmostEqual(values["battery.device.0.wear"], 20.0)
            self.assertAlmostEqual(values["battery.device.0.capacity"], 50.0)
            self.assertEqual(
                next(
                    sensor.reliability
                    for sensor in provider.discover()
                    if sensor.id == "battery.device.0.wear"
                ),
                "calculated-estimate",
            )

        with (
            patch("backend.providers.battery_provider.platform.system", return_value="Windows"),
            patch("backend.providers.battery_provider.battery_device_paths", return_value=["battery-device"]),
            patch(
                "backend.providers.battery_provider.read_battery_device",
                return_value=_battery_reading(design=bindings.BATTERY_UNKNOWN_CAPACITY),
            ),
        ):
            provider.read()
            self.assertIsNone(provider.read()["battery.device.0.wear"])
        self.assertEqual(
            provider.sensor_states()["battery.device.0.wear"][0],
            SensorState.UNAVAILABLE,
        )

    def test_access_denied_is_permission_restricted(self) -> None:
        provider = BatteryProvider()
        with (
            patch("backend.providers.battery_provider.platform.system", return_value="Windows"),
            patch("backend.providers.battery_provider.battery_device_paths", return_value=["battery-device"]),
            patch(
                "backend.providers.battery_provider.read_battery_device",
                side_effect=OSError(bindings.ERROR_ACCESS_DENIED, "Access denied"),
            ),
        ):
            provider.discover()

        self.assertEqual(
            provider.sensor_states()["battery.device.0.voltage"][0],
            SensorState.PERMISSION_RESTRICTED,
        )
        self.assertEqual(
            provider.diagnostics()["battery.device.0.voltage"][1],
            str(bindings.ERROR_ACCESS_DENIED),
        )

    def test_relative_battery_units_are_not_labeled_physical(self) -> None:
        provider = BatteryProvider()
        reading = _battery_reading(
            design=100,
            full=80,
            capacity=40,
            capabilities=bindings.BATTERY_CAPACITY_RELATIVE,
        )
        with (
            patch("backend.providers.battery_provider.platform.system", return_value="Windows"),
            patch("backend.providers.battery_provider.battery_device_paths", return_value=["battery-device"]),
            patch("backend.providers.battery_provider.read_battery_device", return_value=reading),
        ):
            sensors = provider.discover()

        units = {sensor.id: sensor.unit for sensor in sensors}
        self.assertEqual(units["battery.device.0.design_capacity"], "relative units")
        self.assertEqual(units["battery.device.0.rate"], "relative units/hour")


class WindowsProviderTests(unittest.TestCase):
    """Keep unknown system battery-time estimates null with an explicit reason."""

    def test_unknown_battery_lifetime_has_api_path_and_null_state(self) -> None:
        battery = SimpleNamespace(
            percent=64.0,
            power_plugged=False,
            seconds_left=None,
        )
        provider = WindowsProvider()
        with (
            patch("backend.providers.windows_provider.active_processor_count", return_value=None),
            patch("backend.providers.windows_provider.get_battery_status", return_value=battery),
            patch("backend.providers.windows_provider.system_cpu_percent", return_value=None),
            patch("backend.providers.windows_provider.per_core_cpu_percent", return_value=None),
            patch("backend.providers.windows_provider.get_memory_status", return_value=None),
            patch(
                "backend.providers.windows_provider.cpu_frequency",
                return_value=CpuFrequency(None, None),
            ),
        ):
            provider.discover()
            values = provider.read()

        self.assertIsNone(values["battery.seconds_left"])
        state, reason = provider.sensor_states()["battery.seconds_left"]
        self.assertEqual(state, SensorState.UNAVAILABLE)
        self.assertIn("BatteryLifeTime", reason or "")
        self.assertIn("GetSystemPowerStatus", provider.diagnostics()["battery.seconds_left"][0] or "")


class StorageTemperatureProviderTests(unittest.TestCase):
    """Verify access errors are visible and do not become drive-health claims."""

    def test_access_denied_is_permission_restricted(self) -> None:
        provider = StorageTemperatureProvider()
        with (
            patch(
                "backend.providers.storage_temperature_provider.platform.system",
                return_value="Windows",
            ),
            patch(
                "backend.providers.storage_temperature_provider.disk_device_paths",
                return_value=["disk-interface"],
            ),
            patch(
                "backend.providers.storage_temperature_provider.get_storage_device_number",
                return_value=2,
            ),
            patch(
                "backend.providers.storage_temperature_provider.query_physical_drive_temperatures",
                side_effect=OSError(bindings.ERROR_ACCESS_DENIED, "Access denied"),
            ),
        ):
            provider.discover()

        sensor_id = "storage.temperature.disk2.sensor0"
        self.assertEqual(provider.sensor_states()[sensor_id][0], SensorState.PERMISSION_RESTRICTED)
        self.assertEqual(provider.diagnostics()[sensor_id][1], "5")
        self.assertNotIn("health", provider.sensor_states()[sensor_id][1] or "")


class PdhProviderTests(unittest.TestCase):
    """Check PDH missing/no-data states, conservative units, and fan behavior."""

    def test_counter_explanation_failure_becomes_unavailable_sensor(self) -> None:
        class ExplanationFailureSession:
            def add_expanded_counter(self, _path: str) -> list[tuple[str, object]]:
                return [("_TZ.THM0", object())]

            def counter_explanation(self, _handle: object) -> str:
                raise OSError(5, "Access denied")

        provider = PdhProvider()
        provider._session = ExplanationFailureSession()
        definition = _CounterDefinition(
            r"\Thermal Zone Information(*)\Temperature",
            "thermal",
            "ACPI thermal-zone temperature",
            "temperature",
        )
        provider._discover_counter(definition)
        sensor = provider._sensors[0]
        self.assertEqual(sensor.state, SensorState.UNAVAILABLE)
        self.assertIn("counter explanation", sensor.error_message or "")
        self.assertEqual(provider.diagnostics()[sensor.id][1], "5")

    def test_missing_counter_is_unsupported(self) -> None:
        class MissingCounterSession:
            def add_expanded_counter(self, _path: str) -> list[tuple[str, object]]:
                raise PdhStatusError(0xC0000BB9, "PdhAddEnglishCounterW")

            def add_english_counter(self, _path: str) -> object:
                raise PdhStatusError(0xC0000BB9, "PdhAddEnglishCounterW")

            def close(self) -> None:
                return

        provider = PdhProvider()
        with (
            patch("backend.providers.pdh_provider.platform.system", return_value="Windows"),
            patch("backend.providers.pdh_provider.PdhSession", return_value=MissingCounterSession()),
        ):
            sensors = provider.discover()

        missing = next(
            sensor for sensor in sensors
            if sensor.name == "ACPI thermal-zone temperature"
        )
        self.assertEqual(missing.state, SensorState.UNSUPPORTED)
        self.assertIn("Thermal Zone Information", missing.error_message or "")

    def test_gpu_memory_explanation_without_units_names_the_query(self) -> None:
        class MemoryCounterSession:
            def add_expanded_counter(self, _path: str) -> list[tuple[str, object]]:
                return [("luid_0x0000", object())]

            def counter_explanation(self, _handle: object) -> str:
                return "Shared memory used by the adapter"

        provider = PdhProvider()
        provider._session = MemoryCounterSession()
        definition = _CounterDefinition(
            r"\GPU Adapter Memory(*)\Shared Usage",
            "gpu",
            "GPU adapter shared memory usage",
            "gpu-memory",
        )
        provider._discover_counter(definition)

        sensor = provider._sensors[0]
        self.assertEqual(sensor.state, SensorState.UNAVAILABLE)
        self.assertIn("PdhGetCounterInfoW succeeded", sensor.error_message or "")
        self.assertIn("PdhGetCounterInfoW", provider.diagnostics()[sensor.id][0] or "")
        self.assertIsNone(provider.diagnostics()[sensor.id][1])

    def test_no_data_yet_and_valid_temperature_are_distinct(self) -> None:
        provider = PdhProvider()
        binding = _CounterBinding(
            "thermal.temperature.zone",
            object(),
            r"\Thermal Zone Information(*)\Temperature",
            r"\_TZ.THM0",
            "temperature",
            "Temperature of the thermal zone, in degrees Kelvin.",
        )
        no_data = provider._convert_sample(
            binding,
            PdhCounterSample(None, "no data yet"),
        )
        self.assertIsNone(no_data[0])
        self.assertEqual(no_data[1], SensorState.NOT_INITIALIZED)
        self.assertIn("no data yet", no_data[2] or "")

        value = provider._convert_sample(binding, PdhCounterSample(353.15, "valid"))
        self.assertAlmostEqual(value[0], 80.0)
        self.assertIsNone(value[1])

    def test_zero_from_unmeasured_meter_is_unavailable(self) -> None:
        provider = PdhProvider()
        binding = _CounterBinding(
            "power.energy-meter-total",
            object(),
            r"\Energy Meter(*)\Power",
            "_Total",
            "power",
            "The Power counter measures average power over each sampling interval in milliwatts.",
            zero_may_be_unmeasured=True,
        )
        value, state, message = provider._convert_sample(
            binding,
            PdhCounterSample(0.0, "valid"),
        )
        self.assertIsNone(value)
        self.assertEqual(state, SensorState.UNAVAILABLE)
        self.assertEqual(
            message,
            "reported 0; this meter may not be measured on this hardware",
        )

    def test_throttling_estimate_lists_missing_native_inputs(self) -> None:
        provider = PdhProvider()
        with patch("backend.providers.pdh_provider.system_cpu_percent", return_value=None):
            provider._update_throttling_estimate({})

        state, message = provider.sensor_states()["cpu.throttling.estimated"]
        self.assertEqual(state, SensorState.UNAVAILABLE)
        self.assertIn("GetSystemTimes", message or "")
        self.assertIn(r"\Processor Information(_Total)\% of Maximum Frequency", message or "")
        self.assertIn(r"\Thermal Zone Information(*)\Throttle Reasons", message or "")

    def test_fan_is_unsupported_without_launching_a_process(self) -> None:
        class MissingCounterSession:
            def add_expanded_counter(self, _path: str) -> list[tuple[str, object]]:
                raise PdhStatusError(0xC0000BB9, "PdhAddEnglishCounterW")

            def add_english_counter(self, _path: str) -> object:
                raise PdhStatusError(0xC0000BB9, "PdhAddEnglishCounterW")

            def close(self) -> None:
                return

        provider = PdhProvider()
        with (
            patch("backend.providers.pdh_provider.platform.system", return_value="Windows"),
            patch("backend.providers.pdh_provider.PdhSession", return_value=MissingCounterSession()),
            patch("subprocess.run") as run,
        ):
            sensors = provider.discover()
            self.assertFalse(run.called)

        fan = next(sensor for sensor in sensors if sensor.id == "fan.rpm")
        self.assertEqual(fan.state, SensorState.UNSUPPORTED)
        self.assertEqual(
            fan.error_message,
            "Windows does not expose fan speed on this computer",
        )

    def test_wmi_provider_does_not_launch_when_external_programs_disabled(self) -> None:
        provider = WmiThermalProvider()
        with (
            patch("backend.providers.wmi_thermal_provider.platform.system", return_value="Windows"),
            patch("backend.core.config.load_settings") as load_settings,
            patch("backend.providers.wmi_thermal_provider.subprocess.run") as run,
            patch("backend.providers.wmi_thermal_provider.shutil.which") as which,
        ):
            load_settings.return_value.allow_external_programs = False
            sensors = provider.discover()
        self.assertFalse(run.called)
        self.assertFalse(which.called)
        self.assertEqual(sensors[0].state, SensorState.UNSUPPORTED)

    def test_windows_counter_empty_results_keep_api_paths_and_reasons(self) -> None:
        provider = WindowsCounterProvider()
        with (
            patch(
                "backend.providers.windows_counter_provider.platform.system",
                return_value="Windows",
            ),
            patch(
                "backend.providers.windows_counter_provider.get_live_windows_sensors",
                return_value={"sampled_at": "2026-11-10T08:00:00+07:00"},
            ),
        ):
            provider.discover()
            provider.read()

        states = provider.sensor_states()
        diagnostics = provider.diagnostics()
        for sensor_id in ("battery.voltage", "gpu.3d.utilization"):
            self.assertEqual(states[sensor_id][0], SensorState.UNAVAILABLE)
            self.assertIn("empty result", states[sensor_id][1] or "")
            self.assertIsNone(diagnostics[sensor_id][1])
            self.assertIn("Get-CimInstance", diagnostics[sensor_id][0] or "")


if __name__ == "__main__":
    unittest.main()
