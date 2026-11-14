
// API_BASE, apiRequest, setConnection and showToast come from common.js

let selectedRange = "24h";
let resourceChart = null;
let temperatureChart = null;
let networkChart = null;

const getAPI = (path) => apiRequest(path);

// Chart axis colours follow the active light/dark theme.
function themeVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
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

function makeChart(canvasId, oldChart, labels, datasets, emptyId, options = {}) {
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
                borderWidth: 2.5,
                pointRadius: 0,
                pointHoverRadius: 5,
                pointHoverBackgroundColor: "#fff",
                pointHoverBorderWidth: 2,
                borderCapStyle: "round",
                tension: 0.35,
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
                },
                tooltip: {
                    backgroundColor: "#0f1218",
                    padding: 12,
                    cornerRadius: 12,
                    titleFont: { family: "Inter, sans-serif", size: 13 },
                    bodyFont: { family: "Inter, sans-serif", size: 13 },
                    boxPadding: 5,
                    usePointStyle: true
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
                        color: themeVar("--chart-tick", "#7d8497"),
                        font: {
                            family: "Inter, sans-serif",
                            size: 12
                        }
                    },
                    border: {
                        display: false
                    }
                },
                y: {
                    beginAtZero: true,
                    ...(options.max ? { max: options.max } : {}),
                    grid: {
                        color: themeVar("--chart-grid", "#f0f1f5")
                    },
                    border: {
                        display: false
                    },
                    ticks: {
                        color: themeVar("--chart-tick", "#7d8497"),
                        font: {
                            family: "Inter, sans-serif",
                            size: 12
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
                borderColor: "#2f66f0",
                backgroundColor: "#2f66f0"
            },
            {
                label: "Memory",
                data: memory,
                borderColor: "#22c55e",
                backgroundColor: "#22c55e"
            },
            {
                label: "Disk",
                data: disk,
                borderColor: "#f5a524",
                backgroundColor: "#f5a524"
            }
        ],
        "resourceEmpty",
        { max: 100 }
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
                borderColor: "#f0476b",
                backgroundColor: "#f0476b"
            },
            {
                label: "GPU Temperature",
                data: getSeries(data, "gpu_temp_c"),
                borderColor: "#8b6cf0",
                backgroundColor: "#8b6cf0"
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
                borderColor: "#2f66f0",
                backgroundColor: "#2f66f0"
            },
            {
                label: "Transmit",
                data: getSeries(data, "net_tx_kbps"),
                borderColor: "#22c55e",
                backgroundColor: "#22c55e"
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
    const selected = windows[selectedRange] || (selectedRange === "7d" ? windows.all : null);

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
                <td class="cell-title">${label}</td>
                <td class="num">${format(values.mean)}</td>
                <td class="num">${format(values.min)}</td>
                <td class="num">${format(values.max)}</td>
                <td class="num">${format(values.p95)}</td>
                <td class="num">${format(values.std)}</td>
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

        setConnection(true);

    } catch (error) {
        console.error("Trends loading error:", error);

        setConnection(false);

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
