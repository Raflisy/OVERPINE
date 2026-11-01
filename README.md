# OVERPINE Project Software

Local hardware monitoring platform (FastAPI + browser dashboard).
Status: **Phase 3: local device dashboard.** The dashboard identifies the computer using locally reported manufacturer and model-family data, and detects the processor, graphics adapters, displays, audio/network devices, memory, battery, system drive, firmware, and operating system. Live telemetry includes CPU, memory, disk and network throughput, GPU 3D-engine utilization when Windows exposes it, and battery sensors when available.

The interface supports light and dark themes (following the system preference by default), remembers the selected theme, and uses a minimal system-font stack without external font downloads.

## Run (PowerShell, from this folder)

```powershell
.\run.ps1
```

If PowerShell blocks the script, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Open: http://127.0.0.1:8000  |  API docs: http://127.0.0.1:8000/docs

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Stress test

The Stress test page can load the CPU, memory, disk, and network together or individually. Workloads are generated locally: CPU work uses child processes, disk work writes and reads a temporary file under `data/stress/`, and network traffic uses a TCP server/client bound only to `127.0.0.1`. The page records real psutil and `IoRateSampler` readings and displays unavailable readings as unavailable.

Runs last 5–300 seconds at 10–100% intensity, with an absolute 300-second maximum. Memory allocation is capped at the selected intensity of currently available RAM and never exceeds 60% of the available amount measured when the workload starts. Disk files are capped at 1 GB by default; `StressManager(disk_max_bytes=...)` can lower or raise that application-level cap. Temporary disk files are removed during normal stop, completion, error, and application shutdown.

The watchdog stops workloads if available RAM falls below 15% of total memory, the system drive has less than 2 GB free, an unplugged battery is below 30%, or an available temperature sensor reports at least 95 °C. Temperature is reported as “temperature not monitored” when no sensor is exposed. Stopping manually or shutting down the app also terminates workers and releases their resources. No voltage, clock, fan, power-limit, BIOS, or firmware controls are changed.

GPU stress testing is unsupported for the Intel Iris Xe on this hardware/software interface. The UI and API explicitly report that state; no synthetic GPU utilization or load is generated. The stress API is local-only, and state-changing requests require the app-session `X-Overpine-Token` obtained by the loopback frontend.

## Desktop app

The native Windows desktop launcher serves the existing FastAPI frontend locally and opens it in a pywebview window using Edge WebView2. The window opens at 92% of the primary screen dimensions, and the dashboard layout expands on wider screens while adapting to narrower ones. Source development remains unchanged: `.\run.ps1` still starts Uvicorn in browser mode, and `.\run-desktop.ps1` starts the native window from the existing virtual environment.

Build the one-folder executable from PowerShell in this project directory:

```powershell
.\build-exe.ps1
```

Run the packaged application:

```powershell
.\dist\OVERPINE\OVERPINE.exe
```

Desktop-only build dependencies are listed in `requirements-desktop.txt`; the standard `requirements.txt` remains unchanged. Bundled frontend and default configuration files are read from the application resources. Logs, exports, and stress-test temporary data stay writable: source runs keep them under the project root, while frozen builds use `%LOCALAPPDATA%\OVERPINE`. A user-specific `config.toml` in that writable directory overrides bundled settings.

The native window requires the Microsoft Edge WebView2 runtime, which is preinstalled on Windows 11. If the runtime is missing or the native window cannot open, the launcher attempts to open the local dashboard in the default browser and displays a fallback message. The executable is unsigned, so Windows SmartScreen may warn; antivirus software may also flag unsigned builds.

The desktop app enforces a single running instance, binds only to `127.0.0.1`, and does not request administrator privileges by default. The top-bar administrator restart action is explicit, asks for confirmation, and invokes the Windows UAC prompt; elevated mode retains the same localhost-only and token protections.

## Phase 4: sensor discovery and monitoring

The Sensors page dynamically lists readings exposed by the operating system and supported local providers. It includes bounded per-session min/max/average statistics, bounded history charts, source/reliability labels, provider status, and pause/resume controls. Missing readings remain null and are shown with an explicit state; OVERPINE does not infer a temperature, fan speed, drive temperature, or power value.

For CPU package temperature and CPU package power, install LibreHardwareMonitor from its [official releases](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases), run it as Administrator, then enable **Options → Remote Web Server → Run** on port 8085. OVERPINE only connects to `127.0.0.1` or `localhost`; it never contacts an external host for hardware readings. The Windows ACPI thermal-zone provider is labelled as firmware-reported and may not reflect actual CPU temperature. SMART readings are off by default and require `smartctl` on PATH; enable them with `[smart] enabled = true` in the user config. Simulated readings are off by default and are always marked `SIMULATED` if explicitly enabled.

On the Lenovo ThinkPad L13 Yoga Gen 2, Intel i7-1165G7, and Iris Xe target, sensor exposure depends on the installed firmware, drivers, permissions, and LibreHardwareMonitor. In particular, standard Windows/psutil APIs often do not expose fan RPM or a true CPU/GPU temperature. OVERPINE discovers sensors at runtime and reports those readings as unavailable or unsupported rather than assuming they exist. Run `python -m backend.diagnose` for a real-data report; its output, not the model name, determines what this machine exposes.

The scheduler starts and stops with FastAPI, samples at configured category intervals, keeps a bounded history (default 3,600 samples per sensor), and backs off failing providers up to 60 seconds. Configuration is available in `[monitoring]`, `[monitoring.intervals]`, `[lhm]`, and `[smart]` in `config/default_config.toml` or the per-user `config.toml`. State-changing pause/resume requests require the app-session `X-Overpine-Token`.

Open the Sensors page at `http://127.0.0.1:8000/#sensors` while the local app is running.

## Folders

| Folder | Purpose |
|---|---|
| backend/ | Python backend (API, providers, monitoring, diagnostics, db) |
| frontend/ | Browser UI |
| config/ | default_config.toml |
| helper/ | Elevated sensor helper (later phase) |
| tests/ | pytest tests |
| docs/ | Documentation |
| data/ | SQLite DB and exports |
| logs/ | Log files |

## Current dashboard scope

- Live local connection status and CPU, memory, disk I/O, network throughput, GPU, and battery readings.
- Task Manager-inspired rolling 60-second graphs for CPU, memory, disk, network, and GPU, refreshed every few seconds from local Windows/psutil readings. Each graph includes current, average, and peak values for its rolling history.
- Graph controls let you show or hide individual metrics and choose separate colors for read/write and receive/send series; these preferences are saved in the browser and can be reset.
- Manufacturer and product-family logo images loaded from Wikimedia Commons based on detected device details; only public-domain or CC0 files are shown, with links to their source pages.
- Brand and product marks remain trademarks of their respective owners.
- Automatic model-family, OS, processor, graphics, memory, battery, storage, and firmware detection through the local backend.
- Expandable local hardware inventory for computer/user and OS details, hypervisor/HVCI and Secure Boot status, motherboard/controller names, CPU clocks and caches, individual RAM modules and configured speed, graphics drivers and hardware IDs, monitor EDID details, audio/network devices and drivers, physical disk type/health, BIOS/SMBIOS, boot mode, and available TPM/battery details.
- Hardware specifications are refreshed asynchronously from Windows every 3 minutes, so slow device queries do not pause live charts; live sensor readings update independently every few seconds. Windows does not expose detailed RAM timings/channels and live DRAM clock, ACPI revision, many GPU clocks/engine counts, display gamut/HDR capabilities, or some battery characteristics through standard interfaces. Those values are labeled unavailable rather than inferred; module transfer rates are reported as Windows provides them.
- Disk/network rates are calculated from successive local OS counters. GPU engine use and battery power details are read from Windows performance/WMI providers when present; no third-party telemetry service is used.
- A randomly selected device-specific fact with a Wikipedia source link, plus an original, manufacturer-aware device motto and clear OVERPINE attribution.
- Online fact lookup sends only the detected manufacturer and model to Wikipedia; the computer name and serial number are not sent.
- Light/dark theme toggle with saved preference.
- Temperature sensors still need an additional hardware-specific provider.
