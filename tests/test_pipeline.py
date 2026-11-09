"""Tests for run_pipeline.sh and sysmon launcher (P1)."""

import json
import os
import subprocess
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PIPELINE_SCRIPT = os.path.join(REPO_ROOT, "bin", "run_pipeline.sh")
SYSMON_SCRIPT = os.path.join(REPO_ROOT, "sysmon")
DATA_DIR = os.path.join(REPO_ROOT, "data")


def test_run_pipeline_execution():
    res = subprocess.run([PIPELINE_SCRIPT], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"run_pipeline.sh failed: {res.stderr}"

    status_file = os.path.join(DATA_DIR, "pipeline_status.json")
    assert os.path.exists(status_file), "pipeline_status.json was not created"

    with open(status_file, "r") as f:
        status = json.load(f)

    assert "last_run_epoch" in status and isinstance(status["last_run_epoch"], int)
    assert "duration_s" in status and isinstance(status["duration_s"], (int, float))
    assert "ok" in status and status["ok"] is True
    assert status["failed_stage"] is None

    log_file = os.path.join(DATA_DIR, "pipeline.log")
    assert os.path.exists(log_file), "pipeline.log was not created"
    with open(log_file, "r") as f:
        log_content = f.read()
    assert "Pipeline run started" in log_content
    assert "Pipeline finished" in log_content


def test_sysmon_launcher_cli():
    # Test --help
    res_help = subprocess.run([SYSMON_SCRIPT, "--help"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res_help.returncode == 0
    assert "Usage: ./sysmon" in res_help.stdout

    # Test --init-defaults
    res_init = subprocess.run([SYSMON_SCRIPT, "--init-defaults"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res_init.returncode == 0
    assert os.path.exists(os.path.join(REPO_ROOT, "config", "sysmon.conf"))
    assert os.path.exists(os.path.join(REPO_ROOT, "config", "thresholds.conf"))
