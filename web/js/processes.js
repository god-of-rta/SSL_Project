/*
 * SysMon - Processes page
 * Source: GET /api/stats -> top_processes [{command, avg_cpu, avg_mem}]
 */

function rankList(list, key, label) {
    if (!list.length) {
        return '<li class="empty-state"><strong>No process data yet</strong>Run the analysis from the dashboard.</li>';
    }

    const values = list.map((p) => num(p[key]) ?? 0);
    const scale = Math.max(key === "avg_cpu" ? 100 : 25, ...values);

    return list.map((p, i) => {
        const value = values[i];
        const name = p.command ?? p.name ?? "unknown";
        const tone = value / scale >= .5 ? "hot" : value / scale >= .2 ? "mid" : "";

        return `
            <li class="rank-row">
                <span class="rank-tile" aria-hidden="true">${escapeHTML(String(name).charAt(0))}</span>
                <div>
                    <span class="rank-name">${escapeHTML(name)}</span>
                    <div class="rank-track"><i class="${tone}" style="width:${Math.max(2, value / scale * 100)}%"></i></div>
                </div>
                <span class="rank-value"><strong>${value.toFixed(1)}%</strong> ${label}</span>
            </li>`;
    }).join("");
}

async function loadProcesses() {
    const error = $("processError");

    try {
        const stats = await apiRequest("/api/stats");
        setConnection(true);
        error.hidden = true;

        const list = Array.isArray(stats?.top_processes) ? stats.top_processes : [];

        const byCpu = [...list].sort((a, b) => (num(b.avg_cpu) ?? 0) - (num(a.avg_cpu) ?? 0));
        const byMem = [...list].sort((a, b) => (num(b.avg_mem) ?? 0) - (num(a.avg_mem) ?? 0));

        $("cpuRanks").innerHTML = rankList(byCpu.slice(0, 6), "avg_cpu", "CPU");
        $("memRanks").innerHTML = rankList(byMem.slice(0, 6), "avg_mem", "mem");

        $("processTable").innerHTML = byCpu.length
            ? byCpu.map((p) => `
                <tr>
                    <td class="cell-title">${escapeHTML(p.command ?? p.name ?? "unknown")}</td>
                    <td class="num">${fmt(p.avg_cpu)}%</td>
                    <td class="num">${fmt(p.avg_mem)}%</td>
                </tr>`).join("")
            : '<tr><td colspan="3" class="empty-cell">No process data available</td></tr>';

        $("processUpdated").textContent = stats?.generated_at
            ? `Analysis from ${new Date(stats.generated_at).toLocaleTimeString()}`
            : "No analysis yet";

    } catch (e) {
        console.error("Process load failed:", e);
        setConnection(false);
        error.textContent = "Could not reach the backend. Start the Flask server and refresh.";
        error.hidden = false;
    }
}

$("refreshProcesses").addEventListener("click", loadProcesses);

loadProcesses();
setInterval(loadProcesses, 15000);
