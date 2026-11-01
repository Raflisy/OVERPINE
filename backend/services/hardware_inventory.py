"""Best-effort Windows hardware inventory using built-in CIM providers."""
from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
from datetime import datetime
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any, TypedDict, cast

log = logging.getLogger(__name__)
_windows_powershell_lock = Lock()

_INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$computer = Get-CimInstance Win32_ComputerSystem | Select-Object -First 1 Manufacturer, Model, TotalPhysicalMemory, HypervisorPresent
$os = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1 Caption, BuildNumber, Version, OSArchitecture
$osVersionKey = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$board = Get-CimInstance Win32_BaseBoard | Select-Object -First 1 Manufacturer, Product, Version
$bios = Get-CimInstance Win32_BIOS | Select-Object -First 1 Manufacturer, SMBIOSBIOSVersion, Version, ReleaseDate, SMBIOSMajorVersion, SMBIOSMinorVersion
$processors = @(Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, Caption, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, CurrentClockSpeed, L2CacheSize, L3CacheSize, SocketDesignation, VirtualizationFirmwareEnabled, VMMonitorModeExtensions, SecondLevelAddressTranslationExtensions)
$memory = @(Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, PartNumber, Capacity, Speed, ConfiguredClockSpeed, FormFactor, DataWidth, TotalWidth)
$memoryArrays = @(Get-CimInstance Win32_PhysicalMemoryArray | Select-Object MemoryDevices, MaxCapacityEx)
$graphics = @(Get-CimInstance Win32_VideoController | Select-Object Name, VideoProcessor, DriverVersion, AdapterRAM, VideoModeDescription, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate)
$disks = @(Get-CimInstance Win32_DiskDrive | Select-Object Model, Size, InterfaceType, MediaType)
$signedDrivers = @(Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceID, DeviceName, DriverProviderName, DriverVersion, DriverDate, Manufacturer, DeviceClass)
$driversById = @{}
$driversByName = @{}
foreach ($driver in $signedDrivers) {
    if ($driver.DeviceID) { $driversById[$driver.DeviceID] = $driver }
    if ($driver.DeviceName) { $driversByName[$driver.DeviceName] = $driver }
}
$presentDevices = @(Get-PnpDevice -PresentOnly | Select-Object Class, FriendlyName, InstanceId, Status)
$monitorDevices = @($presentDevices | Where-Object { $_.Class -eq 'Monitor' })
$monitors = @($monitorDevices | ForEach-Object {
    $device = $_
    $edidPath = 'HKLM:\SYSTEM\CurrentControlSet\Enum\' + $device.InstanceId + '\Device Parameters'
    $edid = (Get-ItemProperty -LiteralPath $edidPath -Name EDID -ErrorAction SilentlyContinue).EDID
    $name = $null
    $preferredMode = $null
    $supportedModes = @()
    $week = $null
    $year = $null
    $widthCm = $null
    $heightCm = $null
    if ($edid -and $edid.Length -ge 128) {
        $week = [int]$edid[16]
        $year = 1990 + [int]$edid[17]
        $widthCm = [int]$edid[21]
        $heightCm = [int]$edid[22]
        for ($offset = 54; $offset -le 108; $offset += 18) {
            if ($edid[$offset] -eq 0 -and $edid[$offset + 1] -eq 0 -and $edid[$offset + 3] -eq 0xFC) {
                $name = [System.Text.Encoding]::ASCII.GetString($edid, $offset + 5, 13).Trim([char]0, ' ')
            }
            $pixelClock = [int]$edid[$offset] + ([int]$edid[$offset + 1] * 256)
            if ($pixelClock -gt 0) {
                $horizontal = [int]$edid[$offset + 2] + (([int]$edid[$offset + 4] -band 0xF0) -shl 4)
                $vertical = [int]$edid[$offset + 5] + (([int]$edid[$offset + 7] -band 0xF0) -shl 4)
                $mode = '{0} x {1} @ {2:N2} MHz' -f $horizontal, $vertical, ($pixelClock / 100)
                $supportedModes += $mode
                if (-not $preferredMode) { $preferredMode = $mode }
            }
        }
    }
    $driver = $driversById[$device.InstanceId]
    [ordered]@{
        Name = $name
        FriendlyName = $device.FriendlyName
        InstanceId = $device.InstanceId
        ManufacturerCode = ($device.InstanceId -split '\\')[1] -replace '[0-9A-Fa-f]{4}$', ''
        ProductCode = ($device.InstanceId -split '\\')[1]
        ManufactureWeek = $week
        ManufactureYear = $year
        WidthCm = $widthCm
        HeightCm = $heightCm
        PreferredMode = $preferredMode
        SupportedModes = $supportedModes
        DriverProvider = $driver.DriverProviderName
        DriverVersion = $driver.DriverVersion
        DriverDate = $driver.DriverDate
    }
})
$audioDevices = @($presentDevices | Where-Object { $_.Class -eq 'Media' })
$audio = @($audioDevices | ForEach-Object {
    $device = $_
    $driver = $driversById[$device.InstanceId]
    [ordered]@{
        Name = $device.FriendlyName
        InstanceId = $device.InstanceId
        Status = $device.Status
        Manufacturer = $driver.Manufacturer
        DriverProvider = $driver.DriverProviderName
        DriverVersion = $driver.DriverVersion
        DriverDate = $driver.DriverDate
    }
})
$networkAdapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress)
$network = @($networkAdapters | ForEach-Object {
    $adapter = $_
    $driver = $driversByName[$adapter.InterfaceDescription]
    $pnp = $presentDevices | Where-Object { $_.Class -eq 'Net' -and $_.FriendlyName -eq $adapter.InterfaceDescription } | Select-Object -First 1
    [ordered]@{
        Name = $adapter.Name
        Description = $adapter.InterfaceDescription
        Status = [string]$adapter.Status
        LinkSpeed = $adapter.LinkSpeed
        MacAddress = $adapter.MacAddress
        HardwareId = $pnp.InstanceId
        DriverProvider = $driver.DriverProviderName
        DriverVersion = $driver.DriverVersion
        DriverDate = $driver.DriverDate
    }
})
$batteryStatic = $null
$batteryFull = $null
$batteryCycle = $null
try { $batteryStatic = Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData -ErrorAction Stop | Select-Object -First 1 DeviceName, ManufacturerName, SerialNumber, UniqueID, Chemistry, DesignedCapacity } catch { }
try { $batteryFull = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity -ErrorAction Stop | Select-Object -First 1 FullChargedCapacity } catch { }
try { $batteryCycle = Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount -ErrorAction Stop | Select-Object -First 1 CycleCount } catch { }
$battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1 Name, EstimatedChargeRemaining, BatteryStatus
try {
    $physicalDisks = @(Get-PhysicalDisk | Select-Object FriendlyName, BusType, MediaType, Size, HealthStatus)
} catch {
    $physicalDisks = @()
}
$slots = @(Get-CimInstance Win32_SystemSlot | Select-Object SlotDesignation, Description, CurrentUsage, MaxDataWidth)
try {
    $controllers = @($presentDevices | Where-Object {
        (($_.Class -eq 'USB') -and ($_.FriendlyName -match 'USB.*(Root Hub|Host Controller)')) -or
        ($_.FriendlyName -match 'PCI Express|LPC|PCH|Chipset')
    } | Select-Object Class, FriendlyName, Status)
} catch {
    $controllers = @()
}
try {
    $tpm = Get-Tpm -ErrorAction Stop | Select-Object TpmPresent, TpmReady, TpmEnabled, TpmActivated, ManufacturerIdTxt, ManufacturerVersion
} catch {
    $tpm = $null
}
try {
    $firmwareType = (Get-ComputerInfo -Property BiosFirmwareType -ErrorAction Stop).BiosFirmwareType
} catch {
    $firmwareType = $null
}
try {
    $secureBoot = Get-ItemPropertyValue -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State' -Name UEFISecureBootEnabled -ErrorAction Stop
} catch {
    $secureBoot = $null
}
try {
    $deviceGuard = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop |
        Select-Object -First 1 VirtualizationBasedSecurityStatus, SecurityServicesConfigured, SecurityServicesRunning
} catch {
    $deviceGuard = $null
}
$biosDate = if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { $null }
$inventory = [ordered]@{
    computer = $computer
    userName = $env:USERNAME
    operatingSystemInfo = [ordered]@{
        Caption = $os.Caption
        BuildNumber = $os.BuildNumber
        BuildRevision = $osVersionKey.UBR
        DisplayVersion = $osVersionKey.DisplayVersion
        Version = $os.Version
        Architecture = $os.OSArchitecture
    }
    board = $board
    bios = @{
        Manufacturer = $bios.Manufacturer
        SMBIOSBIOSVersion = $bios.SMBIOSBIOSVersion
        Version = $bios.Version
        ReleaseDate = $biosDate
        SMBIOSMajorVersion = $bios.SMBIOSMajorVersion
        SMBIOSMinorVersion = $bios.SMBIOSMinorVersion
    }
    processors = $processors
    memory = $memory
    memoryArrays = $memoryArrays
    graphics = $graphics
    monitors = $monitors
    audio = $audio
    network = $network
    battery = [ordered]@{
        static = $batteryStatic
        full = $batteryFull
        cycle = $batteryCycle
        status = $battery
    }
    disks = $disks
    physicalDisks = $physicalDisks
    slots = $slots
    controllers = $controllers
    tpm = $tpm
    firmwareType = $firmwareType
    secureBoot = $secureBoot
    deviceGuard = $deviceGuard
}
ConvertTo-Json -InputObject $inventory -Compress -Depth 6
"""

_LIVE_SENSOR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$errors = @()
$gpuUsage = $null
try {
    $gpuEngines = @(Get-CimInstance -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction Stop |
        Where-Object { $_.Name -match 'engtype_3D' } |
        ForEach-Object { [double]$_.UtilizationPercentage })
    if ($gpuEngines.Count -gt 0) {
        $gpuUsage = [math]::Min(100, ($gpuEngines | Measure-Object -Sum).Sum)
    }
} catch {
    $errors += "GPU usage: $($_.Exception.Message)"
}
$battery = $null
try {
    $battery = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction Stop |
        Select-Object -First 1 PowerOnline, Charging, Discharging, RemainingCapacity, Voltage, ChargeRate, DischargeRate
} catch {
    $errors += "Battery sensors: $($_.Exception.Message)"
}
[ordered]@{
    gpuUsagePercent = $gpuUsage
    battery = $battery
    errors = $errors
} | ConvertTo-Json -Compress -Depth 4
"""

_UNAVAILABLE = "Not exposed by the standard Windows hardware interface"


class HardwareRow(TypedDict):
    label: str
    value: str


class HardwareSection(TypedDict):
    title: str
    rows: list[HardwareRow]


HardwareRecord = dict[str, Any]


def _as_list(value: Any) -> list[HardwareRecord]:
    if isinstance(value, dict):
        return [cast(HardwareRecord, value)]
    if isinstance(value, list):
        return [
            cast(HardwareRecord, item)
            for item in cast(list[Any], value)
            if isinstance(item, dict)
        ]
    return []


def _as_record(value: Any) -> HardwareRecord:
    if isinstance(value, dict):
        return cast(HardwareRecord, value)
    return {}


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in cast(list[Any], value) if item]
    return []


def _display(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "Not reported by Windows"
    return f"{value}{suffix}"


def _size_gib(value: Any) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "Not reported by Windows"
    if size <= 0:
        return "Not reported by Windows"
    return f"{size / (1024**3):.1f} GiB"


def _cache_size_mib(value: Any) -> str:
    try:
        return f"{int(value) / 1024:.1f} MiB"
    except (TypeError, ValueError):
        return "Not reported by Windows"


def _memory_form_factor(value: Any) -> str:
    try:
        form_factor = int(value)
    except (TypeError, ValueError):
        return "Not reported by Windows"
    return {
        8: "DIMM",
        12: "SO-DIMM",
        13: "SO-DIMM (BGA)",
        14: "Micro-DIMM",
    }.get(form_factor, f"SMBIOS code {form_factor}")


def _has_positive_integer(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _positive_integer(value: Any) -> int | None:
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def _human_date(value: Any) -> str:
    if value is None or value == "":
        return "Not reported by Windows"
    rendered = str(value)
    if rendered.startswith("/Date(") and rendered.endswith(")/"):
        try:
            return datetime.fromtimestamp(
                int(rendered[6:-2]) / 1000
            ).date().isoformat()
        except ValueError:
            return "Not reported by Windows"
    return rendered.split("T", 1)[0].split(" ", 1)[0]


def _available_hvci_state(device_guard: HardwareRecord) -> str:
    running = device_guard.get("SecurityServicesRunning", [])
    configured = device_guard.get("SecurityServicesConfigured", [])
    if not isinstance(running, list):
        running = [running]
    if not isinstance(configured, list):
        configured = [configured]
    if 2 in running or "2" in running:
        return "Enabled"
    if 2 in configured or "2" in configured:
        return "Configured but not running"
    if device_guard.get("VirtualizationBasedSecurityStatus") == 0:
        return "Disabled"
    if device_guard.get("VirtualizationBasedSecurityStatus") == 2:
        return "Disabled"
    return "Not reported by Windows"


def _inventory_command() -> dict[str, Any] | None:
    if platform.system() != "Windows":
        return None

    executable = shutil.which("powershell.exe")
    if executable is None:
        log.warning("Windows PowerShell was not found; hardware inventory is unavailable")
        return None

    try:
        with _windows_powershell_lock:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", _INVENTORY_SCRIPT],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
    except subprocess.TimeoutExpired:
        log.warning("Windows hardware inventory query timed out after 45 seconds")
        return None
    except OSError as error:
        log.warning("Windows hardware inventory query failed: %s", error)
        return None

    if result.returncode != 0:
        detail = result.stderr.strip() or f"PowerShell exited with code {result.returncode}"
        log.warning("Windows hardware inventory query failed: %s", detail)
        return None

    try:
        inventory = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        log.warning("Windows hardware inventory returned invalid JSON: %s", error)
        return None
    if not isinstance(inventory, dict):
        log.warning("Windows hardware inventory returned an unexpected response")
        return None
    return cast(dict[str, Any], inventory)


_inventory_lock = Lock()
_inventory_expires_at = 0.0
_inventory_result: dict[str, Any] | None = None
_inventory_refreshing = False
_inventory_refresh_interval = 180


def _refresh_inventory_background() -> None:
    global _inventory_expires_at, _inventory_refreshing, _inventory_result
    result: dict[str, Any] | None = None
    try:
        result = _inventory_command()
    finally:
        with _inventory_lock:
            if result is not None:
                _inventory_result = result
            _inventory_expires_at = monotonic() + _inventory_refresh_interval
            _inventory_refreshing = False


def _cached_inventory() -> dict[str, Any] | None:
    global _inventory_refreshing
    with _inventory_lock:
        if (
            platform.system() == "Windows"
            and monotonic() >= _inventory_expires_at
            and not _inventory_refreshing
        ):
            _inventory_refreshing = True
            Thread(
                target=_refresh_inventory_background,
                name="overpine-windows-inventory",
                daemon=True,
            ).start()
        return _inventory_result


def _join_values(values: list[str], fallback: str = "Not reported by Windows") -> str:
    return " | ".join(value for value in values if value) or fallback


def _physical_disk_rows(
    inventory: HardwareRecord,
    fallback_rows: list[str],
) -> list[str]:
    physical_disks = _as_list(inventory.get("physicalDisks"))
    if not physical_disks:
        return fallback_rows
    return [
        _join_values(
            [
                str(disk.get("FriendlyName") or "Disk"),
                str(disk.get("BusType") or ""),
                str(disk.get("MediaType") or ""),
                _size_gib(disk.get("Size")),
                str(disk.get("HealthStatus") or ""),
            ]
        )
        for disk in physical_disks
    ]


def _build_sections(
    inventory: HardwareRecord,
    memory_total_gib: float,
    operating_system: str,
    os_version: str,
    architecture: str,
) -> list[HardwareSection]:
    computer = _as_record(inventory.get("computer"))
    board = _as_record(inventory.get("board"))
    bios = _as_record(inventory.get("bios"))
    processors = _as_list(inventory.get("processors"))
    processor = processors[0] if processors else {}
    memory_modules = _as_list(inventory.get("memory"))
    memory_arrays = _as_list(inventory.get("memoryArrays"))
    graphics_adapters = _as_list(inventory.get("graphics"))
    monitors = _as_list(inventory.get("monitors"))
    audio_devices = _as_list(inventory.get("audio"))
    network_adapters = _as_list(inventory.get("network"))
    battery_info = _as_record(inventory.get("battery"))
    battery_static = _as_record(battery_info.get("static"))
    battery_full = _as_record(battery_info.get("full"))
    battery_cycle = _as_record(battery_info.get("cycle"))
    device_guard = _as_record(inventory.get("deviceGuard"))
    operating_system_info = _as_record(inventory.get("operatingSystemInfo"))
    disks = _as_list(inventory.get("disks"))
    slots = _as_list(inventory.get("slots"))
    controllers = _as_list(inventory.get("controllers"))
    tpm = _as_record(inventory.get("tpm"))

    computer_model = _join_values(
        [str(computer.get("Manufacturer") or ""), str(computer.get("Model") or "")]
    )
    board_model = _join_values(
        [str(board.get("Manufacturer") or ""), str(board.get("Product") or "")]
    )
    chipset_controllers = [
        str(item["FriendlyName"])
        for item in controllers
        if item.get("Class") == "System"
        and item.get("FriendlyName")
        and any(
            keyword in str(item.get("FriendlyName", "")).casefold()
            for keyword in ("lpc", "pch", "chipset")
        )
    ]
    usb_controllers = [
        str(item["FriendlyName"])
        for item in controllers
        if item.get("Class") == "USB" and item.get("FriendlyName")
    ]
    pcie_controllers = [
        str(item["FriendlyName"])
        for item in controllers
        if "pci express" in str(item.get("FriendlyName", "")).casefold()
        and item.get("FriendlyName")
    ]
    reported_slots = [
        _join_values(
            [
                str(slot.get("SlotDesignation") or ""),
                str(slot.get("Description") or ""),
                f"{slot['MaxDataWidth']}-bit width"
                if slot.get("MaxDataWidth")
                else "",
            ]
        )
        for slot in slots
    ]
    installed_bytes = 0
    modules: list[str] = []
    for module in memory_modules:
        if _has_positive_integer(module.get("Capacity")):
            installed_bytes += int(module["Capacity"])
        module_name = _join_values(
            [
                str(module.get("Manufacturer") or "").strip(),
                str(module.get("PartNumber") or "").strip(),
            ],
            "Memory module",
        )
        speed = module.get("ConfiguredClockSpeed") or module.get("Speed")
        modules.append(
            _join_values(
                [
                    f"{module_name} · {_size_gib(module.get('Capacity'))}",
                    f"{speed} MT/s configured" if speed else "",
                    (
                        _memory_form_factor(module.get("FormFactor"))
                        if module.get("FormFactor")
                        else ""
                    ),
                ]
            )
        )
    installed_memory_gib = round(
        installed_bytes / (1024**3),
        1,
    ) or memory_total_gib

    disk_rows: list[str] = []
    for disk in disks:
        disk_rows.append(
            _join_values(
                [
                    str(disk.get("Model") or "Disk"),
                    _size_gib(disk.get("Size")),
                    str(disk.get("InterfaceType") or ""),
                    str(disk.get("MediaType") or ""),
                ]
            )
        )

    firmware_type = inventory.get("firmwareType")
    firmware_mode = (
        "Legacy BIOS"
        if firmware_type in (1, "1")
        else "UEFI"
        if firmware_type in (2, "2")
        else "Not reported by Windows"
    )
    secure_boot = inventory.get("secureBoot")
    secure_boot_state = (
        "Enabled"
        if secure_boot in (1, "1", True)
        else "Disabled"
        if secure_boot in (0, "0", False)
        else "Not reported by Windows"
    )
    tpm_state = (
        "Not installed"
        if tpm.get("TpmPresent") is False
        else _join_values(
            [
                f"Manufacturer {tpm['ManufacturerIdTxt']}"
                if tpm.get("TpmPresent") and tpm.get("ManufacturerIdTxt")
                else "",
                f"Version {tpm['ManufacturerVersion']}"
                if tpm.get("TpmPresent") and tpm.get("ManufacturerVersion")
                else "",
                "Ready" if tpm.get("TpmReady") else "",
            ],
            (
                "Status unavailable; Windows did not return TPM details"
                if inventory.get("tpm") is None
                else "Status not reported by Windows"
            ),
        )
    )
    os_build = _join_values(
        [
            (
                f"{operating_system_info['BuildNumber']}.{operating_system_info['BuildRevision']}"
                if operating_system_info.get("BuildNumber") is not None
                and operating_system_info.get("BuildRevision") is not None
                else str(operating_system_info.get("BuildNumber") or os_version)
            ),
            str(operating_system_info.get("DisplayVersion") or ""),
        ]
    )
    monitor_rows: list[HardwareRow] = []
    for index, monitor in enumerate(monitors):
        monitor_title = f"Display {index + 1}"
        monitor_details = _join_values(
            [
                str(monitor.get("Name") or monitor.get("FriendlyName") or ""),
                str(monitor.get("ManufacturerCode") or ""),
                str(monitor.get("ProductCode") or ""),
            ]
        )
        monitor_rows.extend(
            [
                {"label": f"{monitor_title} name and ID", "value": monitor_details},
                {
                    "label": f"{monitor_title} manufacture date",
                    "value": (
                        f"Week {monitor['ManufactureWeek']}, {monitor['ManufactureYear']}"
                        if monitor.get("ManufactureWeek") and monitor.get("ManufactureYear")
                        else "Not reported by Windows"
                    ),
                },
                {
                    "label": f"{monitor_title} physical size",
                    "value": (
                        f"{monitor['WidthCm']} × {monitor['HeightCm']} cm"
                        if monitor.get("WidthCm") and monitor.get("HeightCm")
                        else "Not reported by Windows"
                    ),
                },
                {
                    "label": f"{monitor_title} preferred timing",
                    "value": _display(monitor.get("PreferredMode")),
                },
                {
                    "label": f"{monitor_title} EDID-reported video modes",
                    "value": _join_values(
                        _as_strings(monitor.get("SupportedModes"))
                    ),
                },
                {
                    "label": f"{monitor_title} hardware ID",
                    "value": _display(monitor.get("InstanceId")),
                },
                {
                    "label": f"{monitor_title} driver",
                    "value": _join_values(
                        [
                            str(monitor.get("DriverProvider") or ""),
                            str(monitor.get("DriverVersion") or ""),
                            _human_date(monitor.get("DriverDate"))
                            if monitor.get("DriverDate")
                            else "",
                        ]
                    ),
                },
            ]
        )
    if not monitor_rows:
        monitor_rows.append(
            {"label": "Connected displays", "value": "Not reported by Windows"}
        )
    monitor_rows.extend(
        [
            {"label": "Monitor color depth and gamut coverage", "value": _UNAVAILABLE},
            {"label": "Monitor chromaticity and gamma", "value": _UNAVAILABLE},
            {
                "label": "HDR, AdaptiveSync and advanced display features",
                "value": _UNAVAILABLE,
            },
        ]
    )

    graphics_detail_rows: list[HardwareRow] = []
    for index, adapter in enumerate(graphics_adapters):
        graphics_detail_rows.extend(
            [
                {
                    "label": f"Adapter {index + 1}",
                    "value": str(adapter.get("Name") or "Display adapter"),
                },
                {
                    "label": f"Adapter {index + 1} video processor",
                    "value": _display(adapter.get("VideoProcessor")),
                },
                {
                    "label": f"Adapter {index + 1} reported video memory",
                    "value": _size_gib(adapter.get("AdapterRAM")),
                },
                {
                    "label": f"Adapter {index + 1} driver",
                    "value": _join_values(
                        [
                            str(adapter.get("DriverVersion") or ""),
                            _human_date(adapter.get("DriverDate"))
                            if adapter.get("DriverDate")
                            else "",
                        ]
                    ),
                },
                {
                    "label": f"Adapter {index + 1} hardware ID",
                    "value": _display(adapter.get("PNPDeviceID")),
                },
                {
                    "label": f"Adapter {index + 1} additional engine and memory clocks",
                    "value": _UNAVAILABLE,
                },
                {
                    "label": f"Adapter {index + 1} memory bus, execution units and PCIe link",
                    "value": _UNAVAILABLE,
                },
                {
                    "label": f"Adapter {index + 1} Resizable BAR support",
                    "value": _UNAVAILABLE,
                },
            ]
        )
    graphics_detail_rows.extend(
        [
            {"label": "GPU codename and detailed performance units", "value": _UNAVAILABLE},
            {"label": "GPU dedicated/shared memory usage limits", "value": _UNAVAILABLE},
        ]
    )

    audio_rows: list[HardwareRow] = []
    for index, device in enumerate(audio_devices):
        audio_rows.extend(
            [
                {
                    "label": f"Audio device {index + 1}",
                    "value": _display(device.get("Name")),
                },
                {
                    "label": f"Audio device {index + 1} manufacturer and driver",
                    "value": _join_values(
                        [
                            str(device.get("Manufacturer") or ""),
                            str(device.get("DriverProvider") or ""),
                            str(device.get("DriverVersion") or ""),
                            _human_date(device.get("DriverDate"))
                            if device.get("DriverDate")
                            else "",
                        ]
                    ),
                },
                {
                    "label": f"Audio device {index + 1} hardware ID",
                    "value": _display(device.get("InstanceId")),
                },
            ]
        )
    if not audio_rows:
        audio_rows.append(
            {"label": "Audio devices", "value": "Not reported by Windows"}
        )

    network_rows: list[HardwareRow] = []
    for index, adapter in enumerate(network_adapters):
        network_rows.extend(
            [
                {
                    "label": f"Network adapter {index + 1}",
                    "value": _join_values(
                        [
                            str(adapter.get("Description") or adapter.get("Name") or ""),
                            str(adapter.get("Status") or ""),
                        ]
                    ),
                },
                {
                    "label": f"Network adapter {index + 1} link speed",
                    "value": _display(adapter.get("LinkSpeed")),
                },
                {
                    "label": f"Network adapter {index + 1} hardware address",
                    "value": _display(adapter.get("MacAddress")),
                },
                {
                    "label": f"Network adapter {index + 1} driver and hardware ID",
                    "value": _join_values(
                        [
                            str(adapter.get("DriverProvider") or ""),
                            str(adapter.get("DriverVersion") or ""),
                            str(adapter.get("HardwareId") or ""),
                        ]
                    ),
                },
                {
                    "label": f"Network adapter {index + 1} transmit/receive buffer sizes",
                    "value": _UNAVAILABLE,
                },
            ]
        )
    if not network_rows:
        network_rows.append(
            {"label": "Physical network adapters", "value": "Not reported by Windows"}
        )

    battery_status = _as_record(battery_info.get("status"))
    battery_design = _positive_integer(battery_static.get("DesignedCapacity"))
    battery_full_capacity = _positive_integer(
        battery_full.get("FullChargedCapacity")
    )
    battery_wear = "Not reported by Windows"
    if (
        battery_design is not None
        and battery_full_capacity is not None
    ):
        wear_percent = max(
            0.0,
            (battery_design - battery_full_capacity) / battery_design * 100,
        )
        battery_wear = f"{wear_percent:.1f}%"
    battery_rows: list[HardwareRow] = [
        {
            "label": "Battery name and manufacturer",
            "value": _join_values(
                [
                    str(
                        battery_static.get("DeviceName")
                        or battery_status.get("Name")
                        or ""
                    ),
                    str(battery_static.get("ManufacturerName") or ""),
                ]
            ),
        },
        {
            "label": "Battery chemistry",
            "value": _display(battery_static.get("Chemistry")),
        },
        {
            "label": "Designed capacity",
            "value": _display(battery_design, " mWh"),
        },
        {
            "label": "Full-charge capacity",
            "value": _display(battery_full_capacity, " mWh"),
        },
        {"label": "Estimated wear", "value": battery_wear},
        {
            "label": "Charge cycles",
            "value": _display(battery_cycle.get("CycleCount")),
        },
    ]

    return [
        {
            "title": "Computer & motherboard",
            "rows": [
                {"label": "Computer brand and model", "value": computer_model},
                {"label": "Product code", "value": _display(computer.get("Model"))},
                {"label": "Motherboard manufacturer and model", "value": board_model},
                {"label": "Motherboard version", "value": _display(board.get("Version"))},
                {
                    "label": "Related chipset controller",
                    "value": _join_values([str(value) for value in chipset_controllers]),
                },
                {
                    "label": "SMBIOS-reported chassis slots",
                    "value": _join_values([str(value) for value in reported_slots]),
                },
                {
                    "label": "PCI Express controllers",
                    "value": _join_values([str(value) for value in pcie_controllers]),
                },
                {
                    "label": "PCI Express maximum version",
                    "value": _UNAVAILABLE,
                },
                {
                    "label": "Detected USB host controllers",
                    "value": _join_values([str(value) for value in usb_controllers]),
                },
                {
                    "label": "USB maximum supported version",
                    "value": _UNAVAILABLE,
                },
            ],
        },
        {
            "title": "Processor",
            "rows": [
                {"label": "Processor name", "value": _display(processor.get("Name"))},
                {
                    "label": "Vendor and processor signature",
                    "value": _join_values(
                        [
                            str(processor.get("Manufacturer") or ""),
                            str(processor.get("Caption") or ""),
                        ]
                    ),
                },
                {
                    "label": "Cores and logical processors",
                    "value": _join_values(
                        [
                            f"{processor['NumberOfCores']} cores"
                            if processor.get("NumberOfCores")
                            else "",
                            f"{processor['NumberOfLogicalProcessors']} logical processors"
                            if processor.get("NumberOfLogicalProcessors")
                            else "",
                        ]
                    ),
                },
                {
                    "label": "Reported maximum clock",
                    "value": _display(processor.get("MaxClockSpeed"), " MHz"),
                },
                {
                    "label": "Cached Windows clock snapshot",
                    "value": _display(processor.get("CurrentClockSpeed"), " MHz"),
                },
                {
                    "label": "L2 and L3 cache",
                    "value": _join_values(
                        [
                            f"L2 {_cache_size_mib(processor['L2CacheSize'])}"
                            if processor.get("L2CacheSize")
                            else "",
                            f"L3 {_cache_size_mib(processor['L3CacheSize'])}"
                            if processor.get("L3CacheSize")
                            else "",
                        ]
                    ),
                },
                {
                    "label": "SMBIOS socket designation",
                    "value": _display(processor.get("SocketDesignation")),
                },
                {
                    "label": "Virtualization extensions",
                    "value": _join_values(
                        [
                            f"Firmware virtualization {'available' if processor['VirtualizationFirmwareEnabled'] else 'not enabled'}"
                            if processor.get("VirtualizationFirmwareEnabled") is not None
                            else "",
                            f"VM monitor extensions {'available' if processor['VMMonitorModeExtensions'] else 'not reported'}"
                            if processor.get("VMMonitorModeExtensions") is not None
                            else "",
                            f"SLAT {'available' if processor['SecondLevelAddressTranslationExtensions'] else 'not reported'}"
                            if processor.get("SecondLevelAddressTranslationExtensions") is not None
                            else "",
                        ]
                    ),
                },
                {"label": "CPU feature flags and security mitigations", "value": _UNAVAILABLE},
                {"label": "TDP and live per-core clocks", "value": _UNAVAILABLE},
                {"label": "Processor code name and TDP", "value": _UNAVAILABLE},
            ],
        },
        {
            "title": "Memory",
            "rows": [
                {"label": "Installed memory", "value": f"{installed_memory_gib:.1f} GiB"},
                *[
                    {"label": f"Memory module {index + 1}", "value": value}
                    for index, value in enumerate(modules)
                ],
                {
                    "label": "Configured module speed",
                    "value": _join_values(
                        [
                            f"{module['ConfiguredClockSpeed'] or module.get('Speed')} MT/s"
                            for module in memory_modules
                            if module.get("ConfiguredClockSpeed") or module.get("Speed")
                        ]
                    ),
                },
                {
                    "label": "Reported memory slots",
                    "value": _join_values(
                        [
                            str(array["MemoryDevices"])
                            for array in memory_arrays
                            if array.get("MemoryDevices") is not None
                        ]
                    ),
                },
                {"label": "Memory channels and timings", "value": _UNAVAILABLE},
                {"label": "Maximum supported memory clock", "value": _UNAVAILABLE},
                {
                    "label": "Current DRAM timings and command rate",
                    "value": _UNAVAILABLE,
                },
                {"label": "Live DRAM clock and voltage", "value": _UNAVAILABLE},
                {
                    "label": "Maximum memory capacity",
                    "value": _join_values(
                        [
                            _size_gib(array["MaxCapacityEx"])
                            for array in memory_arrays
                            if _has_positive_integer(array.get("MaxCapacityEx"))
                        ]
                    ),
                },
            ],
        },
        {
            "title": "Graphics",
            "rows": graphics_detail_rows
            or [{"label": "Display adapter", "value": "Not reported by Windows"}],
        },
        {"title": "Displays", "rows": monitor_rows},
        {"title": "Audio", "rows": audio_rows},
        {"title": "Network", "rows": network_rows},
        {"title": "Power & battery", "rows": battery_rows},
        {
            "title": "Storage",
            "rows": [
                {
                    "label": f"Physical drive {index + 1}",
                    "value": value,
                }
                for index, value in enumerate(
                    _physical_disk_rows(inventory, disk_rows)
                )
            ]
            or [{"label": "Physical drives", "value": "Not reported by Windows"}],
        },
        {
            "title": "System & security",
            "rows": [
                {
                    "label": "Computer name",
                    "value": _display(computer.get("DNSHostName") or platform.node()),
                },
                {
                    "label": "Current user",
                    "value": _display(inventory.get("userName")),
                },
                {
                    "label": "Operating system",
                    "value": _join_values(
                        [
                            str(operating_system_info.get("Caption") or operating_system),
                            str(operating_system_info.get("Architecture") or architecture),
                        ]
                    ),
                },
                {"label": "Windows build", "value": os_build},
                {
                    "label": "Hypervisor",
                    "value": (
                        "A hypervisor is active; some hardware results may be virtualized"
                        if computer.get("HypervisorPresent") is True
                        else "Not reported by Windows"
                        if computer.get("HypervisorPresent") is None
                        else "No hypervisor reported"
                    ),
                },
                {
                    "label": "Hypervisor-protected Code Integrity (HVCI)",
                    "value": _available_hvci_state(device_guard),
                },
                {
                    "label": "BIOS manufacturer",
                    "value": _display(bios.get("Manufacturer")),
                },
                {
                    "label": "BIOS date",
                    "value": _display(bios.get("ReleaseDate")),
                },
                {
                    "label": "BIOS version",
                    "value": _display(
                        bios.get("SMBIOSBIOSVersion") or bios.get("Version")
                    ),
                },
                {
                    "label": "SMBIOS version",
                    "value": _join_values(
                        [
                            str(bios["SMBIOSMajorVersion"])
                            if bios.get("SMBIOSMajorVersion") is not None
                            else "",
                            str(bios["SMBIOSMinorVersion"])
                            if bios.get("SMBIOSMinorVersion") is not None
                            else "",
                        ],
                        "Not reported by Windows",
                    ),
                },
                {"label": "Firmware boot mode", "value": firmware_mode},
                {"label": "Secure Boot state", "value": secure_boot_state},
                {"label": "TPM", "value": tpm_state},
                {"label": "ACPI version", "value": _UNAVAILABLE},
            ],
        },
    ]


def get_hardware_sections(
    memory_total_gib: float,
    operating_system: str,
    os_version: str,
    architecture: str,
) -> tuple[list[HardwareSection], str]:
    """Return detailed hardware sections plus an inventory status message."""
    inventory = _cached_inventory()
    if inventory is None:
        if platform.system() != "Windows":
            return [], "Detailed hardware inventory is currently supported on Windows."
        with _inventory_lock:
            inventory = _inventory_result
            refreshing = _inventory_refreshing
        if inventory is None:
            return (
                [],
                "Windows hardware details are being collected in the background."
                if refreshing
                else "Detailed hardware inventory could not be read; see the local log.",
            )
    return (
        _build_sections(
            inventory,
            memory_total_gib,
            operating_system,
            os_version,
            architecture,
        ),
        "Hardware specifications refresh every 3 minutes; supported live sensors update every few seconds. Unavailable values are not inferred.",
    )


def get_hardware_inventory() -> dict[str, Any] | None:
    """Return the briefly cached raw Windows inventory for live interface matching."""
    return _cached_inventory()


_sensor_lock = Lock()
_sensor_result: dict[str, Any] = {}
_sensor_thread_started = False


def _query_live_windows_sensors() -> dict[str, Any]:
    executable = shutil.which("powershell.exe")
    if executable is None:
        log.warning("Windows PowerShell was not found; live GPU and battery sensors are unavailable")
        return {}

    with _sensor_lock, _windows_powershell_lock:
        try:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", _LIVE_SENSOR_SCRIPT],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            log.warning("Live Windows sensor query timed out after 10 seconds")
            return {}
        except OSError as error:
            log.warning("Live Windows sensor query failed: %s", error)
            return {}

    if result.returncode != 0:
        detail = result.stderr.strip() or f"PowerShell exited with code {result.returncode}"
        log.warning("Live Windows sensor query failed: %s", detail)
        return {}
    try:
        sensors = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        log.warning("Live Windows sensor query returned invalid JSON: %s", error)
        return {}
    if not isinstance(sensors, dict):
        log.warning("Live Windows sensor query returned an unexpected response")
        return {}
    sensors = cast(dict[str, Any], sensors)
    warnings = sensors.get("errors")
    if isinstance(warnings, list):
        for warning in cast(list[Any], warnings):
            if isinstance(warning, str):
                log.warning("Live Windows sensor is unavailable: %s", warning)
    return sensors


def _sample_windows_sensors() -> None:
    global _sensor_result
    while True:
        started = monotonic()
        result = _query_live_windows_sensors()
        result["sampled_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        with _sensor_lock:
            _sensor_result = result
        sleep(max(0, 3 - (monotonic() - started)))


def get_live_windows_sensors() -> dict[str, Any]:
    """Return the latest asynchronously sampled Windows GPU and battery sensors."""
    global _sensor_thread_started
    if platform.system() != "Windows":
        return {}
    with _sensor_lock:
        if not _sensor_thread_started:
            _sensor_thread_started = True
            Thread(
                target=_sample_windows_sensors,
                name="overpine-windows-sensors",
                daemon=True,
            ).start()
        return dict(_sensor_result)
