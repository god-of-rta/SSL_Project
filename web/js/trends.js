
const API_BASE = "http://127.0.0.1:5000";

let selectedRange = "24h";
let resourceChart = null;
let temperatureChart = null;
let networkChart = null;

async function getAPI(path) {
    const response = await fetch(`${API_BASE}${path}`);

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }

    return response.json();
}

function getColumn(data, name) {
    return data.columns?.indexOf(name) ?? -1;
}

function getSeries(data, columnName) {
    const index = getColumn(data, columnName);

    if (index < 0) return [];

    return (data.rows || []).map(row => {
        const value = row[index];
        return value === null || value === undefined
            ? null
            : Number(value);
    });
}

function getLabels(data) {
    const index = getColumn(data, "epoch");

    if (index < 0) return [];

    return (data.rows || []).map(row => {
        const date = new Date(Number(row[index]) * 1000);

        return selectedRange === "7d"
            ? date.toLocaleDateString()
            : date.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit"
            });
    });
}

function average(values) {
    const valid = values.filter(
        value => value !== null && Number.isFinite(value)
    );

    if (!valid.length) return null;

    return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}

function displayAverage(id, values, suffix = "%") {
    const value = average(values);

    document.getElementById(id).textContent =
        value === null ? "n/a" : `${value.toFixed(1)}${suffix}`;
}

function showEmpty(id, show) {
    document.getElementById(id).style.display =
        show ? "grid" : "none";
}


// --------------------------------------------------
// CHART CREATION
// --------------------------------------------------

function makeChart(canvasId, oldChart, labels, datasets, emptyId) {
    const canvas = document.getElementById(canvasId);

    if (oldChart) oldChart.destroy();

    const hasData = datasets.some(dataset =>
        dataset.data.some(value => value !== null)
    );

    showEmpty(emptyId, !hasData);

    if (!hasData) return null;

    if (typeof Chart === "undefined") {
        showEmpty(emptyId, true);
        document.getElementById(emptyId).textContent =
            "Chart.js is not installed locally yet.";
        return null;
    }

    return new Chart(canvas, {
        type: "line",
        data: {
            labels,
            datasets: datasets.map(dataset => ({
                ...dataset,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.3,
                spanGaps: false
            }))
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: "index"
            },
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        maxTicksLimit: 8,
                        color: "#8c98aa",
                        font: {
                            size: 9
                        }
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: {
                        color: "#edf0f5"
                    },
                    ticks: {
                        color: "#8c98aa",
                        font: {
                            size: 9
                        }
                    }
                }
            }
        }
    });
}


// --------------------------------------------------
// RESOURCE CHART
// --------------------------------------------------

function renderResourceChart(data) {
    const labels = getLabels(data);

    const cpu = getSeries(data, "cpu_pct");
    const memory = getSeries(data, "mem_used_pct");
    const disk = getSeries(data, "disk_used_pct");

    displayAverage("cpuAverage", cpu);
    displayAverage("memoryAverage", memory);
    displayAverage("diskAverage", disk);

    const gpu = getSeries(data, "gpu_util_pct");
    displayAverage("gpuAverage", gpu);

    resourceChart = makeChart(
        "resourceChart",
        resourceChart,
        labels,
        [
            {
                label: "CPU",
                data: cpu,
                borderColor: "#5c83ff",
                backgroundColor: "#5c83ff"
            },
            {
                label: "Memory",
                data: memory,
                borderColor: "#32cda2",
                backgroundColor: "#32cda2"
            },
            {
                label: "Disk",
                data: disk,
                borderColor: "#f4b24a",
                backgroundColor: "#f4b24a"
            }
        ],
        "resourceEmpty"
    );
}


// --------------------------------------------------
// TEMPERATURE CHART
// --------------------------------------------------

function renderTemperatureChart(data) {
    const labels = getLabels(data);

    temperatureChart = makeChart(
        "temperatureChart",
        temperatureChart,
        labels,
        [
            {
                label: "CPU Temperature",
                data: getSeries(data, "cpu_temp_c"),
                borderColor: "#ed7785",
                backgroundColor: "#ed7785"
            },
            {
                label: "GPU Temperature",
                data: getSeries(data, "gpu_temp_c"),
                borderColor: "#9275ed",
                backgroundColor: "#9275ed"
            }
        ],
        "temperatureEmpty"
    );
}


// --------------------------------------------------
// NETWORK CHART
// --------------------------------------------------

function renderNetworkChart(data) {
    const labels = getLabels(data);

    networkChart = makeChart(
        "networkChart",
        networkChart,
        labels,
        [
            {
                label: "Receive",
                data: getSeries(data, "net_rx_kbps"),
                borderColor: "#4387f4",
                backgroundColor: "#4387f4"
            },
            {
                label: "Transmit",
                data: getSeries(data, "net_tx_kbps"),
                borderColor: "#32cda2",
                backgroundColor: "#32cda2"
            }
        ],
        "networkEmpty"
    );
}


// --------------------------------------------------
// STATISTICS TABLE
// --------------------------------------------------

function renderStatistics(stats) {
    const body = document.getElementById("statisticsBody");
    const windows = stats.windows || {};
    const selected = windows[selectedRange];

    document.getElementById("statisticsRange").textContent =
        selectedRange === "1h" ? "Last 1 hour" :
            selectedRange === "24h" ? "Last 24 hours" : "Last 7 days";

    if (!selected) {
        body.innerHTML = `
            <tr>
                <td colspan="6" class="empty-cell">
                    No statistics available for this period.
                </td>
            </tr>`;
        return;
    }

    const metrics = [
        ["CPU", "cpu_pct"],
        ["Memory", "mem_used_pct"],
        ["Swap", "swap_used_pct"],
        ["Disk", "disk_used_pct"],
        ["CPU Temperature", "cpu_temp_c"],
        ["GPU Utilization", "gpu_util_pct"],
        ["GPU Temperature", "gpu_temp_c"]
    ];

    body.innerHTML = metrics.map(([label, key]) => {
        const values = selected[key];

        if (!values || typeof values !== "object") return "";

        const format = value =>
            value === null || value === undefined
                ? "n/a"
                : Number(value).toFixed(2);

        return `
            <tr>
                <td>${label}</td>
                <td>${format(values.mean)}</td>
                <td>${format(values.min)}</td>
                <td>${format(values.max)}</td>
                <td>${format(values.p95)}</td>
                <td>${format(values.std)}</td>
            </tr>`;
    }).filter(Boolean).join("") || `
        <tr>
            <td colspan="6" class="empty-cell">
                No statistics available.
            </td>
        </tr>`;
}


// --------------------------------------------------
// LOAD DATA
// --------------------------------------------------

async function loadTrends() {
    try {
        const [metrics, stats] = await Promise.all([
            getAPI(`/api/metrics?range=${selectedRange}`),
            getAPI("/api/stats")
        ]);

        renderResourceChart(metrics);
        renderTemperatureChart(metrics);
        renderNetworkChart(metrics);
        renderStatistics(stats);

        const dot = document.getElementById("connectionDot");
        dot.classList.add("online");
        dot.classList.remove("offline");

        document.getElementById("connectionText").textContent =
            "Connected";

    } catch (error) {
        console.error("Trends loading error:", error);

        document.getElementById("connectionText").textContent =
            "Offline";

        ["resourceEmpty", "temperatureEmpty", "networkEmpty"]
            .forEach(id => showEmpty(id, true));
    }
}


// --------------------------------------------------
// RANGE FILTERS
// --------------------------------------------------

document.querySelectorAll(".range-button").forEach(button => {
    button.addEventListener("click", () => {
        document.querySelectorAll(".range-button").forEach(item =>
            item.classList.remove("active")
        );

        button.classList.add("active");
        selectedRange = button.dataset.range;

        loadTrends();
    });
});

document.getElementById("refreshButton")
    .addEventListener("click", loadTrends);


// --------------------------------------------------
// INITIALIZE
// --------------------------------------------------

loadTrends();
