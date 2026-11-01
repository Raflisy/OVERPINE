import { initializeStressPage } from "./pages/stress.js";
import { initializeSensorsPage } from "./pages/sensors.js";

const themeStorageKey = "overpine-theme";
const root = document.documentElement;
const themeToggle = document.getElementById("theme-toggle");
const themeLabel = document.getElementById("theme-toggle-label");
const adminRestartButton = document.getElementById("restart-admin");
const adminRestartStatus = document.getElementById("admin-restart-status");
const monitorHistory = [];
const monitorHistoryWindowMs = 60_000;
const graphSettingsStoragePrefix = "overpine-graph-";
const monitorGraphs = [
  {
    key: "cpu",
    title: "Processor",
    cardId: "cpu-monitor-card",
    statsId: "cpu-chart-stats",
    explanationId: "cpu-chart-explanation",
    explanation: "Total CPU utilization reported by the operating system. Clock speed is shown in the current reading when available.",
    series: [
      { key: "cpu", label: "CPU", path: "cpu-chart", color: "#43b8d2", variable: "--chart-cpu-color", unit: "percent" },
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
      { key: "memory", label: "In use", path: "memory-chart", color: "#4a91d9", variable: "--chart-memory-color", unit: "percent" },
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
      { key: "diskRead", label: "Read", path: "disk-read", color: "#91b75b", variable: "--chart-disk-read-color", unit: "rate" },
      { key: "diskWrite", label: "Write", path: "disk-write", color: "#d7a348", variable: "--chart-disk-write-color", unit: "rate" },
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
      { key: "networkReceive", label: "Receive", path: "network-receive", color: "#d65b83", variable: "--chart-network-receive-color", unit: "rate" },
      { key: "networkSend", label: "Send", path: "network-send", color: "#f09ab3", variable: "--chart-network-send-color", unit: "rate" },
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
      { key: "gpu", label: "3D engine", path: "gpu-chart", color: "#aa72c8", variable: "--chart-gpu-color", unit: "percent" },
    ],
  },
];
let messageForModel = "";
let deviceNoteRetryAt = 0;
let deviceLogoLookupKey = "";

const commonsLogoFallbacks = {
  lenovo: {
    imageUrl: "https://upload.wikimedia.org/wikipedia/commons/b/b8/Lenovo_logo_2015.svg",
  },
  thinkpad: {
    imageUrl: "https://upload.wikimedia.org/wikipedia/commons/0/09/ThinkPad_Logo.svg",
  },
};

const deviceBrands = [
  {
    key: "lenovo",
    label: "Lenovo",
    aliases: /\blenovo\b/i,
    families: [
      { label: "ThinkPad", pattern: /\bthinkpad\b/i },
      { label: "ThinkBook", pattern: /\bthinkbook\b/i },
      { label: "Yoga", pattern: /\byoga\b/i },
      { label: "Legion", pattern: /\blegion\b/i },
      { label: "IdeaPad", pattern: /\bideapad\b/i },
    ],
  },
  {
    key: "dell",
    label: "Dell",
    aliases: /\bdell\b/i,
    families: ["XPS", "Latitude", "Precision", "Inspiron", "Alienware"],
  },
  {
    key: "hp",
    label: "HP",
    aliases: /\b(?:hp|hewlett[\s-]*packard)\b/i,
    families: ["EliteBook", "ProBook", "Pavilion", "OMEN", "ZBook"],
  },
  {
    key: "apple",
    label: "Apple",
    aliases: /\bapple\b/i,
    families: ["MacBook Pro", "MacBook Air", "MacBook", "iMac", "Mac mini", "Mac Studio"],
  },
  {
    key: "microsoft",
    label: "Microsoft",
    aliases: /\bmicrosoft\b/i,
    families: ["Surface"],
  },
  {
    key: "asus",
    label: "ASUS",
    aliases: /\basus(?:tek)?\b/i,
    families: ["ROG", "ZenBook", "VivoBook", "TUF", "ProArt"],
  },
  {
    key: "acer",
    label: "Acer",
    aliases: /\bacer\b/i,
    families: ["Predator", "Swift", "Aspire", "TravelMate"],
  },
  {
    key: "msi",
    label: "MSI",
    aliases: /\b(?:msi|micro[\s-]*star)\b/i,
    families: ["Titan", "Stealth", "Raider", "Prestige", "Modern"],
  },
  {
    key: "toshiba",
    label: "Toshiba",
    aliases: /\b(?:toshiba|dynabook)\b/i,
    families: ["Dynabook", "Satellite", "Tecra", "Portégé"],
  },
  {
    key: "lg",
    label: "LG",
    aliases: /\blg\b/i,
    families: ["gram"],
  },
  {
    key: "huawei",
    label: "Huawei",
    aliases: /\bhuawei\b/i,
    families: ["MateBook"],
  },
  {
    key: "razer",
    label: "Razer",
    aliases: /\brazer\b/i,
    families: ["Blade"],
  },
  {
    key: "samsung",
    label: "Samsung",
    aliases: /\bsamsung\b/i,
    families: ["Galaxy Book"],
  },
  {
    key: "framework",
    label: "Framework",
    aliases: /\bframework\b/i,
    families: [],
  },
];

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

const savedTheme = window.localStorage.getItem(themeStorageKey);
setTheme(savedTheme === "light" || savedTheme === "dark"
  ? savedTheme
  : "dark");

themeToggle.addEventListener("click", () => {
  const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
  window.localStorage.setItem(themeStorageKey, nextTheme);
  setTheme(nextTheme);
});

async function responseMessage(response, fallback) {
  const body = (await response.text()).trim();
  if (!body) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(body);
    return parsed.detail || parsed.message || fallback;
  } catch {
    return body.slice(0, 240);
  }
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
  const saved = window.localStorage.getItem(
    graphPreferenceKey(graph.key, `color-${series.key}`),
  );
  return /^#[0-9a-f]{6}$/i.test(saved || "") ? saved : series.color;
}

function createMonitorStatistics(graph) {
  const table = document.createElement("table");
  table.className = "monitor-statistics";
  table.setAttribute("aria-label", `${graph.title} current, average, and peak over the last 60 seconds`);
  const header = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const label of ["Metric", "Current", "Average", "Peak"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    headerRow.append(cell);
  }
  header.append(headerRow);
  const body = document.createElement("tbody");
  for (const series of graph.series) {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = series.label;
    row.append(label);
    for (const stat of ["current", "average", "peak"]) {
      const cell = document.createElement("td");
      cell.id = `${graph.key}-${series.key}-${stat}`;
      cell.textContent = "—";
      row.append(cell);
    }
    body.append(row);
  }
  table.append(header, body);
  document.getElementById(graph.statsId).replaceChildren(table);
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
    visibility.checked = window.localStorage.getItem(
      graphPreferenceKey(graph.key, "visible"),
    ) !== "false";
    visibilityLabel.append(visibility, document.createTextNode(graph.title));
    heading.append(visibilityLabel);
    setting.append(heading);

    const card = document.getElementById(graph.cardId);
    card.hidden = !visibility.checked;
    visibility.addEventListener("change", () => {
      card.hidden = !visibility.checked;
      window.localStorage.setItem(
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
        window.localStorage.setItem(
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
      window.localStorage.removeItem(graphPreferenceKey(graph.key, "visible"));
      const setting = document.getElementById(`graph-visible-${graph.key}`).closest(".graph-setting");
      const visibility = document.getElementById(`graph-visible-${graph.key}`);
      const card = document.getElementById(graph.cardId);
      visibility.checked = true;
      card.hidden = false;
      for (const [index, series] of graph.series.entries()) {
        window.localStorage.removeItem(
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

function findDeviceFamily(searchText, brand) {
  return brand?.families.find((candidate) =>
    typeof candidate === "string"
      ? new RegExp(`\\b${candidate.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(searchText)
      : candidate.pattern.test(searchText),
  );
}

async function findCommonsLogo(searchTerm) {
  try {
    return await fetchCommonsLogoFromCommons(searchTerm);
  } catch (error) {
    const fallback = commonsLogoFallbacks[searchTerm.toLowerCase()];
    if (fallback) {
      return fallback;
    }
    throw error;
  }
}

async function fetchCommonsLogoFromCommons(searchTerm) {
  const searchUrl = new URL("https://commons.wikimedia.org/w/api.php");
  searchUrl.search = new URLSearchParams({
    action: "query",
    list: "search",
    srsearch: `${searchTerm} logo`,
    srnamespace: "6",
    srlimit: "10",
    format: "json",
    origin: "*",
  });
  const searchResponse = await fetch(searchUrl, { cache: "force-cache" });
  if (!searchResponse.ok) {
    throw new Error(`Wikimedia Commons search returned HTTP ${searchResponse.status}`);
  }
  const searchData = await searchResponse.json();
  const searchTerms = searchTerm.toLowerCase().split(/\s+/);
  const matchingTitles = (searchData.query?.search || [])
    .map((result) => result.title)
    .filter((title) => {
      const lowerTitle = title.toLowerCase();
      const isImage = /\.(svg|png|webp)$/i.test(lowerTitle);
      return isImage
        && lowerTitle.includes("logo")
        && searchTerms.every((term) => lowerTitle.includes(term));
    })
    .sort((left, right) => {
      const score = (title) => {
        const terms = title.toLowerCase()
          .replace(/^file:/, "")
          .replace(/\.(svg|png|webp)$/i, "")
          .split(/[^a-z0-9]+/)
          .filter(Boolean);
        const extras = terms.filter((term) =>
          term !== "logo" && !searchTerms.includes(term),
        ).length;
        return extras + (title.toLowerCase().endsWith(".svg") ? 0 : 10);
      };
      return score(left) - score(right);
    });
  if (matchingTitles.length === 0) {
    return commonsLogoFallbacks[searchTerm.toLowerCase()] || null;
  }

  const infoUrl = new URL("https://commons.wikimedia.org/w/api.php");
  infoUrl.search = new URLSearchParams({
    action: "query",
    titles: matchingTitles.join("|"),
    prop: "imageinfo",
    iiprop: "url|extmetadata",
    format: "json",
    origin: "*",
  });
  const infoResponse = await fetch(infoUrl, { cache: "force-cache" });
  if (!infoResponse.ok) {
    throw new Error(`Wikimedia Commons image details returned HTTP ${infoResponse.status}`);
  }
  const infoData = await infoResponse.json();
  const pages = Object.values(infoData.query?.pages || {});
  const pagesByTitle = new Map(pages.map((page) => [page.title, page]));
  const image = matchingTitles
    .map((title) => pagesByTitle.get(title)?.imageinfo?.[0])
    .find((candidate) => {
      const metadata = candidate?.extmetadata || {};
      const license = metadata.LicenseShortName?.value;
      return candidate?.url
        && new URL(candidate.url).hostname === "upload.wikimedia.org"
        && candidate.descriptionurl?.startsWith("https://commons.wikimedia.org/wiki/")
        && metadata.Copyrighted?.value === "False"
        && (license === "Public domain" || /^CC0(?:\s|$)/i.test(license || ""))
        && !metadata.Categories?.value?.includes("Superseded logos");
    });
  return image
    ? { imageUrl: image.url }
    : commonsLogoFallbacks[searchTerm.toLowerCase()] || null;
}

function setLogoImage(imageId, containerId, image, altText) {
  const imageElement = document.getElementById(imageId);
  const containerElement = document.getElementById(containerId);
  if (!image) {
    containerElement.hidden = true;
    return false;
  }
  imageElement.src = image.imageUrl;
  imageElement.alt = altText;
  imageElement.onerror = () => {
    containerElement.hidden = true;
    const gallery = document.getElementById("device-logo-gallery");
    const status = document.getElementById("device-logo-status");
    const hasVisibleLogo = [...gallery.querySelectorAll(".device-logo-container")]
      .some((container) => !container.hidden);
    gallery.hidden = !hasVisibleLogo;
    status.textContent = "A device logo image couldn't be loaded from Wikimedia Commons.";
    status.hidden = false;
  };
  containerElement.hidden = false;
  return true;
}

async function renderDeviceLogos(brandLabel, familyLabel) {
  const lookupKey = `${brandLabel}|${familyLabel || ""}`.toLowerCase();
  if (deviceLogoLookupKey === lookupKey) {
    return;
  }
  deviceLogoLookupKey = lookupKey;
  const gallery = document.getElementById("device-logo-gallery");
  const status = document.getElementById("device-logo-status");
  gallery.hidden = true;
  status.hidden = false;
  status.textContent = "Looking for manufacturer and product-family logos online...";
  setLogoImage("device-brand-logo", "device-brand-logo-container", null, "");
  setLogoImage("device-family-logo", "device-family-logo-container", null, "");

  try {
    const [brandResult, familyResult] = await Promise.allSettled([
      findCommonsLogo(brandLabel),
      familyLabel ? findCommonsLogo(familyLabel) : Promise.resolve(null),
    ]);
    if (deviceLogoLookupKey !== lookupKey) {
      return;
    }

    const brandLogo = brandResult.status === "fulfilled" ? brandResult.value : null;
    const familyLogo = familyResult.status === "fulfilled" ? familyResult.value : null;
    const hasBrandLogo = setLogoImage(
      "device-brand-logo",
      "device-brand-logo-container",
      brandLogo,
      `${brandLabel} logo`,
    );
    const hasFamilyLogo = setLogoImage(
      "device-family-logo",
      "device-family-logo-container",
      familyLogo,
      `${familyLabel} logo`,
    );
    gallery.hidden = !hasBrandLogo && !hasFamilyLogo;
    const failedRequests = [brandResult, familyResult]
      .filter((result) => result.status === "rejected");
    status.hidden = hasBrandLogo && hasFamilyLogo;
    status.textContent = failedRequests.length > 0
      ? `Some device logos couldn't be loaded: ${failedRequests[0].reason.message}`
      : hasBrandLogo || hasFamilyLogo
        ? "A reusable logo image wasn't found for one part of this device."
        : "No public-domain or CC0 device logos were found online.";
  } catch (error) {
    if (deviceLogoLookupKey !== lookupKey) {
      return;
    }
    status.textContent = `Device logos couldn't be loaded: ${error.message}`;
  }
}

function renderDeviceBrand(system) {
  const searchText = [
    system.manufacturer,
    system.device_model,
    system.product_code,
  ].filter(Boolean).join(" ");
  const brand = deviceBrands.find((candidate) =>
    candidate.aliases.test(system.manufacturer || searchText),
  ) || deviceBrands.find((candidate) => candidate.aliases.test(searchText));
  const manufacturer = system.manufacturer === "Not detected"
    ? "Computer"
    : system.manufacturer;
  const brandLabel = brand?.label || manufacturer;
  const family = findDeviceFamily(searchText, brand);
  const familyLabel = typeof family === "string" ? family : family?.label;
  if (brandLabel === "Computer") {
    deviceLogoLookupKey = "";
    document.getElementById("device-logo-gallery").hidden = true;
    document.getElementById("device-logo-status").hidden = true;
    return;
  }
  renderDeviceLogos(brandLabel, familyLabel);
}

function updateMonitorCharts(system) {
  const now = Date.now();
  monitorHistory.push({
    time: now,
    cpu: Math.min(100, Math.max(0, Number(system.cpu_usage_percent) || 0)),
    memory: Math.min(100, Math.max(0, Number(system.memory_used_percent) || 0)),
    diskRead: Number.isFinite(system.disk_read_bytes_per_sec)
      ? Math.max(0, system.disk_read_bytes_per_sec)
      : null,
    diskWrite: Number.isFinite(system.disk_write_bytes_per_sec)
      ? Math.max(0, system.disk_write_bytes_per_sec)
      : null,
    networkReceive: Number.isFinite(system.network_receive_bytes_per_sec)
      ? Math.max(0, system.network_receive_bytes_per_sec)
      : null,
    networkSend: Number.isFinite(system.network_send_bytes_per_sec)
      ? Math.max(0, system.network_send_bytes_per_sec)
      : null,
    gpu: Number.isFinite(system.gpu_usage_percent)
      ? Math.min(100, Math.max(0, system.gpu_usage_percent))
      : null,
  });
  while (monitorHistory.length > 1 && now - monitorHistory[0].time > monitorHistoryWindowMs) {
    monitorHistory.shift();
  }

  const formatMonitorValue = (value, unit) => {
    if (value === null || !Number.isFinite(value)) {
      return "—";
    }
    if (unit === "percent") {
      return `${value.toFixed(1)}%`;
    }
    if (value >= 1024 ** 2) {
      return `${(value / (1024 ** 2)).toFixed(2)} MiB/s`;
    }
    if (value >= 1024) {
      return `${(value / 1024).toFixed(1)} KiB/s`;
    }
    return `${value.toFixed(0)} B/s`;
  };
  for (const graph of monitorGraphs) {
    const graphSamples = graph.series.flatMap((series) =>
      monitorHistory
        .filter((sample) => Number.isFinite(sample[series.key]))
        .map((sample) => sample[series.key]),
    );
    const sharedScale = graph.series[0].unit === "percent"
      ? 100
      : Math.max(1024, ...graphSamples) * 1.1;

    for (const series of graph.series) {
      const samples = monitorHistory.filter((sample) =>
        Number.isFinite(sample[series.key]),
      );
      const points = samples.map((sample) => ({
        x: 24 + ((sample.time - (now - monitorHistoryWindowMs)) / monitorHistoryWindowMs) * 592,
        y: 148 - (sample[series.key] / sharedScale) * 128,
      }));
      const linePath = points.map((point, index) =>
        `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`,
      ).join(" ");
      const first = points[0];
      const last = points[points.length - 1];
      document.getElementById(`${series.path}-line`).setAttribute("d", linePath);
      document.getElementById(`${series.path}-area`).setAttribute(
        "d",
        first && last
          ? `${linePath} L${last.x.toFixed(1)} 148 L${first.x.toFixed(1)} 148 Z`
          : "",
      );
      const values = samples.map((sample) => sample[series.key]);
      const latestValue = monitorHistory[monitorHistory.length - 1][series.key];
      const average = values.length > 0
        ? values.reduce((total, value) => total + value, 0) / values.length
        : null;
      const peak = values.length > 0 ? Math.max(...values) : null;
      document.getElementById(`${graph.key}-${series.key}-current`).textContent =
        formatMonitorValue(latestValue, series.unit);
      document.getElementById(`${graph.key}-${series.key}-average`).textContent =
        formatMonitorValue(average, series.unit);
      document.getElementById(`${graph.key}-${series.key}-peak`).textContent =
        formatMonitorValue(peak, series.unit);
    }
  }

  const latest = monitorHistory[monitorHistory.length - 1];
  const rateLabel = (value) => {
    if (value === null) {
      return "—";
    }
    if (value >= 1024 ** 2) {
      return `${(value / (1024 ** 2)).toFixed(2)} MiB/s`;
    }
    if (value >= 1024) {
      return `${(value / 1024).toFixed(1)} KiB/s`;
    }
    return `${value.toFixed(0)} B/s`;
  };

  document.getElementById("cpu-chart-current").textContent =
    `${latest.cpu.toFixed(0)}%${Number.isFinite(system.cpu_frequency_mhz) ? ` · ${(system.cpu_frequency_mhz / 1000).toFixed(2)} GHz` : ""}`;
  document.getElementById("memory-chart-current").textContent =
    `${latest.memory.toFixed(0)}%`;
  const memoryTotal = Number(system.memory_total_gib) || 0;
  const memoryUsed = Math.max(
    0,
    memoryTotal - (Number(system.memory_available_gib) || 0),
  );
  const memoryAvailable = Math.max(0, memoryTotal - memoryUsed);
  document.getElementById("memory-composition-used").style.width =
    `${latest.memory}%`;
  document.querySelector(".memory-composition").setAttribute(
    "aria-label",
    `${memoryUsed.toFixed(1)} GiB in use, ${memoryAvailable.toFixed(1)} GiB available`,
  );
  document.getElementById("memory-composition-used-label").textContent =
    `In use ${memoryUsed.toFixed(1)} GiB`;
  document.getElementById("memory-composition-available-label").textContent =
    `Available ${memoryAvailable.toFixed(1)} GiB`;
  document.getElementById("disk-chart-current").textContent =
    `R ${rateLabel(latest.diskRead)} · W ${rateLabel(latest.diskWrite)}`;
  document.getElementById("network-chart-current").textContent =
    `↓ ${rateLabel(latest.networkReceive)} · ↑ ${rateLabel(latest.networkSend)}`;
  document.getElementById("gpu-chart-current").textContent =
    latest.gpu === null
      ? system.sensor_sampled_at ? "Unavailable" : "Sampling…"
      : `${latest.gpu.toFixed(0)}%`;
}

function renderHardwareDetails(system) {
  const grid = document.getElementById("hardware-details-grid");
  const hasPreviousSections = grid.querySelector("details") !== null;
  const openSections = new Set(
    [...grid.querySelectorAll("details[open] summary")].map((summary) => summary.textContent),
  );
  grid.replaceChildren();

  const sections = Array.isArray(system.hardware_sections)
    ? system.hardware_sections
    : [];
  for (const [index, section] of sections.entries()) {
    const details = document.createElement("details");
    details.className = "hardware-details-card";
    details.open = hasPreviousSections
      ? openSections.has(section.title)
      : index === 0;

    const heading = document.createElement("summary");
    heading.textContent = section.title;
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
    grid.append(details);
  }
  document.getElementById("hardware-inventory-status").textContent =
    system.hardware_inventory_status
      || "Extended hardware details are not available.";
}

function renderSystemInfo(system) {
  renderHardwareDetails(system);
  renderDeviceBrand(system);
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
  try {
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
  }
}

initializeGraphSettings();
initializeStressPage();
initializeSensorsPage();
updateDashboard();
window.setInterval(updateDashboard, 3000);
