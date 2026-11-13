"""Python module for the test hardware inventory portion of the OVERPINE project."""

import unittest

from backend.services.hardware_inventory import _build_sections
from backend.winapi.cpu import CpuCache, CpuFrequency, ProcessorTopology
from backend.winapi.network import NetworkInterface


def _row(sections, section_title, row_label):
    section = next(item for item in sections if item["title"] == section_title)
    return next(row for row in section["rows"] if row["label"] == row_label)["value"]


def _hardware_sections_format_windows_inventory_without_guessing():
    sections = _build_sections(
        {
            "computer": {
                "Manufacturer": "LENOVO",
                "Model": "20VLS23T00",
                "HypervisorPresent": True,
            },
            "userName": "ThinkPad",
            "operatingSystemInfo": {
                "Caption": "Microsoft Windows 11 Pro",
                "BuildNumber": "26100",
                "BuildRevision": 9711,
                "DisplayVersion": "24H2",
                "Architecture": "64-bit",
            },
            "board": {
                "Manufacturer": "LENOVO",
                "Product": "20VLS23T00",
                "Version": "SDK0J40697 WIN",
            },
            "bios": {
                "Manufacturer": "LENOVO",
                "SMBIOSBIOSVersion": "R1FET65W (1.39)",
                "ReleaseDate": "2026-04-28",
                "SMBIOSMajorVersion": 3,
                "SMBIOSMinorVersion": 2,
            },
            "processors": [
                {
                    "Name": "Intel Core i7-1165G7",
                    "Manufacturer": "GenuineIntel",
                    "Caption": "Intel64 Family 6 Model 140 Stepping 1",
                    "NumberOfCores": 4,
                    "NumberOfLogicalProcessors": 8,
                    "MaxClockSpeed": 2803,
                    "L2CacheSize": 5120,
                    "L3CacheSize": 12288,
                }
            ],
            "memory": [
                {
                    "Manufacturer": "Samsung",
                    "PartNumber": "M471A1G44AB0-CWE",
                    "Capacity": 8 * 1024**3,
                    "ConfiguredClockSpeed": 3200,
                    "FormFactor": 12,
                },
                {
                    "Manufacturer": "Samsung",
                    "PartNumber": "M471A1G44AB0-CWE",
                    "Capacity": 8 * 1024**3,
                    "ConfiguredClockSpeed": 3200,
                    "FormFactor": 12,
                },
            ],
            "memoryArrays": [{"MemoryDevices": 2}],
            "graphics": [
                {
                    "Name": "Intel Iris Xe Graphics",
                    "DriverVersion": "32.0",
                    "DriverDate": "2026-06-17T00:00:00",
                    "AdapterRAM": 1024**3,
                    "PNPDeviceID": "PCI\\VEN_8086&DEV_9A49",
                    "CurrentHorizontalResolution": 1920,
                    "CurrentVerticalResolution": 1080,
                }
            ],
            "monitors": [
                {
                    "Name": "DELL P/N: 9X6HV",
                    "FriendlyName": "Integrated Monitor",
                    "InstanceId": "DISPLAY\\NCP004C\\4&23A84C03&0&UID8388688",
                    "ManufacturerCode": "NCP",
                    "ProductCode": "NCP004C",
                    "ManufactureWeek": 20,
                    "ManufactureYear": 2019,
                    "WidthCm": 29,
                    "HeightCm": 17,
                    "PreferredMode": "1920 x 1080 @ 138.65 MHz",
                    "SupportedModes": [
                        "1920 x 1080 @ 138.65 MHz",
                        "1920 x 1080 @ 110.90 MHz",
                    ],
                    "DriverProvider": "Microsoft",
                    "DriverVersion": "10.0.26100.9278",
                    "DriverDate": "2006-06-21T00:00:00",
                }
            ],
            "audio": [
                {
                    "Name": "Realtek High Definition Audio",
                    "InstanceId": "INTELAUDIO\\FUNC_01&VEN_10EC",
                    "Manufacturer": "Realtek",
                    "DriverProvider": "Realtek",
                    "DriverVersion": "6.0.9239.1",
                    "DriverDate": "2021-09-16T00:00:00",
                }
            ],
            "battery": {
                "static": {
                    "DeviceName": "5B10W13933",
                    "ManufacturerName": "SMP",
                    "Chemistry": "Lithium Ion",
                    "DesignedCapacity": 46000,
                },
                "full": {"FullChargedCapacity": 39900},
                "cycle": {"CycleCount": 769},
            },
            "disks": [],
            "physicalDisks": [
                {
                    "FriendlyName": "WDC PC SN730",
                    "BusType": "NVMe",
                    "MediaType": "SSD",
                    "Size": 256 * 1024**3,
                    "HealthStatus": "Healthy",
                }
            ],
            "slots": [],
            "controllers": [
                {
                    "Class": "USB",
                    "FriendlyName": "Intel USB 3.10 eXtensible Host Controller",
                }
            ],
            "tpm": None,
            "firmwareType": 2,
            "secureBoot": 0,
            "deviceGuard": {
                "VirtualizationBasedSecurityStatus": 2,
                "SecurityServicesConfigured": [0],
                "SecurityServicesRunning": [0],
            },
        },
        memory_total_gib=15.7,
        operating_system="Windows 11",
        os_version="10.0.26100",
        architecture="AMD64",
        topology=ProcessorTopology(
            4,
            8,
            (
                CpuCache(2, 5 * 1024**2, 0),
                CpuCache(3, 12 * 1024**2, 0),
            ),
        ),
        frequency=CpuFrequency(1200.0, 2803.0),
        network_interfaces=[
            NetworkInterface(
                4,
                "Wi-Fi",
                "Intel Wi-Fi 6 AX201 160MHz",
                6,
                True,
                2,
                True,
                459_000_000,
                459_000_000,
                100,
                200,
                "00:11:22:33:44:55",
                ("192.0.2.10",),
            )
        ],
    )

    assert _row(
        sections, "Computer & motherboard", "Computer brand and model"
    ) == "LENOVO | 20VLS23T00"
    assert _row(sections, "Memory", "Installed memory") == "16.0 GiB"
    assert _row(sections, "Processor", "Cores and logical processors") == (
        "4 cores | 8 logical processors"
    )
    assert _row(sections, "Processor", "Windows-reported cache sizes") == (
        "L2 5.0 MiB | L3 12.0 MiB"
    )
    assert "Samsung | M471A1G44AB0-CWE" in _row(
        sections, "Memory", "Memory module 1"
    )
    assert "SO-DIMM" in _row(sections, "Memory", "Memory module 1")
    assert "NVMe | SSD" in _row(sections, "Storage", "Physical drive 1")
    assert _row(sections, "System & security", "Firmware boot mode") == "UEFI"
    assert _row(sections, "System & security", "Secure Boot state") == "Disabled"
    assert _row(sections, "System & security", "TPM") == (
        "Status unavailable; Windows did not return TPM details"
    )
    assert _row(sections, "System & security", "Current user") == "ThinkPad"
    assert _row(sections, "System & security", "Windows build") == "26100.9711 | 24H2"
    assert "hypervisor is active" in _row(
        sections, "System & security", "Hypervisor"
    )
    assert _row(
        sections, "System & security", "Hypervisor-protected Code Integrity (HVCI)"
    ) == "Disabled"
    assert "Week 20, 2019" in _row(sections, "Displays", "Display 1 manufacture date")
    assert _row(sections, "Displays", "Display 1 preferred timing") == (
        "1920 x 1080 @ 138.65 MHz"
    )
    assert _row(sections, "Displays", "Display 1 EDID-reported video modes") == (
        "1920 x 1080 @ 138.65 MHz | 1920 x 1080 @ 110.90 MHz"
    )
    assert _row(sections, "Network", "Network adapter 1 link speed") == (
        "Transmit 459 Mbit/s / receive 459 Mbit/s"
    )
    assert _row(sections, "Network", "Network adapter 1 IP addresses") == "192.0.2.10"
    assert _row(sections, "Network", "Network adapter 1") == (
        "Intel Wi-Fi 6 AX201 160MHz | Wi-Fi | down | connected"
    )
    assert "Windows status code" not in _row(
        sections, "Network", "Network adapter 1"
    )
    assert _row(sections, "Power & battery", "Estimated wear") == "13.3%"
    assert _row(sections, "Power & battery", "Charge cycles") == "769"
    assert "Not exposed" in _row(
        sections, "Memory", "Memory channels and timings"
    )


class HardwareInventoryTests(unittest.TestCase):
    """Verify hardware inventory presentation without inferred values."""

    def test_hardware_sections_format_windows_inventory_without_guessing(self) -> None:
        _hardware_sections_format_windows_inventory_without_guessing()
