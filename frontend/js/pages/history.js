import { LineChart } from "../charts/line-chart.js";

const UNIT_DECIMAL_PLACES = new Map([
  ["%", 1],
  ["C", 1],
  ["°C", 1],
  ["MB/s", 2],
  ["V", 2],
  ["relative units", 2],
  ["relative units/hour", 2],
  ["bool", 0],
  ["cycles", 0],
  ["flags", 0],
  ["MHz", 0],
  ["mV", 0],
  ["mW", 0],
  ["mWh", 0],
  ["s", 0],
]);

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "Size unavailable";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function csvCell(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return `"${text.replaceAll('"', '""')}"`;
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

export function initializeHistoryPage() {
  const page = document.getElementById("history-page");
  if (!page) return;

  const status = document.getElementById("history-status");
  const warning = document.getElementById("history-warning");
  const sizeText = document.getElementById("history-database-size");
  const retention = document.getElementById("history-retention");
  const sensorSelect = document.getElementById("history-sensor");
  const rangeSelect = document.getElementById("history-range");
  const exportButton = document.getElementById("history-export");
  const clearButton = document.getElementById("history-clear");
  const canvas = document.getElementById("history-chart");
  let chart;
  let timer;
  let active = false;
  let loading = false;
  let selectedSensor;
  let chartSensorId;
  let chartSensorLabel;
  let samples = [];
  let refreshIntervalMs = 3000;

  function showStorageStatus(storageStatus) {
    sizeText.textContent = `Database ${formatBytes(storageStatus.database_size_bytes)}`;
    retention.textContent = storageStatus.enabled
      ? `Retention: ${storageStatus.retention_days} days · raw 2 min · 1/5-minute aggregates · size limit: ${storageStatus.max_mb} MB`
      : "History is turned off in config/default_config.toml.";
    warning.hidden = !storageStatus.warning;
    warning.textContent = storageStatus.warning || "";
  }

  function updateChart(sensor, records) {
    const sensorId = sensor?.id || "";
    const sensorLabel = sensor
      ? `${sensor.name}${sensor.unit ? ` (${sensor.unit})` : ""}`
      : "Sensor reading";
    if (chart && (chartSensorId !== sensorId || chartSensorLabel !== sensorLabel)) {
      chart.destroy();
      chart = undefined;
    }
    if (!chart) {
      chart = new LineChart(canvas, {
        series: [{
          key: "value",
          label: sensorLabel,
          color: "#43b8d2",
          cssVariable: "--accent",
          format: (value) => {
            const unit = sensor?.unit || "";
            const decimals = UNIT_DECIMAL_PLACES.get(unit) ?? 2;
            return `${value.toFixed(decimals)} ${unit}`.trim();
          },
        }],
      });
      chartSensorId = sensorId;
      chartSensorLabel = sensorLabel;
    }
    chart.update(records.map((record) => ({
      time: record.ts * 1000,
      values: {
        value: record.state === "AVAILABLE" ? record.value : null,
      },
    })));
  }

  async function loadSamples() {
    const sensor = sensorSelect.selectedOptions[0];
    if (!sensorSelect.value || !sensor) {
      samples = [];
      exportButton.disabled = true;
      updateChart(null, []);
      status.textContent = "Select a sensor with recorded history.";
      return;
    }

    const duration = Number(rangeSelect.value);
    const end = Date.now() / 1000;
    const query = new URLSearchParams({
      sensor: sensorSelect.value,
      from: String(end - duration),
      to: String(end),
      resolution: "auto",
    });
    status.textContent = "Loading stored samples…";
    const body = await readJson(await fetch(`/api/history?${query}`, {
      cache: "no-store",
    }));
    samples = body.samples;
    updateChart({
      id: sensor.id,
      name: sensor.textContent,
      unit: sensor.dataset.unit || "",
    }, samples);
    exportButton.disabled = samples.length === 0;
    if (samples.length) {
      const fiveMinuteAverages = samples.filter(
        (sample) => sample.source === "five_minute",
      ).length;
      const minuteAverages = samples.filter(
        (sample) => sample.source === "minute",
      ).length;
      const raw = samples.length - fiveMinuteAverages - minuteAverages;
      const descriptions = [];
      if (fiveMinuteAverages) {
        descriptions.push(
          `${fiveMinuteAverages} 5-minute average${fiveMinuteAverages === 1 ? "" : "s"}`,
        );
      }
      if (minuteAverages) {
        descriptions.push(
          `${minuteAverages} 1-minute average${minuteAverages === 1 ? "" : "s"}`,
        );
      }
      if (raw) {
        descriptions.push(`${raw} raw reading${raw === 1 ? "" : "s"}`);
      }
      status.textContent =
        `${descriptions.join(" + ")} · ${samples.length} chart point${samples.length === 1 ? "" : "s"}`;
    } else {
      status.textContent = "No samples in this time range.";
    }
    showStorageStatus(body.status);
  }

  async function refresh() {
    if (!active || loading) return;
    loading = true;
    try {
      const body = await readJson(await fetch("/api/history/sensors", {
        cache: "no-store",
      }));
      showStorageStatus(body.status);
      const sensorIds = body.sensors.map((sensor) => sensor.id);
      if (selectedSensor && !sensorIds.includes(selectedSensor)) {
        selectedSensor = undefined;
      }
      sensorSelect.replaceChildren();
      for (const sensor of body.sensors) {
        const option = document.createElement("option");
        option.value = sensor.id;
        option.textContent = sensor.name;
        option.dataset.unit = sensor.unit;
        sensorSelect.append(option);
      }
      if (sensorSelect.options.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No history yet";
        sensorSelect.append(option);
        sensorSelect.disabled = true;
      } else {
        sensorSelect.disabled = false;
        sensorSelect.value = selectedSensor || sensorIds[0];
        selectedSensor = sensorSelect.value;
      }
      await loadSamples();
    } catch (error) {
      status.textContent = `Could not load history: ${error.message}`;
      warning.hidden = false;
      warning.textContent = error.message;
    } finally {
      loading = false;
    }
  }

  function exportCsv() {
    if (samples.length === 0) return;
    const header = ["timestamp", "value", "minimum", "maximum", "state"];
    const rows = samples.map((sample) => [
      new Date(sample.ts * 1000).toISOString(),
      sample.value,
      sample.min,
      sample.max,
      sample.state,
    ]);
    const content = [header, ...rows]
      .map((row) => row.map(csvCell).join(","))
      .join("\r\n");
    const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${sensorSelect.value}-history.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function clearHistory() {
    if (!window.confirm("Clear all locally stored sensor history?")) return;
    clearButton.disabled = true;
    try {
      const tokenBody = await readJson(await fetch("/api/v1/stress/token", {
        cache: "no-store",
      }));
      if (typeof tokenBody.token !== "string" || !tokenBody.token) {
        throw new Error("The server returned no session token");
      }
      const body = await readJson(await fetch("/api/history/clear", {
        method: "POST",
        headers: { "X-Overpine-Token": tokenBody.token },
        cache: "no-store",
      }));
      showStorageStatus(body.status);
      status.textContent = "History cleared.";
      selectedSensor = undefined;
      await refresh();
    } catch (error) {
      status.textContent = `Could not clear history: ${error.message}`;
    } finally {
      clearButton.disabled = false;
    }
  }

  sensorSelect.addEventListener("change", () => {
    selectedSensor = sensorSelect.value;
    loadSamples().catch((error) => {
      status.textContent = `Could not load history: ${error.message}`;
    });
  });
  rangeSelect.addEventListener("change", () => {
    loadSamples().catch((error) => {
      status.textContent = `Could not load history: ${error.message}`;
    });
  });
  exportButton.addEventListener("click", exportCsv);
  clearButton.addEventListener("click", clearHistory);

  function updateRoute() {
    active = window.location.hash.slice(1) === "history";
    if (active) {
      refresh();
      if (!timer) timer = window.setInterval(refresh, refreshIntervalMs);
    } else if (timer) {
      window.clearInterval(timer);
      timer = undefined;
    }
  }

  window.addEventListener("overpine-settings-updated", (event) => {
    const seconds = event.detail?.refresh_interval_seconds;
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 60) return;
    refreshIntervalMs = seconds * 1000;
    if (active) {
      if (timer) window.clearInterval(timer);
      timer = window.setInterval(refresh, refreshIntervalMs);
    }
  });

  window.addEventListener("hashchange", updateRoute);
  updateRoute();
}
