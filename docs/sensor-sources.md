# Windows sensor sources

This document describes how OVERPINE reads each native sensor, the units it
accepts, and what the operating system or device may not expose. Every row
currently says **verified on my laptop: no**. Only the user's own
`python -m backend.diagnose` output can establish what their computer exposes.

The implementation uses only the Python standard library and user-mode Windows
APIs. It does not install drivers or write hardware settings. External programs
are disabled by default; the native providers do not launch them.

## Battery-class sensors

| Sensor | Windows API/counter | Microsoft documentation checked | Unit | Admin needed | Limits | Verified on my laptop |
|---|---|---|---|---|---|---|
| Design capacity | SetupAPI battery-class interface; `IOCTL_BATTERY_QUERY_TAG`; `IOCTL_BATTERY_QUERY_INFORMATION` / `BatteryInformation` | [Battery Information](https://learn.microsoft.com/en-us/windows/win32/power/battery-information); [IOCTL_BATTERY_QUERY_TAG](https://learn.microsoft.com/en-us/windows/win32/power/ioctl-battery-query-tag); [IOCTL_BATTERY_QUERY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/power/ioctl-battery-query-information); [BATTERY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/power/battery-information-str) | mWh, or relative units when `BATTERY_CAPACITY_RELATIVE` is set | No admin requested; device ACL may restrict access | Empty battery ports are not batteries. Unknown capacity is null. | no |
| Full-charge capacity | `IOCTL_BATTERY_QUERY_INFORMATION` / `BatteryInformation` | [BATTERY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/power/battery-information-str) | mWh, or relative units | No admin requested; device ACL may restrict access | Unknown capacity is null. | no |
| Cycle count | `IOCTL_BATTERY_QUERY_INFORMATION` / `BatteryInformation` | [BATTERY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/power/battery-information-str) | cycles | No admin requested; device ACL may restrict access | Windows documents zero as unsupported; zero is not shown as a measured cycle count. | no |
| Current capacity | `IOCTL_BATTERY_QUERY_STATUS` / `BATTERY_STATUS.Capacity`, divided by full-charge capacity | [IOCTL_BATTERY_QUERY_STATUS](https://learn.microsoft.com/en-us/windows/win32/power/ioctl-battery-query-status); [BATTERY_STATUS](https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str) | % | No admin requested; device ACL may restrict access | Relative-capacity batteries use a ratio only; unknown or zero denominator produces null. | no |
| Voltage | `IOCTL_BATTERY_QUERY_STATUS` / `BATTERY_STATUS.Voltage` | [BATTERY_STATUS](https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str) | mV | No admin requested; device ACL may restrict access | `BATTERY_UNKNOWN_VOLTAGE` produces null. | no |
| Charge/discharge rate | `IOCTL_BATTERY_QUERY_STATUS` / `BATTERY_STATUS.Rate` | [BATTERY_STATUS](https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str) | mW, or relative units/hour | No admin requested; device ACL may restrict access | Positive is charging, negative is discharging. `BATTERY_UNKNOWN_RATE` produces null. | no |
| Power state | `IOCTL_BATTERY_QUERY_STATUS` / `BATTERY_STATUS.PowerState` | [BATTERY_STATUS](https://learn.microsoft.com/en-us/windows/win32/power/battery-status-str) | Windows state flags | No admin requested; device ACL may restrict access | Flags are reported as flags, not inferred as a health score. | no |
| Battery wear | Estimate from valid design and full-charge capacities | [BATTERY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/power/battery-information-str) | % | Same as capacity queries | `(design - full charge) / design * 100`; shown only for valid, consistent capacities. Reliability is `calculated-estimate`, not a direct sensor reading. | no |

The battery class enumerates battery **ports**, which can be empty. The
provider queries a zero-timeout tag before reading data, reports an empty slot
as no battery, and does not wait indefinitely.

## Storage temperature

| Sensor | Windows API/counter | Microsoft documentation checked | Unit | Admin needed | Limits | Verified on my laptop |
|---|---|---|---|---|---|---|
| Physical-drive temperature | SetupAPI `GUID_DEVINTERFACE_DISK`; `IOCTL_STORAGE_GET_DEVICE_NUMBER`; `IOCTL_STORAGE_QUERY_PROPERTY` with `StorageDeviceTemperatureProperty` | [GUID_DEVINTERFACE_DISK](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/guid-devinterface-disk); [IOCTL_STORAGE_GET_DEVICE_NUMBER](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_get_device_number); [STORAGE_DEVICE_NUMBER](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-storage_device_number); [IOCTL_STORAGE_QUERY_PROPERTY](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_query_property); [STORAGE_PROPERTY_ID](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ne-winioctl-storage_property_id); [STORAGE_TEMPERATURE_DATA_DESCRIPTOR](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ns-ntddstor-_storage_temperature_data_descriptor); [STORAGE_TEMPERATURE_INFO](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddstor/ns-ntddstor-_storage_temperature_info) | °C | Opens `\\.\PhysicalDriveN` with zero requested access first; access denied is reported as `PERMISSION_RESTRICTED` | Unsupported property is `UNSUPPORTED`. No temperature or free-space reading is interpreted as drive health. | no |

## PDH counters

English counter paths are passed to PDH and registered only when Windows
exposes them. `PdhGetCounterInfoW` supplies each counter's explanation; a unit
conversion is performed only when that explanation confirms the unit. Microsoft
Learn describes the PDH/performance-counter interface, while the precise
explanation is supplied by the local Windows counter provider.

| Sensor | English PDH counter path | Microsoft documentation checked | Unit | Admin needed | Limits | Verified on my laptop |
|---|---|---|---|---|---|---|
| ACPI thermal-zone temperature | `\Thermal Zone Information(*)\Temperature` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | Kelvin from the counter explanation, converted to °C | No admin requested; counter ACL may restrict access | Reliability `reported-by-firmware`; note: “ACPI thermal zone: may not equal the CPU core temperature.” Never labeled as CPU-core temperature. | no |
| Passive limit | `\Thermal Zone Information(*)\% Passive Limit` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | % | No admin requested; counter ACL may restrict access | Counter explanation defines 100% as unconstrained and 0% as fully constrained. | no |
| Throttle reasons | `\Thermal Zone Information(*)\Throttle Reasons` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | Boolean presence derived from the reported counter | No admin requested; counter ACL may restrict access | Does not identify a CPU-core temperature or a specific throttle cause. | no |
| CPU maximum-frequency percentage | `\Processor Information(_Total)\% of Maximum Frequency` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | % | No admin requested; counter ACL may restrict access | Windows warns this counter can be inaccurate when processors regulate frequency outside Windows control. | no |
| Energy-meter power | `\Energy Meter(*)\Power` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | mW from the counter explanation, converted to W | No admin requested; counter ACL may restrict access | Zero for `DRAM` and `_Total` instances is `UNAVAILABLE` with the note “reported 0; this meter may not be measured on this hardware”. | no |
| Power-meter power | `\Power Meter(*)\Power` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | mW from the counter explanation, converted to W | No admin requested; counter ACL may restrict access | Zero `_Total` is reported as potentially unmeasured, not as 0 W. | no |
| GPU engine utilization | `\GPU Engine(*)\Utilization Percentage` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | % | No admin requested; counter ACL may restrict access | Per process/engine instances are not combined into a fabricated total. | no |
| Dedicated GPU memory | `\GPU Adapter Memory(*)\Dedicated Usage` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | Not published unless the explanation states a unit | No admin requested; counter ACL may restrict access | The provider explanation observed during implementation did not state a unit. The value is left null and is not converted or labeled as bytes. | no |
| Shared GPU memory | `\GPU Adapter Memory(*)\Shared Usage` | [About Performance Counters](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters); [PdhGetCounterInfoW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetcounterinfow) | Not published unless the explanation states a unit | No admin requested; counter ACL may restrict access | The provider explanation observed during implementation did not state a unit. The value is left null and is not converted or labeled as bytes. | no |

PDH rate counters require two samples at least one second apart. A first sample
is `NOT_INITIALIZED` with “no data yet”, not an unavailable reading.

## Derived and unsupported sensors

| Sensor | Inputs/API | Unit | Limits | Verified on my laptop |
|---|---|---|---|---|
| CPU throttling (estimated) | `GetSystemTimes` CPU utilization, PDH `% of Maximum Frequency`, and either `% Passive Limit` or `Throttle Reasons` | Boolean | Available only when all inputs exist. High load is defined as at least 80%; the estimate is true only when frequency is below 100% and a limiter is active. Reliability is `calculated-estimate`. | no |
| Fan speed | No API or process is queried | RPM | Always `UNSUPPORTED` with “Windows does not expose fan speed on this computer.” | no |
