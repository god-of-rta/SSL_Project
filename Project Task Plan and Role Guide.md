# SysMon: Project Task Plan and Role Guide

Unix System Monitoring Dashboard (Topic #6) · Team of five · Due **Nov 16, 2026, 10 AM** · Plan written Nov 6, 2026

## 1. What we are building

A system monitor that anyone can run without touching the terminal. A background **Bash collector** samples CPU, memory, swap, disk, load, network, temperature and top processes every few seconds and writes CSV files. **Python** cleans the data, analyses it (statistics, trends, a disk-full forecast) and runs a **threshold-based alert engine**. A small local **Flask server** controls the collector, runs the pipeline on a timer and serves an **HTML/CSS/JS dashboard** with Start/Stop buttons, live charts, an alert history and a settings page. Alerts appear on the webpage and as desktop notifications (`notify-send`), with optional email or Telegram.

The end user runs one launcher, `./sysmon`, and everything else happens in the browser.

## 2. Roles at a glance

| # | Name | Role | Owns |
| --- | --- | --- | --- |
| P1 | \_\_\_\_\_\_\_\_ | **Integrator + Analysis** | Repo, PR review and merging, `INTERFACES.md`, launcher, pipeline glue, integration test, README/report assembly; `analyzer.py`, `stats.json`, disk forecast |
| P2 | \_\_\_\_\_\_\_\_ | **Collector + Cleaner** | `collector.sh`, `start.sh`, `stop.sh`, `status.sh`, rotation, temperature fallback; `cleaner.py` (plain-Python version), cleaning report, cleaner tests |
| P3 | \_\_\_\_\_\_\_\_ | **Alerts + Backend API** | `alerts.py` (state machine, delivery), `thresholds.conf` handling, `server.py` (Flask API, collector control, pipeline thread), API and alert tests |
| P4 | \_\_\_\_\_\_\_\_ | **Frontend** | All pages in `web/` (HTML/CSS/JS), live charts, alert toasts and badge, settings form, gnuplot daily-summary chart |
| P5 | \_\_\_\_\_\_\_\_ | **Data, Testing and Profiling** | `gen_data.py`, fixtures, `stress.sh`, test runner, `cProfile` analysis, optimized cleaner (pandas/vectorized), incremental processing, awk comparison, collector-overhead measurement, benchmark plots |

All five also write their own section of the README, the LaTeX report and the Beamer slides, and each prepares a one-minute part of the demo.

## 3. Architecture and data flow

```
collector.sh --> data/raw/metrics_YYYYMMDD.csv
                 data/raw/processes_YYYYMMDD.csv
                        |
                   cleaner.py --> data/clean/metrics.csv + cleaning_report.json
                        |
                   analyzer.py --> data/stats.json
                        |
                   alerts.py  --> data/alerts.csv, alerts.log, data/alert_state.json
                        |         + notify-send / email / Telegram
                        v
                   server.py (Flask, 127.0.0.1) <--> browser (web/*.html, JS)
```

`run_pipeline.sh` runs cleaner, analyzer and alerts in order. `server.py` runs it on a timer in a background thread (with a lock so runs never overlap) and also on demand from the "Refresh now" button.

## 4. Repository layout

```
sysmon/
  sysmon                  launcher (P1)
  bin/        collector.sh start.sh stop.sh status.sh   (P2)
              run_pipeline.sh                           (P1)
              stress.sh gen_data.py                     (P5)
  src/        cleaner.py (P2)  cleaner_fast.py (P5)
              analyzer.py (P1)  alerts.py server.py (P3)
  config/     sysmon.conf thresholds.conf               (defaults: P1)
  web/        index.html trends.html alerts.html processes.html
              settings.html style.css static/           (P4)
  data/       raw/ clean/ stats.json alerts.csv         (git-ignored)
  tests/      fixtures/ test_*.py test_integration.sh
  docs/       INTERFACES.md TASKS.md AI_USAGE.md
  report/     report.tex slides.tex sections/
  requirements.txt  README.md  .gitignore
```

## 5. Interface contracts (finalize in `docs/INTERFACES.md` by Nov 7)

These are the only things the modules share. Nobody changes them without telling the whole team.

**Raw metrics CSV** (`metrics_YYYYMMDD.csv`, one row per sample, header on every new file):

`epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,net_rx_kbps,net_tx_kbps,cpu_temp_c`

If a value is unavailable (for example temperature), leave the field empty.

**Raw processes CSV** (`processes_YYYYMMDD.csv`, top 5 by CPU per sample): `epoch,pid,command,cpu_pct,mem_pct,rank`

**Clean metrics CSV** (`data/clean/metrics.csv`): the raw columns plus `is_filled,is_gap,is_outlier` (0 or 1).

**`cleaning_report.json`**: `rows_read, dropped_duplicates, dropped_invalid, filled, gaps, outliers`.

**`sysmon.conf`** (INI): `interval_s`, `data_dir`, `top_n`, `pipeline_interval_s`, `port`.

**`thresholds.conf`** (INI, one section per metric):

```
[cpu_pct]
op = >
threshold = 85
sustained = 3
cooldown_s = 600
severity = warning
```

**`stats.json`** (written by `analyzer.py`):

```
{ "generated_at": "...",
  "windows": { "1h": {"cpu_pct": {"mean":0,"min":0,"max":0,"p95":0,"std":0}, ...},
               "24h": {...}, "all": {...} },
  "hourly": { "cpu_pct": [ {"hour":"...","mean":0}, ... ] },
  "top_processes": [ {"command":"...","avg_cpu":0,"avg_mem":0} ],
  "disk_forecast": { "days_to_full": null, "slope_pct_per_day": 0 },
  "anomalies": { "cpu_pct": 0 },
  "data_quality": { "gaps": 0, "filled": 0 } }
```

**`alerts.csv`**: `id,epoch,iso_time,metric,value,threshold,severity,state,process,acknowledged` (`state` is `firing` or `resolved`).

**Collector control:** the server only calls `start.sh`, `stop.sh` and `status.sh`. `status.sh` exits `0` if running and `1` if not.

**API** (all JSON, localhost only):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | collector running, last sample time, last pipeline run |
| `POST /api/collector/start`, `/api/collector/stop` | control the collector |
| `POST /api/pipeline/run` | run clean, analyze, alert now |
| `GET /api/metrics?range=1h` | downsampled clean data for charts |
| `GET /api/stats` | contents of `stats.json` |
| `GET /api/alerts`, `POST /api/alerts/ack` | alert history, mark as read |
| `POST /api/alerts/test` | fire a test alert |
| `GET /api/config`, `POST /api/config` | read and save thresholds and settings |

## 6. Role descriptions and requirements

### P1: Integrator + Analysis

**Integrator duties**

- Create the repo, protect `main` (no direct pushes), add `.gitignore` (`data/`, virtual environments, credentials), PR template and folder skeleton. Done by Nov 6 evening.
- Write `docs/INTERFACES.md` from section 5 and get everyone's sign-off by Nov 7.
- Review every PR within 12 hours. Merge only if the code runs, has a test or a how-to-test note, and does not break the interfaces. Another teammate reviews P1's own PRs.
- After every merge, run `tests/test_integration.sh` on `main`. If `main` breaks, revert first, then ask for a fix.
- Write `sysmon` (launcher): create the virtual environment if missing, install `requirements.txt`, create `data/` and default configs, start `server.py` on `127.0.0.1`, open the browser with `xdg-open`.
- Write `run_pipeline.sh` (cleaner, analyzer, alerts, with clear exit codes and logging).
- Write `test_integration.sh`: run the collector for about 10 s at a 1 s interval, check row and field counts, run the pipeline, check `stats.json` exists, run `stress.sh` and check an alert row appears.
- Merge the README sections, build the LaTeX report and Beamer slides, tag the final commit, submit.
- Post a short status in the group chat each evening: "main works end to end" or "main broken: X".

**Analysis requirements (`src/analyzer.py`)**

- Input: `data/clean/metrics.csv` and the processes CSVs. Output: `data/stats.json` as specified.
- Per metric, for the last hour, last 24 hours and all time: mean, min, max, p95, standard deviation. Skip rows flagged `is_gap`.
- Hourly averages and a rolling mean for trend lines.
- Top processes by average CPU and memory over a window.
- Disk-full forecast: linear fit of `disk_used_pct` against time (`numpy.polyfit`); `days_to_full` is `null` if the slope is zero or negative.
- Z-score anomaly count per metric.
- Command-line options: `--input`, `--output`, `--since` (for incremental runs later).
- Unit tests with fixture data and known expected values, including empty input and a single row.
- Acceptance: runs on the P5 synthetic datasets (100k and 500k rows) without errors, and the output validates against the `stats.json` shape.

### P2: Collector + Cleaner

**Collector requirements (`bin/`)**

- `collector.sh` loops every `interval_s` (from `sysmon.conf`, default 5) and appends to the two raw CSV files above. Write the header when a new file is created. Rotate files daily by date in the filename.
- Read `/proc` directly to keep overhead low: CPU from two reads of `/proc/stat` (delta), memory and swap from `/proc/meminfo`, load from `/proc/loadavg`, network from `/proc/net/dev` deltas, disk from `df -P /`.
- Temperature from `/sys/class/thermal/thermal_zone*/temp` (divide by 1000), falling back to `sensors`; empty field if neither exists. Add a `--simulate-temp` flag that produces plausible values, so the demo never depends on one laptop.
- Top processes from `ps -eo pid,comm,%cpu,%mem --sort=-%cpu`, five rows per sample.
- Use a PID file and a lock so it cannot run twice. Handle SIGTERM and SIGINT with `trap` and exit cleanly. Never crash on one bad reading.
- `start.sh` (background with `nohup`, writes PID), `stop.sh` (sends SIGTERM, removes a stale PID file), `status.sh` (exit `0` running, `1` not running, and detects stale PID files).
- Acceptance: runs for 10 minutes with no errors; every row has exactly 13 fields; a second start is refused; `stop.sh` leaves no leftover process.
- Check temperature access on every teammate's laptop by Nov 7 and report who has it.

**Cleaner requirements (`src/cleaner.py`, plain-Python `csv` version)**

- Read all raw metrics files, parse timestamps, sort by time, drop exact duplicates.
- Validate ranges (percentages 0 to 100, temperature 0 to 120). Drop invalid rows and count them.
- Fill gaps of up to 3 missing samples by forward fill (`is_filled=1`). Mark longer gaps with `is_gap=1` instead of inventing data, because they mean the collector was down.
- Flag outliers with a rolling median (`is_outlier=1`) but never delete them, since a real spike is what we want to keep.
- Write `data/clean/metrics.csv` and `cleaning_report.json`.
- Options: `--input-dir`, `--output`, and a `--since` flag for incremental processing (coordinate with P5).
- Unit tests using P5's fixtures: duplicates, gaps, out-of-range values, a real spike.
- Acceptance: the output follows the clean CSV contract exactly; running it twice on the same input gives identical output.

### P3: Alerts + Backend API

**Alert engine requirements (`src/alerts.py`)**

- Read `thresholds.conf` and the latest clean data. Reload the config on each run so settings changes apply without a restart.
- Per rule, a state machine **OK, FIRING, RESOLVED**. A rule fires only after `sustained` consecutive samples break the threshold, and does not repeat within `cooldown_s`. Notify on transitions only, never on every sample.
- Persist state in `data/alert_state.json` so a restart does not re-fire active alerts.
- Add a built-in "no data for 60 s" rule that detects a dead collector.
- Attach the top process at that moment to each alert.
- Write each alert to `alerts.csv` and `alerts.log`, then deliver: `notify-send` (failure is logged and ignored), optional email through `smtplib` and optional Telegram bot, with credentials read from environment variables only and never committed.
- A `send_test_alert()` function that goes through the same path as real alerts.
- Unit tests with value sequences covering sustained, cooldown, resolve and the no-data rule.
- Acceptance: a spike of 1 sample does not fire; a sustained breach fires exactly once; a recovery produces one resolved record.

**Backend requirements (`src/server.py`, Flask)**

- Implement the API table in section 5 and serve `web/` as static files.
- Bind to `127.0.0.1` only. Actions are fixed: never build a shell command from browser input. Validate every config value on save (numbers in range, known metric names, allowed operators).
- Control the collector only through `start.sh`, `stop.sh`, `status.sh`.
- Run the pipeline every `pipeline_interval_s` in a background thread, with a lock to prevent overlapping runs.
- `GET /api/metrics` must downsample by range (for example 1 hour to raw, 24 hours to 1-minute averages) so responses stay small. Coordinate benchmarks with P5.
- API tests with the Flask test client: status, start/stop, config validation (rejecting bad values), alerts endpoints.
- Acceptance: all endpoints return the documented JSON; invalid input returns a 400 with a message and changes nothing.

### P4: Frontend

**Requirements (`web/`)**

- Pages: **index** (control panel and status cards), **trends** (charts), **alerts** (history), **processes** (top consumers), **settings** (thresholds, interval, notification method).
- Control panel: Start and Stop buttons, a status light (running, stopped, last sample time), "Refresh analysis now", and a "Send test alert" button.
- Status cards for CPU, memory, disk and temperature that turn green, amber or red against the thresholds from `/api/config`, so visuals and alerts always agree.
- Live charts that poll the API every few seconds. Use Chart.js saved locally in `web/static/` (no CDN, so the demo works offline). Ranges: 1 hour, 24 hours, 7 days.
- Alerts: a toast and a red badge when a new alert id appears, a history table (time, metric, value, severity, process), and an "acknowledge" button.
- Settings form with client-side validation and clear success and error messages.
- A gnuplot script (`scripts/plot_trends.gp`) that renders a daily summary SVG, shown on the trends page.
- Clean responsive CSS, large readable fonts for the demo projector, and a layout that survives an empty or failing API (show "no data yet", never a blank page).
- Work from mock JSON in `tests/fixtures/` until P3's API is ready; do not wait for the backend.
- Acceptance: every button works against the real API; the page recovers if the server restarts; no console errors.

### P5: Data, Testing and Profiling

**Requirements**

- `bin/gen_data.py`: generate synthetic raw CSVs in the exact collector format at 100k, 500k and 1M rows with a fixed random seed, including duplicates, gaps, out-of-range values, outlier spikes and slow disk growth. Also create small fixtures for tests. **Deliver the first version by Nov 7**, because P1, P2 and P3 all need it.
- `bin/stress.sh`: a bounded, safe CPU and memory load (duration argument, automatic cleanup, a hard cap on memory) to trigger alerts in the demo.
- Test runner (`run_tests.sh`) that runs all Python unit tests and shell tests and prints a clear pass/fail summary. Coordinate the test layout with everyone.
- **Profiling (2 marks):** run `cProfile` on the cleaner and analyzer at the three data sizes, record the top bottlenecks, then optimize: a pandas or vectorized `cleaner_fast.py`, incremental processing (only new rows, using a stored offset or timestamp), and downsampling in `/api/metrics`. Document each change with before and after timings.
- Unix-tools comparison: implement one statistic (for example mean CPU per hour) in awk and compare with the Python versions.
- Collector overhead: measure the collector's own CPU and memory use, and compare reading `/proc` against calling `top` for each sample.
- gnuplot plots: runtime against row count for each implementation, and a before/after bar chart.
- Keep a `docs/PROFILING.md` with commands, raw numbers and conclusions, which becomes the report's profiling chapter.
- Acceptance: results are reproducible with one script (`bin/run_benchmarks.sh`); the optimized version produces output identical to the plain version on the same input.

## 7. Pipeline task list

Dates are targets. "Dep" is what must exist first.

| ID | Task | Owner | Dep | Target |
| --- | --- | --- | --- | --- |
| T01 | Create repo, protect `main`, `.gitignore`, PR template, folder skeleton | P1 | none | Nov 6 |
| T02 | Write and agree `docs/INTERFACES.md` | P1 (all review) | T01 | Nov 7 |
| T03 | Default `sysmon.conf` and `thresholds.conf` | P1 | T02 | Nov 7 |
| T04 | Synthetic data generator v1 and small fixtures | P5 | T02 | Nov 7 |
| T05 | Check temperature sensor access on all laptops | P2 (all report) | none | Nov 7 |
| T06 | `collector.sh` (metrics, processes, rotation, simulate-temp) | P2 | T02 | Nov 8 |
| T07 | `start.sh`, `stop.sh`, `status.sh`, PID and lock handling | P2 | T06 | Nov 9 |
| T08 | `cleaner.py` plain version and cleaning report | P2 | T02, T04 | Nov 9 |
| T09 | Cleaner unit tests | P2 | T08 | Nov 10 |
| T10 | `analyzer.py` (stats, hourly, top processes) | P1 | T02, T04 | Nov 9 |
| T11 | Disk forecast, anomaly counts, analyzer tests | P1 | T10 | Nov 10 |
| T12 | `alerts.py` state machine, state file, tests | P3 | T02, T04 | Nov 9 |
| T13 | Alert delivery (`notify-send`, optional email or Telegram), no-data rule | P3 | T12 | Nov 11 |
| T14 | `server.py` skeleton and read-only endpoints | P3 | T02 | Nov 9 |
| T15 | Collector control, pipeline thread, config endpoints, API tests | P3 | T07, T14 | Nov 12 |
| T16 | Frontend pages and styling using mock JSON | P4 | T02 | Nov 9 |
| T17 | Live charts, status cards, control panel wired to the API | P4 | T14 | Nov 11 |
| T18 | Alert toast, badge, history, settings form | P4 | T15 | Nov 12 |
| T19 | gnuplot daily summary chart | P4 | T08 | Nov 12 |
| T20 | `run_pipeline.sh` and `sysmon` launcher | P1 | T08, T10, T12 | Nov 10 |
| T21 | `stress.sh` | P5 | T06 | Nov 9 |
| T22 | `test_integration.sh` and first end-to-end run | P1 | T20, T21 | Nov 11 |
| T23 | Test runner and test coverage check | P5 | T09, T11, T12 | Nov 11 |
| T24 | Profile cleaner and analyzer, record bottlenecks | P5 | T08, T10, T04 | Nov 11 |
| T25 | Optimized cleaner, incremental processing, downsampling | P5 (with P2, P3) | T24 | Nov 13 |
| T26 | awk comparison, collector overhead, benchmark plots | P5 | T24 | Nov 13 |
| T27 | README sections from every owner, merged README | All, P1 merges | T22 | Nov 14 |
| T28 | Report sections (one `.tex` per person) | All | T22 | Nov 14 |
| T29 | Beamer slides (one to two slides per person) | All, P1 builds | T28 | Nov 14 |
| T30 | `AI_USAGE.md` completed by everyone | All | none | Nov 14 |
| T31 | **Feature freeze**, bug fixes only after this | All | all above | Nov 14 night |
| T32 | Demo rehearsal (twice, timed) and fixes | All | T31 | Nov 15 |
| T33 | Final tag, clean-clone test, submission | P1 | T32 | Nov 16 before 10 AM |

## 8. Timeline

- **Nov 6 to 7:** repo, interfaces agreed, fixtures and generator v1, sensor check.
- **Nov 8 to 9:** every module has a first working version against fixtures; everything merged by the evening of Nov 9.
- **Nov 10 to 11:** first end-to-end run (collector, pipeline, dashboard). P1 logs what breaks.
- **Nov 12 to 13:** buttons, alert delivery, settings form, profiling and optimization.
- **Nov 14:** feature freeze at night. Documents drafted, tests passing, README complete.
- **Nov 15:** two timed rehearsals; fix only what they expose; tag the final commit; test from a fresh clone.
- **Nov 16 morning:** submit well before 10 AM and confirm the required submission method with the professor.

The end-semester exams are close, so the scope is deliberately staged. If time runs out, cut in this order: email or Telegram delivery, the settings form, the 7-day chart, the awk comparison. Never cut tests, profiling, the README or the demo script.

## 9. Git workflow

- `main` always runs. No direct pushes.
- One branch per task, named `feature/<topic>` or `fix/<topic>`.
- Small PRs (under about 300 lines) with a description of what changed and how to test it.
- P1 reviews and merges all PRs; P1's own PRs are reviewed by another teammate.
- Merge with merge commits or rebase-merge, not one giant squash, because the commit history is graded (2 marks). Use clear prefixes: `feat:`, `fix:`, `test:`, `docs:`.
- Commit early and often from day one, from every person. A history that is one person on one day looks bad.
- Never commit credentials, `data/` or virtual environments. Commit only small fixtures.

## 10. Grading checklist and who owns it

| Criterion (marks) | What we need | Owner |
| --- | --- | --- |
| Git history (2) | Meaningful commits and PRs from all five | Everyone, P1 enforces |
| README (2) | Covered topics, installation, usage; one section per module | P1 merges, everyone writes |
| Bash scripts (2) | `collector.sh`, `start/stop/status.sh`, `run_pipeline.sh`, `sysmon`, `stress.sh` | P2, P1, P5 |
| Test cases (1) | Unit tests per module plus the integration test | Everyone, P5 coordinates |
| Profiling (2) | `cProfile` results, bottlenecks, optimization, before/after | P5 |
| Execution (2) | Clean clone runs with `./sysmon` | P1 |
| LaTeX report and Beamer (4) | One section each, consistent style | Everyone, P1 builds |
| 5-minute demo (5) | Rehearsed, one minute per person | Everyone |

**Class topics covered** (needs at least five; to be confirmed with the professor whether Git, LaTeX and Bash count): Unix tools and process monitoring, Python and its libraries, HTML and CSS, gnuplot, profiling, plus Bash, Git and LaTeX.

## 11. Report, slides and demo

**Report:** `report/report.tex` pulls in one section per person with `\input{sections/...}`: design, how it works, tests, results. P5's profiling chapter is the technical centrepiece.

**Demo script (5 minutes):**

- 0:00 to 0:30, architecture on one slide (P1).
- 0:30 to 1:30, run `./sysmon`, press Start, show live data (P2 and P4).
- 1:30 to 3:00, press the stress test; CPU turns red, popup and alert row appear, then resolve (P3 and P5).
- 3:00 to 4:30, forecast and the profiling before/after chart (P1 and P5).
- 4:30 to 5:00, summary and what we would do next.

Rehearse twice with the real stopwatch, on the laptop and projector setup you will actually use. Have a simulated-temperature fallback and a recorded backup of the demo.

## 12. Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Temperature sensor missing on the demo laptop | `--simulate-temp` mode |
| `notify-send` fails in some sessions | Log and continue; web alerts still work |
| Teammates on non-Linux machines | Use WSL, a VM or Docker; decide on Nov 6 |
| Integration problems found late | First end-to-end run on Nov 10 to 11, not Nov 14 |
| Someone falls behind | P1 reassigns using the cut order in section 8 |
| Flask or pip not installed on the demo machine | Launcher installs into a virtual environment; test on a fresh clone |

## 13. AI usage declaration

The professor requires that AI use be declared and that every student can explain it. Keep `docs/AI_USAGE.md` with one entry per person: what the AI helped with, which files, and what was changed by hand. Every team member must be able to explain every line in their own module and the overall design. Do not copy code from other groups or the internet without citing it.
