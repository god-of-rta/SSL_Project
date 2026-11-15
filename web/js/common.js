/*
 * SysMon - shared frontend code
 * Loaded first on every page: API helper, sidebar, toasts,
 * connection status and the alert badge.
 */

// Use same-origin relative URLs when served by Flask; fall back for file:// previews.
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:5000" : "";

const $ = (id) => document.getElementById(id);


// --------------------------------------------------
// API
// --------------------------------------------------

async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });

    if (!response.ok) {
        // The contract returns {"ok": false, "error": "...", "field": "..."}
        let body = null;
        try { body = await response.json(); } catch (e) { /* not JSON */ }
        const error = new Error(body?.error || `API error ${response.status}`);
        error.field = body?.field;
        error.status = response.status;
        throw error;
    }

    if (response.status === 204) return null;

    const data = await response.json();
    if (data && data.ok === false) {
        const error = new Error(data.error || "API request failed");
        error.field = data.field;
        error.status = response.status;
        throw error;
    }

    return data;
}

const post = (path, body = {}) =>
    apiRequest(path, { method: "POST", body: JSON.stringify(body) })
        .then((result) => {
            if (path === "/api/alerts/test") playAlertRing();
            return result;
        });


// --------------------------------------------------
// FORMATTING
// --------------------------------------------------

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    })[c]);
}

function num(value) {
    if (value === undefined || value === null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}

function fmt(value, decimals = 1) {
    const n = num(value);
    return n === null ? "--" : n.toFixed(decimals);
}

function relativeTime(epoch) {
    const e = num(epoch);
    if (e === null) return "—";
    const diff = Math.max(0, Math.round(Date.now() / 1000 - e));
    if (diff < 5) return "just now";
    if (diff < 60) return `${diff} s ago`;
    if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
    return new Date(e * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
}

function fullTime(epoch) {
    const e = num(epoch);
    return e === null ? "" : new Date(e * 1000).toLocaleString();
}

const METRIC_LABELS = {
    cpu_pct: "CPU",
    mem_used_pct: "Memory",
    swap_used_pct: "Swap",
    disk_used_pct: "Disk",
    load1: "Load (1 min)",
    cpu_temp_c: "CPU temperature",
    gpu_util_pct: "GPU utilization",
    gpu_mem_used_pct: "GPU memory",
    gpu_temp_c: "GPU temperature"
};

function metricUnit(metric) {
    if (!metric || metric === "load1") return "";
    return metric.endsWith("_c") ? "°C" : "%";
}

function ruleLabel(rule) {
    const words = { cpu: "CPU", gpu: "GPU", mem: "memory", temp: "temperature" };
    const text = String(rule || "alert").split("_")
        .map((w) => words[w] || w).join(" ");
    return text.charAt(0).toUpperCase() + text.slice(1);
}

function alertSummary(alert) {
    if (alert.rule === "no_data") return "No data from the collector";
    const unit = metricUnit(alert.metric);
    const value = num(alert.value);
    const limit = num(alert.threshold);
    if (value === null) return ruleLabel(alert.rule);
    return `${fmt(value)}${unit} (limit ${limit === null ? "—" : limit}${unit})`;
}


// --------------------------------------------------
// ICONS + SIDEBAR
// --------------------------------------------------

const ICONS = {
    pulse: '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
    dashboard: '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
    trends: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    processes: '<rect x="5" y="5" width="14" height="14" rx="3"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    alerts: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2.2"/><circle cx="9" cy="17" r="2.2"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.5-5.8L20 8"/><path d="M20 3v5h-5"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
};

function icon(name, size = 20) {
    return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

const NAV_LINKS = [
    { page: "dashboard", href: "index.html", label: "Dashboard" },
    { page: "trends", href: "trends.html", label: "Trends" },
    { page: "processes", href: "processes.html", label: "Processes" },
    { page: "alerts", href: "alerts.html", label: "Alerts", badge: true },
    { page: "settings", href: "settings.html", label: "Settings" }
];

// Quick actions available from every page (floating rail).
const RAIL_ACTIONS = [
    { icon: "bolt", label: "Run analysis", path: "/api/pipeline/run", ok: "Analysis finished." },
    { icon: "play", label: "Start collector", path: "/api/collector/start", ok: "Collector started." },
    { icon: "stop", label: "Stop collector", path: "/api/collector/stop", ok: "Collector stopped." },
    { icon: "alerts", label: "Send test alert", path: "/api/alerts/test", ok: "Test alert sent." }
];

function renderSidebar() {
    const mount = $("sidebar");
    if (!mount) return;

    const current = document.body.dataset.page;

    const links = NAV_LINKS.map((item) => `
        <a href="${item.href}" class="pill-link${item.page === current ? " active" : ""}"
           ${item.page === current ? 'aria-current="page"' : ""}>
            ${item.label}
            ${item.badge ? '<span class="nav-count" id="alertBadge" hidden>0</span>' : ""}
        </a>`).join("");

    const actions = RAIL_ACTIONS.map((a) => `
        <button type="button" class="rail-btn" data-path="${a.path}" data-ok="${a.ok}"
                data-tip="${a.label}" aria-label="${a.label}">${icon(a.icon, 19)}</button>`).join("");

    mount.innerHTML = `
        <header class="topnav">
            <a href="index.html" class="topnav-brand" aria-label="SysMon home">
                <span class="brand-mark">${icon("pulse", 20)}</span>
                <span class="topnav-name">SysMon</span>
            </a>
            <nav class="pillnav" aria-label="Main">${links}</nav>
            <div class="topnav-status" title="Backend connection">
                <span class="status-indicator" id="sidebarDot"></span>
                <span id="sidebarStatus">Disconnected</span>
                <small id="sidebarSubstatus">Waiting for backend</small>
            </div>
        </header>
        <nav class="rail" aria-label="Quick actions">
            ${actions}
            <span class="rail-sep" aria-hidden="true"></span>
            <button type="button" class="rail-btn theme-btn" id="themeToggle" data-tip="Switch theme"
                    aria-label="Switch between light and dark theme">
                <span class="i-moon">${icon("moon", 19)}</span><span class="i-sun">${icon("sun", 19)}</span>
            </button>
            <a href="settings.html" class="rail-btn${current === "settings" ? " active" : ""}"
               data-tip="Settings" aria-label="Settings">${icon("settings", 19)}</a>
        </nav>`;

    $("themeToggle").addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem("sysmon-theme", next); } catch (e) { /* storage unavailable */ }
        // charts read their colours when drawn, so redraw them
        if (typeof window.loadTrends === "function") window.loadTrends();
    });

    initNavIndicator();

    mount.querySelectorAll(".rail-btn[data-path]").forEach((button) => {
        button.addEventListener("click", () => withCollectorLock(button.dataset.path, () => withBusy(button, async () => {
            try {
                await post(button.dataset.path);
                showToast(button.dataset.ok, "success");
                refreshAfterAction();
            } catch (error) {
                console.error(error);
                showToast(error.message || "Action failed. Check the backend connection.", "error");
                return false;
            }
        })));
    });
}

// --------------------------------------------------
// LIQUID GLASS HIGHLIGHT (light follows the cursor across glass cards)
// --------------------------------------------------

function initGlassLight() {
    if (!window.matchMedia("(hover: hover)").matches) return;

    let frame = 0;
    document.addEventListener("pointermove", (event) => {
        const card = event.target.closest && event.target.closest(".panel, .stat-card");
        if (!card || frame) return;

        frame = requestAnimationFrame(() => {
            frame = 0;
            const box = card.getBoundingClientRect();
            card.style.setProperty("--mx", `${event.clientX - box.left}px`);
            card.style.setProperty("--my", `${event.clientY - box.top}px`);
        });
    }, { passive: true });
}

// --------------------------------------------------
// MOTION HELPERS
// --------------------------------------------------

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

// Counts a number up/down to its new value instead of jumping.
function animateNumber(el, text) {
    if (!el) return;

    const target = parseFloat(text);
    if (REDUCED_MOTION.matches || Number.isNaN(target) || !/^-?\d/.test(String(text))) {
        cancelAnimationFrame(el._raf);
        el._n = Number.isNaN(target) ? undefined : target;
        el.textContent = text;
        return;
    }

    const decimals = (String(text).split(".")[1] || "").length;
    const from = el._n ?? 0;
    const began = performance.now();
    const duration = 900;

    cancelAnimationFrame(el._raf);
    const step = (now) => {
        const t = Math.min(1, (now - began) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = (from + (target - from) * eased).toFixed(decimals);
        if (t < 1) el._raf = requestAnimationFrame(step);
    };
    el._n = target;
    el._raf = requestAnimationFrame(step);
}

// Plays an entrance animation the first time (or when the signature changes),
// so lists redrawn by auto-refresh do not replay it every few seconds.
function playOnce(el, signature) {
    if (!el || el.dataset.played === signature) return;
    el.dataset.played = signature;
    el.classList.add("grow");
    clearTimeout(el._growTimer);
    el._growTimer = setTimeout(() => el.classList.remove("grow"), 2200);
}

// Sliding highlight behind the active nav pill; it glides from the
// previous page's position so page changes feel continuous.
function initNavIndicator() {
    const nav = document.querySelector(".pillnav");
    const active = nav && nav.querySelector(".pill-link.active");
    if (!active) return;

    const bar = document.createElement("span");
    bar.className = "pill-indicator";
    nav.prepend(bar);

    const place = (left, width) => {
        bar.style.transform = `translateX(${left}px)`;
        bar.style.width = `${width}px`;
    };

    const left = active.offsetLeft, width = active.offsetWidth;
    let previous = null;
    try { previous = JSON.parse(sessionStorage.getItem("sysmon-nav")); } catch (error) { /* ignore */ }

    if (previous && previous.w && !REDUCED_MOTION.matches) {
        bar.style.transition = "none";
        place(previous.l, previous.w);
        bar.getBoundingClientRect();
        bar.style.transition = "";
        requestAnimationFrame(() => place(left, width));
    } else {
        place(left, width);
    }

    const remember = () => {
        try {
            sessionStorage.setItem("sysmon-nav", JSON.stringify({ l: active.offsetLeft, w: active.offsetWidth }));
        } catch (error) { /* ignore */ }
    };
    remember();

    // The alert badge appears after load and the window can be resized,
    // so keep the indicator locked onto the active link.
    if ("ResizeObserver" in window) {
        const follow = () => { place(active.offsetLeft, active.offsetWidth); remember(); };
        const observer = new ResizeObserver(follow);
        observer.observe(nav);
        observer.observe(active);
    }
}

// Material-style ripple on buttons.
function initRipple() {
    document.addEventListener("pointerdown", (event) => {
        const button = event.target.closest && event.target.closest(".btn");
        if (!button || button.disabled || REDUCED_MOTION.matches) return;

        const box = button.getBoundingClientRect();
        const size = Math.max(box.width, box.height) * 2;
        const ripple = document.createElement("span");
        ripple.className = "ripple";
        ripple.style.width = ripple.style.height = `${size}px`;
        ripple.style.left = `${event.clientX - box.left - size / 2}px`;
        ripple.style.top = `${event.clientY - box.top - size / 2}px`;
        button.appendChild(ripple);
        ripple.addEventListener("animationend", () => ripple.remove());
    });
}

// --------------------------------------------------
// COLLECTOR BUTTON LOCKING
// Start/Stop are disabled while a request is running, and the one that
// does not apply to the current state stays disabled.
// --------------------------------------------------

const collectorState = { running: null, busy: false };

function syncCollectorButtons() {
    const { running, busy } = collectorState;
    const lock = (selector, disabled) => document.querySelectorAll(selector).forEach((button) => {
        button.disabled = disabled;
        if (button.tagName === "BUTTON" && button.classList.contains("btn")) {
            button.title = disabled && !busy
                ? (selector.includes("start") ? "The collector is already running" : "The collector is not running")
                : "";
        }
    });

    lock('#startCollector, .rail-btn[data-path="/api/collector/start"]', busy || running === true);
    lock('#stopCollector, .rail-btn[data-path="/api/collector/stop"]', busy || running === false);
}

// Wraps a collector action so both buttons stay locked until it finishes.
async function withCollectorLock(path, task) {
    const isCollector = path.startsWith("/api/collector/");
    if (isCollector) {
        collectorState.busy = true;
        syncCollectorButtons();
    }

    try {
        const result = await task();
        if (isCollector && result !== false) collectorState.running = path.endsWith("/start");
        return result;
    } finally {
        if (isCollector) collectorState.busy = false;
        syncCollectorButtons();
    }
}

// --------------------------------------------------
// TEST-ALERT RING (bell tone synthesised in the browser, no audio file)
// --------------------------------------------------

let audioCtx = null;

function audioContext() {
    if (!audioCtx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        audioCtx = new Ctx();
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
}

// Browsers only allow sound after a user gesture, so prepare on first touch.
["pointerdown", "keydown"].forEach((name) =>
    document.addEventListener(name, audioContext, { once: true, passive: true }));

function bellStrike(ctx, out, start) {
    const base = 880;
    // inharmonic partials give a bell-like timbre
    [[1, 1], [2.01, .55], [2.76, .38], [5.4, .2], [8.93, .09]].forEach(([ratio, level]) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        const decay = 1.5 / Math.sqrt(ratio);

        osc.type = "sine";
        osc.frequency.value = base * ratio;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(level, start + 0.006);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + decay);

        osc.connect(gain).connect(out);
        osc.start(start);
        osc.stop(start + decay + 0.05);
    });
}

function playAlertRing() {
    // wiggle the bell icons while it rings
    document.querySelectorAll('.rail-btn[data-path="/api/alerts/test"], #sendTestAlert').forEach((button) => {
        button.classList.add("ringing");
        setTimeout(() => button.classList.remove("ringing"), 1700);
    });

    const ctx = audioContext();
    if (!ctx) return;

    const master = ctx.createGain();
    master.gain.value = 0.3;
    master.connect(ctx.destination);

    const now = ctx.currentTime + 0.02;
    [0, 0.5, 1.0].forEach((offset) => bellStrike(ctx, master, now + offset));
}

// Re-fetch whatever the current page shows after a quick action.
function refreshAfterAction() {
    refreshAlerts();
    ["loadDashboard", "loadProcesses", "loadTrends"].forEach((name) => {
        if (typeof window[name] === "function") window[name]();
    });
}


// --------------------------------------------------
// TOASTS
// --------------------------------------------------

function showToast(message, type = "info") {
    let container = $("toastContainer");

    if (!container) {
        container = document.createElement("div");
        container.id = "toastContainer";
        container.className = "toast-container";
        container.setAttribute("aria-live", "polite");
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}


// --------------------------------------------------
// CONNECTION STATUS
// --------------------------------------------------

let isOnline = false;

function setConnection(online) {
    isOnline = online;

    $("sidebarDot")?.classList.toggle("online", online);
    $("sidebarDot")?.classList.toggle("offline", !online);

    if ($("sidebarStatus")) $("sidebarStatus").textContent = online ? "Connected" : "Disconnected";
    if ($("sidebarSubstatus")) {
        $("sidebarSubstatus").textContent = online ? "Backend responding" : "Waiting for backend";
    }
}


// --------------------------------------------------
// ALERTS: badge, toast on new alert, page event
// --------------------------------------------------

let knownAlertIds = null;
let latestAlerts = [];

async function refreshAlerts() {
    try {
        const data = await apiRequest("/api/alerts");
        const alerts = Array.isArray(data?.alerts) ? data.alerts : [];
        latestAlerts = alerts;
        setConnection(true);

        const unread = alerts.filter((a) => a.state === "firing" && !a.acknowledged).length;
        const badge = $("alertBadge");
        if (badge) {
            badge.textContent = unread > 99 ? "99+" : unread;
            badge.hidden = unread === 0;
        }

        const ids = new Set(alerts.map((a) => a.id));

        if (knownAlertIds) {
            alerts
                .filter((a) => !knownAlertIds.has(a.id) && a.state === "firing")
                .slice(0, 3)
                .forEach((a) => showToast(
                    `${ruleLabel(a.rule)}: ${alertSummary(a)}`,
                    a.severity === "critical" ? "error" : "info"
                ));
        }

        knownAlertIds = ids;
        document.dispatchEvent(new CustomEvent("sysmon:alerts", { detail: alerts }));
        return alerts;
    } catch (error) {
        setConnection(false);
        console.error("Alert refresh failed:", error);
        document.dispatchEvent(new CustomEvent("sysmon:alerts-error"));
        return null;
    }
}


// --------------------------------------------------
// BUTTON HELPER: disable while a request runs
// --------------------------------------------------

async function withBusy(button, task) {
    if (!button) return task();
    const label = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
        return await task();
    } finally {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.innerHTML = label;
    }
}


// Scripts sit at the end of <body>, so the sidebar mount already exists.
renderSidebar();
initGlassLight();
initRipple();
refreshAlerts();
setInterval(refreshAlerts, 15000);
