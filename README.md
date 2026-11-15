# SysMon — Unix System Monitoring Dashboard

> **Topic #6: Unix System Monitoring**  
> A lightweight, terminal-free system monitoring suite combining low-overhead Bash collectors, automated Python data cleaning & statistical forecasting, threshold-calibrated alerting, and a local web dashboard.

---

## 📌 Overview

**SysMon** provides real-time and historical insights into Linux/Unix system performance without requiring manual terminal interaction once launched.

The system operates via a modular, five-stage pipeline:
1. **Background Collector (`bin/collector.sh`):** Samples `/proc`, `/sys`, `df`, `ps`, and optional AMD/NVIDIA GPU telemetry (`nvidia-smi`) on a configurable interval, rotating daily CSV files under `data/raw/` and auto-detecting host hardware specs in `data/system_info.json`.
2. **Data Cleaner (`src/cleaner.py` / `src/cleaner_fast.py`):** Validates value bounds, removes duplicate samples, forward-fills short gaps ($\le 3$ samples), flags long gaps and rolling-median outliers, enforces rolling data retention (`retention_hours`), and writes `data/clean/metrics.csv`. Includes both a pure Python reference implementation and an optimized vectorized engine (`cleaner_fast.py`).
3. **Statistical Analyzer (`src/analyzer.py`):** Computes multi-window statistical summaries (`15m`, `1h`, `6h`, `24h`, `7d`, `all`), hourly baseline trends, and aggregated top resource-consuming processes into `data/stats.json`.
4. **Threshold-Calibrated Alert Engine (`src/alerts.py`):** Evaluates sustained threshold breaches, cooldown windows, and dead-collector `no_data` staleness against `config/thresholds.conf`. Thresholds auto-calibrate to the host's actual hardware (CPU cores and thermal ceilings). Manages state transitions (`OK` $\rightarrow$ `FIRING` $\rightarrow$ `RESOLVED`), logs events to `data/alerts.csv`, triggers desktop notifications (`notify-send`), and plays an audio synth chime in the browser.
5. **Backend & Web Dashboard (`src/server.py` & `web/`):** A local Flask REST API with background thread scheduling, config profile switching (`default` vs `demo`), downsampled metric delivery, and an interactive web interface (`Overview`, `Trends`, `Processes`, `Alerts`, `Settings`) at `http://127.0.0.1:5000`.

---

## 🏗️ Architecture & Data Flow

```text
    /proc, /sys, df, ps, GPU sensors (NVIDIA/AMD)
                       │
                       ▼
    bin/collector.sh ──► data/raw/{metrics,processes}_YYYYMMDD.csv + data/system_info.json
                       │
                       ▼  (scheduled every pipeline_interval_s by src/server.py)
    bin/run_pipeline.sh
      ├─► src/cleaner.py   ──► data/clean/{metrics.csv, cleaning_report.json} (+ retention pruning)
      ├─► src/analyzer.py  ──► data/stats.json
      └─► src/alerts.py    ──► data/{alerts.csv, alert_state.json, alerts.log}
                       │
                       ▼
    src/server.py (Flask API) ◄──► Web Dashboard (HTML5 / CSS / Vanilla JS / Chart.js)
                                  (http://127.0.0.1:5000)
```

---

## 📂 Repository Structure

```text
.
├── sysmon                  # One-command launcher (venv setup, dependency check, Flask daemon, browser)
├── run_tests.sh            # Automated test runner (pytest + shell sanity tests)
├── requirements.txt        # Python dependencies (flask, numpy, pandas, pytest)
├── bin/
│   ├── collector.sh        # Direct /proc, /sys, process, and GPU metrics collector
│   ├── start.sh            # Starts collector as background daemon (PID & lockfile checks)
│   ├── stop.sh             # Gracefully terminates collector process
│   ├── status.sh           # Checks collector daemon state (exit 0: running / 1: stopped)
│   ├── run_pipeline.sh     # Orchestrates cleaner -> analyzer -> alerts pipeline
│   ├── stress.sh           # Safe load generator (CPU cores, RAM allocation, GPU workload)
│   ├── gen_data.py         # Deterministic synthetic CSV generator for testing & benchmarks
│   ├── profile_run.py      # Automated cProfile runner across varying dataset scales
│   └── run_benchmarks.sh   # Benchmark orchestration script & gnuplot plot generator
├── config/
│   ├── sysmon.conf         # Daemon intervals, data retention, GPU toggles, and server port
│   └── thresholds.conf     # Alert threshold rules, severity, sustained breaches, and cooldowns
├── src/
│   ├── cleaner.py          # Reference pure-Python CSV cleaner and gap/outlier processor
│   ├── cleaner_fast.py     # Vectorized (pandas/numpy) high-throughput cleaner
│   ├── analyzer.py         # Rolling window statistics, disk regression forecast, and anomalies
│   ├── alerts.py           # Stateful alert engine with dynamic hardware calibration & notifications
│   └── server.py           # Flask REST API, static asset host, and pipeline scheduler
├── web/
│   ├── index.html          # Overview dashboard (live gauges, charts, quick actions, alerts badge)
│   ├── trends.html         # Historical trend visualizations & disk full projection charts
│   ├── processes.html      # Process breakdown with dynamic pagination ("Show More / Less")
│   ├── alerts.html         # Alert history table, resolution management & acknowledgement
│   ├── settings.html       # Web UI for threshold editing, profile selection, and intervals
│   ├── style.css           # Clean responsive dashboard stylesheet
│   ├── trends.css          # Chart layout stylesheet
│   ├── js/                 # Vanilla JS application controllers (app, common, trends, processes, alerts, settings)
│   └── static/             # Offline dependencies (Chart.js UMD bundle)
├── tests/
│   ├── fixtures/           # Standardized test data (raw/clean CSVs, mock system_info.json)
│   ├── test_alerts.py      # Unit tests for alert rules, state machine transitions, and cooldowns
│   ├── test_analyzer.py    # Unit tests for statistical windows, regression, and process metrics
│   ├── test_cleaner.py     # Unit tests for bounds validation, duplicate removal, and gap filling
│   ├── test_cleaner_fast.py# Verification that cleaner_fast.py matches cleaner.py output
│   ├── test_collector.py   # Collector lifecycle, PID locking, and signal handling tests
│   ├── test_gen_data.py    # Synthetic generator format and reproducibility tests
│   ├── test_pipeline.py    # End-to-end pipeline execution and CLI contract tests
│   └── test_server.py      # Flask REST API endpoint tests and validation checks
├── docs/
│   ├── INTERFACES.md       # Formal CSV formats, JSON schemas, and REST API specification
│   ├── PROFILING.md        # Comprehensive cProfile analysis, bottleneck breakdown & speedup logs
│   ├── AI_USAGE.md         # Coursework AI assistance log
│   ├── TASKS.md            # Work breakdown and milestone tracking
│   └── benchmarks/         # Performance charts, runtime plots, and markdown summaries
└── data/                   # Runtime data directory (gitignored)
```

---

## ⚙️ Configuration & Hardware-Aware Calibration

SysMon configuration is decoupled into daemon behavior (`config/sysmon.conf`) and alerting logic (`config/thresholds.conf`).

### 1. `config/sysmon.conf`
Controls runtime daemon behavior and pipeline intervals:
```ini
[sysmon]
interval_s = 5            # Sampling frequency for collector.sh (seconds)
data_dir = data           # Runtime data directory
top_n = 5                 # Number of top CPU/memory processes to record per sample
pipeline_interval_s = 10  # Frequency of cleaner -> analyzer -> alerts pipeline execution
port = 5000               # Port for the Flask dashboard server
simulate_temp = false     # Fallback synthetic temperatures if hardware sensors are absent
collect_gpu = true        # Enable GPU metrics collection (auto-detects NVIDIA/AMD)
gpu_poll_every = 3        # Sub-sampling multiplier for GPU queries to minimize driver overhead
retention_hours = 168     # Automatic data pruning threshold (168h = 7 days; 0 = disabled)
alerts_enabled = true     # Global toggle to pause/resume alert firing
```

### 2. `config/thresholds.conf`
Defines operational limits, persistence requirements, and notification rules. SysMon avoids arbitrary hardcoded limits by dynamically calibrating rules to the host machine's physical hardware on startup (via `src/alerts.py --init-defaults`):
- **CPU Load (`load_high`):** Dynamically set to $2 \times \text{logical cores}$ (e.g., threshold is set to `64.0` on a 32-thread CPU, avoiding false alarms on multi-core workstations).
- **Thermal Ceilings (`cpu_temp_warning`, `cpu_temp_critical`):** Scaled against the processor's maximum junction temperature $T_{\max}$ (retrieved from `/sys/class/thermal` or `data/system_info.json`). Warning triggers at $0.85 \times T_{\max}$ and Critical triggers at $0.95 \times T_{\max}$.
- **Profiles (`Default` vs `Demo`):**
  - **Default:** Conservative thresholds for 24/7 monitoring (e.g., CPU > 85% sustained for 3 samples, 10-minute cooldown).
  - **Demo:** Sensitive thresholds for presentation demonstrations (e.g., CPU > 40% sustained for 2 samples, 30s cooldown). Switch profiles with one click in the **Settings** tab.

---

## 🚀 Getting Started

### Prerequisites

- **Operating System:** Linux (Ubuntu 20.04+, Debian, Fedora, Arch) or **WSL2** (though some features like CPU temperature may not work).
- **Shell & Runtime:** Bash 4.0+ and Python 3.8+ with `python3-venv`.
- **System Utilities:** Standard core utilities (`awk`, `df`, `ps`, `grep`, `sed`, `flock`).
- **Optional Tools:**
  - `nvidia-smi` (for NVIDIA GPU metrics)
  - `libnotify-bin` (provides `notify-send` for desktop alert popups)
  - `gnuplot` (for offline benchmark chart generation)

### Quick Start (Recommended)

Clone the repository and run the single launcher script:

```bash
git clone https://github.com/god-of-rta/SSL_Project.git
cd SSL_Project
chmod +x sysmon bin/*.sh run_tests.sh
./sysmon
```

On execution, `./sysmon` will automatically:
1. Verify system tools and create necessary directory trees (`data/raw/`, `data/clean/`, `config/`).
2. Create an isolated virtual environment (`.venv/`) and install all required dependencies.
3. Query hardware specs and generate auto-calibrated configuration files if missing.
4. Launch the local Flask server on `http://127.0.0.1:5000` and open your default browser.

#### Launcher Options
```bash
./sysmon --no-browser      # Start server without launching a browser window
./sysmon --init-defaults   # Initialize directories and hardware configs, then exit
./sysmon --help            # Display launcher help
```

---

## 🛠️ Component Control & Manual Execution

All pipeline components can be managed independently via CLI scripts:

### Collector Control
```bash
./sysmon --no-browser      # Start server without opening a browser tab
./sysmon --init-defaults   # Create directories and default configs, then exit
./sysmon --help            # Show CLI options
```

Once the dashboard opens, use **Start Collector** in the top bar (or run `./bin/start.sh` manually) to begin recording telemetry.

### Manual Component Commands

```bash
# Control the background collector directly
./bin/start.sh [--interval 5] [--simulate-temp]
./bin/status.sh
./bin/stop.sh

# Run the cleaning -> analysis -> alerting pipeline once
./bin/run_pipeline.sh

# Run demo load generator (CPU + memory load, max 120s)
./bin/stress.sh [seconds]

# Run the automated test suite
./run_tests.sh          # Full suite runner (or .venv/bin/pytest -v)
```

### Data Retention (Auto-Cleanup)

To prevent unbounded disk growth during long monitoring sessions, `config/sysmon.conf` includes `retention_hours` (default `168` hours / 7 days). On each pipeline run, raw CSV files, raw rows, and alert history older than `retention_hours` are automatically pruned. Set `retention_hours = 0` in `config/sysmon.conf` or on the **Settings** page (`Data retention (hours)`) to keep data indefinitely.

---

## 📄 License & Academic Integrity

This project is developed for the **Unix Tools / System Software Laboratory (SSL)** coursework.
