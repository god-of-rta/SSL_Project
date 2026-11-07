# SysMon Task List

Written Saturday Nov 7, 2026, about 6:30 PM. **Submission deadline: Monday Nov 16, 10:00 AM.** Feature freeze: **Saturday Nov 14, night.** This file replaces the dates in the plan document. If anything here disagrees with `docs/INTERFACES.md`, `INTERFACES.md` wins.

## 1. What we collect and compute (confirmed)

**Every sample (default every 5 s), 16 columns:** time (`epoch`, `iso_time`); CPU busy %; memory used %, memory available MB; swap used %; disk used % (`/`); load average (1, 5, 15 min); network receive and transmit kbps; CPU temperature; **GPU utilization %, GPU memory used %, GPU temperature** (optional, empty if no GPU).

**Every sample, separate file:** top 5 processes by CPU (pid, name, CPU %, memory %).

**Once at start:** system info (CPU model and core count, total RAM, disk size, kernel, CPU temperature limit if exposed, GPU name and vendor).

**Computed by the analyzer** (over the last hour, last 24 hours and all time): mean, min, max, 95th percentile and standard deviation per metric; hourly averages; top processes; a disk-full forecast; anomaly counts per metric; data-quality counts (gaps and filled samples).

**Alerts** run on CPU, memory, swap, disk, load, CPU temperature and GPU temperature (plus a "collector stopped sending data" rule). Thresholds are user-defined on the Settings page, with defaults that adapt to the machine (core count, CPU temperature limit). There are separate warning and critical rules, and a demo profile with low thresholds.

**Later, only if time allows:** disk read and write speed, battery.

## 2. Milestones

| When                       | Milestone                  | Done when                                                                                        |
|----------------------------|----------------------------|--------------------------------------------------------------------------------------------------|
| **Sat Nov 7, night**       | M0: repo ready             | Skeleton, configs and docs committed, `main` protected, everyone cloned and set their git name   |
| **Sun Nov 8, evening**     | M1: contracts and fixtures | `INTERFACES.md` P5's generator and fixtures merged; server skeleton merged; collector v1 running |
| **Tue Nov 10, night**      | M2: all modules v1 merged  | Collector, cleaner, analyzer, alerts, API and pages each work on their own against fixtures      |
| **Wed Nov 11, night**      | M3: first end-to-end run   | The real collector feeds the pipeline and the dashboard shows live data                          |
| **Fri Nov 13, night**      | M4: features complete      | Buttons, alert delivery, settings page, profiling results and plots done                         |
| **Sat Nov 14, night**      | Freeze                     | Only bug fixes after this. README, report and slides drafts complete, tests passing              |
| **Sun Nov 15**             | Rehearsal                  | Two timed demo runs, fresh-clone test on a second laptop, final tag                              |
| **Mon Nov 16, by 8:30 AM** | Submit                     | Submitted well before 10:00 AM                                                                   |

**If we fall behind, cut in this order:** email and Telegram delivery, adding new alert rules from the Settings page (keep editing existing ones), the 7-day chart, the awk comparison, GPU alerts. Never cut: tests, profiling, README, demo script, or the alert you will show live.

## 3. Rules for everyone

- Branch per task, small PRs, no pushes to `main`, commit messages start with `feat:`, `fix:`, `test:` or `docs:`. Add "AI used: yes/no, and for what" to every PR.
- P1 reviews PRs within 12 hours. Anyone can review anyone, but only P1 merges.
- Commit every day. Update your own entry in `docs/AI_USAGE.md` as you go.
- If you are blocked for more than a few hours, say so in the group chat.

## 4. Tasks by person

### P1: Integrator + Analysis

| #     | Task                                                                                                                                                                          | Due                                      | Done when                                                  |
|-------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------|------------------------------------------------------------|
| P1-1  | Repo skeleton, `.gitignore`, default configs, PR template, protect `main`, add collaborators, tag `v0-skeleton`                                                               | Sat Nov 7, night                         | Everyone can clone and branch                              |
| P1-2  | Commit `INTERFACES.md` v1.1 and `TASKS.md`, post the group message                                                                                                            | Sat night; sign-off by Sun Nov 8, noon   | Sign-off table filled in                                   |
| P1-3  | Create GitHub issues from this list with owner labels                                                                                                                         | Sun Nov 8                                | Each person sees their own issues                          |
| P1-4  | `analyzer.py` v1: windows, hourly means, top processes (start on a small hand-made clean CSV, switch to P5's fixtures when they land)                                         | Mon Nov 9, night                         | Output matches the `stats.json` shape, empty input handled |
| P1-5  | Disk forecast, anomaly counts, analyzer unit tests                                                                                                                            | Tue Nov 10, noon                         | Tests pass, includes GPU columns with empty values         |
| P1-6  | `run_pipeline.sh` with `pipeline_status.json`, and the `sysmon` launcher (virtual environment, install requirements, first-run `--init-defaults`, start server, open browser) | Tue Nov 10, night                        | One command starts everything on a fresh clone             |
| P1-7  | `test_integration.sh` and the first end-to-end run                                                                                                                            | Wed Nov 11, night (M3)                   | Alert row appears after `stress.sh`                        |
| P1-8  | Review PRs and run the integration test after every merge                                                                                                                     | daily                                    | `main` always works                                        |
| P1-9  | README skeleton, LaTeX report skeleton (one section file per person), Beamer skeleton                                                                                         | Thu Nov 12                               | `report/` builds with placeholders                         |
| P1-10 | Collect everyone's sections, build report and slides, finish README                                                                                                           | Sat Nov 14, night                        | PDFs build with no errors                                  |
| P1-11 | Demo script, fresh-clone test, final tag, submit                                                                                                                              | Sun Nov 15, submit Mon Nov 16 by 8:30 AM | Submitted                                                  |

### P2: Collector + Cleaner

| #    | Task                                                                                                                                                                                        | Due               | Done when                                                              |
|------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|------------------------------------------------------------------------|
| P2-1 | Run `cat /sys/class/thermal/thermal_zone*/temp`, `nvidia-smi` and `lspci \| grep -E "VGA\|3D"` on your laptop and post the results in the chat. Say if you are on native Linux, WSL or a VM | Sat Nov 7, night  | Whole team knows what hardware we have                                 |
| P2-2 | `collector.sh` v1: correct 16-column header and rows for CPU, memory, swap, disk, load, network (temperature and GPU empty for now), interval from config, daily files                      | Sun Nov 8, night  | A 10-minute run, every row has 16 fields                               |
| P2-3 | Temperature (hottest zone, `sensors` fallback, `--simulate-temp`) and the top-5 processes file                                                                                              | Mon Nov 9, noon   | Matches section 3.1 and 3.2 of `INTERFACES.md`                         |
| P2-4 | GPU detection (NVIDIA, then AMD, then none), `collect_gpu`, `gpu_poll_every`, and `system_info.json`                                                                                        | Mon Nov 9, night  | Empty GPU fields (not errors) on a machine without a GPU               |
| P2-5 | `start.sh`, `stop.sh`, `status.sh`: PID file, lock, SIGTERM handling, log file                                                                                                              | Mon Nov 9, night  | Second start is refused, stop leaves no process                        |
| P2-6 | `cleaner.py` (plain `csv` version): sort, dedupe, validate, short-gap fill, long-gap flag, outlier flag, cleaning report                                                                    | Tue Nov 10, night | Output follows section 4 exactly, running twice gives identical output |
| P2-7 | Cleaner unit tests using P5's fixtures                                                                                                                                                      | Wed Nov 11, noon  | Duplicate, gap, out-of-range, spike and empty-GPU cases covered        |
| P2-8 | Fix problems from the first end-to-end run, help P5 measure collector overhead                                                                                                              | Thu Nov 12        | Collector fixes merged, overhead numbers measured and documented       |
| P2-9 | README, report and slide sections for collector and cleaner, `AI_USAGE.md` entry                                                                                                            | Fri Nov 13        | Merged                                                                 |

### P3: Frontend

| #    | Task                                                                                                                                         | Due               | Done when                                          |
|------|----------------------------------------------------------------------------------------------------------------------------------------------|-------------------|----------------------------------------------------|
| P3-1 | Clone, read `INTERFACES.md` section 9 and your role, sign off on interfaces                                                                  | Sat Nov 7, night  | Interfaces signed off, repo cloned and verified    |
| P3-2 | Page skeleton: index, trends, alerts, processes, settings, with shared navigation and `style.css`                                            | Sun Nov 8, night  | All five pages open and link to each other         |
| P3-3 | Mock JSON files in `tests/fixtures/` for every API response in section 9                                                                     | Sun Nov 8, night  | Frontend runs with no backend                      |
| P3-4 | Chart.js saved locally, status cards (CPU, memory, disk, CPU temp, GPU) with green, amber and red colouring, trends page charts on mock data | Tue Nov 10, night | Charts render, GPU shows "n/a" when null           |
| P3-5 | Control panel wired to the real API: Start, Stop, Refresh now, Send test alert, status light                                                 | Wed Nov 11, night | Buttons work against P4's server                   |
| P3-6 | Alerts page: toast and badge on a new alert, history table, acknowledge                                                                      | Thu Nov 12        | Toast and badge work, history table updates on ack |
| P3-7 | Settings page: edit thresholds and interval, GPU toggle, "Apply demo profile" and "Reset to defaults"                                        | Fri Nov 13, night | Saves and shows validation errors                  |
| P3-8 | gnuplot daily-summary SVG script, shown on the trends page                                                                                   | Fri Nov 13, night | SVG generates cleanly and displays on trends page  |
| P3-9 | Polish, empty and error states, large fonts for the projector. README, report and slide sections, `AI_USAGE.md` entry                        | Sat Nov 14        | No console errors, no blank pages                  |

### P4: Backend

| #    | Task                                                                                                                                                                       | Due                      | Done when                                                         |
|------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------|-------------------------------------------------------------------|
| P4-1 | Clone, read `INTERFACES.md` section 9 and your role, sign off on interfaces                                                                                                | Sat Nov 7, night         | Interfaces signed off, repo cloned and verified                   |
| P4-2 | `server.py` skeleton: Flask on `127.0.0.1`, serves `web/`, hardcoded `GET /api/status`                                                                                     | Sun Nov 8, night         | P3 can call it                                                    |
| P4-3 | Read-only endpoints: `stats`, `alerts` (merge `acks.json`), `system`, `metrics` with downsampling (1h raw, 24h per minute, 7d per 10 minutes). Empty-state JSON everywhere | Mon Nov 9, night         | Works on P5's fixtures                                            |
| P4-4 | `GET` and `POST /api/config` with validation, `POST /api/config/profile`                                                                                                   | Tue Nov 10, night        | Bad values return 400 with a `field`                              |
| P4-5 | Collector control through `start.sh`, `stop.sh`, `status.sh`; pipeline thread with a lock reading `pipeline_status.json`; ack and test-alert endpoints                     | Wed Nov 11, night        | Start and Stop work (use stubs until P2's scripts land Mon night) |
| P4-6 | API tests with Flask's test client                                                                                                                                         | Thu Nov 12               | Status, control, config validation, alerts covered                |
| P4-7 | With P5, optimize `/api/metrics` for large data. README, report and slide sections, `AI_USAGE.md` entry                                                                    | Fri Nov 13 to Sat Nov 14 | Before and after timings recorded                                 |

### P5: Alerts + Data generation + Profiling

| #     | Task                                                                                                                                                                                                                                        | Due                | Done when                                                                 |
|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------|---------------------------------------------------------------------------|
| P5-1  | Clone, read `INTERFACES.md` sections 3 to 6 and your role, sign off on interfaces                                                                                                                                                           | Sat Nov 7, night   | Interfaces signed off, repo cloned and verified                           |
| P5-2  | **Top priority.** `gen_data.py` (16 columns, fixed seed, `--rows`, injected duplicate, short gap, long gap, out-of-range value, spike, empty GPU rows) and small fixtures: raw, clean, stats, alerts, `system_info.json`, `thresholds.conf` | Sun Nov 8, evening | P1, P2 and P4 can use them                                                |
| P5-3  | `stress.sh`: CPU and memory load, 30 s default, 120 s hard maximum, cleans up after itself                                                                                                                                                  | Mon Nov 9, night   | Reliably triggers the demo-profile alert                                  |
| P5-4  | `alerts.py` core: parse rule sections, state machine (OK, FIRING, RESOLVED), sustained and cooldown, `alerts.csv`, state file, unit tests                                                                                                   | Tue Nov 10, noon   | One sustained breach fires exactly once; a one-sample spike does not fire |
| P5-5  | `--init-defaults` with machine-aware defaults and the demo profile                                                                                                                                                                          | Tue Nov 10, night  | Matches section 6.2                                                       |
| P5-6  | Alert delivery: `notify-send`, the no-data rule, `--test`, optional email or Telegram                                                                                                                                                       | Wed Nov 11, night  | Desktop popup appears, failures are logged not fatal                      |
| P5-7  | `run_tests.sh` that runs every unit and shell test with a clear summary                                                                                                                                                                     | Wed Nov 11         | Single command runs all unit and shell tests with exit code 0             |
| P5-8  | Profile cleaner and analyzer with `cProfile` at 100k, 500k and 1M rows. Record the top bottlenecks                                                                                                                                          | Thu Nov 12         | Numbers in `docs/PROFILING.md`                                            |
| P5-9  | Optimize: pandas or vectorized `cleaner_fast.py`, incremental processing; awk comparison; collector overhead; gnuplot benchmark plots; one script (`run_benchmarks.sh`) that reproduces everything                                          | Fri Nov 13, night  | Optimized output identical to the plain version                           |
| P5-10 | README, report and slide sections for alerts, data and profiling, `AI_USAGE.md` entry                                                                                                                                                       | Sat Nov 14         | PR merged, slides and report sections complete                            |

## 5. Everyone, final days

| When                  | What                                                                                                                          |
|-----------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Sat Nov 14, night     | Feature freeze. Your README, report and slide sections merged.                                                                |
| Sun Nov 15, morning   | Run the demo twice with a stopwatch (5 minutes), one minute each. Fix only what breaks.                                       |
| Sun Nov 15, afternoon | Fresh-clone test on a laptop that has never run the project: `git clone`, `./sysmon`, press Start, trigger an alert.          |
| Sun Nov 15, night     | P1 tags the final commit. Everyone confirms their `AI_USAGE.md` entry is complete and that they can explain their own module. |
