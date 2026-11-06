<<<<<<< HEAD
# SysMon — Unix System Monitoring Dashboard

> **Topic #6: Unix System Monitoring**  
> A lightweight, terminal-free system monitoring suite combining low-overhead Bash collectors, automated Python data cleaning & statistical forecasting, threshold-based alerting, and a local web dashboard.

---

## 📌 Overview

**SysMon** is designed to provide real-time and historical insights into Unix system performance without requiring manual terminal interaction. 

The system operates via an end-to-end pipeline:
1. **Background Collector (Bash):**  
2. **Data Cleaner (Python):**  
3. **Statistical Analyzer (Python):**  
4. **Alert Engine (Python):**  
5. **Backend & Dashboard (Flask & HTML/JS):**  

---

## 🏗️ Architecture & Data Flow


## 📂 Repository Layout

### Prerequisites

- Linux-based operating system (Ubuntu 20.04+, Debian, Arch, or WSL2 with Linux kernel)
- Bash 4+
- Python 3.8+ & `python3-venv`
- Standard Unix utilities 

### Installation & Launch

Clone the repository and run the launcher:
```bash
git clone https://github.com/god-of-rta/SSL_Project.git

```

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
