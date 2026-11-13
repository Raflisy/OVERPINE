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
      body.error?.message || `Request returned HTTP ${response.status}`,
    );
  }
  return body;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "Unavailable";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours} h ${minutes} min` : `${minutes} min ${total % 60} s`;
}

function addDetail(list, label, value) {
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  list.append(term, description);
}

function addBullet(list, text) {
  const item = document.createElement("li");
  item.textContent = text;
  list.append(item);
}

export function initializeDiagnosticsPage() {
  const page = document.getElementById("diagnostics-page");
  if (!page) return;

  const status = document.getElementById("diagnostics-status");
  const counts = document.getElementById("diagnostics-counts");
  const providers = document.getElementById("diagnostics-providers");
  const problems = document.getElementById("diagnostics-problems");
  const problemNote = document.getElementById("diagnostics-problem-note");
  const historyList = document.getElementById("diagnostics-history");
  const copyReport = document.getElementById("diagnostics-copy-report");
  const refresh = document.getElementById("diagnostics-refresh");
  const levelSelect = document.getElementById("log-level");
  const searchInput = document.getElementById("log-search");
  const logStatus = document.getElementById("log-status");
  const logOutput = document.getElementById("log-output");
  const copyLog = document.getElementById("log-copy");
  let latest;
  let loading = false;
  let searchTimer;

  function reportText() {
    if (!latest) return "";
    const lines = [
      `${latest.app} diagnostics report`,
      `Generated: ${latest.generated_at}`,
      `Python: ${latest.python_version}`,
      `System: ${latest.system}`,
      `Monitoring: ${latest.monitoring_state}, session ${formatDuration(latest.session_duration_s)}`,
      "",
      "Sensor states (detail rows excluded):",
      ...Object.entries(latest.sensor_counts).map(([state, n]) => `  ${state}: ${n}`),
      `  Per-process/instance detail sensors not counted: ${latest.detail_sensor_count}`,
      "",
      "Providers:",
      ...latest.providers.map(
        (p) => `  ${p.name}: ${p.state}, ${p.sensor_count} sensors${p.last_error ? `, last error: ${p.last_error}` : ""}`,
      ),
      "",
      "Sensors that need attention:",
      ...(latest.problems.length
        ? latest.problems.map((p) => `  ${p.name} (${p.id}) - ${p.state}${p.message ? `: ${p.message}` : ""}`)
        : ["  none"]),
      "",
      `History database: ${formatBytes(latest.history.database_size_bytes)}, retention ${latest.history.retention_days} days, limit ${latest.history.max_mb} MB`,
    ];
    if (latest.history.warning) lines.push(`History warning: ${latest.history.warning}`);
    return lines.join("\n");
  }

  async function copyText(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = `${label} copied.`;
    } catch {
      status.textContent = "Copy is not available in this browser. Select the text and copy it manually.";
    }
  }

  function renderDiagnostics(data) {
    latest = data;
    counts.replaceChildren();
    addDetail(counts, "Application", data.app);
    addDetail(counts, "Python", data.python_version);
    addDetail(counts, "System", data.system);
    addDetail(counts, "Monitoring", `${data.monitoring_state}, running ${formatDuration(data.session_duration_s)}`);
    for (const [state, n] of Object.entries(data.sensor_counts)) {
      addDetail(counts, state, String(n));
    }
    addDetail(counts, "Detail sensors (not counted)", String(data.detail_sensor_count));

    providers.replaceChildren();
    for (const provider of data.providers) {
      const error = provider.last_error ? ` · last error: ${provider.last_error}` : "";
      addBullet(providers, `${provider.name} · ${provider.state} · ${provider.sensor_count} sensors${error}`);
    }
    if (data.providers.length === 0) addBullet(providers, "No providers are running.");

    problems.replaceChildren();
    for (const problem of data.problems) {
      const message = problem.message ? `: ${problem.message}` : "";
      addBullet(problems, `${problem.name} (${problem.id}) · ${problem.state}${message}`);
    }
    problemNote.textContent = data.problems.length
      ? `${data.problems.length} sensor(s) have no current reading. Nothing is simulated; missing values stay blank.`
      : "Every counted sensor has a current reading.";

    historyList.replaceChildren();
    const history = data.history;
    addDetail(historyList, "Recording", history.enabled ? "On" : "Off");
    addDetail(historyList, "Database size", formatBytes(history.database_size_bytes));
    addDetail(historyList, "Retention", history.retention_days ? `${history.retention_days} days` : "Unavailable");
    addDetail(historyList, "Size limit", history.max_mb ? `${history.max_mb} MB` : "Unavailable");
    addDetail(historyList, "Waiting to be written", String(history.pending_samples ?? 0));
    addDetail(historyList, "Log file size", formatBytes(data.log_file_size_bytes));
    if (history.warning) addDetail(historyList, "Warning", history.warning);
  }

  async function loadLogs() {
    const params = new URLSearchParams({
      level: levelSelect.value,
      limit: "300",
    });
    const query = searchInput.value.trim();
    if (query) params.set("q", query);
    try {
      const body = await readJson(await fetch(`/api/logs?${params}`, { cache: "no-store" }));
      if (!body.available) {
        logOutput.textContent = "";
        logStatus.textContent = body.message || "The log is not available.";
        return;
      }
      logOutput.textContent = body.entries
        .map((e) => `${e.time} ${e.level.padEnd(8)} ${e.logger}: ${e.message}`)
        .join("\n");
      logOutput.scrollTop = logOutput.scrollHeight;
      logStatus.textContent = `Showing ${body.entries.length} of ${body.total_matching} matching entries from ${body.log_file}`;
    } catch (error) {
      logStatus.textContent = `Could not load the log: ${error.message}`;
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    status.textContent = "Reading diagnostics…";
    try {
      renderDiagnostics(await readJson(await fetch("/api/diagnostics", { cache: "no-store" })));
      status.textContent = "Diagnostics read from this computer.";
    } catch (error) {
      status.textContent = `Could not load diagnostics: ${error.message}`;
    } finally {
      loading = false;
    }
    await loadLogs();
  }

  refresh.addEventListener("click", load);
  levelSelect.addEventListener("change", loadLogs);
  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(loadLogs, 300);
  });
  copyReport.addEventListener("click", () => copyText(reportText(), "Report"));
  copyLog.addEventListener("click", () => copyText(logOutput.textContent, "Log"));

  function updateRoute() {
    if (window.location.hash.slice(1) === "diagnostics") load();
  }

  window.addEventListener("hashchange", updateRoute);
  updateRoute();
}
