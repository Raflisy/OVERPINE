"""System AC and battery status from the documented Kernel32 API."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass

from backend.winapi.bindings import (
    BATTERY_CAPACITY_RELATIVE,
    BATTERY_INFORMATION,
    BATTERY_INFORMATION_LEVEL,
    BATTERY_QUERY_INFORMATION,
    BATTERY_STATUS as NATIVE_BATTERY_STATUS,
    BATTERY_TAG_INVALID,
    BATTERY_WAIT_STATUS,
    DWORD,
    ERROR_DEVICE_NOT_CONNECTED,
    ERROR_FILE_NOT_FOUND,
    GUID_DEVINTERFACE_BATTERY,
    GENERIC_READ,
    IOCTL_BATTERY_QUERY_INFORMATION,
    IOCTL_BATTERY_QUERY_STATUS,
    IOCTL_BATTERY_QUERY_TAG,
    SYSTEM_POWER_STATUS,
    bind_function,
    load_library,
    windows_last_error,
)
from backend.winapi.device import (
    close_device,
    device_io_control,
    enumerate_device_paths,
    open_device,
)

# Microsoft Learn: SYSTEM_POWER_STATUS ACLineStatus and BatteryLifeTime values.
_UNKNOWN_VALUE = 0xFF
_UNKNOWN_SECONDS = 0xFFFFFFFF
# Microsoft Learn: SYSTEM_POWER_STATUS BatteryFlag values.
_NO_SYSTEM_BATTERY = 0x80
_BATTERY_CHARGING = 0x08


@dataclass(frozen=True, slots=True)
class BatteryStatus:
    """Contain supported system-level battery values."""

    percent: float | None
    power_plugged: bool | None
    seconds_left: int | None
    charging: bool | None
    discharging: bool | None


@dataclass(frozen=True, slots=True)
class BatteryDeviceReading:
    """Contain raw documented fields from one inserted battery."""

    path: str
    capabilities: int
    design_capacity: int
    full_charge_capacity: int
    cycle_count: int
    power_state: int | None
    capacity: int | None
    voltage_mv: int | None
    rate: int | None
    status_error: OSError | None = None

    @property
    def relative_units(self) -> bool:
        """Return whether the battery reports relative rather than physical units."""
        return bool(self.capabilities & BATTERY_CAPACITY_RELATIVE)


def battery_device_paths() -> list[str]:
    """Enumerate battery-class device interfaces without launching a process."""
    return enumerate_device_paths(GUID_DEVINTERFACE_BATTERY)


def read_battery_device(path: str) -> BatteryDeviceReading | None:
    """Read one inserted battery; return None for an empty or invalid slot."""
    handle = open_device(path, GENERIC_READ)
    try:
        timeout = DWORD(0)
        tag = DWORD()
        try:
            device_io_control(
                handle,
                IOCTL_BATTERY_QUERY_TAG,
                timeout,
                tag,
            )
        except OSError as error:
            if error.errno in (ERROR_FILE_NOT_FOUND, ERROR_DEVICE_NOT_CONNECTED):
                return None
            raise
        if tag.value == BATTERY_TAG_INVALID:
            return None

        information_query = BATTERY_QUERY_INFORMATION(
            tag.value,
            BATTERY_INFORMATION_LEVEL,
            0,
        )
        information = BATTERY_INFORMATION()
        device_io_control(
            handle,
            IOCTL_BATTERY_QUERY_INFORMATION,
            information_query,
            information,
        )

        wait_status = BATTERY_WAIT_STATUS(tag.value, 0, 0, 0, 0)
        status = NATIVE_BATTERY_STATUS()
        status_error: OSError | None = None
        try:
            device_io_control(
                handle,
                IOCTL_BATTERY_QUERY_STATUS,
                wait_status,
                status,
            )
        except OSError as error:
            status_error = error
            status = None

        return BatteryDeviceReading(
            path=path,
            capabilities=int(information.Capabilities),
            design_capacity=int(information.DesignedCapacity),
            full_charge_capacity=int(information.FullChargedCapacity),
            cycle_count=int(information.CycleCount),
            power_state=int(status.PowerState) if status is not None else None,
            capacity=int(status.Capacity) if status is not None else None,
            voltage_mv=int(status.Voltage) if status is not None else None,
            rate=int(status.Rate) if status is not None else None,
            status_error=status_error,
        )
    finally:
        close_device(handle)


def get_battery_status() -> BatteryStatus | None:
    """Read AC and battery charge; return None if Windows reports no battery."""
    kernel32 = load_library("kernel32")
    if kernel32 is None:
        return None

    # Microsoft Learn: GetSystemPowerStatus function (winbase.h).
    function = bind_function(
        kernel32,
        "GetSystemPowerStatus",
        ctypes.c_int32,
        [ctypes.POINTER(SYSTEM_POWER_STATUS)],
    )
    native = SYSTEM_POWER_STATUS()
    if not function(ctypes.byref(native)):
        raise OSError(windows_last_error(), "GetSystemPowerStatus failed")
    if native.BatteryFlag == _NO_SYSTEM_BATTERY:
        return None

    power_plugged = (
        True
        if native.ACLineStatus == 1
        else False
        if native.ACLineStatus == 0
        else None
    )
    percent = (
        float(native.BatteryLifePercent)
        if native.BatteryLifePercent != _UNKNOWN_VALUE
        else None
    )
    seconds_left = (
        int(native.BatteryLifeTime)
        if native.BatteryLifeTime != _UNKNOWN_SECONDS
        else None
    )
    charging = (
        bool(native.BatteryFlag & _BATTERY_CHARGING)
        if native.BatteryFlag != _UNKNOWN_VALUE
        else None
    )
    discharging = (
        not charging
        if power_plugged is False and charging is not None
        else False
        if power_plugged is True
        else None
    )
    return BatteryStatus(
        percent=percent,
        power_plugged=power_plugged,
        seconds_left=seconds_left,
        charging=charging,
        discharging=discharging,
    )
