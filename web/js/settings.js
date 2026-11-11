/*
 * SysMon - Settings page
 * GET /api/config, POST /api/config, POST /api/config/profile
 * Validation errors come back as {"ok": false, "error": "...", "field": "..."}.
 */

const METRICS = Object.keys(METRIC_LABELS);
const SYSMON_FIELDS = ["interval_s", "pipeline_interval_s", "gpu_poll_every"];
const SYSMON_TOGGLES = ["collect_gpu", "simulate_temp"];

let loadedConfig = null;     // last config from the server
let removedRules = new Set(); // rules deleted in the UI, sent as null
let dirty = false;


// --------------------------------------------------
// RENDER
// --------------------------------------------------

function options(list, selected, labelFor = (x) => x) {
    return list.map((value) =>
        `<option value="${escapeHTML(value)}"${value === selected ? " selected" : ""}>${escapeHTML(labelFor(value))}</option>`
    ).join("");
}

function ruleRow(name, rule, isNew = false) {
    const key = escapeHTML(name);
    const field = (part) => `thresholds.${name}.${part}`;

    // The no_data rule has no metric: it checks the age of the newest sample.
    if (name === "no_data") {
        return `
            <tr data-rule="no_data">
                <td><label class="switch"><input type="checkbox" data-part="enabled" data-field="${field("enabled")}" ${rule.enabled ? "checked" : ""} aria-label="Enable no_data rule"><span class="track"></span></label></td>
                <td class="cell-title">No data from collector</td>
                <td colspan="2"><span class="cell-sub">Fires when the newest sample is older than</span></td>
                <td><input class="input" type="number" min="1" data-part="max_age_s" data-field="${field("max_age_s")}" value="${rule.max_age_s ?? 60}" aria-label="Maximum sample age in seconds"></td>
                <td><span class="cell-sub">seconds</span></td>
                <td></td>
                <td><select class="select" data-part="severity" data-field="${field("severity")}" aria-label="Severity">${options(["warning", "critical"], rule.severity || "critical", (s) => s[0].toUpperCase() + s.slice(1))}</select></td>
                <td></td>
            </tr>`;
    }

    return `
        <tr data-rule="${key}"${isNew ? ' data-new="1"' : ""}>
            <td><label class="switch"><input type="checkbox" data-part="enabled" data-field="${field("enabled")}" ${rule.enabled !== false ? "checked" : ""} aria-label="Enable ${key}"><span class="track"></span></label></td>
            <td>${isNew
            ? `<input class="input rule-name" type="text" placeholder="e.g. cpu_very_high" data-part="name" aria-label="Rule name">`
            : `<span class="cell-title">${escapeHTML(ruleLabel(name))}</span><span class="cell-sub">${key}</span>`}</td>
            <td><select class="select metric" data-part="metric" data-field="${field("metric")}" aria-label="Metric">${options(METRICS, rule.metric, (m) => METRIC_LABELS[m])}</select></td>
            <td><select class="select" data-part="op" data-field="${field("op")}" aria-label="Condition">${options([">", ">=", "<", "<="], rule.op || ">")}</select></td>
            <td><input class="input" type="number" step="any" data-part="threshold" data-field="${field("threshold")}" value="${rule.threshold ?? ""}" aria-label="Limit"></td>
            <td><input class="input" type="number" min="1" data-part="sustained" data-field="${field("sustained")}" value="${rule.sustained ?? 1}" aria-label="Samples in a row"></td>
            <td><input class="input" type="number" min="0" data-part="cooldown_s" data-field="${field("cooldown_s")}" value="${rule.cooldown_s ?? 600}" aria-label="Cooldown in seconds"></td>
            <td><select class="select" data-part="severity" data-field="${field("severity")}" aria-label="Severity">${options(["warning", "critical"], rule.severity || "warning", (s) => s[0].toUpperCase() + s.slice(1))}</select></td>
            <td class="row-actions"><button class="btn btn-ghost btn-sm" data-remove type="button" title="Remove rule" aria-label="Remove ${key}">${icon("trash", 18)}</button></td>
        </tr>`;
}

function render(config) {
    loadedConfig = config;
    removedRules = new Set();

    const sysmon = config.sysmon || {};
    SYSMON_FIELDS.forEach((id) => { $(id).value = sysmon[id] ?? ""; });
    SYSMON_TOGGLES.forEach((id) => { $(id).checked = sysmon[id] === true; });

    const rules = config.thresholds || {};
    const names = Object.keys(rules).sort((a, b) =>
        (a === "no_data") - (b === "no_data") || a.localeCompare(b));

    $("rulesBody").innerHTML = names.length
        ? names.map((n) => ruleRow(n, rules[n])).join("")
        : '<tr><td colspan="9" class="empty-cell">No rules yet. Add one to get alerts.</td></tr>';

    setDirty(false);
    clearErrors();
}


// --------------------------------------------------
// DIRTY STATE + ERRORS
// --------------------------------------------------

function setDirty(value) {
    dirty = value;
    $("saveSettings").disabled = !value;
    $("discardChanges").disabled = !value;
    $("saveStatus").textContent = value ? "You have unsaved changes" : "No changes";
}

function clearErrors() {
    $("settingsError").hidden = true;
    document.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
}

function showError(error) {
    clearErrors();
    const box = $("settingsError");
    box.textContent = error.message;
    box.hidden = false;

    if (error.field) {
        const target = document.querySelector(`[data-field="${CSS.escape(error.field)}"]`);
        if (target) {
            target.classList.add("invalid");
            target.focus();
        }
    }

    box.scrollIntoView({ behavior: "smooth", block: "center" });
}

document.querySelector("main").addEventListener("input", (event) => {
    if (event.target.matches("input, select")) setDirty(true);
});
document.querySelector("main").addEventListener("change", (event) => {
    if (event.target.matches("input, select")) setDirty(true);
});


// --------------------------------------------------
// COLLECT + SAVE
// --------------------------------------------------

function collect() {
    const sysmon = {};
    SYSMON_FIELDS.forEach((id) => { if ($(id).value !== "") sysmon[id] = Number($(id).value); });
    SYSMON_TOGGLES.forEach((id) => { sysmon[id] = $(id).checked; });

    const thresholds = {};

    document.querySelectorAll("#rulesBody tr[data-rule]").forEach((row) => {
        const get = (part) => row.querySelector(`[data-part="${part}"]`);
        const name = row.dataset.new ? get("name").value.trim() : row.dataset.rule;
        if (!name) return;

        if (name === "no_data") {
            thresholds.no_data = {
                max_age_s: Number(get("max_age_s").value),
                severity: get("severity").value,
                enabled: get("enabled").checked
            };
            return;
        }

        thresholds[name] = {
            metric: get("metric").value,
            op: get("op").value,
            threshold: get("threshold").value === "" ? null : Number(get("threshold").value),
            sustained: Number(get("sustained").value),
            cooldown_s: Number(get("cooldown_s").value),
            severity: get("severity").value,
            enabled: get("enabled").checked
        };
    });

    removedRules.forEach((name) => { if (!(name in thresholds)) thresholds[name] = null; });

    return { sysmon, thresholds };
}

$("saveSettings").addEventListener("click", async () => {
    clearErrors();

    // Every newly added rule needs a name before the server can store it.
    const unnamed = [...document.querySelectorAll('tr[data-new] [data-part="name"]')]
        .find((input) => input.value.trim() === "");

    if (unnamed) {
        showError(Object.assign(new Error("Give the new rule a name, for example cpu_very_high."), {}));
        unnamed.classList.add("invalid");
        unnamed.focus();
        return;
    }

    await withBusy($("saveSettings"), async () => {
        try {
            await post("/api/config", collect());
            showToast("Settings saved.", "success");
            await loadConfig();
        } catch (error) {
            showError(error);
        }
    });
});

$("discardChanges").addEventListener("click", () => {
    if (loadedConfig) render(loadedConfig);
});


// --------------------------------------------------
// ADD / REMOVE RULES
// --------------------------------------------------

$("addRule").addEventListener("click", () => {
    const body = $("rulesBody");
    if (body.querySelector(".empty-cell")) body.innerHTML = "";

    body.insertAdjacentHTML("afterbegin", ruleRow("new_rule", {
        metric: "cpu_pct", op: ">", threshold: 80, sustained: 3,
        cooldown_s: 600, severity: "warning", enabled: true
    }, true));

    body.querySelector('[data-part="name"]').focus();
    setDirty(true);
});

$("rulesBody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;

    const row = button.closest("tr");
    if (!row.dataset.new) removedRules.add(row.dataset.rule);
    row.remove();
    setDirty(true);
});


// --------------------------------------------------
// PROFILES
// --------------------------------------------------

async function applyProfile(button, profile, message) {
    await withBusy(button, async () => {
        try {
            await post("/api/config/profile", { profile });
            showToast(message, "success");
            await loadConfig();
        } catch (error) {
            showError(error);
        }
    });
}

$("applyDemo").addEventListener("click", () =>
    applyProfile($("applyDemo"), "demo", "Demo profile applied. Alerts will fire quickly under load."));

$("resetDefaults").addEventListener("click", () => {
    if (!confirm("Reset all alert rules to their defaults? Your custom rules will be lost.")) return;
    applyProfile($("resetDefaults"), "default", "Alert rules reset to defaults.");
});


// --------------------------------------------------
// LOAD
// --------------------------------------------------

async function loadConfig() {
    try {
        render(await apiRequest("/api/config"));
        setConnection(true);
    } catch (error) {
        console.error("Config load failed:", error);
        setConnection(false);
        $("rulesBody").innerHTML =
            '<tr><td colspan="9"><div class="empty-state"><strong>Can\'t reach the backend</strong>Start the Flask server, then refresh this page.</div></td></tr>';
        $("saveStatus").textContent = "Settings unavailable";
    }
}

loadConfig();
