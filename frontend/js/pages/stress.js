const metricSeries = [
  { key: "cpu_percent", label: "CPU %", color: "#43b8d2", axis: "percent" },
  { key: "memory_percent", label: "Memory %", color: "#4a91d9", axis: "percent" },
  { key: "disk_mb_per_second", label: "Disk MB/s", color: "#91b75b", axis: "rate" },
  { key: "network_mb_per_second", label: "Network MB/s", color: "#d65b83", axis: "rate" },
];

const resultMetricLabels = {
  cpu_percent: "CPU utilization",
  memory_percent: "Memory utilization",
  app_cpu_percent: "OVERPINE CPU",
  app_memory_mb: "OVERPINE memory (MB)",
  disk_mb_per_second: "Disk throughput (MB/s)",
  network_mb_per_second: "Network throughput (MB/s)",
};

function formatNumber(value, digits = 1) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "Unavailable";
}

function setHidden(element, hidden) {
  element.hidden = hidden;
}

function initializeStressPage() {
  const page = document.getElementById("stress-page");
  if (!page) return;

  const form = document.getElementById("stress-form");
  const message = document.getElementById("stress-message");
  const targetChecks = [...document.querySelectorAll('input[name="stress-target"]')];
  const allToggle = document.getElementById("stress-all");
  const durationInput = document.getElementById("stress-duration");
  const intensityInput = document.getElementById("stress-intensity");
  const intensityValue = document.getElementById("stress-intensity-value");
  const startButton = document.getElementById("stress-start");
  const stopButton = document.getElementById("stress-stop");
  const runSection = document.getElementById("stress-run");
  const runState = document.getElementById("stress-run-state");
  const runTime = document.getElementById("stress-run-time");
  const progress = document.getElementById("stress-progress");
  const abortReason = document.getElementById("stress-abort-reason");
  const temperature = document.getElementById("stress-temperature");
  const targetStatus = document.getElementById("stress-target-status");
  const metricError = document.getElementById("stress-metric-error");
  const chartState = document.getElementById("stress-chart-state");
  const resultsSection = document.getElementById("stress-results");
  const resultsNote = document.getElementById("stress-results-note");
  const resultsTable = document.getElementById("stress-results-table");
  let tokenPromise;
  let chart;
  let pollTimer;
  let samples = [];
  let activeDuration = 10;

  function showMessage(text, kind = "info") {
    message.textContent = text;
    message.dataset.kind = kind;
  }

  function routeTo(route) {
    const selected = ["stress", "sensors"].includes(route) ? route : "home";
    document.body.dataset.route = selected;
    document.querySelectorAll("[data-route-page]").forEach((section) => {
      setHidden(section, section.dataset.routePage !== selected);
    });
    document.querySelectorAll("[data-route-link]").forEach((link) => {
      if (link.dataset.routeLink === selected) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
    if (selected === "stress") {
      refreshStatus().catch((error) => showMessage(error.message, "error"));
    }
  }

  async function getToken() {
    if (!tokenPromise) {
      tokenPromise = fetch("/api/v1/stress/token", { cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) throw new Error(`Could not get session token (HTTP ${response.status})`);
          const body = await response.json();
          if (!body.token) throw new Error("The server returned no stress-test token");
          return body.token;
        })
        .catch((error) => {
          tokenPromise = undefined;
          throw error;
        });
    }
    return tokenPromise;
  }

  async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method === "POST") {
      headers.set("X-Overpine-Token", await getToken());
    }
    const response = await fetch(`/api/v1/stress/${path}`, {
      ...options,
      headers,
      cache: "no-store",
    });
    let body;
    try {
      body = await response.json();
    } catch {
      throw new Error(`Stress API returned unreadable data (HTTP ${response.status})`);
    }
    if (!response.ok) {
      throw new Error(body.detail || `Stress API returned HTTP ${response.status}`);
    }
    return body;
  }

  function ensureChart() {
    if (chart) return chart;
    if (!window.Chart) {
      chartState.textContent = "Chart.js is unavailable; live graph cannot be shown.";
      throw new Error("The local Chart.js asset did not load");
    }
    const context = document.getElementById("stress-chart").getContext("2d");
    chart = new window.Chart(context, {
      type: "line",
      data: {
        labels: [],
        datasets: metricSeries.map((series) => ({
          label: series.label,
          data: [],
          borderColor: series.color,
          backgroundColor: series.color,
          yAxisID: series.axis,
          pointRadius: 0,
          borderWidth: 2,
          spanGaps: false,
          tension: 0.25,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: "index" },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: {
            ticks: { maxTicksLimit: 8 },
            grid: { color: "rgba(128, 128, 128, 0.16)" },
          },
          percent: {
            type: "linear",
            position: "left",
            min: 0,
            max: 100,
            title: { display: true, text: "Percent" },
            grid: { color: "rgba(128, 128, 128, 0.16)" },
          },
          rate: {
            type: "linear",
            position: "right",
            beginAtZero: true,
            title: { display: true, text: "MB/s" },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
    chartState.textContent = "Waiting for live samples";
    return chart;
  }

  function updateChart(metrics) {
    const currentChart = ensureChart();
    const timestamp = new Date().toLocaleTimeString();
    samples.push({
      timestamp,
      values: metricSeries.map((series) => metrics[series.key]),
    });
    if (samples.length > 60) samples = samples.slice(-60);
    currentChart.data.labels = samples.map((sample) => sample.timestamp);
    currentChart.data.datasets.forEach((dataset, index) => {
      dataset.data = samples.map((sample) => sample.values[index]);
    });
    currentChart.update("none");
    chartState.textContent = "Live system readings · unavailable samples are left blank";
  }

  function renderTargets(status) {
    const rows = Object.entries(status.target_states || {});
    const supportedTargets = ["cpu", "memory", "disk", "network"];
    const allSupportedSelected = supportedTargets.every((target) =>
      status.targets?.includes(target),
    );
    if (
      (status.targets?.includes("all") || allSupportedSelected)
      && !rows.some(([target]) => target === "gpu")
    ) {
      rows.push(["gpu", { state: "unsupported", message: "No GPU stress API is available." }]);
    }
    targetStatus.replaceChildren();
    for (const [target, detail] of rows) {
      const badge = document.createElement("span");
      badge.className = "stress-target-badge";
      const title = document.createElement("strong");
      title.textContent = target[0].toUpperCase() + target.slice(1);
      const state = document.createElement("span");
      state.className = `stress-state-text stress-state-text--${detail.state || "unavailable"}`;
      state.textContent = detail.state || "unavailable";
      badge.append(title, state);
      if (detail.message) {
        const explanation = document.createElement("small");
        explanation.textContent = detail.message;
        badge.append(explanation);
      }
      if (detail.error) {
        const error = document.createElement("small");
        error.textContent = detail.error;
        badge.append(error);
      }
      targetStatus.append(badge);
    }
  }

  function renderStatus(status) {
    const running = status.state === "running" || status.state === "stopping";
    setHidden(runSection, status.state === "idle" && !status.targets?.length);
    runState.textContent = status.state;
    runState.className = `stress-state-badge stress-state-badge--${status.state}`;
    const duration = Number(status.duration_s) || activeDuration;
    const remaining = Math.max(0, Number(status.remaining) || 0);
    runTime.textContent = running
      ? `${Math.ceil(remaining)} seconds remaining · ${Number(status.elapsed || 0).toFixed(1)} seconds elapsed`
      : `${Number(status.elapsed || 0).toFixed(1)} seconds elapsed`;
    progress.value = duration > 0
      ? Math.min(100, (Number(status.elapsed || 0) / duration) * 100)
      : 0;
    setHidden(abortReason, !status.abort_reason);
    abortReason.textContent = status.abort_reason || "";
    temperature.textContent = status.cpu_temperature_c === null
      || status.cpu_temperature_c === undefined
      ? status.temperature_status || "temperature not monitored"
      : `CPU temperature: ${formatNumber(status.cpu_temperature_c)} °C`;
    renderTargets(status);
    const errors = status.live_metrics?.errors;
    setHidden(metricError, !errors);
    metricError.textContent = errors
      ? `Some live readings are unavailable: ${Object.entries(errors).map(([name, error]) => `${name}: ${error}`).join("; ")}`
      : "";
    stopButton.hidden = !running;
    startButton.disabled = running;
    targetChecks.forEach((input) => { input.disabled = running; });
    allToggle.disabled = running;
    durationInput.disabled = running;
    intensityInput.disabled = running;
    if (status.live_metrics) {
      try {
        updateChart(status.live_metrics);
      } catch (error) {
        showMessage(error.message, "error");
      }
    }
    if (status.state === "aborted" && status.abort_reason) {
      showMessage(`Test stopped: ${status.abort_reason}`, "error");
    } else if (status.state === "running") {
      showMessage("Stress test is running. Stop is available at any time.", "info");
    } else if (status.state === "stopping") {
      showMessage("Stopping workloads and releasing resources…", "info");
    } else if (status.state === "finished") {
      showMessage("Stress test finished and workers were cleaned up.", "success");
    } else if (status.state === "idle") {
      showMessage("Ready. Choose components, duration, and intensity to begin.", "info");
    }
  }

  function renderResults(result) {
    if (!result) {
      setHidden(resultsSection, true);
      return;
    }
    setHidden(resultsSection, false);
    resultsNote.textContent = result.abort_reason
      ? `Stopped: ${result.abort_reason}`
      : `${result.targets.join(", ")} · ${Number(result.elapsed).toFixed(1)} seconds · ${result.intensity_percent}% intensity`;
    const table = document.createElement("table");
    table.className = "stress-summary-table";
    table.innerHTML = "<thead><tr><th>Metric</th><th>Min</th><th>Average</th><th>Max</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const [key, label] of Object.entries(resultMetricLabels)) {
      const values = result.metrics?.[key];
      const row = document.createElement("tr");
      const name = document.createElement("th");
      name.scope = "row";
      name.textContent = label;
      row.append(name);
      for (const stat of ["min", "avg", "max"]) {
        const cell = document.createElement("td");
        cell.textContent = values ? formatNumber(values[stat]) : "Unavailable";
        row.append(cell);
      }
      body.append(row);
    }
    table.append(body);
    resultsTable.replaceChildren(table);
  }

  async function refreshStatus() {
    const status = await apiRequest("status");
    renderStatus(status);
    if (status.state === "running" || status.state === "stopping") {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(
        () => refreshStatus().catch((error) => showMessage(error.message, "error")),
        1000,
      );
      return;
    }
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = undefined;
    }
    const response = await apiRequest("results");
    renderResults(response.result);
  }

  function syncAllToggle() {
    const supportedChecks = targetChecks.filter((input) => input.value !== "gpu");
    const selected = supportedChecks.filter((input) => input.checked).length;
    allToggle.checked = selected === supportedChecks.length;
    allToggle.indeterminate = selected > 0 && selected < supportedChecks.length;
  }

  allToggle.addEventListener("change", () => {
    targetChecks.forEach((input) => {
      input.checked = allToggle.checked && input.value !== "gpu";
    });
  });
  targetChecks.forEach((input) => input.addEventListener("change", syncAllToggle));
  intensityInput.addEventListener("input", () => {
    intensityValue.value = `${intensityInput.value}%`;
    intensityValue.textContent = `${intensityInput.value}%`;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const duration = Number(durationInput.value);
    if (!Number.isInteger(duration) || duration < 5 || duration > 300) {
      showMessage("Duration must be a whole number from 5 to 300 seconds.", "error");
      return;
    }
    const chosen = targetChecks.filter((input) => input.checked).map((input) => input.value);
    if (chosen.length === 0) {
      showMessage("Choose at least one component to test.", "error");
      return;
    }
    activeDuration = duration;
    samples = [];
    if (chart) {
      chart.data.labels = [];
      chart.data.datasets.forEach((dataset) => { dataset.data = []; });
      chart.update("none");
    }
    startButton.disabled = true;
    showMessage("Starting the selected local workloads…", "info");
    try {
      const status = await apiRequest("start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          targets: allToggle.checked ? ["all"] : chosen,
          duration_s: duration,
          intensity_percent: Number(intensityInput.value),
        }),
      });
      setHidden(resultsSection, true);
      renderStatus(status);
      await refreshStatus();
    } catch (error) {
      startButton.disabled = false;
      showMessage(error.message, "error");
    }
  });
  stopButton.addEventListener("click", async () => {
    stopButton.disabled = true;
    showMessage("Stopping workloads and releasing resources…", "info");
    try {
      const status = await apiRequest("stop", { method: "POST" });
      renderStatus(status);
      await refreshStatus();
    } catch (error) {
      stopButton.disabled = false;
      showMessage(error.message, "error");
    }
  });
  window.addEventListener("hashchange", () => {
    routeTo(window.location.hash.slice(1));
  });

  syncAllToggle();
  routeTo(window.location.hash.slice(1));
}

export { initializeStressPage };
