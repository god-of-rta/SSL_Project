/*
 * SysMon - Processes page
 * Source: GET /api/stats -> top_processes [{command, avg_cpu, avg_mem}]
 * Normalized with GET /api/system -> cpu_cores (matches Linux Resources app)
 */

function rankList(list, key, label, cores = 1) {
    if (!list.length) {
        return '<li class="empty-state"><strong>No process data yet</strong>Run the analysis from the dashboard.</li>';
    }

    const values = list.map((p) => num(p[key]) ?? 0);
    const scale = key === "avg_cpu" ? 100 : Math.max(25, ...values);

    return list.map((p, i) => {
        const value = values[i];
        const name = p.command ?? p.name ?? "unknown";
        const tone = value / scale >= .5 ? "hot" : value / scale >= .2 ? "mid" : "";
        const title = (key === "avg_cpu" && cores > 1 && p.raw_cpu !== undefined)
            ? `${value.toFixed(1)}% total CPU (${p.raw_cpu.toFixed(1)}% across ${cores} cores)`
            : "";

        return `
            <li class="rank-row" title="${escapeHTML(title)}">
                <span class="rank-tile" aria-hidden="true">${escapeHTML(String(name).charAt(0))}</span>
                <div>
                    <span class="rank-name">${escapeHTML(name)}</span>
                    <div class="rank-track"><i class="${tone}" style="width:${Math.max(2, Math.min(100, value / scale * 100))}%"></i></div>
                </div>
                <span class="rank-value"><strong>${value.toFixed(1)}%</strong> ${label}</span>
            </li>`;
    }).join("");
}

let cachedProcesses = [];
let currentLimit = 10;
let detectedCores = 1;

function renderTableRows() {
    const tableBody = $("processTable");
    const countMeta = $("processCountMeta");
    const showMoreBtn = $("showMoreProcesses");
    const select = $("processLimit");

    if (!cachedProcesses.length) {
        tableBody.innerHTML = '<tr><td colspan="3" class="empty-cell">No process data available</td></tr>';
        if (showMoreBtn) {
            showMoreBtn.disabled = true;
            showMoreBtn.hidden = true;
            showMoreBtn.textContent = "All processes shown";
        }
        if (countMeta) countMeta.textContent = "From the latest analysis";
        return;
    }

    const limitVal = select ? select.value : "10";
    let effectiveLimit = limitVal === "all" ? cachedProcesses.length : Number(limitVal) || 10;
    effectiveLimit = Math.max(effectiveLimit, currentLimit);

    const visible = cachedProcesses.slice(0, effectiveLimit);

    tableBody.innerHTML = visible.map((p) => {
        const title = detectedCores > 1 ? `Raw CPU: ${fmt(p.raw_cpu)}% across ${detectedCores} cores` : "";
        return `
        <tr title="${escapeHTML(title)}">
            <td class="cell-title">${escapeHTML(p.command ?? p.name ?? "unknown")}</td>
            <td class="num">${fmt(p.avg_cpu)}%</td>
            <td class="num">${fmt(p.avg_mem)}%</td>
        </tr>`;
    }).join("");

    if (countMeta) {
        countMeta.textContent = `Showing ${visible.length} of ${cachedProcesses.length} tracked processes`;
    }

    if (showMoreBtn) {
        showMoreBtn.hidden = false;
        if (visible.length < cachedProcesses.length) {
            const remaining = cachedProcesses.length - visible.length;
            const nextBatch = Math.min(10, remaining);
            showMoreBtn.disabled = false;
            showMoreBtn.textContent = `Show more (+${nextBatch})`;
            showMoreBtn.title = `Click to show ${nextBatch} more processes`;
        } else {
            showMoreBtn.disabled = true;
            showMoreBtn.textContent = "All processes shown";
            showMoreBtn.title = "All tracked processes are displayed";
        }
    }
}

async function loadProcesses() {
    const error = $("processError");

    try {
        const [stats, system] = await Promise.all([
            apiRequest("/api/stats"),
            apiRequest("/api/system").catch(() => null)
        ]);
        setConnection(true);
        error.hidden = true;

        detectedCores = Number(system?.cpu_cores) > 0 ? Number(system.cpu_cores) : 1;
        const rawList = Array.isArray(stats?.top_processes) ? stats.top_processes : [];
        const list = rawList.map((p) => {
            const rawCpu = num(p.avg_cpu) ?? 0;
            const normCpu = p.avg_cpu_normalized !== undefined && p.avg_cpu_normalized !== null
                ? Math.min(100, Math.max(0, num(p.avg_cpu_normalized) ?? 0))
                : Math.min(100, Math.max(0, rawCpu / detectedCores));
            return {
                ...p,
                raw_cpu: rawCpu,
                avg_cpu: normCpu
            };
        });

        const byCpu = [...list].sort((a, b) => (num(b.avg_cpu) ?? 0) - (num(a.avg_cpu) ?? 0));
        const byMem = [...list].sort((a, b) => (num(b.avg_mem) ?? 0) - (num(a.avg_mem) ?? 0));

        cachedProcesses = byCpu;

        $("cpuRanks").innerHTML = rankList(byCpu.slice(0, 6), "avg_cpu", "CPU", detectedCores);
        $("memRanks").innerHTML = rankList(byMem.slice(0, 6), "avg_mem", "mem", detectedCores);

        renderTableRows();

        $("processUpdated").textContent = stats?.generated_at
            ? `Analysis from ${new Date(stats.generated_at).toLocaleTimeString()}${detectedCores > 1 ? ` (${detectedCores} cores)` : ""}`
            : "No analysis yet";

    } catch (e) {
        console.error("Process load failed:", e);
        setConnection(false);
        error.textContent = "Could not reach the backend. Start the Flask server and refresh.";
        error.hidden = false;
    }
}

$("refreshProcesses").addEventListener("click", loadProcesses);

const selectEl = $("processLimit");
if (selectEl) {
    selectEl.addEventListener("change", (e) => {
        currentLimit = e.target.value === "all" ? cachedProcesses.length : Number(e.target.value) || 10;
        renderTableRows();
    });
}

const showMoreBtn = $("showMoreProcesses");
if (showMoreBtn) {
    showMoreBtn.addEventListener("click", () => {
        if (showMoreBtn.disabled || currentLimit >= cachedProcesses.length) {
            showMoreBtn.disabled = true;
            showMoreBtn.textContent = "All processes shown";
            return;
        }
        currentLimit += 10;
        renderTableRows();
    });
}

loadProcesses();
setInterval(loadProcesses, 15000);
