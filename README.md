# SysMon — Unix System Monitoring Dashboard

> **Topic #6: Unix System Monitoring**  
> A lightweight, terminal-free system monitoring suite combining low-overhead Bash collectors, automated Python data cleaning & statistical forecasting, threshold-based alerting, and a local web dashboard.

---

## 📌 Overview

**SysMon** provides real-time and historical insights into Linux/Unix system performance without requiring manual terminal interaction once launched.

The system operates via a five-stage pipeline:
1. **Background Collector (`bin/collector.sh`):** Samples `/proc`, `/sys`, `df`, and optional AMD/NVIDIA GPU sensors on a configurable interval, logging raw daily CSVs under `data/raw/`.
2. **Data Cleaner (`src/cleaner.py`):** Validates bounds, removes duplicates, forward-fills short gaps, flags long gaps and rolling-median outliers, enforces configurable data retention (`retention_hours`), and writes `data/clean/metrics.csv`.
3. **Statistical Analyzer (`src/analyzer.py`):** Computes windowed summaries (`15m`, `1h`, `6h`, `24h`, `7d`, `all`), hourly baselines, disk-full linear forecasts, z-score anomalies, and aggregated top processes into `data/stats.json`.
4. **Alert Engine (`src/alerts.py`):** Evaluates sustained threshold breaches, cooldowns, and `no_data` staleness against `config/thresholds.conf`, recording state transitions in `data/alerts.csv` and sending desktop notifications when available.
5. **Backend & Web Dashboard (`src/server.py` & `web/`):** Serves the local Flask REST API and dark/light dashboard (`Overview`, `Trends`, `Processes`, `Alerts`, `Settings`) at `http://127.0.0.1:5000`.

---

## 🏗️ Architecture & Data Flow

```text
  /proc, /sys, df, ps, GPU
            │
            ▼
  bin/collector.sh ──► data/raw/{metrics,processes}_YYYYMMDD.csv + data/system_info.json
            │
            ▼  (scheduled every pipeline_interval_s by src/server.py)
  bin/run_pipeline.sh
    ├─► src/cleaner.py   ──► data/clean/{metrics.csv, cleaning_report.json}  (+ retention pruning)
    ├─► src/analyzer.py  ──► data/stats.json
    └─► src/alerts.py    ──► data/{alerts.csv, alert_state.json, alerts.log}
            │
            ▼
  src/server.py (Flask API) ◄──► web/*.html + web/js/*.js (http://127.0.0.1:5000)
```

---

## 📂 Repository Structure

```text
SSL_Project/
├── sysmon                  # One-command launcher (venv setup, config init, Flask server + browser)
├── requirements.txt        # Python dependencies (flask, numpy, pandas, pytest)
├── bin/
│   ├── collector.sh        # Background /proc, /sys, process, and GPU metrics collector
│   ├── start.sh            # Daemonizes collector.sh with PID/lock checks
│   ├── stop.sh             # Cleanly stops collector.sh
│   ├── status.sh           # Reports collector running status (exit 0 running / 1 stopped)
│   ├── run_pipeline.sh     # Runs cleaner -> analyzer -> alerts and updates pipeline_status.json
│   └── gen_data.py         # Deterministic synthetic CSV generator for testing & demos
├── config/
│   ├── sysmon.conf         # Collector, pipeline interval, GPU, and retention_hours settings
│   └── thresholds.conf     # Alert rule definitions and no_data rule
├── src/
│   ├── cleaner.py          # Raw CSV validation, gap/outlier handling, and retention pruning
│   ├── analyzer.py         # Statistical summaries, disk forecast, anomalies, and top processes
│   ├── alerts.py           # Stateful threshold & no_data evaluator with desktop notifications
│   └── server.py           # Flask REST API + static dashboard server + pipeline scheduler
├── web/
│   ├── index.html          # Overview dashboard (live gauges, CPU/memory/temp charts, recent alerts)
│   ├── trends.html         # Historical trends & disk forecast charts
│   ├── processes.html      # Top CPU/memory process breakdown
│   ├── alerts.html         # Filterable alert history & acknowledgement actions
│   ├── settings.html       # Collection intervals, retention_hours, profiles, & alert rules UI
│   ├── style.css           # Shared dashboard theme styles (light/dark)
│   ├── trends.css          # Trends page chart layout styles
│   ├── js/                 # Vanilla JS controllers (app, common, trends, processes, alerts, settings)
│   └── static/             # Bundled Chart.js (chart.umd.js)
├── tests/
│   ├── fixtures/           # Sample CSV/JSON/conf fixtures for isolated tests
│   ├── test_alerts.py      # Unit tests for alert rules, cooldowns, no_data, and defaults
│   ├── test_analyzer.py    # Unit tests for stats windows, regression, and top processes
│   ├── test_cleaner.py     # Unit tests for validation, gap filling, outliers, and retention
│   ├── test_collector.py   # Collector lifecycle & process/GPU regression tests
│   ├── test_gen_data.py    # Synthetic generator & cleaner integration tests
│   ├── test_pipeline.py    # End-to-end pipeline & ./sysmon CLI contract tests
│   └── test_server.py      # Flask API endpoint & config validation tests
├── docs/                   # Interface contracts (INTERFACES.md), task plan, and AI usage notes
└── data/                   # Runtime raw/clean CSVs, JSON state, logs (gitignored except .gitkeep)
```

---

## 🚀 Installation & Running

### Prerequisites

- Linux (Ubuntu 20.04+, Debian, Arch, Fedora) or Windows via **WSL2** (recommended for full `/proc`, `/sys`, and `ps` telemetry; Git Bash is only a limited fallback)
- Bash 4+
- Python 3.8+ (`python3` and `python3-venv`)
- Standard Unix utilities (`awk`, `df`, `ps`, `grep`, `sed`, `flock`; optional `nvidia-smi` for NVIDIA GPUs, `notify-send` for desktop alerts)

### Quick Start (Recommended)

Clone the repository and run the one-command launcher:

```bash
git clone https://github.com/god-of-rta/SSL_Project.git
cd SSL_Project
./sysmon
```

On first launch, `./sysmon` automatically:
1. Creates `data/raw/`, `data/clean/`, and `config/`.
2. Creates a local Python virtual environment in `.venv/` and installs packages from `requirements.txt`.
3. Initializes `config/sysmon.conf` and `config/thresholds.conf` if missing.
4. Starts the Flask backend at `http://127.0.0.1:5000` and opens it in your browser.

Useful launcher flags:

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

# Run the automated test suite
.venv/bin/pytest -v
```

### Data Retention (Auto-Cleanup)

To prevent unbounded disk growth during long monitoring sessions, `config/sysmon.conf` includes `retention_hours` (default `168` hours / 7 days). On each pipeline run, raw CSV files, raw rows, and alert history older than `retention_hours` are automatically pruned. Set `retention_hours = 0` in `config/sysmon.conf` or on the **Settings** page (`Data retention (hours)`) to keep data indefinitely.

---

## 🌿 Git & Contribution Guidelines

- **Protected Main Branch:** All work must be conducted on feature branches (`feature/<name>` or `fix/<name>`). No direct pushes to `main`.
- **Review Requirement:** Every pull request requires review and approval before merging.
- **Commit Conventions:** Use standard conventional commit prefixes:
  - `feat:` for new capabilities
  - `fix:` for bug fixes
  - `test:` for test fixtures and scripts
  - `docs:` for documentation updates
  - `refactor:` for code reorganization
- **Clean Commits:** Do not commit raw runtime data (`data/`), virtual environments (`.venv/`), or sensitive API/email tokens.

---

## 📄 License & Academic Integrity

This project is developed for the **Unix Tools / System Software Laboratory (SSL)** coursework.
