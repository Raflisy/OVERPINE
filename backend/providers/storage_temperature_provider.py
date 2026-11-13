"""Discover and read native storage-device temperature sensors."""
from __future__ import annotations

import platform
from typing import Any

from backend.monitoring.models import Sensor, SensorState
from backend.providers.base import SensorProvider
from backend.winapi.bindings import (
    ERROR_ACCESS_DENIED,
    ERROR_INVALID_FUNCTION,
    ERROR_INVALID_PARAMETER,
    ERROR_NOT_SUPPORTED,
)
from backend.winapi.storage import (
    disk_device_paths,
    get_storage_device_number,
    query_physical_drive_temperatures,
)


def _failure_state(error: OSError) -> tuple[SensorState, str]:
    """Map storage query failures without treating free space or temperature as health."""
    code = error.errno
    message = (
        f"Windows error {code}: {error.strerror or error}"
        if code is not None
        else str(error)
    )
    if code == ERROR_ACCESS_DENIED:
        return SensorState.PERMISSION_RESTRICTED, message
    if code in (ERROR_INVALID_FUNCTION, ERROR_INVALID_PARAMETER, ERROR_NOT_SUPPORTED):
        return SensorState.UNSUPPORTED, message
    return SensorState.UNAVAILABLE, message


class StorageTemperatureProvider(SensorProvider):
    """Expose temperature values only when the storage driver reports them."""

    name = "storage-temperature"
    poll_interval_category = "storage"

    def __init__(self) -> None:
        self._paths: list[str] = []
        self._sensors: list[Sensor] = []
        self._values: dict[str, float | None] = {}
        self._states: dict[str, tuple[SensorState, str | None]] = {}
        self._diagnostics: dict[str, tuple[str | None, str | None]] = {}

    def discover(self) -> list[Sensor]:
        """Enumerate disks and register actual temperatures or explicit missing rows."""
        self._paths = []
        self._sensors = []
        self._values = {}
        self._states = {}
        self._diagnostics = {}
        if platform.system() != "Windows":
            self._add_placeholder(
                0,
                SensorState.UNSUPPORTED,
                "Storage temperature IOCTLs require Windows",
                "IOCTL_STORAGE_QUERY_PROPERTY",
            )
            return list(self._sensors)
        try:
            self._paths = disk_device_paths()
        except OSError as error:
            state, message = _failure_state(error)
            self._add_placeholder(
                0,
                state,
                message,
                "SetupAPI GUID_DEVINTERFACE_DISK enumeration",
                error.errno,
            )
            return list(self._sensors)

        if not self._paths:
            self._add_placeholder(
                0,
                SensorState.UNSUPPORTED,
                "No physical disk interface is present",
                "SetupAPI GUID_DEVINTERFACE_DISK enumeration",
            )
        else:
            self._refresh()
        return list(self._sensors)

    def read(self) -> dict[str, float | None]:
        """Refresh temperatures using read-only DeviceIoControl requests."""
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
        return {
            "name": self.name,
            "state": "available" if self._values else "unavailable",
            "disk_count": len(self._paths),
            "temperature_sensor_count": len(self._values),
        }

    def _refresh(self) -> None:
        """Read each disk independently so one device error does not hide others."""
        values: dict[str, float | None] = {}
        states: dict[str, tuple[SensorState, str | None]] = {}
        diagnostics: dict[str, tuple[str | None, str | None]] = {}
        for path_index, interface_path in enumerate(self._paths):
            try:
                disk_number = get_storage_device_number(interface_path)
            except OSError as error:
                state, message = _failure_state(error)
                self._add_disk_error(
                    path_index,
                    state,
                    message,
                    "DeviceIoControl(IOCTL_STORAGE_GET_DEVICE_NUMBER)",
                    error.errno,
                    states,
                    diagnostics,
                )
                continue
            api_path = (
                f"DeviceIoControl(IOCTL_STORAGE_QUERY_PROPERTY) "
                f"on \\\\.\\PhysicalDrive{disk_number}"
            )
            try:
                readings = query_physical_drive_temperatures(disk_number)
            except OSError as error:
                state, message = _failure_state(error)
                self._add_disk_error(
                    disk_number,
                    state,
                    message,
                    api_path,
                    error.errno,
                    states,
                    diagnostics,
                )
                continue
            except ValueError as error:
                self._add_disk_error(
                    disk_number,
                    SensorState.UNAVAILABLE,
                    str(error),
                    api_path,
                    None,
                    states,
                    diagnostics,
                )
                continue
            if not readings:
                self._add_disk_error(
                    disk_number,
                    SensorState.UNSUPPORTED,
                    "StorageDeviceTemperatureProperty is not reported by this disk",
                    api_path,
                    None,
                    states,
                    diagnostics,
                )
                continue
            for reading in readings:
                sensor_id = self._sensor_id(reading.disk_number, reading.sensor_index)
                values[sensor_id] = float(reading.temperature_c)
                diagnostics[sensor_id] = (api_path, None)

        self._values = values
        self._states = states
        self._diagnostics = diagnostics
        self._sensors = [
            sensor
            for sensor in self._sensors
            if sensor.id in values or sensor.id in states
        ]
        known_ids = {sensor.id for sensor in self._sensors}
        for sensor_id in (*values.keys(), *states.keys()):
            if sensor_id not in known_ids:
                self._sensors.append(self._make_sensor(sensor_id))
                known_ids.add(sensor_id)

    def _add_disk_error(
        self,
        disk_number: int,
        state: SensorState,
        message: str,
        api_path: str,
        error_code: int | None,
        states: dict[str, tuple[SensorState, str | None]],
        diagnostics: dict[str, tuple[str | None, str | None]],
    ) -> None:
        sensor_id = self._sensor_id(disk_number, 0)
        states[sensor_id] = (state, message)
        diagnostics[sensor_id] = (api_path, str(error_code) if error_code else None)

    def _add_placeholder(
        self,
        disk_number: int,
        state: SensorState,
        message: str,
        api_path: str,
        error_code: int | None = None,
    ) -> None:
        sensor_id = self._sensor_id(disk_number, 0)
        self._sensors.append(self._make_sensor(sensor_id))
        self._states[sensor_id] = (state, message)
        self._diagnostics[sensor_id] = (
            api_path,
            str(error_code) if error_code else None,
        )

    @staticmethod
    def _sensor_id(disk_number: int, sensor_index: int) -> str:
        return f"storage.temperature.disk{disk_number}.sensor{sensor_index}"

    @staticmethod
    def _make_sensor(sensor_id: str) -> Sensor:
        name = sensor_id.replace("storage.temperature.", "Drive ").replace(".", " ")
        return Sensor(
            id=sensor_id,
            name=name,
            category="storage",
            unit="C",
            source="storage-ioctl",
            reliability="reported-by-device",
            state=SensorState.NOT_INITIALIZED,
        )
