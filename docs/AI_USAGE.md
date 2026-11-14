# AI Usage Declaration

Project: SysMon  · Last updated: 14/11/2026

## Policy
We used AI tools to assist with this project. Every member understands and can
explain the code and documents they submitted.

## Summary
| Person | Tools used | Used for | Files / sections |
|---|---|---|---|
| Varun | Claude, Antigravity | Architecture planning, interface contracts, data analysis, execution pipeline automation, launcher, LaTex report and slides.
| Harsha | Gemini, Claude | background collector, control scripts, cross-platform fallbacks, data cleaner, unit tests, LaTeX report & slides | bin/collector.sh, bin/start.sh, bin/stop.sh, bin/status.sh, src/cleaner.py, tests/test_cleaner.py, tests/test_collector.py, report/sections/collector_cleaner.tex, report/sections/slides_collector_cleaner.tex |
| Prerit | | | |
| Amogh | Gemini, ChatGPT |Code Review, Library use, Tikz diagram adjustment |Server.py - Threading, Subprocess, Config-parser,Safety Checks using is-instance and optimisation of implemented code |
| Amitha | | | |
| Amulya | | | |

## Per-person log
### Varun (P1: Integrator + Analysis)
- **Tools:** Claude, Antigravity
- **What I used AI for:** 
  - Designing the system architecture in `docs/INTERFACES.md` and defining task breakdowns.
  - Mathematical formulations and anomaly detections in `src/analyzer.py`.
  - Scaffolding unit and edge-case test suites in `tests/test_analyzer.py` and `tests/test_pipeline.py`.
  - Shell syntax for the `./sysmon` master bootstrapper and `bin/run_pipeline.sh`.
  - Reviewing code from others before merging to main branch.
- **What the AI produced vs. what I wrote or changed myself:** 
  - AI assisted with the shell flag parsing, initial test fixtures, and formula drafting.
  - I verified the code, fixed any bugs that arose, and dealt with merge conflicts and system integration issues that arose during the course of the project.
  - I identified system bottlenecks during testing and formulated how to fix them.
- **How I checked it:** 
  - Ran the full automated test suite (`pytest tests/test_analyzer.py`, `pytest tests/test_pipeline.py`) confirming all tests pass.
  - Verified that `./sysmon` cleanly boots on a fresh clone without manual intervention.

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
- **Tools:** ChatGPT, Gemini.
- **What I used AI for:**
  - Creating and refining the LaTeX/TikZ diagram showing the backend architecture and communication flow.
  - Understanding and implementing the use of Python libraries:
    - `threading` — running the periodic pipeline task in a background thread and using `threading.Lock()` to prevent overlapping pipeline executions.
    - `subprocess` — executing the predefined shell scripts (`start.sh`, `stop.sh`, `status.sh`, and `run_pipeline.sh`) and the Python alert script from the backend.
    - `configparser` — reading and updating settings in `sysmon.conf` and `thresholds.conf`.
  - Improving input validation and safer request handling using `isinstance()` checks, type checks, and value-range validation.
  - Understanding the purpose and usage of library functions while developing the backend.

- **What the AI produced vs. what I wrote or changed myself:**
  - AI provided explanations, implementation suggestions, examples, and initial diagram drafts.
  - I reviewed the suggestions, selected what was relevant to the project, and adapted the code and diagram to match the actual backend.
  - I changed and integrated the validation logic, script execution flow, configuration handling, and aggregation choices instead of using suggested output without review.

- **How I checked it:**
  - I read relevant documentation for the Python libraries and functions to understand their purpose, parameters, return values, and behaviour.
  - I compared the documentation with how each function was used in `server.py` and reviewed the surrounding code before incorporating suggestions.
  - I compiled the LaTeX source and visually checked the generated diagram and report layout.


### Amitha 
- **Tools:** 
- **What I used AI for:** 
- **What the AI produced vs. what I wrote or changed myself:** 
- **How I checked it:**

### Amulya 
- **Tools:** 
- **What I used AI for:** 
- **What the AI produced vs. what I wrote or changed myself:** 
- **How I checked it:**

## Team-level use
The project plan, role split, task list and the first draft of INTERFACES.md
were drafted with Claude and then reviewed and adjusted by the team.
