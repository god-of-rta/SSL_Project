
/*
 * SysMon - Dashboard frontend
 * P3: Frontend
 *
 * The frontend communicates with the Flask API.
 * It does not collect system metrics itself.
 */

// Change this if your Flask backend uses another port.
const API_BASE = "http://127.0.0.1:5000";

const $ = (id) => document.getElementById(id);

let refreshTimer = null;
let refreshSeconds = 10;
let previousAlertIds = new Set();

const state = {
    online: false,
    status: null,
    system: null,
    metrics: null,
    alerts: [],
    processes: []
};


// --------------------------------------------------
// API HELPERS
// --------------------------------------------------

async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
    }

    if (response.status === 204) return null;

    return response.json();
}

function firstValue(object, keys) {
    if (!object || typeof object !== "object") return undefined;

    for (const key of keys) {
        if (object[key] !== undefined && object[key] !== null) {
            return object[key];
        }
    }

    return undefined;
}

function findValue(object, keys) {
    if (!object || typeof object !== "object") return undefined;

    const direct = firstValue(object, keys);
    if (direct !== undefined) return direct;

    for (const value of Object.values(object)) {
        if (value && typeof value === "object") {
            const result = findValue(value, keys);
            if (result !== undefined) return result;
        }
    }

    return undefined;
}

function numericValue(value) {
    if (value === undefined || value === null || value === "") {
        return null;
    }

    const result = Number(value);
    return Number.isFinite(result) ? result : null;
}

function formatMetric(value, decimals = 1) {
    const number = numericValue(value);
    return number === null ? "--" : number.toFixed(decimals);
}

function setText(id, value) {
    const element = $(id);
    if (element) element.textContent = value;
}

function setProgress(id, value) {
    const element = $(id);
    if (!element) return;

    const number = numericValue(value);

    element.style.width = number === null
        ? "0%"
        : `${Math.min(100, Math.max(0, number))}%`;
}


// --------------------------------------------------
// NOTIFICATIONS
// --------------------------------------------------

function showToast(message, type = "info") {
    const container = $("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => toast.remove(), 3500);
}


// --------------------------------------------------
// CONNECTION STATUS
// --------------------------------------------------

function updateConnection(isOnline) {
    state.online = isOnline;

    const pill = $("connectionPill");
    const dot = pill?.querySelector(".status-indicator");

    if (dot) {
        dot.classList.toggle("online", isOnline);
        dot.classList.toggle("offline", !isOnline);
    }

    setText("connectionText", isOnline ? "Connected" : "Offline");
    setText("sidebarStatus", isOnline ? "Connected" : "Disconnected");
    setText(
        "sidebarSubstatus",
        isOnline ? "Backend responding" : "Waiting for backend"
    );

    const sidebarDot = $("sidebarDot");
    const collectorDot = $("collectorDot");

    [sidebarDot, collectorDot].forEach((element) => {
        if (!element) return;
        element.classList.toggle("online", isOnline);
        element.classList.toggle("offline", !isOnline);
    });
}


// --------------------------------------------------
// RESOURCE METRICS
// --------------------------------------------------

function updateMetrics(data) {
    if (!data) return;

    const cpu = numericValue(findValue(data, [
        "cpu_percent", "cpu_usage", "cpu", "cpu_pct"
    ]));

    const memory = numericValue(findValue(data, [
        "memory_percent", "memory_usage", "ram_percent",
        "ram_usage", "memory", "mem_percent"
    ]));

    const disk = numericValue(findValue(data, [
        "disk_percent", "disk_usage", "disk", "storage_percent"
    ]));

    const temperature = numericValue(findValue(data, [
        "cpu_temperature", "cpu_temp", "temperature", "temp_c"
    ]));

    setText("cpuValue", formatMetric(cpu));
    setText("memoryValue", formatMetric(memory));
    setText("diskValue", formatMetric(disk));
    setText("temperatureValue", formatMetric(temperature));

    setProgress("cpuBar", cpu);
    setProgress("memoryBar", memory);
    setProgress("diskBar", disk);

    // Temperature progress uses a 100°C reference scale.
    setProgress(
        "temperatureBar",
        temperature === null ? null : temperature
    );

    setText("cpuMeta", cpu === null ? "Unavailable" : "Current reading");
    setText("memoryMeta", memory === null ? "Unavailable" : "Current reading");
    setText("diskMeta", disk === null ? "Unavailable" : "Current reading");
    setText(
        "temperatureMeta",
        temperature === null ? "Unavailable" : "Current reading"
    );

    setText("lastUpdated", `Last updated ${new Date().toLocaleTimeString()}`);
}


// --------------------------------------------------
// SYSTEM INFORMATION
// --------------------------------------------------

function updateSystemInfo(system, status) {
    const combined = {
        ...(system || {}),
        ...(status || {})
    };

    const os = findValue(combined, [
        "os", "operating_system", "platform", "system"
    ]);

    const hostname = findValue(combined, [
        "hostname", "host_name", "node_name"
    ]);

    const cores = findValue(combined, [
        "cpu_cores", "cores", "logical_cpus", "cpu_count"
    ]);

    const sample = findValue(combined, [
        "last_sample", "last_sample_time", "timestamp", "sample_time"
    ]);

    setText("healthOS", os ?? "Unavailable");
    setText("healthCores", cores ?? "—");
    setText("healthSample", sample ?? "—");

    setText("healthCollector", "Check status");
    setText("collectorStatus", "Status unavailable");

    setText("osInfo", os ?? "Unavailable");
    setText("hostnameInfo", hostname ?? "Unavailable");
    setText("coresInfo", cores ?? "—");
    setText("sampleInfo", sample ?? "—");
}


// --------------------------------------------------
// COLLECTOR STATUS
// --------------------------------------------------

function updateCollectorStatus(status) {
    if (!status) return;

    const running = findValue(status, [
        "collector_running", "running", "collector_active", "is_running"
    ]);

    const isRunning =
        running === true ||
        running === 1 ||
        running === "true";

    const label = isRunning ? "Running" : "Stopped";

    setText("collectorStatus", label);
    setText("healthCollector", label);

    const dot = $("collectorDot");
    if (dot) {
        dot.classList.toggle("online", isRunning);
        dot.classList.toggle("offline", !isRunning);
    }

    const health = $("healthStatus");

    if (health) {
        health.innerHTML = `
            <span class="health-status-dot"
                  style="background:${isRunning ? "#22c983" : "#e7a03c"}">
            </span>
            <div>
                <strong>${isRunning ? "Monitoring active" : "Collector stopped"}</strong>
                <small>${isRunning ? "Collector is running" : "Start the collector to monitor"}</small>
            </div>
        `;
    }
}


// --------------------------------------------------
// ALERTS
// --------------------------------------------------

function normalizeArray(data, keys) {
    if (Array.isArray(data)) return data;

    for (const key of keys) {
        if (Array.isArray(data?.[key])) return data[key];
    }

    return [];
}

function updateAlerts(data) {
    const alerts = normalizeArray(data, ["alerts", "items", "results"]);
    state.alerts = alerts;

    const tbody = $("recentAlerts");
    if (!tbody) return;

    const activeAlerts = alerts.filter(alert =>
        !alert.acknowledged && !alert.ack
    );

    setText("alertBadge", activeAlerts.length);

    const newIds = new Set(
        alerts.map((alert, index) =>
            String(alert.id ?? alert.alert_id ?? index)
        )
    );

    for (const id of newIds) {
        if (previousAlertIds.size && !previousAlertIds.has(id)) {
            showToast("A new system alert has been received.", "info");
        }
    }

    previousAlertIds = newIds;

    if (!alerts.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="3" class="empty-cell">No alerts recorded</td>
            </tr>`;
        return;
    }

    tbody.innerHTML = alerts.slice(0, 5).map(alert => {
        const title = alert.message ?? alert.title ?? alert.name ?? "System alert";
        const severity = String(alert.severity ?? alert.level ?? "info").toLowerCase();
        const time = alert.timestamp ?? alert.time ?? alert.created_at ?? "—";

        const safeTitle = escapeHTML(title);
        const safeTime = escapeHTML(time);
        const safeSeverity = ["critical", "warning", "info"].includes(severity)
            ? severity
            : "info";

        return `
            <tr>
                <td>${safeTitle}</td>
                <td><span class="severity ${safeSeverity}">${safeSeverity.toUpperCase()}</span></td>
                <td>${safeTime}</td>
            </tr>`;
    }).join("");
}


// --------------------------------------------------
// PROCESSES
// --------------------------------------------------

function updateProcesses(data) {
    const processes = normalizeArray(data, [
        "processes", "items", "results", "top_processes"
    ]);

    state.processes = processes;

    const tbody = $("topProcesses");
    if (!tbody) return;

    if (!processes.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="3" class="empty-cell">No process data available</td>
            </tr>`;
        return;
    }

    tbody.innerHTML = processes.slice(0, 5).map(process => {
        const name = process.name ?? process.process_name ?? process.command ?? "Unknown";
        const cpu = process.cpu_percent ?? process.cpu ?? process.cpu_usage;
        const memory = process.memory_percent ?? process.memory ?? process.mem_percent;

        return `
            <tr>
                <td>${escapeHTML(name)}</td>
                <td>${formatMetric(cpu)}%</td>
                <td>${formatMetric(memory)}%</td>
            </tr>`;
    }).join("");
}


// --------------------------------------------------
// SAFE HTML OUTPUT
// --------------------------------------------------

function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[character]);
}


// --------------------------------------------------
// PERFORMANCE CHART
// --------------------------------------------------

function updateChart(data) {
    const chart = $("performanceChart");
    const labelsElement = $("chartLabels");

    if (!chart || !labelsElement) return;

    const points = normalizeArray(data, [
        "metrics", "samples", "data", "history", "items"
    ]);

    if (!points.length) {
        chart.innerHTML = '<div class="chart-empty">No performance history available</div>';
        labelsElement.innerHTML = "<span>No data</span>";
        return;
    }

    const cpuValues = points.map(point =>
        numericValue(findValue(point, ["cpu_percent", "cpu_usage", "cpu"]))
    );

    const memoryValues = points.map(point =>
        numericValue(findValue(point, ["memory_percent", "memory_usage", "memory"]))
    );

    const diskValues = points.map(point =>
        numericValue(findValue(point, ["disk_percent", "disk_usage", "disk"]))
    );

    const validValues = [...cpuValues, ...memoryValues, ...diskValues]
        .filter(value => value !== null);

    if (!validValues.length) {
        chart.innerHTML = '<div class="chart-empty">Metric fields not found in API response</div>';
        labelsElement.innerHTML = "<span>Unavailable</span>";
        return;
    }

    const width = 600;
    const height = 164;

    function makePath(values) {
        const usable = values
            .map((value, index) => ({ value, index }))
            .filter(item => item.value !== null);

        if (usable.length < 2) return "";

        return usable.map((item, index) => {
            const x = item.index / Math.max(1, points.length - 1) * width;
            const y = height - (Math.max(0, Math.min(100, item.value)) / 100 * height);

            return `${index === 0 ? "M" : "L"} ${x} ${y}`;
        }).join(" ");
    }

    const cpuPath = makePath(cpuValues);
    const memoryPath = makePath(memoryValues);
    const diskPath = makePath(diskValues);

    chart.innerHTML = `
        <svg class="chart-svg" viewBox="0 0 ${width} ${height}"
             preserveAspectRatio="none"
             role="img" aria-label="System resource usage chart">
            <defs>
                <linearGradient id="cpuGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#5c83ff" stop-opacity=".18"/>
                    <stop offset="100%" stop-color="#5c83ff" stop-opacity="0"/>
                </linearGradient>
            </defs>

            ${cpuPath ? `<path d="${cpuPath} L ${width} ${height} L 0 ${height} Z" fill="url(#cpuGradient)"/>` : ""}
            ${cpuPath ? `<path d="${cpuPath}" fill="none" stroke="#5c83ff" stroke-width="2.5" vector-effect="non-scaling-stroke"/>` : ""}
            ${memoryPath ? `<path d="${memoryPath}" fill="none" stroke="#32cda2" stroke-width="2.5" vector-effect="non-scaling-stroke"/>` : ""}
            ${diskPath ? `<path d="${diskPath}" fill="none" stroke="#f4b24a" stroke-width="2.5" vector-effect="non-scaling-stroke"/>` : ""}
        </svg>
    `;

    const labelCount = Math.min(6, points.length);
    const labels = [];

    for (let i = 0; i < labelCount; i++) {
        const index = Math.round(
            i * (points.length - 1) / Math.max(1, labelCount - 1)
        );

        const label = points[index].timestamp ??
            points[index].time ??
            points[index].date ??
            `Sample ${index + 1}`;

        labels.push(label);
    }

    labelsElement.innerHTML = labels
        .map(label => `<span>${escapeHTML(label)}</span>`)
        .join("");
}


// --------------------------------------------------
// LOAD DASHBOARD DATA
// --------------------------------------------------

async function loadDashboard() {
    try {
        const [status, system, alerts, metrics] = await Promise.all([
            apiRequest("/api/status"),
            apiRequest("/api/system"),
            apiRequest("/api/alerts"),
            apiRequest(`/api/metrics?range=${$("dashboardRange")?.value || "24h"}`)
        ]);

        state.status = status;
        state.system = system;
        state.metrics = metrics;

        updateConnection(true);
        updateCollectorStatus(status);
        updateSystemInfo(system, status);
        updateMetrics(system);
        updateAlerts(alerts);
        updateChart(metrics);

        // Process data is not assumed to be part of /api/system.
        // Add a dedicated process endpoint when the backend contract provides one.

    } catch (error) {
        updateConnection(false);
        setText("lastUpdated", "Backend unavailable");
        showBackendUnavailable();
        console.error("Dashboard refresh failed:", error);
    }
}

function showBackendUnavailable() {
    setText("collectorStatus", "Backend unavailable");
    setText("healthCollector", "Unavailable");
    setText("healthSample", "—");
    setText("healthCores", "—");
    setText("healthOS", "—");

    setText("cpuValue", "--");
    setText("memoryValue", "--");
    setText("diskValue", "--");
    setText("temperatureValue", "--");

    ["cpuBar", "memoryBar", "diskBar", "temperatureBar"]
        .forEach(id => setProgress(id, null));

    $("performanceChart").innerHTML =
        '<div class="chart-empty">Connect the backend to display performance data</div>';
}


// --------------------------------------------------
// API ACTIONS
// --------------------------------------------------

async function performAction(path, method = "POST", body = {}) {
    try {
        await apiRequest(path, {
            method,
            body: JSON.stringify(body)
        });

        showToast("Action completed successfully.", "success");
        await loadDashboard();

    } catch (error) {
        console.error(error);
        showToast("Action failed. Check the backend connection.", "error");
    }
}

$("startCollector").addEventListener("click", () =>
    performAction("/api/collector/start")
);

$("stopCollector").addEventListener("click", () =>
    performAction("/api/collector/stop")
);

$("runPipeline").addEventListener("click", () =>
    performAction("/api/pipeline/run")
);

$("sendTestAlert").addEventListener("click", () =>
    performAction("/api/alerts/test")
);

$("refreshAnalysis").addEventListener("click", () =>
    performAction("/api/pipeline/run")
);

$("headerRefresh").addEventListener("click", loadDashboard);


// --------------------------------------------------
// FILTERS AND AUTO REFRESH
// --------------------------------------------------

$("dashboardRange").addEventListener("change", loadDashboard);

$("chartRange").addEventListener("change", async () => {
    try {
        const data = await apiRequest(
            `/api/metrics?range=${$("chartRange").value}`
        );
        updateChart(data);
    } catch (error) {
        showToast("Unable to load chart data.", "error");
    }
});

function configureAutoRefresh() {
    clearInterval(refreshTimer);

    refreshSeconds = Number($("refreshInterval").value) || 10;

    refreshTimer = setInterval(loadDashboard, refreshSeconds * 1000);
}

$("refreshInterval").addEventListener("change", configureAutoRefresh);


// --------------------------------------------------
// INITIALIZATION
// --------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    loadDashboard();
    configureAutoRefresh();
});
