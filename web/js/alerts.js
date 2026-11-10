/*
 * SysMon - Alerts page
 * GET /api/alerts, POST /api/alerts/ack, POST /api/alerts/test
 * common.js polls the alerts, updates the badge and raises toasts,
 * then fires "sysmon:alerts" which this page renders.
 */

let alertList = [];

function filtered() {
    const state = $("stateFilter").value;
    const severity = $("severityFilter").value;

    return alertList.filter((a) =>
        (state === "all" || a.state === state) &&
        (severity === "all" || a.severity === severity));
}

function renderSummary() {
    // Newest first: the first row seen for a rule is that rule's current state.
    const latest = new Map();
    alertList.forEach((a) => { if (!latest.has(a.rule)) latest.set(a.rule, a); });

    const firing = [...latest.values()].filter((a) => a.state === "firing").length;
    const unack = alertList.filter((a) => a.state === "firing" && !a.acknowledged).length;

    $("firingCount").textContent = firing;
    $("unackCount").textContent = unack;
    $("totalCount").textContent = alertList.length;
    $("firingLevel").className = `level-dot ${firing ? "crit" : "ok"}`;
    $("ackAll").disabled = unack === 0;
}

function renderTable() {
    const rows = filtered();
    const tbody = $("alertTable");

    $("historyCount").textContent = alertList.length
        ? `Showing ${Math.min(rows.length, 200)} of ${alertList.length} · newest first`
        : "Newest first";

    if (!alertList.length) {
        tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">
            <strong>No alerts yet</strong>Alerts appear here when a metric crosses a threshold.</div></td></tr>`;
        return;
    }

    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">No alerts match these filters</td></tr>';
        return;
    }

    tbody.innerHTML = rows.slice(0, 200).map((a) => {
        const resolved = a.state === "resolved";
        const severity = a.severity === "critical" ? "critical" : "warning";

        const action = a.state !== "firing"
            ? ""
            : a.acknowledged
                ? `<span class="acked">${icon("check", 16)} Acknowledged</span>`
                : `<button class="btn btn-quiet btn-sm" data-ack="${a.id}" type="button">Acknowledge</button>`;

        return `
            <tr class="alert-row${resolved ? " is-resolved" : ""}">
                <td><span class="cell-title">${escapeHTML(ruleLabel(a.rule))}</span>
                    <span class="cell-sub">${escapeHTML(alertSummary(a))}</span></td>
                <td><span class="badge ${severity}">${severity === "critical" ? "Critical" : "Warning"}</span></td>
                <td><span class="badge ${resolved ? "ok" : "neutral"}">${resolved ? "Resolved" : "Firing"}</span></td>
                <td>${a.process ? escapeHTML(a.process) : '<span class="cell-sub">—</span>'}</td>
                <td title="${escapeHTML(fullTime(a.epoch))}">${escapeHTML(relativeTime(a.epoch))}</td>
                <td class="row-actions">${action}</td>
            </tr>`;
    }).join("");
}

function render(alerts) {
    alertList = alerts;
    renderSummary();
    renderTable();
}

document.addEventListener("sysmon:alerts", (event) => render(event.detail));

document.addEventListener("sysmon:alerts-error", () => {
    if (alertList.length) return;
    $("alertTable").innerHTML = `<tr><td colspan="6"><div class="empty-state">
        <strong>Can't reach the backend</strong>Start the Flask server, then refresh this page.</div></td></tr>`;
});

$("stateFilter").addEventListener("change", renderTable);
$("severityFilter").addEventListener("change", renderTable);

// Acknowledge one alert (event delegation on the table)
$("alertTable").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-ack]");
    if (!button) return;

    await withBusy(button, async () => {
        try {
            await post("/api/alerts/ack", { ids: [Number(button.dataset.ack)] });
            await refreshAlerts();
        } catch (error) {
            showToast(error.message, "error");
        }
    });
});

$("ackAll").addEventListener("click", async () => {
    await withBusy($("ackAll"), async () => {
        try {
            await post("/api/alerts/ack", { all: true });
            showToast("All alerts acknowledged.", "success");
            await refreshAlerts();
        } catch (error) {
            showToast(error.message, "error");
        }
    });
});

$("sendTestAlert").addEventListener("click", async () => {
    await withBusy($("sendTestAlert"), async () => {
        try {
            await post("/api/alerts/test");
            showToast("Test alert sent.", "success");
            setTimeout(refreshAlerts, 1200);
        } catch (error) {
            showToast(error.message, "error");
        }
    });
});

// common.js may have fetched before this listener was attached
if (latestAlerts.length) render(latestAlerts);
