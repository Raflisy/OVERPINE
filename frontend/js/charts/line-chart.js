const DEFAULT_SERIES_COLORS = [
  "#43b8d2",
  "#4a91d9",
  "#91b75b",
  "#d7a348",
  "#d65b83",
  "#aa72c8",
];

function cssToken(element, name, fallback) {
  const value = getComputedStyle(element).getPropertyValue(name).trim();
  return value || fallback;
}

function displayTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function shortNumber(value) {
  if (!Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (magnitude >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  if (magnitude >= 100) return value.toFixed(0);
  if (magnitude >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function formatValue(series, value) {
  if (!Number.isFinite(value)) return "Unavailable";
  return typeof series.format === "function"
    ? series.format(value)
    : shortNumber(value);
}

function yRange(values) {
  if (values.length === 0) return { minimum: 0, maximum: 1 };
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (minimum === maximum) {
    const padding = Math.max(Math.abs(minimum) * 0.1, 1);
    minimum -= padding;
    maximum += padding;
  } else {
    const padding = (maximum - minimum) * 0.08;
    minimum -= padding;
    maximum += padding;
  }
  if (minimum >= 0) minimum = Math.max(0, minimum);
  return { minimum, maximum };
}

export class LineChart {
  constructor(canvas, options = {}) {
    if (!(canvas instanceof HTMLCanvasElement)) {
      throw new TypeError("LineChart requires a canvas element");
    }
    if (!Array.isArray(options.series) || options.series.length === 0) {
      throw new TypeError("LineChart requires at least one series");
    }
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("The browser could not create a 2D canvas context");
    }

    this.canvas = canvas;
    this.context = context;
    this.series = options.series.map((series, index) => {
      if (!series.key || !series.label) {
        throw new TypeError("Each chart series requires a key and label");
      }
      return {
        ...series,
        color: series.color || DEFAULT_SERIES_COLORS[index % DEFAULT_SERIES_COLORS.length],
      };
    });
    this.timeWindowMs = options.timeWindowMs || null;
    this.samples = [];
    this.hoverIndex = null;
    this.statsElement = options.statsElement || document.createElement("div");
    this.ownsStatsElement = !options.statsElement;
    this.statsElement.classList.add("line-chart-statistics");
    if (this.ownsStatsElement) {
      canvas.insertAdjacentElement("afterend", this.statsElement);
    }

    this.handlePointerMove = (event) => {
      this.hoverIndex = this._nearestSample(event.offsetX);
      this.draw();
    };
    this.handlePointerLeave = () => {
      this.hoverIndex = null;
      this.draw();
    };
    canvas.addEventListener("pointermove", this.handlePointerMove);
    canvas.addEventListener("pointerleave", this.handlePointerLeave);

    this.resizeObserver = new ResizeObserver(() => this.draw());
    this.resizeObserver.observe(canvas);
    this.themeObserver = new MutationObserver(() => this.draw());
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    this.update([]);
  }

  update(samples) {
    if (!Array.isArray(samples)) {
      throw new TypeError("Chart samples must be an array");
    }
    this.samples = samples.map((sample) => {
      if (!Number.isFinite(sample.time) || !sample.values) {
        throw new TypeError("Each chart sample requires a numeric time and values");
      }
      return {
        time: sample.time,
        values: { ...sample.values },
      };
    });
    this.hoverIndex = null;
    this.draw();
    this._renderStatistics();
  }

  draw() {
    const width = this.canvas.clientWidth;
    const height = this.canvas.clientHeight;
    if (width <= 0 || height <= 0) return;

    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const backingWidth = Math.round(width * ratio);
    const backingHeight = Math.round(height * ratio);
    if (this.canvas.width !== backingWidth || this.canvas.height !== backingHeight) {
      this.canvas.width = backingWidth;
      this.canvas.height = backingHeight;
    }
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.context.clearRect(0, 0, width, height);

    const styleElement = this.canvas.closest(".monitor-card") || this.canvas;
    const textColor = cssToken(styleElement, "--text-muted", "#a0a0a0");
    const gridColor = cssToken(styleElement, "--border", "#262626");
    const plot = {
      left: Math.min(48, width * 0.16),
      right: width - 12,
      top: 34,
      bottom: height - 27,
    };
    if (plot.right <= plot.left || plot.bottom <= plot.top) return;

    const latestTime = this.samples.at(-1)?.time || Date.now();
    const firstTime = this.timeWindowMs
      ? latestTime - this.timeWindowMs
      : this.samples[0]?.time ?? latestTime - 1;
    const lastTime = latestTime > firstTime ? latestTime : firstTime + 1;
    const values = this.samples.flatMap((sample) =>
      this.series
        .map((series) => sample.values[series.key])
        .filter(Number.isFinite),
    );
    const range = yRange(values);
    const yAt = (value) => plot.bottom
      - ((value - range.minimum) / (range.maximum - range.minimum))
        * (plot.bottom - plot.top);
    const xAt = (time) => plot.left
      + ((time - firstTime) / (lastTime - firstTime))
        * (plot.right - plot.left);

    this._drawLegend(plot, textColor, styleElement);
    this.context.font = "10px system-ui, sans-serif";
    this.context.textAlign = "right";
    this.context.textBaseline = "middle";
    this.context.fillStyle = textColor;
    this.context.strokeStyle = gridColor;
    this.context.lineWidth = 1;
    for (let index = 0; index <= 4; index += 1) {
      const ratioY = index / 4;
      const y = plot.top + ratioY * (plot.bottom - plot.top);
      const value = range.maximum - ratioY * (range.maximum - range.minimum);
      this.context.beginPath();
      this.context.moveTo(plot.left, y);
      this.context.lineTo(plot.right, y);
      this.context.stroke();
      this.context.fillText(shortNumber(value), plot.left - 7, y);
    }

    this.context.textAlign = "center";
    this.context.textBaseline = "top";
    for (let index = 0; index <= 2; index += 1) {
      const ratioX = index / 2;
      const x = plot.left + ratioX * (plot.right - plot.left);
      const timestamp = firstTime + ratioX * (lastTime - firstTime);
      this.context.beginPath();
      this.context.moveTo(x, plot.top);
      this.context.lineTo(x, plot.bottom);
      this.context.stroke();
      this.context.fillText(displayTime(timestamp), x, plot.bottom + 5);
    }

    for (const series of this.series) {
      const color = cssToken(styleElement, series.cssVariable, series.color);
      this.context.beginPath();
      let connected = false;
      for (const sample of this.samples) {
        const value = sample.values[series.key];
        if (!Number.isFinite(value)) {
          connected = false;
          continue;
        }
        const x = xAt(sample.time);
        const y = yAt(value);
        if (connected) this.context.lineTo(x, y);
        else this.context.moveTo(x, y);
        connected = true;
      }
      this.context.strokeStyle = color;
      this.context.lineWidth = 2;
      this.context.lineJoin = "round";
      this.context.lineCap = "round";
      this.context.stroke();
    }

    if (this.hoverIndex !== null && this.samples[this.hoverIndex]) {
      this._drawTooltip(
        this.samples[this.hoverIndex],
        xAt,
        plot,
        width,
        height,
        styleElement,
      );
    }
  }

  getStatistics() {
    return Object.fromEntries(this.series.map((series) => {
      const values = this.samples
        .map((sample) => sample.values[series.key])
        .filter(Number.isFinite);
      const current = this.samples.at(-1)?.values[series.key];
      return [series.key, {
        current: Number.isFinite(current) ? current : null,
        average: values.length
          ? values.reduce((total, value) => total + value, 0) / values.length
          : null,
        peak: values.length ? Math.max(...values) : null,
      }];
    }));
  }

  destroy() {
    this.resizeObserver.disconnect();
    this.themeObserver.disconnect();
    this.canvas.removeEventListener("pointermove", this.handlePointerMove);
    this.canvas.removeEventListener("pointerleave", this.handlePointerLeave);
    if (this.ownsStatsElement) this.statsElement.remove();
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  _drawLegend(plot, textColor, styleElement) {
    const context = this.context;
    context.font = "10px system-ui, sans-serif";
    context.textBaseline = "middle";
    let x = plot.left;
    let y = 16;
    for (const series of this.series) {
      const labelWidth = context.measureText(series.label).width;
      const itemWidth = labelWidth + 26;
      if (x + itemWidth > plot.right && x > plot.left) {
        x = plot.left;
        y += 14;
      }
      const color = cssToken(styleElement, series.cssVariable, series.color);
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(x, y);
      context.lineTo(x + 13, y);
      context.stroke();
      context.fillStyle = textColor;
      context.textAlign = "left";
      context.fillText(series.label, x + 18, y);
      x += itemWidth;
    }
  }

  _drawTooltip(sample, xAt, plot, width, height, styleElement) {
    const context = this.context;
    const rows = [
      displayTime(sample.time),
      ...this.series.map((series) =>
        `${series.label}: ${formatValue(series, sample.values[series.key])}`),
    ];
    context.font = "11px system-ui, sans-serif";
    const boxWidth = Math.max(...rows.map((row) => context.measureText(row).width)) + 16;
    const boxHeight = rows.length * 17 + 10;
    const pointX = xAt(sample.time);
    const boxX = Math.min(
      Math.max(plot.left, pointX + 10),
      width - boxWidth - 5,
    );
    const boxY = Math.min(Math.max(plot.top, 40), height - boxHeight - 5);
    context.fillStyle = cssToken(styleElement, "--surface-raised", "#0d0d0d");
    context.strokeStyle = cssToken(styleElement, "--border", "#262626");
    context.lineWidth = 1;
    context.beginPath();
    context.roundRect(boxX, boxY, boxWidth, boxHeight, 5);
    context.fill();
    context.stroke();
    context.textAlign = "left";
    context.textBaseline = "top";
    rows.forEach((row, index) => {
      const series = this.series[index - 1];
      context.fillStyle = series
        ? cssToken(styleElement, series.cssVariable, series.color)
        : cssToken(styleElement, "--text", "#f5f5f5");
      context.fillText(row, boxX + 8, boxY + 6 + index * 17);
    });
  }

  _nearestSample(offsetX) {
    if (!this.samples.length) return null;
    const width = this.canvas.clientWidth;
    const left = Math.min(48, width * 0.16);
    const right = width - 12;
    const sampleTime = this._timeAt(offsetX, left, right);
    let nearestIndex = 0;
    let nearestDistance = Infinity;
    for (let index = 0; index < this.samples.length; index += 1) {
      const distance = Math.abs(this.samples[index].time - sampleTime);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }
    }
    return nearestIndex;
  }

  _timeAt(offsetX, left, right) {
    const latestTime = this.samples.at(-1)?.time || Date.now();
    const firstTime = this.timeWindowMs
      ? latestTime - this.timeWindowMs
      : this.samples[0]?.time ?? latestTime - 1;
    const lastTime = latestTime > firstTime ? latestTime : firstTime + 1;
    const ratio = Math.max(0, Math.min(1, (offsetX - left) / (right - left)));
    return firstTime + ratio * (lastTime - firstTime);
  }

  _renderStatistics() {
    const table = document.createElement("table");
    table.className = "monitor-statistics";
    table.setAttribute("aria-label", "Current, average, and peak chart values");
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    for (const label of ["Metric", "Current", "Average", "Peak"]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      header.append(cell);
    }
    head.append(header);
    const body = document.createElement("tbody");
    const statistics = this.getStatistics();
    for (const series of this.series) {
      const row = document.createElement("tr");
      const label = document.createElement("th");
      label.scope = "row";
      label.textContent = series.label;
      row.append(label);
      for (const value of Object.values(statistics[series.key])) {
        const cell = document.createElement("td");
        cell.textContent = value === null ? "—" : formatValue(series, value);
        row.append(cell);
      }
      body.append(row);
    }
    table.append(head, body);
    this.statsElement.replaceChildren(table);
  }
}
