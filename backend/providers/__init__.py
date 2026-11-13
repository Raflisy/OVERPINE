"""Sensor data providers."""

from backend.providers.base import SensorProvider
from backend.providers.battery_provider import BatteryProvider
from backend.providers.io_provider import IoProvider
from backend.providers.pdh_provider import PdhProvider
from backend.providers.storage_temperature_provider import StorageTemperatureProvider
from backend.providers.windows_counter_provider import WindowsCounterProvider
from backend.providers.wmi_thermal_provider import WmiThermalProvider
from backend.providers.windows_provider import WindowsProvider

__all__ = [
    "BatteryProvider",
    "IoProvider",
    "PdhProvider",
    "SensorProvider",
    "StorageTemperatureProvider",
    "WindowsCounterProvider",
    "WindowsProvider",
    "WmiThermalProvider",
]
