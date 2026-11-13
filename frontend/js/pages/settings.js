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

async function requestSessionToken() {
  const body = await readJson(await fetch("/api/v1/stress/token", {
    cache: "no-store",
  }));
  if (typeof body.token !== "string" || !body.token) {
    throw new Error("The server returned no session token");
  }
  return body.token;
}

export function initializeSettingsPage() {
  const page = document.getElementById("settings-page");
  if (!page) return;

  const form = document.getElementById("settings-form");
  const status = document.getElementById("settings-status");
  const settingsFile = document.getElementById("settings-file");
  const restartNote = document.getElementById("settings-restart-note");
  const sensorList = document.getElementById("settings-sensor-list");
  const resetButton = document.getElementById("settings-reset");
  let loading = false;
  let loaded = false;
  let latestBody;

  function setStatus(message, kind = "info") {
    status.textContent = message;
    status.dataset.kind = kind;
  }

  function renderSensors(availableSensors, selectedIds) {
    const selected = new Set(selectedIds);
    const availableById = new Map(
      availableSensors.map((sensor) => [sensor.id, sensor]),
    );
    const sensorIds = [
      ...availableSensors.map((sensor) => sensor.id),
      ...selectedIds.filter((sensorId) => !availableById.has(sensorId)),
    ];
    sensorList.replaceChildren();
    for (const sensorId of sensorIds) {
      const sensor = availableById.get(sensorId);
      const label = document.createElement("label");
      label.className = "settings-sensor-option";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = sensorId;
      checkbox.checked = selected.has(sensorId);
      const description = document.createElement("span");
      description.textContent = sensor
        ? `${sensor.name} (${sensor.id}) · ${sensor.state}`
        : `${sensorId} · not currently discovered`;
      label.append(checkbox, description);
      sensorList.append(label);
    }
    if (sensorIds.length === 0) {
      const empty = document.createElement("p");
      empty.className = "info-subtle";
      empty.textContent = "No sensors are currently discovered.";
      sensorList.append(empty);
    }
  }

  function showRestartRequired(required) {
    restartNote.textContent = required.length
      ? `Saved. Restart OVERPINE to apply: ${required.join(", ")}.`
      : "Saved. No app restart is required.";
  }

  function render(body) {
    latestBody = body;
    const settings = body.settings;
    document.getElementById("setting-history-enabled").checked =
      settings.history_enabled;
    document.getElementById("setting-retention-days").value =
      settings.retention_days;
    document.getElementById("setting-max-mb").value = settings.max_mb;
    document.getElementById("setting-refresh-interval").value =
      settings.refresh_interval_seconds;
    document.getElementById("setting-theme").value = settings.theme;
    document.getElementById("setting-online-device-facts").checked =
      settings.online_device_fact_lookup;
    renderSensors(body.available_sensors, settings.recorded_sensors);
    settingsFile.textContent = `Saved locally at ${body.settings_file}`;
    showRestartRequired(body.restart_required);
  }

  window.addEventListener("overpine-settings-updated", (event) => {
    if (latestBody) {
      render({ ...latestBody, settings: event.detail });
    }
  });

  async function load() {
    if (loading) return;
    loading = true;
    setStatus("Loading saved settings…");
    try {
      const body = await readJson(await fetch("/api/settings", {
        cache: "no-store",
      }));
      render(body);
      loaded = true;
      setStatus("Settings are stored locally on this computer.");
    } catch (error) {
      setStatus(`Could not load settings: ${error.message}`, "error");
    } finally {
      loading = false;
    }
  }

  function collectSettings() {
    return {
      history_enabled: document.getElementById("setting-history-enabled").checked,
      retention_days: Number(
        document.getElementById("setting-retention-days").value,
      ),
      max_mb: Number(document.getElementById("setting-max-mb").value),
      recorded_sensors: [
        ...sensorList.querySelectorAll('input[type="checkbox"]:checked'),
      ].map((checkbox) => checkbox.value),
      refresh_interval_seconds: Number(
        document.getElementById("setting-refresh-interval").value,
      ),
      theme: document.getElementById("setting-theme").value,
      online_device_fact_lookup:
        document.getElementById("setting-online-device-facts").checked,
    };
  }

  async function postSettings(payload) {
    const token = await requestSessionToken();
    return readJson(await fetch("/api/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Overpine-Token": token,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    }));
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (loading) return;
    loading = true;
    setStatus("Saving settings…");
    try {
      const body = await postSettings(collectSettings());
      render(body);
      setStatus("Settings saved.");
      window.dispatchEvent(new CustomEvent("overpine-settings-updated", {
        detail: body.settings,
      }));
    } catch (error) {
      setStatus(`Could not save settings: ${error.message}`, "error");
    } finally {
      loading = false;
    }
  });

  resetButton.addEventListener("click", async () => {
    if (loading) return;
    loading = true;
    setStatus("Resetting settings to config defaults…");
    try {
      const body = await postSettings({ reset: true });
      render(body);
      setStatus("Settings reset to config defaults.");
      window.dispatchEvent(new CustomEvent("overpine-settings-updated", {
        detail: body.settings,
      }));
    } catch (error) {
      setStatus(`Could not reset settings: ${error.message}`, "error");
    } finally {
      loading = false;
    }
  });

  function updateRoute() {
    if (window.location.hash.slice(1) === "settings" && !loaded) {
      load();
    }
  }

  window.addEventListener("hashchange", updateRoute);
  updateRoute();
}
