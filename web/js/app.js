/*
 * SysMon - Dashboard
 * Talks to the Flask API only (see docs/INTERFACES.md section 9).
 * Shared helpers live in common.js.
 */

let refreshTimer = null;
let activeMetric = "cpu_pct";
let metricsData = null;

const RANGE_NAMES = { "1h": "last hour", "24h": "last 24 hours", "7d": "last 7 days" };


// --------------------------------------------------
// METRICS HELPERS
// --------------------------------------------------

function series(data, column) {
    const index = data?.columns?.indexOf(column) ?? -1;
    if (index < 0) return [];
    return (data.rows || []).map((row) => num(row[index]));
}

function lastValue(values) {
    for (let i = values.length - 1; i >= 0; i--) {
        if (values[i] !== null) return values[i];
    }
    return null;
}

function levelFor(value, warn, crit) {
    if (value === null) return "";
    return value >= crit ? "crit" : value >= warn ? "warn" : "ok";
}

function sparkline(id, values) {
    const svg = $(id);
    if (!svg) return;

    const usable = values.filter((v) => v !== null).slice(-60);
    if (usable.length < 2) { svg.innerHTML = ""; return; }

    const min = Math.min(...usable);
    const max = Math.max(...usable);
    const span = max - min || 1;

    const points = usable.map((v, i) => {
        const x = (i / (usable.length - 1)) * 120;
        const y = 30 - ((v - min) / span) * 26;
        return `${x.toFixed(1)} ${y.toFixed(1)}`;
    });

    const line = "M " + points.join(" L ");
    svg.innerHTML =
        `<path class="spark-area" d="${line} L 120 34 L 0 34 Z"/>` +
        `<path class="spark-line" d="${line}"/>`;
}


// --------------------------------------------------
// RADIAL DIALS (tick gauge, swept on load and on every update)
// --------------------------------------------------

const DIAL_TICKS = 56;
const dialState = {};

function buildDial(card) {
    const svg = card.querySelector(".dial-svg");
    const size = 230, R = size / 2, r1 = R - 8, r0 = R - 30;
    const lines = [];

    for (let i = 0; i < DIAL_TICKS; i++) {
        // 300-degree sweep, the gap sits at the bottom
        const a = (-240 + (i / (DIAL_TICKS - 1)) * 300) * (Math.PI / 180);
        const x1 = R + Math.cos(a) * r0, y1 = R + Math.sin(a) * r0;
        const x2 = R + Math.cos(a) * r1, y2 = R + Math.sin(a) * r1;
        lines.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`);
    }

    svg.innerHTML = lines.join("");
    return { lines: [...svg.querySelectorAll("line")], shown: 0, raf: 0 };
}

function setDial(key, value, level, max = 100) {
    const card = document.querySelector(`[data-dial="${key}"]`);
    if (!card) return;

    const state = dialState[key] || (dialState[key] = buildDial(card));
    card.dataset.level = level || "";

    const target = value === null ? 0 : Math.max(0, Math.min(1, value / max)) * DIAL_TICKS;
    const paint = (v) => state.lines.forEach((l, i) => l.classList.toggle("on", i < Math.round(v)));

    cancelAnimationFrame(state.raf);

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        state.shown = target;
        paint(target);
        return;
    }

    const from = state.shown, began = performance.now(), duration = 1200;
    const step = (now) => {
        const t = Math.min(1, (now - began) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        state.shown = from + (target - from) * eased;
        paint(state.shown);
        if (t < 1) state.raf = requestAnimationFrame(step);
    };
    state.raf = requestAnimationFrame(step);
}

// --------------------------------------------------
// STAT CARDS
// --------------------------------------------------

function updateCards(data, system) {
    const cards = [
        { key: "cpu", column: "cpu_pct", warn: 70, crit: 90, meta: "Processor load" },
        { key: "memory", column: "mem_used_pct", warn: 75, crit: 90, meta: "RAM in use" },
        { key: "disk", column: "disk_used_pct", warn: 80, crit: 92, meta: "Root filesystem" },
        { key: "temperature", column: "cpu_temp_c", warn: 75, crit: 90, meta: "Hottest sensor" },
        { key: "gpu", column: "gpu_util_pct", warn: 90, crit: 101, meta: "Graphics load" },
        { key: "load", column: "load1", warn: Infinity, crit: Infinity, meta: "1-minute average" }
    ];

    cards.forEach((card) => {
        const values = series(data, card.column);
        const value = lastValue(values);
        const empty = value === null;

        animateNumber($(`${card.key}Value`), empty
            ? (card.key === "gpu" || card.key === "temperature" ? "n/a" : "--")
            : fmt(value, card.key === "load" ? 2 : 1));

        const dot = $(`${card.key}Level`);
        if (dot) dot.className = `level-dot ${levelFor(value, card.warn, card.crit)}`;
        setDial(card.key, value, levelFor(value, card.warn, card.crit), card.key === "temperature" ? 110 : 100);

        $(`${card.key}Meta`).textContent = empty
            ? (card.key === "gpu" ? "No GPU detected" :
                card.key === "temperature" ? "No sensor available" : "No data")
            : (card.key === "gpu" && system?.gpu?.name ? system.gpu.name : card.meta);

        sparkline(`${card.key}Spark`, values);
    });
}


// --------------------------------------------------
// ACTIVITY CHART (Real-Time Chart.js)
// --------------------------------------------------

let liveChart = null;

function themeVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}

const METRIC_CONFIG = {
    cpu_pct: {
        name: "CPU Usage",
        color: "#2f66f0",
        rgb: "47, 102, 240"
    },
    mem_used_pct: {
        name: "Memory Usage",
        color: "#22c55e",
        rgb: "34, 197, 94"
    },
    disk_used_pct: {
        name: "Disk Usage",
        color: "#f5a524",
        rgb: "245, 165, 36"
    }
};

function timeLabel(epoch, range) {
    const date = new Date(epoch * 1000);
    return range === "7d"
        ? date.toLocaleDateString([], { weekday: "short", day: "numeric" })
        : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function updateChart(data) {
    const canvas = $("performanceChartCanvas");
    const emptyEl = $("chartEmpty");
    const range = $("dashboardRange") ? $("dashboardRange").value : "1h";
    const conf = METRIC_CONFIG[activeMetric] || METRIC_CONFIG.cpu_pct;

    const values = series(data, activeMetric);
    const epochs = series(data, "epoch");

    const hasData = values.some((v) => v !== null && v !== undefined);

    if (!hasData) {
        if (emptyEl) {
            emptyEl.style.display = "grid";
            emptyEl.textContent = "No performance history yet. Start the collector to record data.";
        }
        if (liveChart) {
            liveChart.destroy();
            liveChart = null;
        }
        return;
    }

    if (emptyEl) emptyEl.style.display = "none";

    const labels = epochs.map((ep) => timeLabel(ep, range));

    if (typeof Chart === "undefined" || !canvas) {
        if (emptyEl) {
            emptyEl.style.display = "grid";
            emptyEl.textContent = "Chart.js unavailable";
        }
        return;
    }

    const ctx = canvas.getContext("2d");
    const gradient = ctx.createLinearGradient(0, 0, 0, 250);
    gradient.addColorStop(0, `rgba(${conf.rgb}, 0.35)`);
    gradient.addColorStop(1, `rgba(${conf.rgb}, 0.02)`);

    const tickColor = themeVar("--chart-tick", "#7d8497");
    const gridColor = themeVar("--chart-grid", "rgba(240, 241, 245, 0.08)");

    if (liveChart) {
        liveChart.data.labels = labels;
        liveChart.data.datasets[0].label = conf.name;
        liveChart.data.datasets[0].data = values;
        liveChart.data.datasets[0].borderColor = conf.color;
        liveChart.data.datasets[0].backgroundColor = gradient;
        liveChart.data.datasets[0].pointHoverBackgroundColor = conf.color;
        liveChart.options.scales.x.ticks.color = tickColor;
        liveChart.options.scales.y.ticks.color = tickColor;
        liveChart.options.scales.y.grid.color = gridColor;
        liveChart.update("none");
    } else {
        liveChart = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    label: conf.name,
                    data: values,
                    borderColor: conf.color,
                    backgroundColor: gradient,
                    borderWidth: 2.5,
                    fill: true,
                    tension: 0.35,
                    pointRadius: values.length > 50 ? 0 : 2,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: conf.color,
                    pointHoverBorderColor: "#fff",
                    pointHoverBorderWidth: 2,
                    spanGaps: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    intersect: false,
                    mode: "index"
                },
                animation: {
                    duration: 350
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: "#0f1218",
                        padding: 10,
                        cornerRadius: 10,
                        titleFont: { family: "Inter, sans-serif", size: 12 },
                        bodyFont: { family: "Inter, sans-serif", size: 12 },
                        boxPadding: 4,
                        callbacks: {
                            label: (context) => {
                                const val = context.parsed.y;
                                return ` ${conf.name}: ${val !== null ? val.toFixed(1) + "%" : "--"}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            maxTicksLimit: 7,
                            maxRotation: 0,
                            color: tickColor,
                            font: { family: "Inter, sans-serif", size: 11 }
                        },
                        border: { display: false }
                    },
                    y: {
                        beginAtZero: true,
                        max: 100,
                        grid: {
                            color: gridColor
                        },
                        border: { display: false },
                        ticks: {
                            color: tickColor,
                            font: { family: "Inter, sans-serif", size: 11 },
                            callback: (val) => `${val}%`,
                            stepSize: 25
                        }
                    }
                }
            }
        });
    }
}

$("metricSwitch").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-metric]");
    if (!button) return;

    activeMetric = button.dataset.metric;
    document.querySelectorAll("#metricSwitch button").forEach((b) =>
        b.classList.toggle("active", b === button));

    if (metricsData) updateChart(metricsData);
});


// --------------------------------------------------
// TOP PROCESSES (from /api/stats, normalized to total CPU capacity)
// --------------------------------------------------

function processRows(list, system = null) {
    if (!list.length) {
        return '<li class="empty-state"><strong>No process data yet</strong>Run the analysis to see the busiest processes.</li>';
    }

    const cores = Number(system?.cpu_cores) > 0 ? Number(system.cpu_cores) : 1;

    return list.map((p) => {
        const name = p.command ?? p.name ?? "unknown";
        const rawCpu = num(p.avg_cpu) ?? 0;
        // Normalize by cpu_cores so usage never crosses 100% and matches Linux Resources app
        const normalizedCpu = p.avg_cpu_normalized !== undefined && p.avg_cpu_normalized !== null
            ? Math.min(100, Math.max(0, num(p.avg_cpu_normalized) ?? 0))
            : Math.min(100, Math.max(0, rawCpu / cores));
        const mem = num(p.avg_mem);
        const tone = normalizedCpu >= 50 ? "hot" : normalizedCpu >= 20 ? "mid" : "";
        const titleText = `${normalizedCpu.toFixed(1)}% total CPU${cores > 1 ? ` (${rawCpu.toFixed(1)}% across ${cores} cores)` : ""}`;

        return `
            <li class="rank-row" title="${escapeHTML(titleText)}">
                <span class="rank-tile" aria-hidden="true">${escapeHTML(String(name).charAt(0))}</span>
                <div>
                    <span class="rank-name">${escapeHTML(name)}</span>
                    <div class="rank-track"><i class="${tone}" style="width:${Math.max(2, Math.min(100, normalizedCpu))}%"></i></div>
                </div>
                <span class="rank-value"><strong>${normalizedCpu.toFixed(1)}%</strong> CPU${mem === null ? "" : `<br>${mem.toFixed(1)}% mem`}</span>
            </li>`;
    }).join("");
}

function updateProcesses(stats, system = null) {
    const list = Array.isArray(stats?.top_processes) ? stats.top_processes.slice(0, 5) : [];
    $("topProcesses").innerHTML = processRows(list, system);
    playOnce($("topProcesses"), "first");
}


// --------------------------------------------------
// RECENT ALERTS (fed by common.js)
// --------------------------------------------------

document.addEventListener("sysmon:alerts", (event) => {
    const alerts = event.detail;
    const tbody = $("recentAlerts");

    if (!alerts.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty-cell">No alerts recorded</td></tr>';
        return;
    }

    tbody.innerHTML = alerts.slice(0, 5).map((a) => {
        const severity = a.severity === "critical" ? "critical" : a.severity === "warning" ? "warning" : "info";
        const resolved = a.state === "resolved";

        return `
            <tr>
                <td><span class="cell-title">${escapeHTML(ruleLabel(a.rule))}</span>
                    <span class="cell-sub">${escapeHTML(alertSummary(a))}${resolved ? " · resolved" : ""}</span></td>
                <td><span class="badge ${resolved ? "ok" : severity}">${resolved ? "Resolved" : severity.charAt(0).toUpperCase() + severity.slice(1)}</span></td>
                <td title="${escapeHTML(fullTime(a.epoch))}">${escapeHTML(relativeTime(a.epoch))}</td>
            </tr>`;
    }).join("");
});


// --------------------------------------------------
// HEALTH + COLLECTOR
// --------------------------------------------------

function updateHealth(status, system) {
    const collector = status?.collector || {};
    const pipeline = status?.pipeline || {};
    const running = collector.running === true;

    const sampleAge = collector.last_sample_epoch
        ? Date.now() / 1000 - collector.last_sample_epoch : null;
    const stale = running && sampleAge !== null && sampleAge > 90;

    collectorState.running = running;
    syncCollectorButtons();

    $("collectorStatus").textContent = running ? "Running" : "Stopped";
    $("healthCollector").textContent = running ? `Running (PID ${collector.pid ?? "?"})` : "Stopped";
    $("healthSample").textContent = collector.last_sample_epoch ? relativeTime(collector.last_sample_epoch) : "No samples yet";

    const dot = $("collectorDot");
    dot.classList.toggle("online", running);
    dot.classList.toggle("offline", !running);

    const tone = !running ? "#f5a524" : stale ? "#f5a524" : "#22c55e";
    const title = !running ? "Collector stopped" : stale ? "No fresh samples" : "Monitoring active";
    const hint = !running ? "Start the collector to record data"
        : stale ? "The collector is running but has not written a sample recently"
            : "Collecting samples normally";

    $("healthStatus").innerHTML = `
        <span class="health-status-dot" style="background:${tone};box-shadow:0 0 0 5px ${tone}26"></span>
        <div><strong>${title}</strong><small>${hint}</small></div>`;

    if (pipeline.last_run_epoch) {
        $("healthPipeline").textContent = pipeline.ok === false
            ? `Failed at ${pipeline.failed_stage ?? "a stage"}`
            : `OK · ${relativeTime(pipeline.last_run_epoch)}`;
    } else {
        $("healthPipeline").textContent = "Not run yet";
    }

    const sys = system || {};
    $("healthHost").textContent = sys.hostname || "—";
    $("healthCores").textContent = sys.cpu_model
        ? `${sys.cpu_model}${sys.cpu_cores ? ` · ${sys.cpu_cores} cores` : ""}`
        : (sys.cpu_cores ? `${sys.cpu_cores} cores` : "—");
    $("healthOS").textContent = sys.kernel ? `Linux ${sys.kernel}` : "—";
}


// --------------------------------------------------
// LOAD
// --------------------------------------------------

async function loadDashboard() {
    const range = $("dashboardRange").value;

    const results = await Promise.allSettled([
        apiRequest("/api/status"),
        apiRequest("/api/system"),
        apiRequest(`/api/metrics?range=${range}`),
        apiRequest("/api/stats")
    ]);

    const [status, system, metrics, stats] = results.map((r) => r.status === "fulfilled" ? r.value : null);

    if (results.every((r) => r.status === "rejected")) {
        setConnection(false);
        $("lastUpdated").textContent = "Backend unavailable";
        $("collectorStatus").textContent = "Unknown";
        $("healthStatus").innerHTML = `
            <span class="health-status-dot" style="background:#f0476b;box-shadow:0 0 0 5px #f0476b26"></span>
            <div><strong>Backend unavailable</strong><small>Start the Flask server, then refresh</small></div>`;
        if ($("chartEmpty")) {
            $("chartEmpty").style.display = "grid";
            $("chartEmpty").textContent = "Connect the backend to see activity.";
        }
        if (liveChart) {
            liveChart.destroy();
            liveChart = null;
        }
        $("performanceChart").innerHTML = '<div class="empty-block">Connect the backend to see activity.</div>';
        $("chartLabels").innerHTML = "";
        console.error("Dashboard refresh failed:", results[0].reason);
        return;
    }

    setConnection(true);
    $("lastUpdated").textContent = `Updated ${new Date().toLocaleTimeString()}`;

    if (status) updateHealth(status, system);

    if (metrics) {
        metricsData = metrics;
        updateCards(metrics, system);
        updateChart(metrics);
    }

    if (stats) updateProcesses(stats, system);

    refreshAlerts();
}


// --------------------------------------------------
// ACTIONS
// --------------------------------------------------

async function runAction(button, path, success) {
    await withCollectorLock(path, () => withBusy(button, async () => {
        if (button.dataset.busyLabel) button.textContent = button.dataset.busyLabel;
        try {
            await post(path);
            showToast(success, "success");
            await loadDashboard();
            await refreshAlerts();
        } catch (error) {
            console.error(error);
            showToast(error.message || "Action failed. Check the backend connection.", "error");
            return false;
        }
    }));
}

[
    ["startCollector", "/api/collector/start", "Collector started.", "Starting…"],
    ["stopCollector", "/api/collector/stop", "Collector stopped.", "Stopping…"],
    ["sendTestAlert", "/api/alerts/test", "Test alert sent.", null],
    ["refreshAnalysis", "/api/pipeline/run", "Analysis finished.", "Running…"]
].forEach(([id, path, message, busy]) => {
    const button = $(id);
    if (busy) button.dataset.busyLabel = busy;
    button.addEventListener("click", () => runAction(button, path, message));
});

$("headerRefresh").addEventListener("click", loadDashboard);
$("dashboardRange").addEventListener("change", loadDashboard);


// --------------------------------------------------
// AUTO REFRESH + INIT
// --------------------------------------------------

function configureAutoRefresh() {
    clearInterval(refreshTimer);
    const seconds = Number($("refreshInterval").value) || 10;
    refreshTimer = setInterval(loadDashboard, seconds * 1000);
}

$("refreshInterval").addEventListener("change", configureAutoRefresh);

loadDashboard();
configureAutoRefresh();
