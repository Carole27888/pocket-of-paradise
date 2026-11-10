/* Chart helpers: read data from <script type="application/json"> (json_script) and draw with Chart.js. */
(function () {
  "use strict";
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const STAGE_VARS = {
    enquiry: "--st-enquiry", quoted: "--st-quoted", hot: "--st-hot", itinerary: "--st-itinerary",
    deposit: "#D9A300", confirmed: "#14B8A6", completed: "#22C55E", lost: "--st-lost",
  };
  const SOURCE_VARS = {
    facebook: "--ch-fb", instagram: "--ch-ig", tiktok: "--ch-tt", google: "--ch-go", youtube: "--ch-yt",
    website: "--ch-ws", whatsapp: "--ch-wa", walk_in: "--ch-wi", call: "--ch-cl", dm: "--ch-dm",
    referral: "--ch-rf", agent: "--ch-agent", tripadvisor: "--ch-ta",
  };
  const colour = (v) => (v && v.startsWith("--") ? css(v) : v);
  const palette = () => [css("--primary"), css("--accent"), css("--sun"), css("--primary-active"), css("--peach"),
    css("--info"), css("--success"), css("--text-faint")];

  function readData(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (_) { return null; }
  }

  function baseOptions(extra) {
    return Object.assign({
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { font: { family: css("--font-ui"), size: 12 }, boxWidth: 10, usePointStyle: true } },
        tooltip: { titleFont: { family: css("--font-ui") }, bodyFont: { family: css("--font-ui") } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { family: css("--font-ui"), size: 11 }, color: css("--text-muted") } },
        y: { beginAtZero: true, grid: { color: css("--border") }, ticks: { precision: 0, font: { family: css("--font-ui"), size: 11 }, color: css("--text-muted") } },
      },
    }, extra || {});
  }

  const builders = {
    line(d) {
      return {
        type: "line",
        data: { labels: d.labels, datasets: [{ label: d.label || "Enquiries", data: d.values, borderColor: css("--accent"),
          backgroundColor: "rgba(217,138,34,.12)", fill: true, tension: .35, pointRadius: 2, borderWidth: 2.5 }] },
        options: baseOptions({ plugins: { legend: { display: false } } }),
      };
    },
    bar(d) {
      const colors = (d.codes || []).map((c) => colour(STAGE_VARS[c] || SOURCE_VARS[c]) || css("--primary"));
      return {
        type: "bar",
        data: { labels: d.labels, datasets: [{ label: d.label || "Count", data: d.values,
          backgroundColor: colors.length ? colors : css("--primary"), borderRadius: 6, maxBarThickness: 36 }] },
        options: baseOptions({ plugins: { legend: { display: false } } }),
      };
    },
    multiline(d) {
      const p = palette();
      return { type: "line", data: { labels: d.labels, datasets: d.series.map((s, i) => ({ label: s.label, data: s.data,
        borderColor: p[i % p.length], backgroundColor: p[i % p.length], tension: .3, pointRadius: 0, borderWidth: 2.5 })) },
        options: baseOptions({ plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } },
          interaction: { mode: "index", intersect: false } }) };
    },
    combo(d) {
      const o = baseOptions({ plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } } });
      o.scales.y1 = { position: "right", beginAtZero: true, grid: { display: false }, ticks: { color: css("--text-muted"), font: { size: 11 } } };
      return { type: "bar", data: { labels: d.labels, datasets: [
        { type: "bar", label: "Spend (KES)", data: d.spend, backgroundColor: css("--primary"), borderRadius: 4, yAxisID: "y", order: 2 },
        { type: "line", label: "Enquiries", data: d.enquiries, borderColor: css("--accent"), borderWidth: 2.5, pointRadius: 2, tension: .3, yAxisID: "y1", order: 1 }] }, options: o };
    },
    reachweeks(d) {
      const o = baseOptions({ plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } } });
      o.scales.y1 = { position: "right", beginAtZero: true, grid: { display: false }, ticks: { precision: 0, color: css("--text-muted"), font: { size: 11 } } };
      return { type: "bar", data: { labels: d.labels, datasets: [
        { type: "bar", label: "Reach", data: d.reach, backgroundColor: css("--primary"), borderRadius: 4, yAxisID: "y", order: 2 },
        { type: "line", label: "Enquiries", data: d.enquiries, borderColor: css("--accent"), borderWidth: 2.5, tension: .3, yAxisID: "y1", order: 1 }] }, options: o };
    },
    grouped(d) {
      const nf = (v) => (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? Math.round(v / 1e3) + "K" : v);
      const o = baseOptions({ plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } } });
      o.scales.y.ticks = { callback: nf, color: css("--text-muted"), font: { size: 11 } };
      return { type: "bar", data: { labels: d.labels, datasets: [
        { label: "Income", data: d.income, backgroundColor: css("--primary"), borderRadius: 5, maxBarThickness: 22 },
        { label: "Expenses", data: d.expenses, backgroundColor: css("--accent"), borderRadius: 5, maxBarThickness: 22 }] }, options: o };
    },
    weekly(d) {
      const mk = (label, key, color) => ({ label, data: d[key], backgroundColor: color, borderRadius: 4, maxBarThickness: 18 });
      return { type: "bar", data: { labels: d.labels, datasets: [mk("Planned", "planned", "#EDA036"), mk("Ready", "ready", "#3B6FD4"),
        mk("Departed", "departed", "#14B8A6"), mk("Completed", "completed", "#22A06B")] }, options: baseOptions() };
    },
    revenue(d) {
      const nf = (v) => (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? Math.round(v / 1e3) + "K" : v);
      const o = baseOptions({ scales: {
        x: { grid: { display: false }, ticks: { font: { family: css("--font-ui"), size: 11 }, color: css("--text-muted") } },
        y: { beginAtZero: true, grid: { color: css("--border") }, ticks: { callback: nf, font: { family: css("--font-ui"), size: 11 }, color: css("--text-muted") } } } });
      return {
        type: "bar",
        data: { labels: d.labels, datasets: [
          { type: "bar", label: "Revenue (KES)", data: d.revenue, backgroundColor: css("--primary"), borderRadius: 6, maxBarThickness: 34, order: 2 },
          { type: "line", label: "Target", data: d.target, borderColor: css("--accent"), borderWidth: 2.5, pointRadius: 3, tension: .25, order: 1 }] },
        options: o,
      };
    },
    doughnut(d) {
      const p = palette();
      const colors = (d.codes || []).map((c, i) => colour(SOURCE_VARS[c] || STAGE_VARS[c]) || p[i % p.length]);
      return {
        type: "doughnut",
        data: { labels: d.labels, datasets: [{ data: d.values, backgroundColor: colors.length ? colors : p, borderWidth: 2, borderColor: "#fff" }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "64%",
          plugins: { legend: { display: d.legend !== false, position: d.legendPos || (window.innerWidth < 1500 ? "bottom" : "right"), labels: { font: { family: css("--font-ui"), size: 12 }, boxWidth: 10, usePointStyle: true } } } },
      };
    },
  };

  function draw(canvas) {
    if (!window.Chart || canvas.dataset.drawn) return;
    const data = readData(canvas.dataset.chartSource);
    const kind = canvas.dataset.chart;
    if (!data || !builders[kind]) return;
    const sub = canvas.dataset.chartKey ? data[canvas.dataset.chartKey] : data;
    const arr = sub && (sub.values || sub.planned || sub.enquiries || (sub.series && sub.series[0] && sub.series[0].data) || sub.reach || sub.spend);
    if (!sub || !arr || !arr.some((v) => v > 0)) {
      const p = document.createElement("p");
      p.className = "empty text-sm";
      p.textContent = "No data yet for this period.";
      canvas.replaceWith(p);
      return;
    }
    canvas.dataset.drawn = "1";
    new window.Chart(canvas, builders[kind](sub));
  }

  function init(root) { (root || document).querySelectorAll("canvas[data-chart]").forEach(draw); }
  document.addEventListener("DOMContentLoaded", () => init());
  document.addEventListener("htmx:afterSettle", (e) => init(e.target));
  window.dsCharts = { init };
})();
