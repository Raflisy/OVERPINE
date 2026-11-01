"""Sensor data providers."""

from backend.providers.base import SensorProvider
from backend.providers.io_provider import IoProvider
from backend.providers.lhm_web_provider import LhmWebProvider
from backend.providers.psutil_provider import PsutilProvider
from backend.providers.simulated_provider import SimulatedProvider
from backend.providers.smart_provider import SmartProvider
from backend.providers.windows_counter_provider import WindowsCounterProvider
from backend.providers.wmi_thermal_provider import WmiThermalProvider

__all__ = [
    "IoProvider",
    "LhmWebProvider",
    "PsutilProvider",
    "SensorProvider",
    "SimulatedProvider",
    "SmartProvider",
    "WindowsCounterProvider",
    "WmiThermalProvider",
]
