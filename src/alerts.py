#!/usr/bin/env python3
import argparse
import configparser
import csv
import json
import operator
import os
import shutil
import smtplib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "data")

METRICS = {
    "cpu_pct", "mem_used_pct", "swap_used_pct", "disk_used_pct", "load1",
    "cpu_temp_c", "gpu_util_pct", "gpu_mem_used_pct", "gpu_temp_c",
}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
SEVERITIES = {"warning", "critical"}
ALERT_HEADER = ["id", "epoch", "iso_time", "rule", "metric", "value", "threshold",
                "severity", "state", "process", "acknowledged"]
NOTIFY_MAX_AGE_S = 300  # do not pop up notifications for events older than this


# --------------------------------------------------------------------------- helpers

def iso(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_num(x):
    """12.0 -> '12', 12.345 -> '12.3' (compact numbers for files and messages)."""
    x = round(float(x), 1)
    return str(int(x)) if x == int(x) else str(x)


def atomic_write(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp_", suffix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def log_line(log_path, msg):
    line = "[%s] %s\n" % (iso(time.time()), msg)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        print("alerts: cannot write log: %s" % e, file=sys.stderr)


def to_float(s):
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- config

def load_rules(path, log_path):
    """Return (metric_rules, no_data_rule). Invalid rules are logged and skipped."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    if not os.path.exists(path):
        log_line(log_path, "thresholds file %s not found, no rules loaded" % path)
        return {}, None
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as e:
        raise SystemExit("alerts: cannot parse %s: %s" % (path, e))

    rules, no_data = {}, None
    for name in cp.sections():
        sec = cp[name]
        try:
            enabled = sec.get("enabled", "true").strip().lower() == "true"
            severity = sec.get("severity", "warning").strip().lower()
            if severity not in SEVERITIES:
                raise ValueError("severity must be warning or critical")
            if name == "no_data":
                no_data = {"name": name, "max_age_s": float(sec.get("max_age_s", "60")),
                           "severity": severity, "enabled": enabled}
                continue
            metric = sec.get("metric", "").strip()
            op = sec.get("op", ">").strip()
            if metric not in METRICS:
                raise ValueError("unknown metric %r" % metric)
            if op not in OPS:
                raise ValueError("unknown operator %r" % op)
            sustained = int(float(sec.get("sustained", "1")))
            cooldown = int(float(sec.get("cooldown_s", "0")))
            if sustained < 1 or cooldown < 0:
                raise ValueError("sustained must be >= 1 and cooldown_s >= 0")
            rules[name] = {"name": name, "metric": metric, "op": op,
                           "threshold": float(sec["threshold"]), "sustained": sustained,
                           "cooldown_s": cooldown, "severity": severity, "enabled": enabled}
        except (ValueError, KeyError) as e:
            log_line(log_path, "skipping invalid rule [%s]: %s" % (name, e))
    return rules, no_data


# --------------------------------------------------------------------------- state

def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            st.setdefault("last_epoch", 0)
            st.setdefault("rules", {})
            return st
    except (OSError, ValueError):
        pass
    return {"last_epoch": 0, "rules": {}}


def save_state(path, state):
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def rule_state(state, name):
    return state["rules"].setdefault(name, {"state": "ok", "consecutive": 0, "last_fired_epoch": None})


# --------------------------------------------------------------------------- reading data

def read_new_rows(path, after_epoch):
    """Return (rows with epoch > after_epoch in file order, newest epoch in file).

    When we already processed up to `after_epoch`, only the tail of the file is read,
    so a run stays fast on a big history file.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return [], None
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        header = f.readline().decode("utf-8", "replace").strip().split(",")
        body_start = f.tell()
        chunk = 256 * 1024
        data = b""
        while True:
            start = max(body_start, size - chunk)
            f.seek(start)
            data = f.read()
            if start == body_start:
                break
            # keep reading further back until the first full line in the tail is old enough
            first_nl = data.find(b"\n")
            first_line = data[first_nl + 1:].split(b"\n", 1)[0].decode("utf-8", "replace")
            try:
                first_epoch = int(first_line.split(",", 1)[0])
            except ValueError:
                first_epoch = None
            if after_epoch and first_epoch is not None and first_epoch <= after_epoch:
                data = data[first_nl + 1:]
                break
            if not after_epoch:
                chunk = size  # first run: read everything
            else:
                chunk *= 4
    rows, newest = [], None
    for raw in data.decode("utf-8", "replace").splitlines():
        parts = raw.strip().split(",")
        if len(parts) < len(header) or not parts[0].strip():
            continue  # malformed or partially written line
        try:
            epoch = int(float(parts[0]))
        except ValueError:
            continue
        row = dict(zip(header, parts))
        row["epoch"] = epoch
        if newest is None or epoch > newest:
            newest = epoch
        if epoch > after_epoch:
            rows.append(row)
    rows.sort(key=lambda r: r["epoch"])
    return rows, newest


def top_processes(processes_dir, epochs):
    """Map epoch -> name of the top process (rank 1) at that sample, where known."""
    result = {}
    by_day = {}
    for e in epochs:
        by_day.setdefault(datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y%m%d"), set()).add(e)
    for day, wanted in by_day.items():
        path = os.path.join(processes_dir, "processes_%s.csv" % day)
        if not os.path.exists(path):
            continue
        best = {}  # epoch -> (rank, command)
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        ep, rank = int(float(r["epoch"])), int(float(r.get("rank") or 99))
                    except (ValueError, KeyError, TypeError):
                        continue
                    if ep in wanted and (ep not in best or rank < best[ep][0]):
                        best[ep] = (rank, r.get("command", ""))
        except OSError:
            continue
        for ep, (_, cmd) in best.items():
            result[ep] = cmd
    return result


# --------------------------------------------------------------------------- evaluation

def evaluate(rows, rules, state):
    """Run the state machine over `rows`; return a list of events (dicts)."""
    events = []
    for row in rows:
        if row.get("is_gap") == "1":
            for rs in state["rules"].values():
                rs["consecutive"] = 0  # a long gap breaks continuity
        if row.get("is_filled") == "1":
            continue  # invented rows neither count nor reset
        epoch = row["epoch"]
        for rule in rules.values():
            if not rule["enabled"]:
                continue
            value = to_float(row.get(rule["metric"]))
            if value is None:
                continue  # empty value: does not count, does not reset
            rs = rule_state(state, rule["name"])
            breach = OPS[rule["op"]](value, rule["threshold"])
            if breach:
                rs["consecutive"] += 1
                last = rs.get("last_fired_epoch")
                cooled = last is None or epoch - last >= rule["cooldown_s"]
                if rs["state"] != "firing" and rs["consecutive"] >= rule["sustained"] and cooled:
                    rs["state"] = "firing"
                    rs["last_fired_epoch"] = epoch
                    events.append(make_event(rule, epoch, value, "firing"))
            else:
                rs["consecutive"] = 0
                if rs["state"] == "firing":
                    rs["state"] = "ok"
                    events.append(make_event(rule, epoch, value, "resolved"))
    return events


def make_event(rule, epoch, value, st):
    return {"epoch": epoch, "rule": rule["name"], "metric": rule["metric"], "value": value,
            "threshold": rule["threshold"], "severity": rule["severity"], "state": st, "process": ""}


def evaluate_no_data(no_data, newest_epoch, now, state):
    """Fire if the newest sample is older than max_age_s, resolve when data is fresh again."""
    if not no_data or not no_data["enabled"] or newest_epoch is None:
        return []
    rs = rule_state(state, "no_data")
    age = now - newest_epoch
    ev = {"rule": "no_data", "metric": "", "value": round(age, 1), "threshold": no_data["max_age_s"],
          "severity": no_data["severity"], "process": ""}
    if age > no_data["max_age_s"] and rs["state"] != "firing":
        rs["state"] = "firing"
        rs["last_fired_epoch"] = now
        return [dict(ev, epoch=now, state="firing")]
    if age <= no_data["max_age_s"] and rs["state"] == "firing":
        rs["state"] = "ok"
        return [dict(ev, epoch=now, state="resolved")]
    return []


# --------------------------------------------------------------------------- output

def next_id(alerts_path):
    last = 0
    try:
        with open(alerts_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    last = max(last, int(r["id"]))
                except (ValueError, KeyError, TypeError):
                    pass
    except OSError:
        pass
    return last + 1


def append_alerts(alerts_path, events):
    os.makedirs(os.path.dirname(os.path.abspath(alerts_path)), exist_ok=True)
    new_file = not os.path.exists(alerts_path) or os.path.getsize(alerts_path) == 0
    nid = next_id(alerts_path)
    lines = []
    if new_file:
        lines.append(",".join(ALERT_HEADER))
    for ev in events:
        ev["id"] = nid
        nid += 1
        proc = (ev.get("process") or "").replace(",", "_").replace(" ", "_")
        lines.append(",".join([
            str(ev["id"]), str(ev["epoch"]), iso(ev["epoch"]), ev["rule"], ev["metric"],
            fmt_num(ev["value"]), fmt_num(ev["threshold"]), ev["severity"], ev["state"], proc, "0",
        ]))
    with open(alerts_path, "a", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")  # one write, so readers never see half a row


def describe(ev):
    if ev["rule"] == "no_data":
        what = "no data from collector for %ss" % fmt_num(ev["value"])
    elif ev["rule"] == "test":
        what = "test alert"
    else:
        what = "%s = %s (threshold %s)" % (ev["metric"], fmt_num(ev["value"]), fmt_num(ev["threshold"]))
    s = "%s %s %s: %s" % (ev["state"].upper(), ev["severity"], ev["rule"], what)
    if ev.get("process"):
        s += " [top process: %s]" % ev["process"]
    return s


def notify(ev, log_path):
    """Deliver one event: desktop popup, then optional email / Telegram. Never raises."""
    text = describe(ev)
    title = "SysMon %s" % ev["severity"]
    channels = []

    if shutil.which("notify-send"):
        urgency = "critical" if ev["severity"] == "critical" else "normal"
        try:
            subprocess.run(["notify-send", "-u", urgency, title, text], timeout=5,
                           check=True, capture_output=True)
            channels.append("notify-send")
        except Exception as e:  # noqa: BLE001 - delivery failures are logged, not fatal
            log_line(log_path, "notify-send failed: %s" % e)
    else:
        log_line(log_path, "notify-send not available, skipping desktop popup")

    host, to = os.environ.get("SYSMON_SMTP_HOST"), os.environ.get("SYSMON_SMTP_TO")
    if host and to:
        try:
            msg = EmailMessage()
            msg["Subject"], msg["To"] = "[%s] %s" % (title, ev["rule"]), to
            msg["From"] = os.environ.get("SYSMON_SMTP_FROM", os.environ.get("SYSMON_SMTP_USER", "sysmon@localhost"))
            msg.set_content(text)
            with smtplib.SMTP(host, int(os.environ.get("SYSMON_SMTP_PORT", "587")), timeout=10) as s:
                s.starttls()
                if os.environ.get("SYSMON_SMTP_USER"):
                    s.login(os.environ["SYSMON_SMTP_USER"], os.environ.get("SYSMON_SMTP_PASSWORD", ""))
                s.send_message(msg)
            channels.append("email")
        except Exception as e:  # noqa: BLE001
            log_line(log_path, "email failed: %s" % e)

    token, chat = os.environ.get("SYSMON_TELEGRAM_TOKEN"), os.environ.get("SYSMON_TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": "%s: %s" % (title, text)}).encode()
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token, body, timeout=10).read()
            channels.append("telegram")
        except Exception as e:  # noqa: BLE001
            log_line(log_path, "telegram failed: %s" % type(e).__name__)  # never log the URL (has the token)
    return channels


# --------------------------------------------------------------------------- defaults

def default_rules(system_info, profile):
    cores = system_info.get("cpu_cores") or 4
    tmax = system_info.get("cpu_temp_max_c")
    warn_t = fmt_num(tmax * 0.85) if tmax else "80"
    crit_t = fmt_num(tmax * 0.95) if tmax else "90"
    demo = profile == "demo"
    spec = [  # name, metric, threshold, sustained, severity, cooldown
        ("cpu_high", "cpu_pct", "40" if demo else "85", 2 if demo else 3, "warning", 30 if demo else 600),
        ("mem_high", "mem_used_pct", "60" if demo else "90", 3, "warning", 600),
        ("swap_high", "swap_used_pct", "50", 3, "warning", 600),
        ("disk_high", "disk_used_pct", "90", 1, "warning", 600),
        ("disk_critical", "disk_used_pct", "95", 1, "critical", 600),
        ("load_high", "load1", "%.1f" % (2 * cores), 3, "warning", 600),
        ("cpu_temp_warning", "cpu_temp_c", warn_t, 2, "warning", 600),
        ("cpu_temp_critical", "cpu_temp_c", crit_t, 2, "critical", 600),
        ("gpu_temp_warning", "gpu_temp_c", "80", 2, "warning", 600),
        ("gpu_temp_critical", "gpu_temp_c", "90", 2, "critical", 600),
    ]
    out = []
    for name, metric, thr, sus, sev, cd in spec:
        out.append("[%s]\nmetric = %s\nop = >\nthreshold = %s\nsustained = %d\ncooldown_s = %d\n"
                   "severity = %s\nenabled = true\n" % (name, metric, thr, sus, cd, sev))
    out.append("[no_data]\nmax_age_s = 60\nseverity = critical\nenabled = true\n")
    return "\n".join(out)


def init_defaults(thresholds_path, system_info_path, profile, force):
    if os.path.exists(thresholds_path) and not force:
        print("alerts: %s already exists, not overwriting (use --force)" % thresholds_path)
        return 0
    try:
        with open(system_info_path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    atomic_write(thresholds_path, default_rules(info, profile))
    print("alerts: wrote %s profile to %s" % (profile, thresholds_path))
    return 0


# --------------------------------------------------------------------------- commands

def run_check(args):
    log_path = args.log
    rules, no_data = load_rules(args.thresholds, log_path)
    state = load_state(args.state)
    now = int(args.now if args.now is not None else time.time())

    rows, newest = read_new_rows(args.input, state["last_epoch"])
    events = evaluate(rows, rules, state)
    if rows:
        state["last_epoch"] = rows[-1]["epoch"]
    events += evaluate_no_data(no_data, newest if newest is not None else state["last_epoch"] or None, now, state)

    if events:
        procs = top_processes(args.processes_dir, {e["epoch"] for e in events if e["rule"] != "no_data"})
        for ev in events:
            ev["process"] = procs.get(ev["epoch"], "")
        append_alerts(args.alerts, events)
        for ev in events:
            log_line(log_path, describe(ev))
            if ev["state"] == "firing" and not args.no_notify and now - ev["epoch"] <= NOTIFY_MAX_AGE_S:
                notify(ev, log_path)
    save_state(args.state, state)
    return 0


def run_test(args):
    """Write a test alert (firing then resolved, so the badge clears) and push it through every channel."""
    now = int(args.now if args.now is not None else time.time())
    base = {"rule": "test", "metric": "", "value": 0, "threshold": 0, "severity": "warning", "process": ""}
    firing, resolved = dict(base, epoch=now, state="firing"), dict(base, epoch=now, state="resolved")
    append_alerts(args.alerts, [firing, resolved])
    log_line(args.log, describe(firing))
    channels = [] if args.no_notify else notify(firing, args.log)
    print("alerts: test alert sent (csv, log%s)" % "".join(", " + c for c in channels))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="SysMon alert engine")
    p.add_argument("--input", default=os.path.join(DATA, "clean", "metrics.csv"))
    p.add_argument("--processes-dir", default=os.path.join(DATA, "raw"))
    p.add_argument("--thresholds", default=os.path.join(ROOT, "config", "thresholds.conf"))
    p.add_argument("--state", default=os.path.join(DATA, "alert_state.json"))
    p.add_argument("--alerts", default=os.path.join(DATA, "alerts.csv"))
    p.add_argument("--log", default=os.path.join(DATA, "alerts.log"))
    p.add_argument("--system-info", default=os.path.join(DATA, "system_info.json"))
    p.add_argument("--test", action="store_true", help="send one test alert through every channel")
    p.add_argument("--init-defaults", action="store_true", help="write config/thresholds.conf")
    p.add_argument("--profile", choices=["default", "demo"], default="default")
    p.add_argument("--force", action="store_true", help="with --init-defaults: overwrite")
    p.add_argument("--no-notify", action="store_true", help="skip popups/email/Telegram (tests)")
    p.add_argument("--now", type=int, default=None, help=argparse.SUPPRESS)  # fixed clock for tests
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if os.environ.get("SYSMON_NO_NOTIFY"):
        args.no_notify = True
    try:
        if args.init_defaults:
            return init_defaults(args.thresholds, args.system_info, args.profile, args.force)
        if args.test:
            return run_test(args)
        return run_check(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print("alerts: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())