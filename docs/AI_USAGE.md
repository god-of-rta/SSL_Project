# AI Usage Declaration

Project: SysMon  · Last updated: 14/11/2026

## Policy
We used AI tools to assist with this project. Every member understands and can
explain the code and documents they submitted.

## Summary
| Person | Tools used | Used for | Files / sections |
|---|---|---|---|
| Varun | Claude | planning, design review, debugging | docs/INTERFACES.md  |
| Harsha | Gemini, Claude | background collector, control scripts, cross-platform fallbacks, data cleaner, unit tests, LaTeX report & slides | bin/collector.sh, bin/start.sh, bin/stop.sh, bin/status.sh, src/cleaner.py, tests/test_cleaner.py, tests/test_collector.py, report/sections/collector_cleaner.tex, report/sections/slides_collector_cleaner.tex |

## Per-person log
### Varun 
- **Tools:** Claude
- **What I used AI for:** planning, design review, debugging and making the docs
- **What the AI produced vs. what I wrote or changed myself:** I made the plan and the docs. 
- **How I checked it:**

### Harsha (P2: Collector + Cleaner)
- **Tools:** Gemini, Claude
- **What I used AI for:**
  - Shell script architecture and metric sampling logic for `bin/collector.sh`, POSIX signal trapping, low-overhead direct `/proc` and `/sys` parsing, and GPU power-state detection.
  - Cross-platform process and lock management fallbacks for Git Bash / MSYS2 / Windows environments.
  - Lifecycle management scripts (`bin/start.sh`, `bin/stop.sh`, `bin/status.sh`) with strict interface exit codes and stale PID validation.
  - Plain-Python data cleaning pipeline (`src/cleaner.py`), including CSV schema validation, forward-fill gap interpolation ($\le 3$ samples), rolling-median outlier flagging, and automated retention pruning.
  - Comprehensive unit test suites in `tests/test_collector.py` and `tests/test_cleaner.py`.
  - LaTeX report chapter (`report/sections/collector_cleaner.tex`) and Beamer presentation slides (`report/sections/slides_collector_cleaner.tex`).
- **What the AI produced vs. what I wrote or changed myself:**
  - AI drafted initial shell loops, awk parsing routines, Python data validation structures, gap filling logic, and LaTeX report sections.
  - I reviewed, tuned, and verified the scripts: fixed cross-platform Git Bash / MSYS2 compatibility issues (line endings, lock fallbacks, netstat fallback), tested the lifecycle scripts against race conditions, confirmed mathematical correctness of CPU/network delta calculations, verified that outlier flags do not drop real workload spikes, and ran full test suite checks.
- **How I checked it:**
  - Ran unit tests: `pytest tests/test_collector.py` and `pytest tests/test_cleaner.py` (both passing 100%).
  - Verified live metrics in `data/raw/metrics_YYYYMMDD.csv` (16 columns), `data/raw/processes_YYYYMMDD.csv` (6 columns), `data/system_info.json`, `data/clean/metrics.csv` (19 columns), and `data/clean/cleaning_report.json`.
  - Validated collector lifecycle under multiple concurrent starts, abrupt SIGTERM, and stale PID recovery.

### Prerit 
- **Tools:** 
- **What I used AI for:** 
- **What the AI produced vs. what I wrote or changed myself:** 
- **How I checked it:**

### Amogh 
- **Tools:** 
- **What I used AI for:** 
- **What the AI produced vs. what I wrote or changed myself:** 
- **How I checked it:**

### Varun 
- **Tools:** 
- **What I used AI for:** 
- **What the AI produced vs. what I wrote or changed myself:** 
- **How I checked it:**

## Team-level use
The project plan, role split, task list and the first draft of INTERFACES.md
were drafted with Claude and then reviewed and adjusted by the team.

## Not used for
(e.g. report conclusions, demo script, anything else you want to state)
