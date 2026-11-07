# SysMon Interfaces

**Version:** 1.1 **Owner:** P1 (Integrator)

This file is the contract between modules. Each module reads and writes only what is defined here, so everyone can build against fixtures without waiting for anyone else.

**Rules**

1. If this file and any other document disagree, **this file wins**.
2. Nobody changes an interface in code first. Open a PR against this file, get approval from the owner of every module that touches it, bump the version and add a line to the changelog.
3. Run all scripts from the repository root. Scripts find the root from their own location, so they also work when called from elsewhere.
4. All paths below are relative to the repository root.
5. Readers find columns **by name**, never by position. New columns may be appended at the end of a CSV without breaking anyone.

## 1. Who owns what

| Module | Owner | Writes | Reads |
|---|---|---|---|
| `bin/collector.sh` (+ start/stop/status) | P2 | `data/raw/*.csv`, `data/system_info.json`, `data/collector.pid`, `data/collector.log` | `config/sysmon.conf` |
| `src/cleaner.py` | P2 | `data/clean/metrics.csv`, `data/clean/cleaning_report.json` | `data/raw/metrics_*.csv` |
| `src/analyzer.py` | P1 | `data/stats.json` | `data/clean/metrics.csv`, `data/raw/processes_*.csv` |
| `src/alerts.py` | P5 | `data/alerts.csv`, `data/alerts.log`, `data/alert_state.json`, `config/thresholds.conf` (only via `--init-defaults`) | `data/clean/metrics.csv`, `data/raw/processes_*.csv`, `config/thresholds.conf`, `data/system_info.json` |
| `bin/run_pipeline.sh` | P1 | `data/pipeline.log`, `data/pipeline_status.json` | calls the three Python modules |
| `src/server.py` | P4 | `data/acks.json`, `config/sysmon.conf` and `config/thresholds.conf` (settings page only) | everything in `data/` |
| `web/` | P3 | nothing | the HTTP API only |
| `bin/gen_data.py` | P5 | `data/raw/*.csv` (synthetic) or the path given by `--out` | nothing |

**Single-writer rule:** every file has exactly one writer. If you need to change another module's file, ask its owner.

**Atomic writes:** any file that other modules read while it may be rewritten (`stats.json`, `clean/metrics.csv`, `cleaning_report.json`, `pipeline_status.json`, `alert_state.json`, `acks.json`, `system_info.json`) must be written to a temporary file in the same folder and then renamed over the target (`os.replace` in Python, `mv` in Bash). Readers must tolerate a file that does not exist yet.

## 2. Conventions

- **Encoding and format:** UTF-8, LF line endings, comma-separated, header row on every CSV, no quoting needed (see `command` below).
- **Time:** `epoch` is Unix time in **UTC, integer seconds**. `iso_time` is the same instant as ISO 8601 UTC, e.g. `2026-11-08T14:03:05Z`. `epoch` is the key; `iso_time` is for humans.
- **Numbers:** `.` as decimal separator, no thousands separators. Percentages and temperatures use 1 decimal place, `mem_avail_mb` is an integer, rates use 1 decimal place.
- **Missing values:** an empty CSV field means "not available" (for example no temperature sensor or no GPU). In JSON, missing values are `null`. Never write `0`, `NA` or `-1` for missing.
- **File dates:** raw files are named by the **UTC** date of their rows.

## 3. Raw data (collector output)

### 3.1 `data/raw/metrics_YYYYMMDD.csv`

One row per sample, header written when the file is created.

```
epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,net_rx_kbps,net_tx_kbps,cpu_temp_c,gpu_util_pct,gpu_mem_used_pct,gpu_temp_c
```

| Column | Meaning |
|---|---|
| `cpu_pct` | Total CPU busy percent across all cores, 0 to 100 (delta of two `/proc/stat` reads) |
| `mem_used_pct` | `(MemTotal - MemAvailable) / MemTotal * 100` |
| `mem_avail_mb` | `MemAvailable` in MB |
| `swap_used_pct` | Swap in use, 0 if there is no swap |
| `disk_used_pct` | Use% of the filesystem mounted at `/` |
| `load1`, `load5`, `load15` | Values from `/proc/loadavg` |
| `net_rx_kbps`, `net_tx_kbps` | Summed over all interfaces except `lo`, as kilobits per second since the previous sample (0.0 on the first sample) |
| `cpu_temp_c` | Hottest thermal zone in °C, or empty if unavailable. With `--simulate-temp`, a plausible generated value |
| `gpu_util_pct` | GPU busy percent, 0 to 100, or empty (see 3.4) |
| `gpu_mem_used_pct` | GPU memory used as a percent of GPU memory, or empty |
| `gpu_temp_c` | GPU temperature in °C, or empty |

Every row has exactly **16 fields**. The first sample after start may be skipped, because CPU needs two readings to form a delta.

### 3.2 `data/raw/processes_YYYYMMDD.csv`

The top 5 processes by CPU for each sample (5 rows per sample).

```
epoch,pid,command,cpu_pct,mem_pct,rank
```

- `epoch` matches the sample in the metrics file.
- `command` is the short process name from `ps`. The collector replaces commas, spaces and newlines in it with `_`, so no CSV quoting is needed.
- `cpu_pct` is the per-process value reported by `ps` (can exceed 100 on multi-core machines). It is used for naming the culprit, not for thresholds.
- `rank` is 1 (highest CPU) to 5.

### 3.3 Control scripts

| Script | Behaviour | Exit codes |
|---|---|---|
| `bin/start.sh` | Starts `collector.sh` in the background with `nohup`, writes `data/collector.pid`. Accepts `--interval N` and `--simulate-temp` and passes them on | `0` started, `2` already running, `1` error |
| `bin/stop.sh` | Sends SIGTERM, waits, removes the PID file. Safe to call when nothing is running | `0` stopped or was not running, `1` error |
| `bin/status.sh` | Checks the PID file and that the process really exists (detects stale PID files) | `0` running, `1` not running |

The collector holds a lock (`data/collector.lock`) so two copies can never run, and logs to `data/collector.log`. The server controls the collector **only** through these three scripts.

### 3.4 GPU collection

The collector detects the GPU once at start and uses **one** GPU for the whole run:

1. If an NVIDIA GPU is present (`nvidia-smi` works), use it. Read `utilization.gpu`, `memory.used`, `memory.total` and `temperature.gpu` with `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`.
2. Otherwise, if an AMD GPU with the `amdgpu` driver is present, read `/sys/class/drm/card*/device/gpu_busy_percent`, `mem_info_vram_used`, `mem_info_vram_total` and the temperature from `device/hwmon/hwmon*/temp1_input`.
3. Otherwise leave all three GPU fields empty on every row. This is normal and must never produce an error.

Hybrid laptops (integrated plus dedicated GPU) report the dedicated NVIDIA GPU. Because calling `nvidia-smi` can wake a sleeping dedicated GPU, the collector polls the GPU only every `gpu_poll_every` samples (default 3) and repeats the last value in between. Setting `collect_gpu = false` in `sysmon.conf` turns GPU collection off entirely (fields stay empty).

### 3.5 `data/system_info.json`

Written once by the collector at start (and rewritten on each start):

```json
{
  "collected_at": "2026-11-08T14:00:00Z",
  "hostname": "laptop",
  "kernel": "6.8.0",
  "cpu_model": "AMD Ryzen 9 7940HX",
  "cpu_cores": 16,
  "total_ram_mb": 15900,
  "disk_total_gb": 512,
  "cpu_temp_max_c": 100,
  "gpu": { "vendor": "nvidia", "name": "NVIDIA GeForce RTX 4060 Laptop GPU" }
}
```

`cpu_temp_max_c` is `null` if the hardware does not expose a limit (look at `/sys/class/hwmon/*/temp1_max` or `temp1_crit`). `gpu` is `null` if no GPU was found. The dashboard header and the default thresholds use this file.

## 4. Cleaned data (cleaner output)

### 4.1 `data/clean/metrics.csv`

Sorted by `epoch` ascending, one row per `epoch`, no duplicates. Columns are the 16 raw columns followed by three flags:

```
...raw columns...,is_filled,is_gap,is_outlier
```

| Flag | Meaning |
|---|---|
| `is_filled` | `1` if the row did not exist in the raw data and was created by forward-filling a **short gap** (up to 3 missing samples) |
| `is_gap` | `1` on the **first real row after a long gap** (more than 3 missing samples). No rows are invented for long gaps; the flag tells consumers not to treat this row as continuous with the previous one (no interpolation, no rate calculation across it) |
| `is_outlier` | `1` if the value is a statistical outlier by a rolling median test. The row is **kept**, never deleted |

Invalid rows are dropped and counted in the report: percentages outside 0 to 100, temperatures (CPU or GPU) outside 0 to 120 °C, malformed lines. Empty GPU or temperature fields are valid and stay empty (they are never filled with invented values).

### 4.2 `data/clean/cleaning_report.json`

```json
{
  "generated_at": "2026-11-08T14:03:05Z",
  "rows_read": 120000,
  "dropped_duplicates": 120,
  "dropped_invalid": 45,
  "filled": 310,
  "gaps": 4,
  "outliers": 52,
  "engine": "plain"
}
```

## 5. Analysis output

### 5.1 `data/stats.json`

```json
{
  "generated_at": "2026-11-08T14:03:05Z",
  "latest_epoch": 1794060185,
  "windows": {
    "1h":  { "cpu_pct": {"mean": 12.3, "min": 2.1, "max": 91.0, "p95": 40.2, "std": 9.8}, "mem_used_pct": {}, "gpu_util_pct": {} },
    "24h": {},
    "all": {}
  },
  "hourly": {
    "cpu_pct": [ {"hour": "2026-11-08T13:00:00Z", "mean": 11.8} ]
  },
  "top_processes": [ {"command": "chrome", "avg_cpu": 14.2, "avg_mem": 6.1} ],
  "disk_forecast": { "days_to_full": null, "slope_pct_per_day": 0.0 },
  "anomalies": { "cpu_pct": 3, "mem_used_pct": 0 },
  "data_quality": { "gaps": 4, "filled": 310 }
}
```

- **Windows are measured back from the newest row** (`latest_epoch`), not from the wall clock. This keeps tests and synthetic data reproducible.
- Metrics covered in each window: `cpu_pct`, `mem_used_pct`, `swap_used_pct`, `disk_used_pct`, `load1`, `net_rx_kbps`, `net_tx_kbps`, `cpu_temp_c`, `gpu_util_pct`, `gpu_mem_used_pct`, `gpu_temp_c`. Statistics ignore empty values. A metric with no data in a window is `null`.
- `anomalies` has one count per metric (z-score method), only for metrics that have data.
- `days_to_full` is `null` if disk usage is flat or falling, or there is too little data.
- Numbers are rounded to 2 decimals.

## 6. Alerts

### 6.1 `config/thresholds.conf` (INI): one section per rule

Each section is one rule, so one metric can have several (for example a warning and a critical temperature rule). The section name is the rule name.

```ini
[cpu_temp_warning]
metric = cpu_temp_c
op = >
threshold = 80
sustained = 2
cooldown_s = 600
severity = warning
enabled = true

[cpu_temp_critical]
metric = cpu_temp_c
op = >
threshold = 90
sustained = 2
cooldown_s = 600
severity = critical
enabled = true

[no_data]
max_age_s = 60
severity = critical
enabled = true
```

| Key | Allowed values |
|---|---|
| `metric` | `cpu_pct`, `mem_used_pct`, `swap_used_pct`, `disk_used_pct`, `load1`, `cpu_temp_c`, `gpu_util_pct`, `gpu_mem_used_pct`, `gpu_temp_c` |
| `op` | `>`, `>=`, `<`, `<=` |
| `threshold` | a number |
| `sustained` | integer 1 or more: consecutive samples that must break the threshold. Empty values do not count and do not reset the count |
| `cooldown_s` | integer 0 or more: minimum seconds before the same rule fires again |
| `severity` | `warning` or `critical` |
| `enabled` | `true` or `false` |

The special `[no_data]` section has no `metric`: it fires if the newest sample is older than `max_age_s` (a dead collector).

### 6.2 Default thresholds (system-aware)

Thresholds are **user-defined**: the user edits them on the Settings page. The first time the app runs, `src/alerts.py --init-defaults` writes a starting `thresholds.conf` using facts about the machine from `data/system_info.json`:

| Rule name | Metric and condition | Sustained | Severity |
|---|---|---|---|
| `cpu_high` | `cpu_pct` > 85 | 3 | warning |
| `mem_high` | `mem_used_pct` > 90 | 3 | warning |
| `swap_high` | `swap_used_pct` > 50 | 3 | warning |
| `disk_high` | `disk_used_pct` > 90 | 1 | warning |
| `disk_critical` | `disk_used_pct` > 95 | 1 | critical |
| `load_high` | `load1` > 2 × `cpu_cores` | 3 | warning |
| `cpu_temp_warning` | `cpu_temp_c` > 85% of `cpu_temp_max_c` (80 if unknown) | 2 | warning |
| `cpu_temp_critical` | `cpu_temp_c` > 95% of `cpu_temp_max_c` (90 if unknown) | 2 | critical |
| `gpu_temp_warning` | `gpu_temp_c` > 80 | 2 | warning |
| `gpu_temp_critical` | `gpu_temp_c` > 90 | 2 | critical |
| `no_data` | newest sample older than 60 s | n/a | critical |

There is deliberately no default GPU-utilization alert, because 100% GPU use is normal under load. Users can add one.

The **demo profile** (`--profile demo`) lowers `cpu_high` to `cpu_pct > 40`, `sustained = 2`, `cooldown_s = 30` and `mem_high` to 60, so `bin/stress.sh` fires an alert within seconds. The Settings page has an "Apply demo profile" button and a "Reset to defaults" button.

### 6.3 `data/alerts.csv`

Written only by `alerts.py`. One row per **state change** (never one per sample).

```
id,epoch,iso_time,rule,metric,value,threshold,severity,state,process,acknowledged
```

- `id` is a unique, increasing integer per row.
- `rule` is the rule name from `thresholds.conf` (`no_data` for the dead-collector rule, with `metric` empty).
- `state` is `firing` or `resolved`. A `resolved` row has the same `rule` as the `firing` row it closes.
- `process` is the top process by CPU at that moment, or empty.
- `acknowledged` is always `0` in the file. Acknowledgements live in `data/acks.json` (below).
- `data/alert_state.json` is private to `alerts.py` (its per-rule memory). Nobody else reads or writes it.

### 6.4 `data/acks.json`

Written only by the server: `{"acknowledged_ids": [3, 4, 7]}`. The API merges this into the `acknowledged` field it returns.

### 6.5 Notifications

`alerts.py` delivers through, in this order: the CSV and log, `notify-send` (failure is logged and ignored), then optional email or Telegram. Credentials come from **environment variables only** (`SYSMON_SMTP_*`, `SYSMON_TELEGRAM_*`) and are never committed.

## 7. Settings

### 7.1 `config/sysmon.conf` (INI)

```ini
[sysmon]
interval_s = 5
data_dir = data
top_n = 5
pipeline_interval_s = 10
port = 5000
simulate_temp = false
collect_gpu = true
gpu_poll_every = 3
```

## 8. Command-line contracts

| Command | Arguments | Success |
|---|---|---|
| `src/cleaner.py` | `--input-dir data/raw --output data/clean/metrics.csv --report data/clean/cleaning_report.json [--engine plain\|pandas]` | exit `0` |
| `src/analyzer.py` | `--input data/clean/metrics.csv --processes-dir data/raw --output data/stats.json` | exit `0` |
| `src/alerts.py` | `--input data/clean/metrics.csv --processes-dir data/raw --thresholds config/thresholds.conf --state data/alert_state.json --alerts data/alerts.csv` | exit `0` |
| `src/alerts.py --test` | sends one test alert through every channel | exit `0` |
| `src/alerts.py --init-defaults` | `[--profile default\|demo] [--force]`: writes `config/thresholds.conf` from section 6.2. Without `--force` it never overwrites an existing file | exit `0` |
| `bin/gen_data.py` | `--rows N --out DIR --seed S` | exit `0` |
| `bin/stress.sh` | `[seconds]` (default 30, hard maximum 120) | exit `0` |

- Defaults for all paths are the ones shown, so each tool also works with no arguments.
- Any error: print a clear message to stderr and exit non-zero. Never crash on missing or empty input; produce valid empty output instead.
- `--since` (incremental processing) is **reserved** for P5's optimization work and will be added to this table once profiling shows where it is needed.

### 8.1 `bin/run_pipeline.sh`

Runs cleaner, analyzer, alerts, in that order. It stops at the first failure, logs to `data/pipeline.log`, and writes `data/pipeline_status.json`:

```json
{ "last_run_epoch": 1794060185, "duration_s": 1.8, "ok": true, "failed_stage": null }
```

Exit `0` only if all three stages succeed. The server runs this script on a timer (`pipeline_interval_s`) and on demand, and reads the status file for `/api/status`. Two runs must never overlap (the server holds a lock).

## 9. HTTP API

Served by `src/server.py` on `127.0.0.1:<port>`. All responses are JSON. Errors always look like `{"ok": false, "error": "message", "field": "optional.field.name"}` with status 400 (bad input) or 500 (server problem).

| Endpoint | Request | Response |
|---|---|---|
| `GET /api/status` | none | see below |
| `GET /api/system` | none | the contents of `system_info.json`, or `{}` if it does not exist yet |
| `POST /api/collector/start` | none | `{"ok": true, "running": true}` |
| `POST /api/collector/stop` | none | `{"ok": true, "running": false}` |
| `POST /api/pipeline/run` | none | `{"ok": true}` after the run finishes, or `{"ok": false, "error": "..."}` |
| `GET /api/metrics?range=1h` | `range` is `1h`, `24h` or `7d` | see below |
| `GET /api/stats` | none | the contents of `stats.json`, or `{}` if it does not exist yet |
| `GET /api/alerts` | none | `{"alerts": [ ... ]}` newest first, rows as in `alerts.csv` with `acknowledged` merged from `acks.json` and `value`, `threshold` as numbers |
| `POST /api/alerts/ack` | `{"ids": [3, 4]}` or `{"all": true}` | `{"ok": true}` |
| `POST /api/alerts/test` | none | `{"ok": true}` |
| `GET /api/config` | none | see below |
| `POST /api/config` | same shape as GET (partial allowed) | `{"ok": true}` or a 400 error with `field` |
| `POST /api/config/profile` | `{"profile": "default"}` or `{"profile": "demo"}` | `{"ok": true}` (runs `alerts.py --init-defaults --profile X --force`) |

**`GET /api/status`:**

```json
{
  "server_time_epoch": 1794060190,
  "collector": { "running": true, "pid": 4242, "last_sample_epoch": 1794060185 },
  "pipeline":  { "last_run_epoch": 1794060185, "duration_s": 1.8, "ok": true, "failed_stage": null }
}
```

**`GET /api/config`:**

```json
{
  "sysmon": { "interval_s": 5, "pipeline_interval_s": 10, "simulate_temp": false, "collect_gpu": true, "gpu_poll_every": 3 },
  "thresholds": {
    "cpu_temp_warning":  { "metric": "cpu_temp_c", "op": ">", "threshold": 80, "sustained": 2, "cooldown_s": 600, "severity": "warning",  "enabled": true },
    "cpu_temp_critical": { "metric": "cpu_temp_c", "op": ">", "threshold": 90, "sustained": 2, "cooldown_s": 600, "severity": "critical", "enabled": true },
    "no_data":           { "max_age_s": 60, "severity": "critical", "enabled": true }
  }
}
```

On `POST /api/config`, sending a rule name that does not exist creates it, and sending `{"thresholds": {"rule_name": null}}` deletes it. The `[no_data]` rule cannot be deleted, only disabled.

**`GET /api/metrics`** returns a compact columnar format and is **downsampled by the server** (about 1 hour: raw samples; 24 hours: 1-minute averages; 7 days: 10-minute averages), so responses stay small. `columns` lists every metric column available, and missing values are `null`:

```json
{
  "range": "1h",
  "step_s": 5,
  "columns": ["epoch", "cpu_pct", "mem_used_pct", "disk_used_pct", "cpu_temp_c", "gpu_util_pct", "gpu_temp_c"],
  "rows": [[1794060180, 12.4, 41.0, 63.2, 54.0, 7.0, 48.0], [1794060185, 13.1, 41.2, 63.2, 54.5, null, null]]
}
```

**Empty states:** with no data yet, every GET returns valid empty JSON (`{}`, `{"alerts": []}`, `{"rows": []}`), never an error. The frontend shows "no data yet".

**Security:** the server binds to `127.0.0.1` only, and runs only the fixed actions above. It never builds a shell command from request data. Config values are validated before anything is written (known metric names, allowed operators, numbers in range).

## 10. Fixtures and mock data

`tests/fixtures/` holds small files in exactly the formats above: `raw_metrics.csv` (16 columns, with a duplicate, a short gap, a long gap, an out-of-range value, a CPU spike, and some rows with empty GPU fields), `raw_processes.csv`, `clean_metrics.csv`, `stats.json`, `alerts.csv`, `system_info.json`, `thresholds.conf`, and one JSON file per API response. P5 creates the raw and clean fixtures by Sunday Nov 8 evening. P3 builds the frontend against the JSON fixtures until the real API exists.

## 11. Changelog

- **1.1:** added GPU columns (raw CSV is now 16 fields) and the GPU collection rule; added `data/system_info.json` and `GET /api/system`; thresholds are now one section per rule with a `metric` key and an `enabled` flag; added system-aware default thresholds, `--init-defaults`, the demo profile and `POST /api/config/profile`; added `rule` to `alerts.csv`; added `collect_gpu` and `gpu_poll_every` settings.
- **1.0:** first version, written from section 5 of the plan.
