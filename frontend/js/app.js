import { initializeStressPage } from "./pages/stress.js";
import { initializeSensorsPage } from "./pages/sensors.js";
import { initializeHistoryPage } from "./pages/history.js";
import { initializeAboutPage } from "./pages/about.js";
import { initializeSettingsPage } from "./pages/settings.js";
import { initializeHelpPage } from "./pages/help.js";
import { initializeDiagnosticsPage } from "./pages/diagnostics.js";
import { initializeDiagnosticsPage } from "./pages/diagnostics.js";
initializeDiagnosticsPage();
import { LineChart } from "./charts/line-chart.js";

const themeStorageKey = "overpine-theme";
const root = document.documentElement;
const themeToggle = document.getElementById("theme-toggle");
const themeLabel = document.getElementById("theme-toggle-label");
const adminRestartButton = document.getElementById("restart-admin");
const adminRestartStatus = document.getElementById("admin-restart-status");
const monitorHistory = [];
const monitorHistoryWindowMs = 60_000;
const monitorCharts = new Map();
const graphSettingsStoragePrefix = "overpine-graph-";
const hardwareSectionsStorageKey = "overpine-hardware-sections";
const rightHardwareSectionTitles = new Set([
  "Processor",
  "Graphics",
  "Audio",
  "Power & battery",
  "System & security",
]);
const onlineLookupToggle = document.getElementById("online-device-lookups");
const onlineLookupStatus = document.getElementById("online-lookup-status");
const monitorGraphs = [
  {
    key: "cpu",
    title: "Processor",
    cardId: "cpu-monitor-card",
    statsId: "cpu-chart-stats",
    explanationId: "cpu-chart-explanation",
    explanation: "Total CPU utilization reported by the operating system. Clock speed is shown in the current reading when available.",
    series: [
      { key: "cpu", label: "CPU", color: "#43b8d2", variable: "--chart-cpu-color", unit: "percent" },
    ],
  },
  {
    key: "memory",
    title: "Memory",
    cardId: "memory-monitor-card",
    statsId: "memory-chart-stats",
    explanationId: "memory-chart-explanation",
    explanation: "Physical memory in use, with the current in-use and available capacity shown above.",
    series: [
      { key: "memory", label: "In use", color: "#4a91d9", variable: "--chart-memory-color", unit: "percent" },
    ],
  },
  {
    key: "disk",
    title: "Disk",
    cardId: "disk-monitor-card",
    statsId: "disk-chart-stats",
    explanationId: "disk-chart-explanation",
    explanation: "Read and write throughput calculated from successive operating-system disk counters.",
    series: [
      { key: "diskRead", label: "Read", color: "#91b75b", variable: "--chart-disk-read-color", unit: "rate" },
      { key: "diskWrite", label: "Write", color: "#d7a348", variable: "--chart-disk-write-color", unit: "rate" },
    ],
  },
  {
    key: "network",
    title: "Network",
    cardId: "network-monitor-card",
    statsId: "network-chart-stats",
    explanationId: "network-chart-explanation",
    explanation: "Receive and send throughput across active physical network interfaces.",
    series: [
      { key: "networkReceive", label: "Receive", color: "#d65b83", variable: "--chart-network-receive-color", unit: "rate" },
      { key: "networkSend", label: "Send", color: "#f09ab3", variable: "--chart-network-send-color", unit: "rate" },
    ],
  },
  {
    key: "gpu",
    title: "GPU 3D",
    cardId: "gpu-monitor-card",
    statsId: "gpu-chart-stats",
    explanationId: "gpu-chart-explanation",
    explanation: "3D-engine utilization from Windows performance counters. A missing reading means Windows did not expose a supported sensor.",
    series: [
      { key: "gpu", label: "3D engine", color: "#aa72c8", variable: "--chart-gpu-color", unit: "percent" },
    ],
  },
];
let messageForModel = "";
let deviceNoteRetryAt = 0;
let onlineLookupsEnabled = false;
let onlineLookupsReady = Promise.resolve();
let warnedStorageUnavailable = false;
let currentAppSettings;
let dashboardRefreshTimer;
let dashboardRefreshIntervalSeconds = 3;
let dashboardUpdating = false;

function warnStorageUnavailable(error) {
  if (warnedStorageUnavailable) {
    return;
  }
  warnedStorageUnavailable = true;
  console.warn(
    "Browser preferences could not be accessed; using in-memory defaults.",
    error,
  );
}

function readStoredValue(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    warnStorageUnavailable(error);
    return null;
  }
}

function writeStoredValue(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    warnStorageUnavailable(error);
  }
}

function removeStoredValue(key) {
  try {
    window.localStorage.removeItem(key);
  } catch (error) {
    warnStorageUnavailable(error);
  }
}

function setTheme(theme) {
  const isDark = theme === "dark";
  root.dataset.theme = isDark ? "dark" : "light";
  themeToggle.setAttribute("aria-pressed", String(isDark));
  themeToggle.setAttribute(
    "aria-label",
    `Switch to ${isDark ? "light" : "dark"} mode`,
  );
  themeLabel.textContent = `${isDark ? "Light" : "Dark"} mode`;
  document.querySelector('meta[name="theme-color"]').content = isDark
    ? "#000000"
    : "#f5f2eb";
}

const savedTheme = readStoredValue(themeStorageKey);
setTheme(savedTheme === "light" || savedTheme === "dark"
  ? savedTheme
  : "dark");

themeToggle.addEventListener("click", async () => {
  const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
  setTheme(nextTheme);
  try {
    await saveAppSettings({ theme: nextTheme });
    writeStoredValue(themeStorageKey, nextTheme);
  } catch (error) {
    setTheme(currentAppSettings?.theme || (nextTheme === "dark" ? "light" : "dark"));
    console.error("Could not save theme setting:", error);
  }
});

async function responseMessage(response, fallback) {
  const body = (await response.text()).trim();
  if (!body) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(body);
    return parsed.error?.message || parsed.detail || parsed.message || fallback;
  } catch {
    return body.slice(0, 240);
  }
}

async function loadAppSettings() {
  const response = await fetch("/api/settings", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await responseMessage(
      response,
      `Could not load settings (HTTP ${response.status})`,
    ));
  }
  const body = await response.json();
  if (!body.settings || typeof body.settings !== "object") {
    throw new Error("The server returned invalid settings");
  }
  return body.settings;
}

const appSettingsReady = loadAppSettings().then((settings) => {
  window.dispatchEvent(new CustomEvent("overpine-settings-updated", {
    detail: settings,
  }));
  return settings;
});
appSettingsReady.catch((error) => {
  console.error("Could not load saved application settings:", error);
});

function applyAppSettings(settings) {
  currentAppSettings = settings;
  onlineLookupsEnabled = settings.online_device_fact_lookup === true;
  dashboardRefreshIntervalSeconds = settings.refresh_interval_seconds;
  window.overpineRefreshIntervalSeconds = dashboardRefreshIntervalSeconds;
  setTheme(settings.theme);
  scheduleDashboardRefresh(dashboardRefreshIntervalSeconds);
  onlineLookupToggle.checked = onlineLookupsEnabled;
  onlineLookupStatus.textContent = onlineLookupsEnabled
    ? "Online device facts are enabled."
    : "Off by default. When disabled, no online device fact lookup is made.";
}

window.addEventListener("overpine-settings-updated", (event) => {
  applyAppSettings(event.detail);
});

async function saveAppSettings(changes) {
  await appSettingsReady;
  const token = await getSessionToken();
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Overpine-Token": token,
    },
    body: JSON.stringify({ ...currentAppSettings, ...changes }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await responseMessage(
      response,
      `Could not save settings (HTTP ${response.status})`,
    ));
  }
  const body = await response.json();
  if (!body.settings) {
    throw new Error("The server did not confirm the saved settings");
  }
  window.dispatchEvent(new CustomEvent("overpine-settings-updated", {
    detail: body.settings,
  }));
  return body;
}

function scheduleDashboardRefresh(seconds) {
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 60) return;
  if (dashboardRefreshTimer) {
    window.clearInterval(dashboardRefreshTimer);
  }
  dashboardRefreshTimer = window.setInterval(updateDashboard, seconds * 1000);
}

adminRestartButton.addEventListener("click", async () => {
  if (!window.confirm("Restart OVERPINE as administrator? Windows will ask you to approve the elevation.")) {
    return;
  }
  adminRestartButton.disabled = true;
  adminRestartStatus.textContent = "Requesting Windows administrator approval…";
  try {
    const tokenResponse = await fetch("/api/v1/stress/token", { cache: "no-store" });
    if (!tokenResponse.ok) {
      throw new Error(await responseMessage(
        tokenResponse,
        `Could not get session token (HTTP ${tokenResponse.status})`,
      ));
    }
    const { token } = await tokenResponse.json();
    const response = await fetch("/api/v1/admin/restart-as-admin", {
      method: "POST",
      headers: { "X-Overpine-Token": token },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(await responseMessage(
        response,
        `Administrator restart returned HTTP ${response.status}`,
      ));
    }
    adminRestartStatus.textContent = "Restart requested. Approve the Windows prompt if shown.";
  } catch (error) {
    adminRestartStatus.textContent = `Administrator restart failed: ${error.message}`;
    adminRestartButton.disabled = false;
  }
});

function graphPreferenceKey(graphKey, preference) {
  return `${graphSettingsStoragePrefix}${graphKey}-${preference}`;
}

function savedGraphColor(graph, series) {
  const saved = readStoredValue(
    graphPreferenceKey(graph.key, `color-${series.key}`),
  );
  return /^#[0-9a-f]{6}$/i.test(saved || "") ? saved : series.color;
}

function formatMonitorValue(value, unit) {
  if (value === null || !Number.isFinite(value)) return "—";
  if (unit === "percent") return `${value.toFixed(1)}%`;
  if (value >= 1024 ** 2) return `${(value / (1024 ** 2)).toFixed(2)} MiB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB/s`;
  return `${value.toFixed(0)} B/s`;
}

function createMonitorStatistics(graph) {
  const series = graph.series.map((item) => ({
    ...item,
    cssVariable: item.variable,
    format: (value) => formatMonitorValue(value, item.unit),
  }));
  const chart = new LineChart(
    document.getElementById(`${graph.key}-monitor-chart`),
    {
      series,
      timeWindowMs: monitorHistoryWindowMs,
      statsElement: document.getElementById(graph.statsId),
    },
  );
  monitorCharts.set(graph.key, chart);
  document.getElementById(graph.explanationId).textContent = graph.explanation;
}

function initializeGraphSettings() {
  const settingsList = document.getElementById("graph-settings-list");
  for (const graph of monitorGraphs) {
    const setting = document.createElement("section");
    setting.className = "graph-setting";

    const heading = document.createElement("div");
    heading.className = "graph-setting-heading";
    const visibilityLabel = document.createElement("label");
    const visibility = document.createElement("input");
    visibility.type = "checkbox";
    visibility.id = `graph-visible-${graph.key}`;
    visibility.checked = readStoredValue(
      graphPreferenceKey(graph.key, "visible"),
    ) !== "false";
    visibilityLabel.append(visibility, document.createTextNode(graph.title));
    heading.append(visibilityLabel);
    setting.append(heading);

    const card = document.getElementById(graph.cardId);
    card.hidden = !visibility.checked;
    visibility.addEventListener("change", () => {
      card.hidden = !visibility.checked;
      writeStoredValue(
        graphPreferenceKey(graph.key, "visible"),
        String(visibility.checked),
      );
    });

    for (const series of graph.series) {
      const colorLabel = document.createElement("label");
      colorLabel.className = "graph-series-setting";
      const label = document.createElement("span");
      label.textContent = `${series.label} color`;
      const color = document.createElement("input");
      color.type = "color";
      color.id = `graph-series-color-${graph.key}-${series.key}`;
      color.value = savedGraphColor(graph, series);
      card.style.setProperty(series.variable, color.value);
      colorLabel.append(label, color);
      setting.append(colorLabel);
      color.addEventListener("input", () => {
        card.style.setProperty(series.variable, color.value);
        monitorCharts.get(graph.key)?.draw();
        writeStoredValue(
          graphPreferenceKey(graph.key, `color-${series.key}`),
          color.value,
        );
      });
    }
    settingsList.append(setting);
    createMonitorStatistics(graph);
  }

  document.getElementById("reset-graph-settings").addEventListener("click", () => {
    for (const graph of monitorGraphs) {
      removeStoredValue(graphPreferenceKey(graph.key, "visible"));
      const setting = document.getElementById(`graph-visible-${graph.key}`).closest(".graph-setting");
      const visibility = document.getElementById(`graph-visible-${graph.key}`);
      const card = document.getElementById(graph.cardId);
      visibility.checked = true;
      card.hidden = false;
      for (const [index, series] of graph.series.entries()) {
        removeStoredValue(
          graphPreferenceKey(graph.key, `color-${series.key}`),
        );
        setting.querySelectorAll('input[type="color"]')[index].value = series.color;
        card.style.setProperty(series.variable, series.color);
      }
    }
  });
}

function formatUptime(seconds) {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${remainingSeconds}s`;
}

function setMeter(id, value) {
  const meter = document.getElementById(id);
  const percentage = Math.min(100, Math.max(0, Number(value) || 0));
  meter.style.width = `${percentage}%`;
  meter.parentElement.setAttribute("aria-valuenow", percentage.toFixed(1));
}

function setConnectionStatus(connected, message) {
  const status = document.getElementById("backend-status");
  const indicator = document.getElementById("status-indicator");
  status.textContent = connected ? "Your device is here" : "Device unavailable";
  indicator.className = `status-dot ${connected ? "status-dot--connected" : "status-dot--error"}`;
  document.getElementById("status-message").textContent = message;
}

async function getSessionToken() {
  const response = await fetch("/api/v1/stress/token", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not get session token (HTTP ${response.status})`);
  }
  const body = await response.json();
  if (typeof body.token !== "string" || !body.token) {
    throw new Error("The server returned no session token");
  }
  return body.token;
}

async function initializeOnlineLookupSetting() {
  onlineLookupToggle.disabled = true;
  try {
    const settings = await appSettingsReady;
    onlineLookupsEnabled = settings.online_device_fact_lookup === true;
    onlineLookupToggle.checked = onlineLookupsEnabled;
    onlineLookupStatus.textContent = onlineLookupsEnabled
      ? "Online device facts are enabled."
      : "Off by default. When disabled, no online device fact lookup is made.";
  } catch (error) {
    onlineLookupToggle.checked = false;
    onlineLookupStatus.textContent = `Could not read online lookup settings: ${error.message}`;
    throw error;
  } finally {
    onlineLookupToggle.disabled = false;
  }
}

onlineLookupToggle.addEventListener("change", async () => {
  const enabled = onlineLookupToggle.checked;
  const previousEnabled = onlineLookupsEnabled;
  onlineLookupToggle.disabled = true;
  onlineLookupStatus.textContent = "Updating online lookup preference…";
  if (!enabled) {
    onlineLookupsEnabled = false;
    messageForModel = "";
    deviceNoteRetryAt = 0;
    document.getElementById("device-fact").textContent =
      "Online device facts are off in Settings.";
    document.getElementById("device-fact-source").hidden = true;
    document.getElementById("device-motto").textContent =
      "Quietly getting things done.";
  }
  try {
    await saveAppSettings({ online_device_fact_lookup: enabled });
    if (enabled) {
      deviceNoteRetryAt = 0;
      await updateDashboard();
    }
  } catch (error) {
    onlineLookupsEnabled = previousEnabled;
    onlineLookupToggle.checked = previousEnabled;
    onlineLookupStatus.textContent =
      `Could not update online lookup preference: ${error.message}`;
    if (previousEnabled) {
      await updateDashboard();
    }
  } finally {
    onlineLookupToggle.disabled = false;
  }
});

function updateMonitorCharts(system) {
  const now = Date.now();
  const percentage = (value) => (
    Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null
  );
  const rate = (value) => (
    Number.isFinite(value) ? Math.max(0, value) : null
  );
  monitorHistory.push({
    time: now,
    cpu: percentage(system.cpu_usage_percent),
    memory: percentage(system.memory_used_percent),
    diskRead: rate(system.disk_read_bytes_per_sec),
    diskWrite: rate(system.disk_write_bytes_per_sec),
    networkReceive: rate(system.network_receive_bytes_per_sec),
    networkSend: rate(system.network_send_bytes_per_sec),
    gpu: percentage(system.gpu_usage_percent),
  });
  while (monitorHistory.length > 1 && now - monitorHistory[0].time > monitorHistoryWindowMs) {
    monitorHistory.shift();
  }

  for (const graph of monitorGraphs) {
    const chart = monitorCharts.get(graph.key);
    chart?.update(monitorHistory.map((sample) => ({
      time: sample.time,
      values: Object.fromEntries(
        graph.series.map((series) => [series.key, sample[series.key]]),
      ),
    })));
  }

  const latest = monitorHistory[monitorHistory.length - 1];
  document.getElementById("cpu-chart-current").textContent =
    `${latest.cpu === null ? "Unavailable" : `${latest.cpu.toFixed(0)}%`}${Number.isFinite(system.cpu_frequency_mhz) ? ` · ${(system.cpu_frequency_mhz / 1000).toFixed(2)} GHz` : ""}`;
  document.getElementById("memory-chart-current").textContent =
    latest.memory === null ? "Unavailable" : `${latest.memory.toFixed(0)}%`;
  const hasMemoryCapacity = Number.isFinite(system.memory_total_gib)
    && Number.isFinite(system.memory_available_gib);
  const memoryTotal = hasMemoryCapacity ? system.memory_total_gib : null;
  const memoryUsed = hasMemoryCapacity
    ? Math.max(0, memoryTotal - system.memory_available_gib)
    : null;
  const memoryAvailable = hasMemoryCapacity
    ? Math.max(0, system.memory_available_gib)
    : null;
  document.getElementById("memory-composition-used").style.width =
    latest.memory === null ? "0%" : `${latest.memory}%`;
  document.querySelector(".memory-composition").setAttribute(
    "aria-label",
    hasMemoryCapacity
      ? `${memoryUsed.toFixed(1)} GiB in use, ${memoryAvailable.toFixed(1)} GiB available`
      : "Memory capacity unavailable",
  );
  document.getElementById("memory-composition-used-label").textContent =
    hasMemoryCapacity ? `In use ${memoryUsed.toFixed(1)} GiB` : "In use unavailable";
  document.getElementById("memory-composition-available-label").textContent =
    hasMemoryCapacity
      ? `Available ${memoryAvailable.toFixed(1)} GiB`
      : "Available unavailable";
  document.getElementById("disk-chart-current").textContent =
    `R ${formatMonitorValue(latest.diskRead, "rate")} · W ${formatMonitorValue(latest.diskWrite, "rate")}`;
  document.getElementById("network-chart-current").textContent =
    `↓ ${formatMonitorValue(latest.networkReceive, "rate")} · ↑ ${formatMonitorValue(latest.networkSend, "rate")}`;
  document.getElementById("gpu-chart-current").textContent =
    latest.gpu === null
      ? system.sensor_sampled_at ? "Unavailable" : "Sampling…"
      : `${latest.gpu.toFixed(0)}%`;
}

function readOpenHardwareSections() {
  const saved = readStoredValue(hardwareSectionsStorageKey);
  if (saved === null) {
    return null;
  }
  try {
    const titles = JSON.parse(saved);
    return Array.isArray(titles)
      ? new Set(titles.filter((title) => typeof title === "string"))
      : null;
  } catch (error) {
    warnStorageUnavailable(error);
    return null;
  }
}

function saveOpenHardwareSections(grid) {
  const openTitles = [...grid.querySelectorAll("details[open]")]
    .map((details) => details.dataset.sectionTitle);
  writeStoredValue(hardwareSectionsStorageKey, JSON.stringify(openTitles));
}

function renderHardwareDetails(system) {
  const grid = document.getElementById("hardware-details-grid");
  const hasPreviousSections = grid.querySelector("details") !== null;
  const previousOpenSections = new Set(
    [...grid.querySelectorAll("details[open]")]
      .map((details) => details.dataset.sectionTitle),
  );
  const savedOpenSections = readOpenHardwareSections();
  const openSections = savedOpenSections
    ?? (hasPreviousSections ? previousOpenSections : null);
  const sections = Array.isArray(system.hardware_sections)
    ? system.hardware_sections
    : [];
  const columns = [
    document.createElement("div"),
    document.createElement("div"),
  ];
  columns[0].className = "hardware-details-column";
  columns[1].className = "hardware-details-column";
  grid.replaceChildren(...columns);

  for (const [index, section] of sections.entries()) {
    const details = document.createElement("details");
    details.className = "hardware-details-card";
    details.dataset.sectionTitle = section.title;
    details.style.order = String(index);
    details.open = openSections
      ? openSections.has(section.title)
      : index === 0;

    const heading = document.createElement("summary");
    heading.textContent = section.title;
    heading.setAttribute("aria-expanded", String(details.open));
    details.addEventListener("toggle", () => {
      heading.setAttribute("aria-expanded", String(details.open));
      saveOpenHardwareSections(grid);
    });
    details.append(heading);

    const rows = document.createElement("dl");
    rows.className = "hardware-details-list";
    for (const row of section.rows || []) {
      const item = document.createElement("div");
      item.className = "hardware-details-row";

      const label = document.createElement("dt");
      label.textContent = row.label;
      const value = document.createElement("dd");
      value.textContent = row.value;
      if (String(row.value).startsWith("Not ")) {
        value.classList.add("hardware-value--unavailable");
      }

      item.append(label, value);
      rows.append(item);
    }
    details.append(rows);
    const column = rightHardwareSectionTitles.has(section.title)
      ? columns[1]
      : columns[0];
    column.append(details);
  }
  document.getElementById("hardware-inventory-status").textContent =
    system.hardware_inventory_status
      || "Extended hardware details are not available.";
}

function renderSystemInfo(system) {
  renderHardwareDetails(system);
  document.getElementById("page-title").textContent =
    system.device_model === "Not detected"
      ? "Your computer, up close."
      : system.device_model;
  document.getElementById("device-manufacturer").textContent =
    system.manufacturer === "Not detected"
      ? "Computer profile"
      : system.manufacturer;
  document.getElementById("device-hostname").textContent =
    `Device name · ${system.device_name}`;
  document.getElementById("processor-value").textContent = system.processor;
  document.getElementById("core-details").textContent =
    `${system.physical_cores} cores · ${system.logical_processors} threads`;

  const graphics = Array.isArray(system.graphics_adapters)
    ? system.graphics_adapters
    : [];
  document.getElementById("graphics-value").textContent =
    graphics.length > 0 ? graphics.join(" · ") : "No adapter reported";

  document.getElementById("os-value").textContent =
    `${system.operating_system} · ${system.architecture}`;
  document.getElementById("os-details").textContent =
    `Build ${system.os_version}`;
  document.getElementById("bios-value").textContent = system.bios_version;

  const drive = system.system_drive;
  document.getElementById("drive-value").textContent = drive
    ? drive.mountpoint
    : "Not detected";
  document.getElementById("drive-details").textContent = drive
    ? `${drive.total_gib} GiB total · ${drive.free_gib} GiB free`
    : "System drive details unavailable";

  const cpu = Number(system.cpu_usage_percent);
  document.getElementById("system-cpu-value").innerHTML =
    `${cpu.toFixed(1)}<span>%</span>`;
  setMeter("cpu-meter", cpu);

  const memoryTotal = Number(system.memory_total_gib);
  const memoryAvailable = Number(system.memory_available_gib);
  const memoryUsed = Math.max(0, memoryTotal - memoryAvailable);
  document.getElementById("memory-used-value").innerHTML =
    `${memoryUsed.toFixed(1)}<span> GiB</span>`;
  document.getElementById("memory-caption").textContent =
    `of ${memoryTotal.toFixed(1)} GiB installed`;
  setMeter("memory-meter", system.memory_used_percent);
  updateMonitorCharts(system);

  const battery = system.battery;
  const batteryValue = document.getElementById("battery-value");
  const batteryCaption = document.getElementById("battery-caption");
  const batteryMeter = document.getElementById("battery-meter");
  if (battery && Number.isFinite(battery.percent)) {
    batteryValue.innerHTML = `${Number(battery.percent).toFixed(0)}<span>%</span>`;
    const chargeRate = Number(battery.charge_rate_mw);
    const powerState = battery.power_plugged
      ? battery.charging === true
        ? `Charging${Number.isFinite(chargeRate) ? ` · ${(chargeRate / 1000).toFixed(1)} W` : ""}`
        : "Connected to power"
      : battery.seconds_left !== null && battery.seconds_left !== undefined
        ? `${formatUptime(battery.seconds_left)} remaining`
        : "Running on battery";
    batteryCaption.textContent = powerState;
    setMeter("battery-meter", battery.percent);
  } else {
    batteryValue.innerHTML = `—<span></span>`;
    batteryCaption.textContent = battery
      ? "Battery charge unavailable"
      : "No battery detected";
    setMeter("battery-meter", 0);
  }
}

async function updateDeviceNote(system) {
  if (!onlineLookupsEnabled) {
    document.getElementById("device-fact").textContent =
      "Online device facts are off in Settings.";
    document.getElementById("device-fact-source").hidden = true;
    document.getElementById("device-motto").textContent =
      "Quietly getting things done.";
    return;
  }
  const modelKey = `${system.manufacturer}|${system.device_model}`.toLowerCase();
  if (messageForModel === modelKey && Date.now() < deviceNoteRetryAt) {
    return;
  }
  messageForModel = modelKey;
  deviceNoteRetryAt = Number.POSITIVE_INFINITY;

  const factElement = document.getElementById("device-fact");
  const factSource = document.getElementById("device-fact-source");
  const motto = document.getElementById("device-motto");
  factElement.textContent = "Finding a fact about this model...";
  factSource.hidden = true;
  motto.textContent = "A little something for your device.";

  try {
    const response = await fetch("/api/v1/device-note", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Device note returned HTTP ${response.status}`);
    }
    const note = await response.json();
    if (!onlineLookupsEnabled) return;

    if (note.fact && note.source_url) {
      factElement.textContent = note.fact;
      factSource.href = note.source_url;
      factSource.textContent = `Source: ${note.source}`;
      factSource.title =
        "The online lookup uses the manufacturer and model only, not your computer name or serial number.";
      factSource.hidden = false;
      deviceNoteRetryAt = Number.POSITIVE_INFINITY;
    } else {
      factElement.textContent =
        note.message || "No sourced fact is available for this model right now.";
      deviceNoteRetryAt = Date.now() + 300_000;
    }
    motto.textContent = note.motto;
  } catch (error) {
    if (!onlineLookupsEnabled) return;
    factElement.textContent =
      `The online fact is unavailable right now: ${error.message}`;
    motto.textContent = "Quietly getting things done.";
    deviceNoteRetryAt = Date.now() + 300_000;
  }
}

async function updateOverviewCpuTemperature() {
  const temperature = document.getElementById("overview-cpu-temperature");
  temperature.textContent = "Checking sensor availability…";
  temperature.removeAttribute("title");
  try {
    const response = await fetch(
      "/api/v1/sensors/cpu.package.temperature",
      { cache: "no-store" },
    );
    if (response.status === 404) {
      temperature.textContent = "CPU temperature unavailable";
      return;
    }
    if (!response.ok) {
      throw new Error(`Sensor API returned HTTP ${response.status}`);
    }
    const sensor = await response.json();
    if (
      sensor.state === "AVAILABLE"
      && typeof sensor.value === "number"
      && Number.isFinite(sensor.value)
      && sensor.source !== "SIMULATED"
    ) {
      temperature.textContent = `${sensor.value.toFixed(1)} °C`;
      return;
    }
    temperature.textContent = "CPU temperature unavailable";
    if (sensor.error_message) {
      temperature.title = sensor.error_message;
    }
  } catch (error) {
    temperature.textContent = "CPU temperature could not be read";
    temperature.title = error.message;
  }
}

async function updateDashboard() {
  if (dashboardUpdating) return;
  dashboardUpdating = true;
  try {
    await onlineLookupsReady;
    const [healthResponse, systemResponse] = await Promise.all([
      fetch("/api/v1/health", { cache: "no-store" }),
      fetch("/api/v1/system", { cache: "no-store" }),
    ]);
    if (!healthResponse.ok) {
      throw new Error(`Health check returned HTTP ${healthResponse.status}`);
    }
    if (!systemResponse.ok) {
      throw new Error(`Device detection returned HTTP ${systemResponse.status}`);
    }
    const [health, system] = await Promise.all([
      healthResponse.json(),
      systemResponse.json(),
    ]);

    adminRestartButton.hidden = health.is_admin !== false;
    renderSystemInfo(system);
    await updateOverviewCpuTemperature();
    await updateDeviceNote(system);
    const sensorSample = system.sensor_sampled_at
      ? new Date(system.sensor_sampled_at).toLocaleTimeString()
      : "waiting for sensor sample";
    document.getElementById("last-updated").textContent =
      `Updated ${new Date().toLocaleTimeString()} · GPU/battery ${sensorSample}`;
    setConnectionStatus(
      true,
      `Connected locally · OVERPINE has been running for ${formatUptime(health.uptime_seconds)}.`,
    );
  } catch (error) {
    setConnectionStatus(false, `Couldn't read this computer's details: ${error.message}`);
  } finally {
    dashboardUpdating = false;
  }
}

let desktopHeartbeatToken;
let heartbeatWarningLogged = false;

async function sendDesktopHeartbeat() {
  try {
    if (!desktopHeartbeatToken) {
      const tokenResponse = await fetch("/api/v1/stress/token", {
        cache: "no-store",
      });
      if (!tokenResponse.ok) {
        throw new Error(`Could not get session token (HTTP ${tokenResponse.status})`);
      }
      const tokenBody = await tokenResponse.json();
      if (!tokenBody.token) {
        throw new Error("The server returned no session token");
      }
      desktopHeartbeatToken = tokenBody.token;
    }

    const response = await fetch("/api/v1/heartbeat", {
      method: "POST",
      headers: { "X-Overpine-Token": desktopHeartbeatToken },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Heartbeat returned HTTP ${response.status}`);
    }
    heartbeatWarningLogged = false;
  } catch (error) {
    if (!heartbeatWarningLogged) {
      console.warn("OVERPINE desktop heartbeat failed:", error);
      heartbeatWarningLogged = true;
    }
  }
}

initializeGraphSettings();
initializeStressPage();
initializeSensorsPage();
initializeHistoryPage();
initializeAboutPage();
initializeSettingsPage();
initializeHelpPage();
onlineLookupsReady = initializeOnlineLookupSetting();
sendDesktopHeartbeat();
window.setInterval(sendDesktopHeartbeat, 2000);
updateDashboard();
scheduleDashboardRefresh(currentAppSettings?.refresh_interval_seconds || 3);
