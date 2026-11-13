import { LineChart } from "../charts/line-chart.js";

const stateLabels = {
  AVAILABLE: "Available",
  UNAVAILABLE: "Unavailable",
  UNSUPPORTED: "Unsupported",
  PERMISSION_RESTRICTED: "Needs admin",
  TEMPORARILY_DISCONNECTED: "Disconnected",
  ERROR: "Error",
  NOT_INITIALIZED: "Not initialized",
};

const categoryOrder = [
  "cpu",
  "gpu",
  "memory",
  "storage",
  "battery",
  "thermal",
  "fan",
  "power",
  "network",
];

function formatValue(value, digits = 2) {
  return typeof value === "number" && Number.isFinite(value)
    ? Number(value.toFixed(digits)).toString()
    : "Unavailable";
}

function formatTimestamp(timestamp) {
  if (typeof timestamp !== "number" || !Number.isFinite(timestamp)) {
    return "Not sampled";
  }
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? "Not sampled" : date.toLocaleTimeString();
}

function formatDuration(seconds) {
  const elapsed = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor((elapsed % 3600) / 60);
  const remainder = elapsed % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

async function readJson(response) {
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text.trim().slice(0, 240) || `Server returned HTTP ${response.status}`);
  }
  if (!response.ok) {
    throw new Error(
      body.error?.message || body.detail || `Request returned HTTP ${response.status}`,
    );
  }
  return body;
}

export function initializeSensorsPage() {
  const page = document.getElementById("sensors-page");
  if (!page) return;

  const statusText = document.getElementById("sensors-status");
  const categoryFilter = document.getElementById("sensor-category-filter");
  const stateFilter = document.getElementById("sensor-state-filter");
  const groups = document.getElementById("sensor-groups");
  const pauseToggle = document.getElementById("sensor-pause-toggle");
  const sessionDuration = document.getElementById("sensor-session-duration");
  const historyPanel = document.getElementById("sensor-history-panel");
  const historyTitle = document.getElementById("sensor-history-title");
  const historyDetail = document.getElementById("sensor-history-detail");
  const historyStatus = document.getElementById("sensor-history-status");
  const historyCanvas = document.getElementById("sensor-history-chart");
  let sensors = [];
  let monitoringStatus = null;
  let sessionTimer;
  let refreshTimer;
  let historyChart;
  let active = false;
  let loading = false;
  let refreshIntervalMs = 3000;

  function showStatus(message, kind = "info") {
    statusText.textContent = message;
    statusText.dataset.kind = kind;
  }

  function stateBadge(state) {
    const badge = document.createElement("span");
    const safeState = Object.hasOwn(stateLabels, state) ? state : "ERROR";
    badge.className = `sensor-state-badge sensor-state-badge--${safeState.toLowerCase().replaceAll("_", "-")}`;
    badge.textContent = stateLabels[safeState];
    return badge;
  }

  function buildSensorRow(sensor) {
    const row = document.createElement("tr");
    const nameCell = document.createElement("th");
    nameCell.scope = "row";
    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "sensor-name-button";
    selectButton.textContent = sensor.name;
    selectButton.title = `Show history for ${sensor.name}`;
    selectButton.addEventListener("click", () => showHistory(sensor));
    nameCell.append(selectButton);
    if (sensor.error_message) {
      const detail = document.createElement("small");
      detail.className = "sensor-error-detail";
      detail.textContent = sensor.error_message;
      nameCell.append(detail);
    }
    row.append(nameCell);

    const valueCell = document.createElement("td");
    valueCell.textContent = formatValue(sensor.value);
    const unit = document.createElement("span");
    unit.className = "sensor-unit";
    unit.textContent = sensor.value === null ? "" : ` ${sensor.unit}`;
    valueCell.append(unit);
    row.append(valueCell);

    const stateCell = document.createElement("td");
    stateCell.append(stateBadge(sensor.state));
    row.append(stateCell);
    for (const value of [sensor.min, sensor.max, sensor.avg]) {
      const cell = document.createElement("td");
      cell.textContent = formatValue(value);
      row.append(cell);
    }
    for (const value of [sensor.source, sensor.reliability, formatTimestamp(sensor.last_update)]) {
      const cell = document.createElement("td");
      cell.textContent = value || "Unavailable";
      row.append(cell);
    }
    return row;
  }

  function renderSensors() {
    groups.replaceChildren();
    const category = categoryFilter.value;
    const state = stateFilter.value;
    const visibleSensors = sensors.filter((sensor) =>
      (!category || sensor.category === category)
      && (!state || sensor.state === state));
    if (visibleSensors.length === 0) {
      const empty = document.createElement("p");
      empty.className = "sensor-empty-state";
      empty.textContent = sensors.length === 0
        ? "No sensors have been discovered yet. Providers may still be starting or the hardware may not expose readable sensors."
        : "No sensors match the selected filters.";
      groups.append(empty);
      return;
    }
    const categories = [...new Set(visibleSensors.map((sensor) => sensor.category))]
      .sort((left, right) => categoryOrder.indexOf(left) - categoryOrder.indexOf(right));
    for (const currentCategory of categories) {
      const section = document.createElement("section");
      section.className = "sensor-category-group";
      const heading = document.createElement("h2");
      heading.textContent = `${currentCategory.slice(0, 1).toUpperCase()}${currentCategory.slice(1)}`;
      section.append(heading);
      const tableWrap = document.createElement("div");
      tableWrap.className = "sensor-table-wrap";
      const table = document.createElement("table");
      table.className = "sensor-table";
      const head = document.createElement("thead");
      const headerRow = document.createElement("tr");
      for (const label of ["Sensor", "Value", "State", "Min", "Max", "Avg", "Source", "Reliability", "Updated"]) {
        const cell = document.createElement("th");
        cell.scope = "col";
        cell.textContent = label;
        headerRow.append(cell);
      }
      head.append(headerRow);
      table.append(head);
      const body = document.createElement("tbody");
      for (const sensor of visibleSensors.filter((item) => item.category === currentCategory)) {
        body.append(buildSensorRow(sensor));
      }
      table.append(body);
      tableWrap.append(table);
      section.append(tableWrap);
      groups.append(section);
    }
  }

  function updateStatusPresentation() {
    if (!monitoringStatus) return;
    const paused = monitoringStatus.paused === true;
    pauseToggle.textContent = paused ? "Resume monitoring" : "Pause monitoring";
    pauseToggle.disabled = monitoringStatus.running !== true;
    sessionDuration.textContent =
      `Session ${formatDuration(monitoringStatus.session_duration_s)}`;
    const providerErrors = (monitoringStatus.providers || [])
      .filter((provider) => provider.last_error)
      .map((provider) => `${provider.name}: ${provider.last_error}`);
    const providerNotes = (monitoringStatus.providers || [])
      .flatMap((provider) => [
        provider.note,
        provider.message,
        provider.temperatures,
        provider.fans,
        provider.gpu_note,
        ...(Array.isArray(provider.notes) ? provider.notes : []),
      ])
      .filter((note) => typeof note === "string" && note.length > 0);
    const stateCounts = monitoringStatus.sensor_counts || {};
    const count = Object.values(stateCounts).reduce((total, value) => total + value, 0);
    let message = `${count} discovered sensor${count === 1 ? "" : "s"} · monitoring ${monitoringStatus.state}.`;
    if (providerErrors.length) {
      message += ` Provider status: ${providerErrors.join(" · ")}`;
    }
    const uniqueNotes = [...new Set(providerNotes)].slice(0, 5);
    if (uniqueNotes.length) {
      message += ` Sensor notes: ${uniqueNotes.join(" · ")}`;
    }
    showStatus(
      message,
      providerErrors.length || uniqueNotes.length ? "warning" : "info",
    );
  }

  async function refresh() {
    if (loading || !active) return;
    loading = true;
    try {
      const [sensorResponse, statusResponse] = await Promise.all([
        fetch("/api/v1/sensors", { cache: "no-store" }),
        fetch("/api/v1/monitoring/status", { cache: "no-store" }),
      ]);
      const [sensorBody, statusBody] = await Promise.all([
        readJson(sensorResponse),
        readJson(statusResponse),
      ]);
      sensors = Array.isArray(sensorBody.sensors) ? sensorBody.sensors : [];
      monitoringStatus = statusBody;
      renderSensors();
      updateStatusPresentation();
    } catch (error) {
      showStatus(`Could not load sensor data: ${error.message}`, "error");
    } finally {
      loading = false;
    }
  }

  async function getToken() {
    const response = await fetch("/api/v1/stress/token", { cache: "no-store" });
    const body = await readJson(response);
    if (typeof body.token !== "string" || !body.token) {
      throw new Error("The server returned no session token");
    }
    return body.token;
  }

  async function toggleMonitoring() {
    pauseToggle.disabled = true;
    try {
      const token = await getToken();
      const endpoint = monitoringStatus?.paused
        ? "/api/v1/monitoring/resume"
        : "/api/v1/monitoring/pause";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "X-Overpine-Token": token },
        cache: "no-store",
      });
      monitoringStatus = await readJson(response);
      updateStatusPresentation();
      await refresh();
    } catch (error) {
      showStatus(`Could not change monitoring state: ${error.message}`, "error");
      pauseToggle.disabled = false;
    }
  }

  async function showHistory(sensor) {
    historyPanel.hidden = false;
    historyTitle.textContent = sensor.name;
    historyDetail.textContent = `${sensor.source} · ${sensor.reliability} · ${sensor.unit}`;
    historyStatus.textContent = "Loading sensor history…";
    try {
      const response = await fetch(
        `/api/v1/sensors/${encodeURIComponent(sensor.id)}/history?limit=3600`,
        { cache: "no-store" },
      );
      const body = await readJson(response);
      if (historyChart) historyChart.destroy();
      historyChart = new LineChart(historyCanvas, {
        series: [{
          key: "value",
          label: `${sensor.name} (${sensor.unit})`,
          color: "#43b8d2",
          format: (value) => `${formatValue(value)} ${sensor.unit}`,
        }],
      });
      historyChart.update(body.history.map((sample) => ({
        time: sample.timestamp * 1000,
        values: { value: sample.value },
      })));
      historyStatus.textContent = body.history.length
        ? `${body.history.length} sample${body.history.length === 1 ? "" : "s"} in this session`
        : "No measurements have been recorded yet.";
    } catch (error) {
      historyStatus.textContent = `Could not load history: ${error.message}`;
    }
  }

  function closeHistory() {
    if (historyChart) {
      historyChart.destroy();
      historyChart = undefined;
    }
    historyPanel.hidden = true;
  }

  categoryFilter.addEventListener("change", renderSensors);
  stateFilter.addEventListener("change", renderSensors);
  pauseToggle.addEventListener("click", toggleMonitoring);
  document.getElementById("sensor-history-close").addEventListener("click", closeHistory);
  window.addEventListener("hashchange", updateRoute);

  function updateRoute() {
    active = window.location.hash.slice(1) === "sensors";
    if (active) {
      refresh();
      if (!refreshTimer) {
        refreshTimer = window.setInterval(refresh, refreshIntervalMs);
        sessionTimer = window.setInterval(() => {
          if (monitoringStatus) {
            monitoringStatus.session_duration_s += 1;
            sessionDuration.textContent =
              `Session ${formatDuration(monitoringStatus.session_duration_s)}`;
          }
        }, 1000);
      }
    } else {
      if (refreshTimer) window.clearInterval(refreshTimer);
      if (sessionTimer) window.clearInterval(sessionTimer);
      refreshTimer = undefined;
      sessionTimer = undefined;
    }
  }

  window.addEventListener("overpine-settings-updated", (event) => {
    const seconds = event.detail?.refresh_interval_seconds;
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 60) return;
    refreshIntervalMs = seconds * 1000;
    if (active) {
      if (refreshTimer) window.clearInterval(refreshTimer);
      refreshTimer = window.setInterval(refresh, refreshIntervalMs);
    }
  });

  updateRoute();
}
