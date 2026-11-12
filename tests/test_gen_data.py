"""Tests for bin/gen_data.py and Section 10 test fixtures."""

import csv
import filecmp
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GEN_SCRIPT = os.path.join(REPO_ROOT, "bin", "gen_data.py")
CLEANER_SCRIPT = os.path.join(REPO_ROOT, "src", "cleaner.py")
FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")


def test_gen_data_deterministic(tmp_path):
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    for out_dir in (out_a, out_b):
        res = subprocess.run(
            [sys.executable, GEN_SCRIPT, "--rows", "120", "--out", str(out_dir), "--seed", "7"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr

    files_a = sorted(p.name for p in out_a.iterdir())
    files_b = sorted(p.name for p in out_b.iterdir())
    assert files_a == files_b
    assert any(name.startswith("metrics_") for name in files_a)
    assert any(name.startswith("processes_") for name in files_a)
    for name in files_a:
        assert filecmp.cmp(out_a / name, out_b / name, shallow=False)


def test_gen_data_faults_and_cleaner_integration(tmp_path):
    for rows_n in (150, 300):
        raw_dir = tmp_path / f"raw_{rows_n}"
        clean_csv = tmp_path / f"clean_{rows_n}.csv"
        report_json = tmp_path / f"report_{rows_n}.json"

        res = subprocess.run(
            [sys.executable, GEN_SCRIPT, "--rows", str(rows_n), "--out", str(raw_dir), "--seed", "1"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr

        metric_files = sorted(raw_dir.glob("metrics_*.csv"))
        proc_files = sorted(raw_dir.glob("processes_*.csv"))
        assert metric_files and proc_files

        with open(metric_files[0], newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows[0]) == 16
        assert any(r[13] == "" and r[14] == "" and r[15] == "" for r in rows[1:])

        with open(proc_files[0], newline="", encoding="utf-8") as f:
            prows = list(csv.reader(f))
        assert len(prows[0]) == 6

        res_clean = subprocess.run(
            [
                sys.executable, CLEANER_SCRIPT,
                "--input-dir", str(raw_dir),
                "--output", str(clean_csv),
                "--report", str(report_json),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_clean.returncode == 0, res_clean.stderr

        with open(report_json, encoding="utf-8") as f:
            report = json.load(f)
        assert report["dropped_duplicates"] >= 1
        assert report["dropped_invalid"] >= 1
        assert report["filled"] >= 1
        assert report["gaps"] >= 1
        assert report["outliers"] >= 1


def test_gen_data_clean_flag_and_validation(tmp_path):
    clean_dir = tmp_path / "clean_raw"
    res = subprocess.run(
        [sys.executable, GEN_SCRIPT, "--rows", "100", "--out", str(clean_dir), "--clean", "--seed", "3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr

    clean_csv = tmp_path / "out.csv"
    report_json = tmp_path / "rep.json"
    subprocess.run(
        [
            sys.executable, CLEANER_SCRIPT,
            "--input-dir", str(clean_dir),
            "--output", str(clean_csv),
            "--report", str(report_json),
        ],
        check=True,
    )
    with open(report_json, encoding="utf-8") as f:
        report = json.load(f)
    assert report["rows_read"] == 100
    assert report["dropped_duplicates"] == 0
    assert report["dropped_invalid"] == 0
    assert report["filled"] == 0
    assert report["gaps"] == 0

    for bad_args in (["--rows", "0"], ["--interval", "0"], ["--interval", "-5"]):
        bad = subprocess.run(
            [sys.executable, GEN_SCRIPT, "--out", str(tmp_path / "bad"), *bad_args],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad.returncode != 0


def test_section10_fixtures_present():
    expected = [
        "raw_metrics.csv",
        "raw_processes.csv",
        "clean_metrics.csv",
        "stats.json",
        "alerts.csv",
        "system_info.json",
        "thresholds.conf",
    ]
    for name in expected:
        path = os.path.join(FIXTURES_DIR, name)
        assert os.path.isfile(path) and os.path.getsize(path) > 0, f"Missing fixture: {name}"
