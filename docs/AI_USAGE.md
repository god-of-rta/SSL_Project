# AI Usage Declaration

Project: SysMon  · Last updated: 15/11/2026

## Policy
We used AI tools to assist with this project. Every member understands and can
explain the code and documents they submitted.

## Per-person log
### Varun (P1: Integrator + Analysis)
- **Tools:** Claude, Antigravity
- **What I used AI for:** System architecture planning, statistical anomaly formulas in `src/analyzer.py`, test scaffolding, and pipeline runner automation.
- **What the AI produced vs. what I wrote or changed myself:** AI drafted initial shell scripts and mathematical routines; I resolved merge conflicts, fixed edge cases, and implemented hardware optimizations.
- **How I checked it:** Ran pytest test suites (`test_analyzer.py`, `test_pipeline.py`) and verified end-to-end `./sysmon` execution.

### Harsha (P2: Collector + Cleaner)
- **Tools:** Gemini, Antigravity
- **What I used AI for:** Shell sampling logic for `bin/collector.sh`, control scripts (`start.sh`, `stop.sh`, `status.sh`), data cleaning in `src/cleaner.py`, and LaTeX/Beamer slides.
- **What the AI produced vs. what I wrote or changed myself:** AI drafted shell loops, CSV parsing, and gap-filling logic; I resolved Windows/Git Bash compatibility, race conditions, and signal handling.
- **How I checked it:** Validated with `pytest tests/test_cleaner.py` and `pytest tests/test_collector.py`, verifying live CSV outputs and collector lifecycle.

### Prerit (P3: Frontend UI/UX)
- **Tools:** ChatGPT
- **What I used AI for:** Designing the frontend UI/UX layout, HTML5/CSS3 dashboard structure, and Chart.js telemetry visualization components.
- **What the AI produced vs. what I wrote or changed myself:** AI suggested dashboard templates and styling rules; I customized the dark-theme UI, integrated live REST API data feeds, and refined layout responsiveness.
- **How I checked it:** Tested web dashboard responsiveness across desktop browsers, verified live chart updates, and inspected browser console logs for errors.

### Amogh (P4: Backend & Systems)
- **Tools:** ChatGPT, Gemini
- **What I used AI for:** Designing Flask backend architecture, threading and lock management in `server.py`, subprocess execution, and the LaTeX/TikZ data flow diagram.
- **What the AI produced vs. what I wrote or changed myself:** AI provided boilerplate examples and initial diagram drafts; I implemented strict input validation (`isinstance`), configuration parsers, and safe process execution.
- **How I checked it:** Verified API endpoints with curl, tested daemon thread concurrency, and compiled LaTeX TikZ diagrams without errors.

### Amitha (P5: Testing & Documentation)
- **Tools:** Claude
- **What I used AI for:** LaTeX report and presentation modular assembly, merging individual `.tex` sections, and designing stress-testing simulation scripts.
- **What the AI produced vs. what I wrote or changed myself:** AI assisted with LaTeX formatting syntax and stress simulation templates; I resolved compilation conflicts, aligned cross-references, and executed multi-core stress tests.
- **How I checked it:** Compiled master `report.pdf` and `slides.pdf` cleanly using pdflatex, and validated system stability under simulated CPU/memory loads.

### Amulya (P6: Alerts & Profiling)
- **Tools:** Claude
- **What I used AI for:** Implementing rule-based threshold evaluation in `src/alerts.py` and benchmarking system execution profiles for the final report.
- **What the AI produced vs. what I wrote or changed myself:** AI suggested notification dispatch structures and profiling helper routines; I calibrated metric thresholds, tuned execution bottlenecks, and wrote alerts integration logic.
- **How I checked it:** Verified alert triggers against simulated threshold breaches, inspected log outputs, and measured runtime latency across collection cycles.

## Team-level use
The project plan, role split, task list and the first draft of INTERFACES.md
were drafted with Claude and then reviewed and adjusted by the team.
