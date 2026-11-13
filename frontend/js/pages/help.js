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

const stateExplanations = {
  UNAVAILABLE: "No current value is available from the source.",
  UNSUPPORTED: "This value is not exposed through a supported local interface.",
  PERMISSION_RESTRICTED: "Windows denied access because permission is missing.",
  TEMPORARILY_DISCONNECTED: "The sensor source is temporarily disconnected.",
  ERROR: "The sensor provider reported an error.",
  NOT_INITIALIZED: "The provider has not completed discovery or sampling.",
};

export function initializeHelpPage() {
  const page = document.getElementById("help-page");
  if (!page) return;

  const status = document.getElementById("help-sensor-status");
  const list = document.getElementById("help-sensor-list");
  const dataFolder = document.getElementById("help-data-folder");
  let loading = false;

  async function load() {
    if (loading) return;
    loading = true;
    status.textContent = "Reading current sensor states…";
    try {
      const [sensorBody, settingsBody] = await Promise.all([
        readJson(await fetch("/api/v1/sensors", { cache: "no-store" })),
        readJson(await fetch("/api/settings", { cache: "no-store" })),
      ]);
      const unavailable = sensorBody.sensors.filter(
        (sensor) => sensor.state !== "AVAILABLE",
      );
      list.replaceChildren();
      for (const sensor of unavailable) {
        const item = document.createElement("li");
        const explanation = sensor.error_message
          || stateExplanations[sensor.state]
          || `Reported state: ${sensor.state}`;
                item.textContent = explanation.includes("allow_external_programs")
          ? `${sensor.name} (${sensor.id}) · disabled by your setting (windows_tools.allow_external_programs), not a hardware limit`
          : `${sensor.name} (${sensor.id}) · ${sensor.state}: ${explanation}`;
          list.append(item);
      }
      status.textContent = unavailable.length
        ? `${unavailable.length} currently discovered sensor(s) are not available.`
        : sensorBody.sensors.length
          ? "All currently discovered sensors are available."
          : "No sensors have been discovered yet.";
      dataFolder.textContent = settingsBody.data_directory;
    } catch (error) {
      status.textContent = `Could not read live sensor states: ${error.message}`;
      dataFolder.textContent = "Could not read the data folder.";
    } finally {
      loading = false;
    }
  }

  function updateRoute() {
    if (window.location.hash.slice(1) === "help") load();
  }

  window.addEventListener("hashchange", updateRoute);
  updateRoute();
}
