"""Read per-battery capacity, rate, voltage, and power state through Win32."""
from __future__ import annotations

import platform
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider
from backend.winapi.battery import (
    BatteryDeviceReading,
    battery_device_paths,
    read_battery_device,
)
from backend.winapi.bindings import (
    BATTERY_UNKNOWN_CAPACITY,
    BATTERY_UNKNOWN_RATE,
    BATTERY_UNKNOWN_VOLTAGE,
    ERROR_ACCESS_DENIED,
    ERROR_DEVICE_NOT_CONNECTED,
    ERROR_FILE_NOT_FOUND,
    ERROR_INVALID_FUNCTION,
    ERROR_NOT_SUPPORTED,
)

_FIELD_NAMES = {
    "design_capacity": "design capacity",
    "full_charge_capacity": "full-charge capacity",
    "cycle_count": "cycle count",
    "voltage": "voltage",
    "rate": "charge/discharge rate",
    "charge_rate": "charge rate",
    "discharge_rate": "discharge rate",
    "capacity": "current capacity",
    "power_state": "power-state flags",
    "wear": "wear estimate",
}


def _failure_state(error: OSError) -> tuple[SensorState, str]:
    """Map known Win32 errors to explicit sensor states and retain their code."""
    code = error.errno
    message = f"Windows error {code}: {error.strerror or error}"
    if code == ERROR_ACCESS_DENIED:
        return SensorState.PERMISSION_RESTRICTED, message
    if code in (
        ERROR_DEVICE_NOT_CONNECTED,
        ERROR_FILE_NOT_FOUND,
        ERROR_INVALID_FUNCTION,
        ERROR_NOT_SUPPORTED,
    ):
        return SensorState.UNSUPPORTED, message
    return SensorState.UNAVAILABLE, message


class BatteryProvider(SensorProvider):
    """Expose native battery-class IOCTL readings without modifying device state."""

    name = "battery-native"
    poll_interval_category = "battery"

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._sensors: list[Sensor] = []
        self._values: dict[str, float | None] = {}
        self._states: dict[str, tuple[SensorState, str | None]] = {}
        self._diagnostics: dict[str, tuple[str | None, str | None]] = {}
        self._last_readings: list[BatteryDeviceReading | None] = []

    def discover(self) -> list[Sensor]:
        """Find battery interfaces, probe each once, and register all field rows."""
        self._paths = []
        self._last_readings = []
        self._states = {}
        self._diagnostics = {}
        self._values = {}
        discovery_error: OSError | None = None
        if platform.system() == "Windows":
            try:
                self._paths = battery_device_paths()
            except OSError as error:
                discovery_error = error

        count = max(1, len(self._paths))
        self._sensors = [
            sensor
            for index in range(count)
            for sensor in self._field_sensors(index, None)
        ]
        self._values = {sensor.id: None for sensor in self._sensors}
        if discovery_error is not None:
            self._set_device_error(0, discovery_error, "SetupAPI battery enumeration")
        elif not self._paths:
            self._set_device_state(
                0,
                SensorState.UNSUPPORTED,
                "No battery device interface is present",
                "SetupAPI battery device-interface enumeration",
            )
        else:
            self._refresh()
        return list(self._sensors)

    def read(self) -> dict[str, float | None]:
        """Refresh all discovered battery interfaces and keep unavailable values null."""
        if not self._sensors:
            self.discover()
        elif self._paths:
            self._refresh()
        return dict(self._values)

    def sensor_states(self) -> dict[str, tuple[SensorState, str | None]]:
        return dict(self._states)

    def diagnostics(self) -> dict[str, tuple[str | None, str | None]]:
        return dict(self._diagnostics)

    def capabilities(self) -> dict[str, Any]:
        """Report whether a battery interface was enumerated."""
        available = any(reading is not None for reading in self._last_readings)
        return {
            "name": self.name,
            "state": "available" if available else "unsupported",
            "battery_count": sum(
                reading is not None for reading in self._last_readings
            ),
        }

    def _field_sensors(
        self,
        index: int,
        reading: BatteryDeviceReading | None,
    ) -> list[Sensor]:
        """Build one sensor row for each battery field, including unsupported ones."""
        relative = reading.relative_units if reading is not None else False
        capacity_unit = "relative units" if relative else "mWh"
        rate_unit = "relative units/hour" if relative else "mW"
        units = {
            "design_capacity": capacity_unit,
            "full_charge_capacity": capacity_unit,
            "cycle_count": "cycles",
            "voltage": "mV",
            "rate": rate_unit,
            "charge_rate": rate_unit,
            "discharge_rate": rate_unit,
            "capacity": "%",
            "power_state": "flags",
            "wear": "%",
        }
        return [
            Sensor(
                id=f"battery.device.{index}.{field}",
                name=f"Battery {index + 1} {label}",
                category="battery",
                unit=units[field],
                source="battery-ioctl",
                reliability=(
                    "calculated-estimate"
                    if field in ("capacity", "wear")
                    else "reported-by-windows"
                ),
                state=SensorState.NOT_INITIALIZED,
            )
            for field, label in _FIELD_NAMES.items()
        ]

    def _refresh(self) -> None:
        """Read each previously enumerated interface without raising to the scheduler."""
        self._values = {sensor.id: None for sensor in self._sensors}
        self._states = {}
        self._diagnostics = {}
        self._last_readings = []
        for index, path in enumerate(self._paths):
            try:
                reading = read_battery_device(path)
            except OSError as error:
                self._last_readings.append(None)
                self._set_device_error(index, error, "DeviceIoControl battery query")
                continue
            self._last_readings.append(reading)
            if reading is None:
                self._set_device_state(
                    index,
                    SensorState.UNSUPPORTED,
                    "No battery is installed in this battery slot",
                    "IOCTL_BATTERY_QUERY_TAG",
                )
                continue
            self._update_reading(index, reading)

    def _update_reading(self, index: int, reading: BatteryDeviceReading) -> None:
        """Map documented sentinels and valid raw values into sensor fields."""
        prefix = f"battery.device.{index}."
        unit = "relative units" if reading.relative_units else "mWh"
        rate_unit = "relative units/hour" if reading.relative_units else "mW"
        for sensor in self._sensors:
            if sensor.id.startswith(prefix):
                field = sensor.id.rsplit(".", 1)[-1]
                if field in ("design_capacity", "full_charge_capacity"):
                    sensor.unit = unit
                elif field in ("rate", "charge_rate", "discharge_rate"):
                    sensor.unit = rate_unit
        self._diagnostics.update(
            {
                f"{prefix}{field}": (
                    "DeviceIoControl(IOCTL_BATTERY_QUERY_INFORMATION or "
                    "IOCTL_BATTERY_QUERY_STATUS)",
                    None,
                )
                for field in _FIELD_NAMES
            }
        )
        self._record_value(
            prefix + "design_capacity",
            reading.design_capacity,
            BATTERY_UNKNOWN_CAPACITY,
            "BATTERY_UNKNOWN_CAPACITY",
        )
        self._record_value(
            prefix + "full_charge_capacity",
            reading.full_charge_capacity,
            BATTERY_UNKNOWN_CAPACITY,
            "BATTERY_UNKNOWN_CAPACITY",
        )
        if reading.cycle_count == 0:
            self._set_state(
                prefix + "cycle_count",
                SensorState.UNSUPPORTED,
                "Battery does not support a cycle counter (CycleCount is 0)",
            )
        else:
            self._values[prefix + "cycle_count"] = float(reading.cycle_count)

        if reading.status_error is not None:
            state, message = _failure_state(reading.status_error)
            for field in (
                "voltage",
                "rate",
                "charge_rate",
                "discharge_rate",
                "capacity",
                "power_state",
            ):
                self._set_state(prefix + field, state, message)
                self._diagnostics[prefix + field] = (
                    "DeviceIoControl(IOCTL_BATTERY_QUERY_STATUS)",
                    str(reading.status_error.errno),
                )
        else:
            self._record_value(
                prefix + "voltage",
                reading.voltage_mv,
                BATTERY_UNKNOWN_VOLTAGE,
                "BATTERY_UNKNOWN_VOLTAGE",
            )
            self._record_value(
                prefix + "rate",
                reading.rate,
                BATTERY_UNKNOWN_RATE,
                "BATTERY_UNKNOWN_RATE",
            )
            if reading.rate is not None and reading.rate != BATTERY_UNKNOWN_RATE:
                self._values[prefix + "charge_rate"] = float(
                    max(0, reading.rate)
                )
                self._values[prefix + "discharge_rate"] = float(
                    max(0, -reading.rate)
                )
            else:
                message = "Windows reported BATTERY_UNKNOWN_RATE"
                for field in ("charge_rate", "discharge_rate"):
                    self._set_state(
                        prefix + field,
                        SensorState.UNAVAILABLE,
                        message,
                    )
            self._record_value(
                prefix + "capacity",
                reading.capacity,
                BATTERY_UNKNOWN_CAPACITY,
                "BATTERY_UNKNOWN_CAPACITY",
            )
            if reading.power_state is None:
                self._set_state(
                    prefix + "power_state",
                    SensorState.UNAVAILABLE,
                    "Battery status did not include power-state flags",
                )
            else:
                self._values[prefix + "power_state"] = float(reading.power_state)

        design = self._valid_capacity(reading.design_capacity)
        full = self._valid_capacity(reading.full_charge_capacity)
        if design is None or full is None:
            self._set_state(
                prefix + "wear",
                SensorState.UNAVAILABLE,
                "Wear requires valid design and full-charge capacities",
            )
        elif design <= 0 or full <= 0 or full > design:
            self._set_state(
                prefix + "wear",
                SensorState.UNAVAILABLE,
                "Battery capacities are inconsistent for a wear estimate",
            )
        else:
            self._values[prefix + "wear"] = 100.0 * (design - full) / design

        if reading.status_error is None:
            full_capacity = self._valid_capacity(reading.full_charge_capacity)
            current_capacity = self._valid_capacity(reading.capacity)
            if full_capacity is None or current_capacity is None or full_capacity <= 0:
                self._set_state(
                    prefix + "capacity",
                    SensorState.UNAVAILABLE,
                    "Current capacity percentage requires valid current and full-charge capacities",
                )
            else:
                self._values[prefix + "capacity"] = (
                    100.0 * current_capacity / full_capacity
                )

    def _record_value(
        self,
        sensor_id: str,
        value: int | None,
        sentinel: int,
        sentinel_name: str,
    ) -> None:
        if value is None or value == sentinel:
            self._set_state(
                sensor_id,
                SensorState.UNAVAILABLE,
                f"Windows reported {sentinel_name}",
            )
        else:
            self._values[sensor_id] = float(value)

    @staticmethod
    def _valid_capacity(value: int | None) -> float | None:
        if value is None or value == BATTERY_UNKNOWN_CAPACITY:
            return None
        return float(value)

    def _set_device_error(
        self,
        index: int,
        error: OSError,
        api_path: str,
    ) -> None:
        state, message = _failure_state(error)
        self._set_device_state(index, state, message, api_path, error.errno)

    def _set_device_state(
        self,
        index: int,
        state: SensorState,
        message: str,
        api_path: str,
        error_code: int | None = None,
    ) -> None:
        prefix = f"battery.device.{index}."
        for field in _FIELD_NAMES:
            sensor_id = prefix + field
            self._states[sensor_id] = (state, message)
            self._diagnostics[sensor_id] = (api_path, str(error_code) if error_code else None)

    def _set_state(
        self,
        sensor_id: str,
        state: SensorState,
        message: str,
    ) -> None:
        self._states[sensor_id] = (state, message)
