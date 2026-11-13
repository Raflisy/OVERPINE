function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
      body.error?.message || `Request returned HTTP ${response.status}`,
    );
  }
  return body;
}

function addDetail(list, label, value) {
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value || "Not provided";
  list.append(term, description);
}

export function initializeAboutPage() {
  const page = document.getElementById("about-page");
  if (!page) return;

  const status = document.getElementById("about-status");
  const details = document.getElementById("about-details");
  const importStatus = document.getElementById("about-import-check");
  const findings = document.getElementById("about-import-findings");
  let loading = false;
  let loaded = false;

  async function load() {
    if (loading || loaded) return;
    loading = true;
    status.textContent = "Reading local application details…";
    try {
      const about = await readJson(await fetch("/api/about", {
        cache: "no-store",
      }));
      details.replaceChildren();
      addDetail(details, "Application", `${about.app_name} ${about.app_version}`);
      addDetail(details, "Python", about.python_version);
      addDetail(details, "Windows", about.windows_version);
      addDetail(details, "Machine model", about.machine_model);
      addDetail(details, "Data folder", about.data_directory);
      addDetail(details, "History database", formatBytes(about.database_size_bytes));
      const firstSample = about.first_sample_date
        ? new Date(about.first_sample_date).toLocaleString()
        : "No recorded samples";
      addDetail(details, "First recorded sample", firstSample);
      if (about.history_warning) {
        addDetail(details, "History status", about.history_warning);
      }

      document.getElementById("about-standard-library").textContent =
        about.standard_library_statement;
      const importCheck = about.import_check;
      importStatus.textContent = importCheck.standard_library_only
        ? "Import check: no third-party imports found."
        : `Import check found ${importCheck.imports_found.length} issue(s).`;
      findings.replaceChildren();
      for (const finding of importCheck.imports_found) {
        const item = document.createElement("li");
        item.textContent = finding;
        findings.append(item);
      }
      for (const finding of importCheck.external_frontend_urls) {
        const item = document.createElement("li");
        item.textContent = `External frontend URL: ${finding}`;
        findings.append(item);
      }
      document.getElementById("about-credits").textContent =
        about.credits || "Not provided in config/default_config.toml.";
      document.getElementById("about-course-university").textContent =
        about.course_university
          || "Not provided in config/default_config.toml.";
      status.textContent = "Details read from this computer.";
      loaded = true;
    } catch (error) {
      status.textContent = `Could not load About details: ${error.message}`;
    } finally {
      loading = false;
    }
  }

  function updateRoute() {
    if (window.location.hash.slice(1) === "about") load();
  }

  window.addEventListener("hashchange", updateRoute);
  updateRoute();
}
