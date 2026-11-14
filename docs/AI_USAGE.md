# AI Usage Declaration

Project: SysMon  · Last updated: 14/11/2026

## Policy
We used AI tools to assist with this project. Every member understands and can
explain the code and documents they submitted.

## Summary
| Person | Tools used | Used for | Files / sections |
|---|---|---|---|
| Varun | Claude, Antigravity | Architecture planning, interface contracts, statistical algorithms, pipeline automation, launcher,| `sysmon`, `bin/run_pipeline.sh`, `src/analyzer.py`, `tests/test_analyzer.py`, `tests/test_pipeline.py`, `docs/INTERFACES.md`|
| Harsha | Gemini | background collector, control scripts, cross-platform fallbacks | bin/collector.sh, bin/start.sh, bin/stop.sh, bin/status.sh |
| Prerit | | | |
| Amogh | | | |
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

### Harsha 
- **Tools:** Gemini
- **What I used AI for:** Shell script skeletons, cross-platform flock/ps fallbacks, and signal handling.
- **What the AI produced vs. what I wrote or changed myself:** Verified the scripts by running them and checking for deadlocks.
- **How I checked it:** Tested start/stop/status scripts and verified outputs in data/raw/metrics_*.csv and system_info.json.

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

## Not used for
- Final evaluation conclusions and individual test verification.
- Live demo script execution and interactive system presentation.