# OVERPINE Project Software

Local hardware monitoring platform with a standard-library HTTP server and browser dashboard.
Version: **0.5.0**
Status: **Phase 3: local device dashboard.** The dashboard identifies the computer using locally reported manufacturer and model data, and detects the processor, graphics adapters, displays, audio/network devices, memory, battery, system drive, firmware, and operating system. Live telemetry includes CPU, memory, disk and network throughput, GPU 3D-engine utilization when Windows exposes it, and battery sensors when available.

The interface supports light and dark themes, remembers the selected theme in the local settings file, and uses a minimal system-font stack without external font downloads.

## Run (PowerShell, from this folder)

```powershell
.\run.ps1
```

The standard-library launcher can also be started with:

```powershell
python -m backend
```

If PowerShell blocks the script, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Open the printed local URL. API documentation is in [docs/api.md](docs/api.md); the application does not serve a `/docs` page.

## Test

```powershell
python -m unittest discover
```

## Stress test

The Stress test page can load the CPU, memory, disk, and network together or individually. Workloads are generated locally: CPU work uses child processes, disk work writes and reads a temporary file under `data/stress/`, and network traffic uses a TCP server/client bound only to `127.0.0.1`. It displays readings exposed by the local operating system and reports unavailable values as unavailable.

Runs last 5–300 seconds at 10–100% intensity, with an absolute 300-second maximum. Memory allocation is capped at the selected intensity of currently available RAM and never exceeds 60% of the available amount measured when the workload starts. Disk files are capped at 1 GB by default; `StressManager(disk_max_bytes=...)` can lower or raise that application-level cap. Temporary disk files are removed during normal stop, completion, error, and application shutdown.

The watchdog stops workloads if available RAM falls below 15% of total memory, the system drive has less than 2 GB free, an unplugged battery is below 30%, or an available temperature sensor reports at least 95 °C. Temperature is reported as “temperature not monitored” when no sensor is exposed. Stopping manually or shutting down the app also terminates workers and releases their resources. No voltage, clock, fan, power-limit, BIOS, or firmware controls are changed.

GPU stress testing is unsupported for the Intel Iris Xe on this hardware/software interface. The UI and API explicitly report that state; no synthetic GPU utilization or load is generated. The stress API is local-only, and state-changing requests require the app-session `X-Overpine-Token` obtained by the loopback frontend.

## Desktop app

The native desktop window and `.exe` build are paused until Step 6 and the lecturer's answer about PyInstaller. `.\run-desktop.ps1` and `.\build-exe.ps1` print this status and do not install packages or launch a build. Browser mode remains available through `.\run.ps1`.

## Phase 4: sensor discovery and monitoring

The Sensors page dynamically lists readings exposed by the operating system and supported local providers. It includes bounded per-session min/max/average statistics, bounded history charts, source/reliability labels, provider status, and pause/resume controls. Missing readings remain null and are shown with an explicit state; OVERPINE does not infer a temperature, fan speed, drive temperature, or power value.

Sensor availability depends on the hardware, Windows, installed drivers, and permissions. OVERPINE reports readings exposed by supported local sources; it does not recommend additional third-party sensor tools. Missing readings remain unavailable or unsupported rather than being inferred. The application does not provide simulated sensor readings.

On the Lenovo ThinkPad L13 Yoga Gen 2, Intel i7-1165G7, and Iris Xe target, sensor exposure depends on firmware, Windows, drivers, and permissions. Windows may not expose fan RPM or a true CPU/GPU temperature. OVERPINE discovers readings at runtime and reports unavailable or unsupported values rather than assuming they exist. Run `python -m backend.diagnose` for a real-data report; its output, not the model name, determines what this machine exposes.

The scheduler starts and stops with the application, samples at configured category intervals, keeps a bounded history (default 3,600 samples per sensor), and backs off failing providers up to 60 seconds. Configuration is available in `[monitoring]` and `[monitoring.intervals]` in `config/default_config.toml` or the per-user `config.toml`. State-changing pause/resume requests require the app-session `X-Overpine-Token`.

Open the Sensors page at `http://127.0.0.1:8000/#sensors` while the local app is running.

## Phase 4: persistent sensor history

The History page stores real sensor values and their states in the app-owned
SQLite database at `data/history.db` in source mode, or
`%LOCALAPPDATA%\OVERPINE\data\history.db` when frozen. Writes are batched
approximately every five seconds. By default, only the selected system-wide
CPU, memory, disk, network, battery, GPU, and available temperature sensors are
recorded. Unchanged values are stored at most once every ten seconds; changes
greater than 0.01 and state transitions are stored immediately. `NOT_INITIALIZED`
and per-process or per-counter detail sensors are not recorded. Raw readings are
kept for two minutes, one-minute summaries for roughly the next 10-15 minutes,
and five-minute summaries for older history. Missing readings remain null and
appear as gaps in the chart. The default retention is seven days and the hard
database-size limit is 100 MiB, including the database and WAL. Oldest rows are
pruned first when either limit is reached.

History defaults to on. Change `[history] enabled`, `record_sensors`,
`retention_days`, or `max_mb` in `config/default_config.toml` or the per-user
`config.toml` to adjust the defaults. The Settings page writes user changes to
`data/settings.json`, never to `config/default_config.toml`; history recording,
retention, size limit, and sensor selection apply after an app restart.
Storage errors are logged and shown on the History page without stopping live
monitoring. The History page can export the selected range as CSV, including
the sample state; clearing stored history requires the app-session token.
See [the History API](docs/api.md#persistent-history).

## Phase 5: About, Settings, and Help

The About page shows the app, Python, Windows, model, local data-folder and
history details. Its live import check runs the same scanner as
`python tools/check_imports.py`. Credits and course/university text can be
filled in under `[about]` in `config/default_config.toml`.

The Settings page controls history recording and limits, recorded sensors,
the refresh interval, theme, and optional online device-fact lookup. Editable
settings are saved in `data/settings.json`; history options take effect after
restart. Online device-fact lookup is off by default and is the only feature
that can use the network. Every settings change and reset requires the
app-session `X-Overpine-Token`.

The Help page describes sensor states, lists non-available sensors from live
discovery, and explains local storage, privacy, and run/test/diagnostic
commands. The pages are available from the top navigation. Their API endpoints
are documented in [docs/api.md](docs/api.md#about-and-application-settings).

Native battery-class IOCTL readings, storage-device temperature properties, and English PDH counters are probed at runtime. Unsupported, restricted, and not-yet-sampled sensors remain null with explicit states. The thermal-zone counter is firmware-reported and is not CPU-core temperature; fan RPM is reported unsupported without launching a program. PDH counter units are accepted only when Windows provides an explanation. See [docs/sensor-sources.md](docs/sensor-sources.md).

External Windows programs are disabled by default with `[windows_tools] allow_external_programs = false`. The diagnosis command can include counter/API paths and recorded error codes:

```powershell
python -m backend.diagnose
python -m backend.diagnose --verbose
```

## Folders

| Folder | Purpose |
|---|---|
| backend/ | Python backend (API, providers, monitoring, diagnostics, db) |
| frontend/ | Browser UI |
| config/ | default_config.toml |
| helper/ | Elevated sensor helper (later phase) |
| tests/ | standard-library unittest tests |
| docs/ | Documentation |
| data/ | SQLite DB and exports |
| logs/ | Log files |

## Current dashboard scope

- Live local connection status and CPU, memory, disk I/O, network throughput, GPU, and battery readings.
- Task Manager-inspired rolling 60-second graphs for CPU, memory, disk, network, and GPU, refreshed every few seconds from local operating-system readings. Each graph includes current, average, and peak values for its rolling history.
- Graph controls let you show or hide individual metrics and choose separate colors for read/write and receive/send series; these preferences are saved in the browser and can be reset.
- Automatic model-family, OS, processor, graphics, memory, battery, storage, and firmware detection through the local backend.
- Expandable local hardware inventory for computer/user and OS details, hypervisor/HVCI and Secure Boot status, motherboard/controller names, CPU clocks and caches, individual RAM modules and configured speed, graphics drivers and hardware IDs, monitor EDID details, audio/network devices and drivers, physical disk type/health, BIOS/SMBIOS, boot mode, and available TPM/battery details.
- Hardware specifications are refreshed asynchronously from Windows every 3 minutes, so slow device queries do not pause live charts; live sensor readings update independently every few seconds. Windows does not expose detailed RAM timings/channels and live DRAM clock, ACPI revision, many GPU clocks/engine counts, display gamut/HDR capabilities, or some battery characteristics through standard interfaces. Those values are labeled unavailable rather than inferred; module transfer rates are reported as Windows provides them.
- Disk/network rates are calculated from successive local OS counters. GPU engine use and battery power details are read from Windows performance/WMI providers when present; no third-party telemetry service is used.
- An optional, off-by-default device fact lookup, plus an original device motto and clear OVERPINE attribution. When enabled, only the detected manufacturer and model are used for the lookup; the computer name and serial number are not sent.
- Light/dark theme toggle with saved preference.
- Temperature sensors still need an additional hardware-specific provider.
