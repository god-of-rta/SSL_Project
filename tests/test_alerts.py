
import csv
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
import alerts  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, "src", "alerts.py")
T0 = 1_794_000_000
HEADER = ("epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,"
          "net_rx_kbps,net_tx_kbps,cpu_temp_c,gpu_util_pct,gpu_mem_used_pct,gpu_temp_c,is_filled,is_gap,is_outlier")

RULES = """[cpu_high]
metric = cpu_pct
op = >
threshold = 85
sustained = 3
cooldown_s = 60
severity = warning
enabled = true

[gpu_temp_warning]
metric = gpu_temp_c
op = >
threshold = 80
sustained = 2
cooldown_s = 0
severity = warning
enabled = true

[no_data]
max_age_s = 60
severity = critical
enabled = true
"""


def write_metrics(path, rows):
    """rows: list of dicts with epoch plus any metric overrides; the rest default to harmless values."""
    with open(path, "w") as f:
        f.write(HEADER + "\n")
        for r in rows:
            v = dict(cpu_pct="10.0", mem_used_pct="30.0", mem_avail_mb="8000", swap_used_pct="0.0",
                     disk_used_pct="50.0", load1="0.5", load5="0.5", load15="0.5", net_rx_kbps="1.0",
                     net_tx_kbps="1.0", cpu_temp_c="50.0", gpu_util_pct="", gpu_mem_used_pct="", gpu_temp_c="",
                     is_filled="0", is_gap="0", is_outlier="0")
            v.update({k: str(x) for k, x in r.items()})
            f.write(",".join([v["epoch"], alerts.iso(int(v["epoch"]))] + [v[c] for c in HEADER.split(",")[2:]]) + "\n")


def series(**cols):
    """Build rows at 5 s spacing; each kwarg is a list of values (None = empty field)."""
    n = max(len(v) for v in cols.values())
    return [dict(epoch=T0 + 5 * i, **{k: ("" if v[i] is None else v[i]) for k, v in cols.items()}) for i in range(n)]


@pytest.fixture
def env(tmp_path):
    (tmp_path / "thresholds.conf").write_text(RULES)
    return tmp_path


def run(env, rows=None, now=None, extra=()):
    if rows is not None:
        write_metrics(env / "metrics.csv", rows)
    argv = ["--input", str(env / "metrics.csv"), "--processes-dir", str(env), "--thresholds", str(env / "thresholds.conf"),
            "--state", str(env / "state.json"), "--alerts", str(env / "alerts.csv"), "--log", str(env / "alerts.log"),
            "--no-notify", "--now", str(now if now is not None else T0 + 10_000_000)] + list(extra)
    assert alerts.main(argv) == 0
    return read_alerts(env)


def read_alerts(env):
    p = env / "alerts.csv"
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- state machine

def test_sustained_breach_fires_exactly_once_then_resolves(env):
    rows = run(env, series(cpu_pct=[10, 90, 90, 90, 91, 92, 93, 94, 10]), now=T0 + 45)
    assert [(r["rule"], r["state"]) for r in rows] == [("cpu_high", "firing"), ("cpu_high", "resolved")]
    assert rows[0]["epoch"] == str(T0 + 15)  # third consecutive breaching sample
    assert rows[0]["value"] == "90" and rows[0]["threshold"] == "85" and rows[0]["severity"] == "warning"


def test_one_sample_spike_does_not_fire(env):
    assert run(env, series(cpu_pct=[10, 99, 10, 99, 10, 99, 10]), now=T0 + 30) == []


def test_exact_threshold_is_not_a_breach_for_gt(env):
    assert run(env, series(cpu_pct=[85, 85, 85, 85]), now=T0 + 15) == []


def test_cooldown_blocks_refire_until_elapsed(env):
    # fires at T0+10, resolves at T0+15, breaches again from T0+20..T0+30 (only 20 s after first fire: cooldown 60)
    cpu = [90, 90, 90, 10, 90, 90, 90, 90]
    rows = run(env, series(cpu_pct=cpu), now=T0 + 35)
    assert [r["state"] for r in rows] == ["firing", "resolved"]


def test_refire_after_cooldown(env):
    cpu = [90, 90, 90, 10] + [90] * 3 + [10] * 1
    rows = series(cpu_pct=cpu)
    for r in rows[4:]:
        r["epoch"] += 100  # second incident well past the 60 s cooldown
    out = run(env, rows, now=T0 + 140)
    assert [r["state"] for r in out] == ["firing", "resolved", "firing", "resolved"]


def test_empty_values_neither_count_nor_reset(env):
    rows = run(env, series(gpu_temp_c=[85, None, None, 85, 85]), now=T0 + 25)
    assert [r["state"] for r in rows] == ["firing"]
    assert rows[0]["epoch"] == str(T0 + 15)


def test_no_gpu_columns_never_alerts_or_crashes(env):
    assert run(env, series(gpu_temp_c=[None] * 10), now=T0 + 50) == []


def test_gap_resets_consecutive_count(env):
    rows = series(cpu_pct=[90, 90, 90, 90])
    rows[2]["is_gap"] = 1  # breach count restarts at the gap row
    out = run(env, rows, now=T0 + 15)
    assert out == []  # only 2 consecutive samples after the gap


def test_filled_rows_are_ignored(env):
    rows = series(cpu_pct=[90, 90, 90])
    rows[1]["is_filled"] = 1
    assert run(env, rows, now=T0 + 10) == []  # only 2 real breaching samples


def test_disabled_rule_does_not_fire(env):
    (env / "thresholds.conf").write_text(RULES.replace("enabled = true\n\n[gpu", "enabled = false\n\n[gpu", 1))
    assert run(env, series(cpu_pct=[99] * 6), now=T0 + 25) == []


def test_invalid_rule_is_skipped_not_fatal(env):
    (env / "thresholds.conf").write_text(RULES + "\n[bad]\nmetric = nonsense\nthreshold = 1\n")
    assert run(env, series(cpu_pct=[99] * 4), now=T0 + 15)[0]["rule"] == "cpu_high"
    assert "skipping invalid rule [bad]" in (env / "alerts.log").read_text()


# ---------------------------------------------------------------- incremental runs

def test_state_carries_across_runs_and_does_not_duplicate(env):
    all_rows = series(cpu_pct=[90] * 6 + [10])
    assert run(env, all_rows[:2], now=T0 + 5) == []          # 2 breaching samples so far
    out = run(env, all_rows[:5], now=T0 + 20)                # third arrives in a later run
    assert [r["state"] for r in out] == ["firing"]
    out = run(env, all_rows, now=T0 + 30)                    # rerun with more data
    assert [r["state"] for r in out] == ["firing", "resolved"]
    out = run(env, now=T0 + 30)                              # rerun, nothing new
    assert len(out) == 2


def test_ids_increase(env):
    out = run(env, series(cpu_pct=[90] * 4 + [10]), now=T0 + 20)
    assert [r["id"] for r in out] == ["1", "2"]
    assert all(r["acknowledged"] == "0" for r in out)


def test_csv_header_matches_contract(env):
    run(env, series(cpu_pct=[90] * 4), now=T0 + 15)
    assert (env / "alerts.csv").read_text().splitlines()[0] == ",".join(alerts.ALERT_HEADER)


# ---------------------------------------------------------------- no_data

def test_no_data_fires_when_stale_and_resolves_when_fresh(env):
    write_metrics(env / "metrics.csv", series(cpu_pct=[10, 10]))
    assert run(env, now=T0 + 5 + 30) == []
    out = run(env, now=T0 + 5 + 61)
    assert [(r["rule"], r["state"], r["metric"], r["severity"]) for r in out] == [("no_data", "firing", "", "critical")]
    assert len(run(env, now=T0 + 5 + 120)) == 1              # still stale: no repeat
    write_metrics(env / "metrics.csv", series(cpu_pct=[10] * 40))  # collector is back
    out = run(env, now=T0 + 195 + 5)
    assert [r["state"] for r in out] == ["firing", "resolved"]


def test_missing_input_is_valid_empty_output(env):
    assert run(env) == []
    assert json.load(open(env / "state.json"))["last_epoch"] == 0


def test_empty_input_file(env):
    (env / "metrics.csv").write_text("")
    assert run(env) == []


def test_header_only_input(env):
    (env / "metrics.csv").write_text(HEADER + "\n")
    assert run(env) == []


def test_truncated_last_line_is_ignored(env):
    write_metrics(env / "metrics.csv", series(cpu_pct=[90, 90, 90]))
    with open(env / "metrics.csv", "a") as f:
        f.write("1794000020,2026-11-")  # collector/cleaner mid-write
    assert [r["state"] for r in run(env, now=T0 + 10)] == ["firing"]


# ---------------------------------------------------------------- culprit process

def test_firing_row_names_top_process(env):
    day = alerts.datetime.fromtimestamp(T0 + 10, tz=alerts.timezone.utc).strftime("%Y%m%d")
    with open(env / ("processes_%s.csv" % day), "w") as f:
        f.write("epoch,pid,command,cpu_pct,mem_pct,rank\n")
        f.write("%d,2,bash,1.0,0.1,2\n%d,1,stress-ng,95.0,1.0,1\n" % (T0 + 10, T0 + 10))
    out = run(env, series(cpu_pct=[90] * 3), now=T0 + 10)
    assert out[0]["process"] == "stress-ng"


# ---------------------------------------------------------------- tail reading

def test_tail_read_matches_full_read_on_big_file(env):
    n = 20000
    write_metrics(env / "metrics.csv", series(cpu_pct=[10] * n))
    full, newest = alerts.read_new_rows(str(env / "metrics.csv"), 0)
    assert len(full) == n and newest == T0 + 5 * (n - 1)
    cut = T0 + 5 * (n - 50)
    tail, _ = alerts.read_new_rows(str(env / "metrics.csv"), cut)
    assert [r["epoch"] for r in tail] == [r["epoch"] for r in full if r["epoch"] > cut]


# ---------------------------------------------------------------- --test and --init-defaults

def test_test_alert_writes_firing_then_resolved(env):
    assert alerts.main(["--test", "--alerts", str(env / "alerts.csv"), "--log", str(env / "alerts.log"),
                        "--no-notify", "--now", str(T0)]) == 0
    rows = read_alerts(env)
    assert [(r["rule"], r["state"]) for r in rows] == [("test", "firing"), ("test", "resolved")]


def test_init_defaults_uses_machine_facts(env):
    info = env / "system_info.json"
    info.write_text(json.dumps({"cpu_cores": 16, "cpu_temp_max_c": 100}))
    conf = env / "new.conf"
    assert alerts.main(["--init-defaults", "--thresholds", str(conf), "--system-info", str(info)]) == 0
    rules, no_data = alerts.load_rules(str(conf), str(env / "l.log"))
    assert rules["load_high"]["threshold"] == 32
    assert rules["cpu_temp_warning"]["threshold"] == 85 and rules["cpu_temp_critical"]["threshold"] == 95
    assert rules["cpu_high"]["threshold"] == 85 and rules["cpu_high"]["sustained"] == 3
    assert rules["disk_critical"]["severity"] == "critical" and "gpu_util" not in " ".join(rules)
    assert no_data["max_age_s"] == 60 and len(rules) == 10


def test_init_defaults_unknown_hardware_falls_back(env):
    conf = env / "new.conf"
    alerts.main(["--init-defaults", "--thresholds", str(conf), "--system-info", str(env / "missing.json")])
    rules, _ = alerts.load_rules(str(conf), str(env / "l.log"))
    assert rules["cpu_temp_warning"]["threshold"] == 80 and rules["cpu_temp_critical"]["threshold"] == 90


def test_init_defaults_never_overwrites_without_force(env):
    conf = env / "t.conf"
    conf.write_text("# mine\n")
    assert alerts.main(["--init-defaults", "--thresholds", str(conf)]) == 0
    assert conf.read_text() == "# mine\n"
    assert alerts.main(["--init-defaults", "--thresholds", str(conf), "--force", "--profile", "demo"]) == 0
    rules, _ = alerts.load_rules(str(conf), str(env / "l.log"))
    assert rules["cpu_high"]["threshold"] == 40 and rules["cpu_high"]["sustained"] == 2
    assert rules["cpu_high"]["cooldown_s"] == 30 and rules["mem_high"]["threshold"] == 60


def test_shipped_thresholds_conf_parses():
    rules, no_data = alerts.load_rules(os.path.join(REPO_ROOT, "config", "thresholds.conf"), os.devnull)
    assert len(rules) == 10 and no_data is not None


def test_cli_runs_with_no_input(tmp_path):
    res = subprocess.run([sys.executable, SCRIPT, "--input", str(tmp_path / "nope.csv"),
                          "--thresholds", str(tmp_path / "nope.conf"), "--state", str(tmp_path / "s.json"),
                          "--alerts", str(tmp_path / "a.csv"), "--log", str(tmp_path / "a.log"), "--no-notify"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr