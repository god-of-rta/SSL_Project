"""Unit tests for SysMon Analyzer (P1: Integrator + Analysis).

Tests statistical windows, empty/missing inputs, GPU empty handling,
gap skipping, disk forecast linear regression, z-score anomalies,
hourly averages, top processes, and CLI contracts.
"""

import csv
import json
import os
import subprocess
import tempfile
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ANALYZER_SCRIPT = os.path.join(REPO_ROOT, "src", "analyzer.py")
PYTHON_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
if not os.path.exists(PYTHON_BIN):
    PYTHON_BIN = "python3"

METRICS_HEADER = [
    "epoch",
    "iso_time",
    "cpu_pct",
    "mem_used_pct",
    "mem_avail_mb",
    "swap_used_pct",
    "disk_used_pct",
    "load1",
    "load5",
    "load15",
    "net_rx_kbps",
    "net_tx_kbps",
    "cpu_temp_c",
    "gpu_util_pct",
    "gpu_mem_used_pct",
    "gpu_temp_c",
    "is_filled",
    "is_gap",
    "is_outlier",
]

PROCESSES_HEADER = ["epoch", "pid", "command", "cpu_pct", "mem_pct", "rank"]


def run_analyzer(input_path, proc_dir, output_path, *extra_args):
    cmd = [
        PYTHON_BIN,
        ANALYZER_SCRIPT,
        "--input",
        input_path,
        "--processes-dir",
        proc_dir,
        "--output",
        output_path,
    ] + list(extra_args)
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


def test_missing_or_empty_input():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "nonexistent.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0, f"Expected 0 on missing input: {res.stderr}"

        with open(output_file, "r") as f:
            stats = json.load(f)

        assert stats["latest_epoch"] is None
        assert stats["top_processes"] == []
        assert stats["disk_forecast"]["days_to_full"] is None
        assert stats["disk_forecast"]["slope_pct_per_day"] == 0.0
        assert stats["data_quality"]["gaps"] == 0
        assert stats["data_quality"]["filled"] == 0
        assert "1h" in stats["windows"]
        assert "all" in stats["windows"]


def test_header_only_input():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)

        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        assert stats["latest_epoch"] is None
        assert stats["hourly"]["cpu_pct"] == []


def test_single_row():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            writer.writerow([
                "1700000000",
                "2023-11-14T22:13:20Z",
                "25.0",
                "50.0",
                "8192",
                "0.0",
                "60.0",
                "1.5",
                "1.0",
                "0.8",
                "200.0",
                "100.0",
                "45.0",
                "",
                "",
                "",
                "0",
                "0",
                "0",
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        assert stats["latest_epoch"] == 1700000000
        cpu_stats = stats["windows"]["1h"]["cpu_pct"]
        assert cpu_stats["mean"] == 25.0
        assert cpu_stats["min"] == 25.0
        assert cpu_stats["max"] == 25.0
        assert cpu_stats["std"] == 0.0

        # GPU metrics should be null since fields were empty
        assert stats["windows"]["1h"]["gpu_util_pct"] is None
        assert stats["windows"]["1h"]["gpu_mem_used_pct"] is None
        assert stats["windows"]["1h"]["gpu_temp_c"] is None

        # Disk forecast with single point cannot fit a slope
        assert stats["disk_forecast"]["days_to_full"] is None
        assert stats["disk_forecast"]["slope_pct_per_day"] == 0.0


def test_gpu_columns_empty_and_valid():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        # Machine without GPU: GPU columns empty
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            for i in range(5):
                writer.writerow([
                    str(1700000000 + i * 5),
                    f"2023-11-14T22:13:{20 + i * 5:02d}Z",
                    str(10.0 + i),
                    str(40.0 + i),
                    "8000",
                    "0.0",
                    str(50.0 + i * 0.1),
                    "1.0",
                    "1.0",
                    "1.0",
                    "100.0",
                    "50.0",
                    "42.0",
                    "",  # gpu_util_pct empty
                    "",  # gpu_mem_used_pct empty
                    "",  # gpu_temp_c empty
                    "0",
                    "0",
                    "0",
                ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        win1h = stats["windows"]["1h"]
        assert win1h["gpu_util_pct"] is None
        assert win1h["gpu_mem_used_pct"] is None
        assert win1h["gpu_temp_c"] is None
        assert win1h["cpu_pct"]["min"] == 10.0
        assert win1h["cpu_pct"]["max"] == 14.0


def test_gap_skipping_and_data_quality():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            # Row 1: normal, cpu 10
            writer.writerow([
                "1700000000", "2023-11-14T22:00:00Z", "10.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])
            # Row 2: is_filled=1, cpu 20 (should be included in stats)
            writer.writerow([
                "1700000005", "2023-11-14T22:00:05Z", "20.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "1", "0", "0"
            ])
            # Row 3: is_gap=1, cpu 1000 (must be SKIPPED from stats!)
            writer.writerow([
                "1700000060", "2023-11-14T22:01:00Z", "1000.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "1", "0"
            ])
            # Row 4: normal, cpu 30
            writer.writerow([
                "1700000065", "2023-11-14T22:01:05Z", "30.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        # Gap count should be 1, filled count should be 1
        assert stats["data_quality"]["gaps"] == 1
        assert stats["data_quality"]["filled"] == 1

        # The 1000.0 cpu value on the gap row must not be in stats
        cpu_stats = stats["windows"]["all"]["cpu_pct"]
        # Expected rows in stats: 10.0, 20.0, 30.0 -> mean = 20.0, max = 30.0
        assert cpu_stats["mean"] == 20.0
        assert cpu_stats["max"] == 30.0
        assert cpu_stats["min"] == 10.0


def test_time_windows():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        base_epoch = 1700000000
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            # Sample 2 hours ago: cpu = 80
            writer.writerow([
                str(base_epoch - 7200), "2023-11-14T20:00:00Z", "80.0", "40.0", "8000",
                "0.0", "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])
            # Sample 30 min ago: cpu = 20
            writer.writerow([
                str(base_epoch - 1800), "2023-11-14T21:30:00Z", "20.0", "40.0", "8000",
                "0.0", "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])
            # Sample at latest_epoch: cpu = 30
            writer.writerow([
                str(base_epoch), "2023-11-14T22:00:00Z", "30.0", "40.0", "8000",
                "0.0", "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        # 1h window should only have 20.0 and 30.0 (mean = 25.0, max = 30.0)
        win_1h = stats["windows"]["1h"]["cpu_pct"]
        assert win_1h["mean"] == 25.0
        assert win_1h["max"] == 30.0

        # 24h window should have all 3 samples: 80, 20, 30 (mean = 43.33, max = 80.0)
        win_24h = stats["windows"]["24h"]["cpu_pct"]
        assert win_24h["mean"] == 43.33
        assert win_24h["max"] == 80.0


def test_disk_forecast():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        base_epoch = 1700000000
        # Positive growth: disk grows 5% in 1 day (86400 seconds)
        # Starting from 50% at day 0 to 55% at day 1
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            writer.writerow([
                str(base_epoch), "2023-11-14T00:00:00Z", "10.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])
            writer.writerow([
                str(base_epoch + 86400), "2023-11-15T00:00:00Z", "10.0", "40.0", "8000", "0.0",
                "55.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        df = stats["disk_forecast"]
        assert df["slope_pct_per_day"] == 5.0
        # At 55%, remaining to 100% is 45%. 45 / 5.0 = 9.0 days
        assert df["days_to_full"] == 9.0

        # Now test falling disk usage (slope <= 0 -> days_to_full is null)
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            writer.writerow([
                str(base_epoch), "2023-11-14T00:00:00Z", "10.0", "40.0", "8000", "0.0",
                "55.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])
            writer.writerow([
                str(base_epoch + 86400), "2023-11-15T00:00:00Z", "10.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)
        assert stats["disk_forecast"]["days_to_full"] is None
        assert stats["disk_forecast"]["slope_pct_per_day"] == -5.0


def test_anomalies_zscore():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        output_file = os.path.join(tmpdir, "stats.json")

        base_epoch = 1700000000
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            # 50 normal rows with CPU around 10.0
            for i in range(50):
                writer.writerow([
                    str(base_epoch + i * 5), "2023-11-14T00:00:00Z", "10.0", "40.0", "8000",
                    "0.0", "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                    "0", "0", "0"
                ])
            # One extreme anomaly at 100.0
            writer.writerow([
                str(base_epoch + 50 * 5), "2023-11-14T00:04:10Z", "100.0", "40.0", "8000",
                "0.0", "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        assert stats["anomalies"]["cpu_pct"] >= 1
        assert stats["anomalies"]["mem_used_pct"] == 0


def test_top_processes():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "metrics.csv")
        proc_dir = os.path.join(tmpdir, "raw")
        os.makedirs(proc_dir, exist_ok=True)
        output_file = os.path.join(tmpdir, "stats.json")

        base_epoch = 1700000000
        with open(input_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(METRICS_HEADER)
            writer.writerow([
                str(base_epoch), "2023-11-14T00:00:00Z", "10.0", "40.0", "8000", "0.0",
                "50.0", "1.0", "1.0", "1.0", "100.0", "50.0", "40.0", "", "", "",
                "0", "0", "0"
            ])

        # Write process log
        proc_file = os.path.join(proc_dir, "processes_20231114.csv")
        with open(proc_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(PROCESSES_HEADER)
            writer.writerow([str(base_epoch), "101", "python3", "45.0", "8.0", "1"])
            writer.writerow([str(base_epoch), "102", "chrome", "30.0", "12.0", "2"])
            writer.writerow([str(base_epoch), "103", "systemd", "0.5", "0.2", "3"])

        res = run_analyzer(input_file, proc_dir, output_file)
        assert res.returncode == 0
        with open(output_file, "r") as f:
            stats = json.load(f)

        top_procs = stats["top_processes"]
        assert len(top_procs) == 3
        assert top_procs[0]["command"] == "python3"
        assert top_procs[0]["avg_cpu"] == 45.0
        assert top_procs[0]["avg_mem"] == 8.0
        assert top_procs[1]["command"] == "chrome"
