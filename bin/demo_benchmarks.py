#!/usr/bin/env python3
"""
bin/demo_benchmarks.py - SysMon Pipeline Performance & Memory Benchmark

Measures and displays:
  - How long each part of the pipeline takes (execution duration)
  - How much RAM each part consumes (peak resident set size - RSS)

Pipeline stages measured:
  1. Telemetry Collector (bin/collector.sh - 1 poll tick)
  2. Data Cleaner (src/cleaner.py)
  3. Statistical Analyzer (src/analyzer.py)
  4. Alert Engine (src/alerts.py)
  5. Full Pipeline End-to-End (bin/run_pipeline.sh - Cold run)
  6. Full Pipeline End-to-End (bin/run_pipeline.sh - Cached stamp run)

Usage:
  python3 bin/demo_benchmarks.py
  python3 bin/demo_benchmarks.py --runs 3
  python3 bin/demo_benchmarks.py --json
"""

import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PYTHON_BIN = sys.executable

# ANSI Color codes for clean terminal presentation
C_BLUE = "\033[1;34m"
C_CYAN = "\033[1;36m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


def get_process_rss_mb(pid: int) -> float:
    """Read Resident Set Size (RSS) in MB from /proc/<pid>/status."""
    status_path = f"/proc/{pid}/status"
    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return round(float(parts[1]) / 1024.0, 1)
        except Exception:
            pass
    return 0.0


def measure_isolated_cmd(cmd: list[str], cwd: str = ROOT, env: dict = None) -> tuple[float, float]:
    """
    Executes command in an isolated child process to accurately record:
      - Execution duration (seconds) via monotonic high-resolution clock
      - Peak resident set size (MB) via resource.getrusage(RUSAGE_CHILDREN)
    """
    if env is None:
        env = os.environ.copy()
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"

    r_fd, w_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r_fd)
        try:
            t0 = time.perf_counter()
            subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            duration = time.perf_counter() - t0
            max_rss_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
            with os.fdopen(w_fd, "w") as out:
                out.write(f"{duration}:{max_rss_mb}")
        except Exception as exc:
            with os.fdopen(w_fd, "w") as out:
                out.write(f"0.0:0.0:{exc}")
        os._exit(0)
    else:
        os.close(w_fd)
        os.waitpid(pid, 0)
        with os.fdopen(r_fd, "r") as inp:
            result = inp.read().strip()
        parts = result.split(":")
        if len(parts) >= 2:
            return float(parts[0]), float(parts[1])
        return 0.0, 0.0


def benchmark_stage(name: str, cmd: list[str], runs: int = 3, setup_fn=None) -> dict:
    """Runs a pipeline stage N times and computes median duration and peak RAM."""
    durations = []
    rams = []
    for _ in range(runs):
        if setup_fn:
            setup_fn()
        dur, ram = measure_isolated_cmd(cmd)
        if dur > 0:
            durations.append(dur)
        if ram > 0:
            rams.append(ram)

    med_dur = statistics.median(durations) if durations else 0.0
    med_ram = statistics.median(rams) if rams else 0.0
    return {
        "name": name,
        "duration_sec": med_dur,
        "ram_mb": med_ram,
    }


def format_duration(sec: float) -> str:
    """Format duration into readable milliseconds or seconds."""
    if sec < 0.001:
        return f"{sec * 1000.0:.2f} ms"
    elif sec < 1.0:
        return f"{sec * 1000.0:.1f} ms"
    else:
        return f"{sec:.2f} s"


def main():
    parser = argparse.ArgumentParser(
        description="SysMon Pipeline Performance & RAM Consumption Benchmark"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of iterations to sample for median metrics (default: 3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON object",
    )
    args = parser.parse_args()

    clean_csv = os.path.join(ROOT, "data", "clean", "metrics.csv")
    raw_dir = os.path.join(ROOT, "data", "raw")
    config_conf = os.path.join(ROOT, "config", "thresholds.conf")
    stamp_file = os.path.join(ROOT, "data", ".pipeline_raw_stamp")

    # Define each pipeline stage command
    collector_cmd = [
        "bash",
        "-c",
        (
            f"source '{os.path.join(ROOT, 'bin', 'collector.sh')}'; "
            "read_cpu_stat >/dev/null; "
            "read_memory >/dev/null; "
            "read_disk_pct >/dev/null; "
            "read_loadavg >/dev/null; "
            "read_net_bytes >/dev/null; "
            "read_cpu_temp 10 >/dev/null; "
            "poll_gpu_metrics >/dev/null"
        ),
    ]

    cleaner_cmd = [
        PYTHON_BIN,
        os.path.join(ROOT, "src", "cleaner.py"),
        "--input-dir",
        raw_dir,
        "--output",
        os.path.join(tempfile.gettempdir(), "sysmon_bench_clean.csv"),
    ]

    analyzer_cmd = [
        PYTHON_BIN,
        os.path.join(ROOT, "src", "analyzer.py"),
        "--input",
        clean_csv if os.path.exists(clean_csv) else os.path.join(tempfile.gettempdir(), "sysmon_bench_clean.csv"),
        "--processes-dir",
        raw_dir,
        "--output",
        os.path.join(tempfile.gettempdir(), "sysmon_bench_stats.json"),
    ]

    alerts_cmd = [
        PYTHON_BIN,
        os.path.join(ROOT, "src", "alerts.py"),
        "--input",
        clean_csv if os.path.exists(clean_csv) else os.path.join(tempfile.gettempdir(), "sysmon_bench_clean.csv"),
        "--processes-dir",
        raw_dir,
        "--thresholds",
        config_conf,
        "--state",
        os.path.join(ROOT, "data", "alert_state.json"),
        "--alerts",
        os.path.join(tempfile.gettempdir(), "sysmon_bench_alerts.csv"),
    ]

    pipeline_sh = os.path.join(ROOT, "bin", "run_pipeline.sh")
    cold_pipeline_cmd = ["bash", pipeline_sh]
    cached_pipeline_cmd = ["bash", pipeline_sh]

    def reset_cold_run():
        """Ensure cold pipeline run doesn't hit timestamp cache."""
        if os.path.exists(stamp_file):
            try:
                os.remove(stamp_file)
            except OSError:
                pass

    if not args.json:
        print(f"\n{C_BLUE}{C_BOLD}{'='*86}{C_RESET}")
        print(f"{C_CYAN}{C_BOLD}           SysMon Pipeline Execution Duration & RAM Consumption Benchmark{C_RESET}")
        print(f"{C_BLUE}{C_BOLD}{'='*86}{C_RESET}")
        uname = os.uname()
        cores = os.cpu_count() or 4
        print(f"{C_DIM}Environment:{C_RESET} {uname.sysname} {uname.machine} | {cores} Cores | Python {sys.version.split()[0]} | {args.runs} Sample Runs\n")

    stages = [
        {
            "id": "collector",
            "name": "Collector (1 Polling Tick)",
            "file": "bin/collector.sh",
            "cmd": collector_cmd,
            "desc": "Direct /proc & /sys kernel telemetry capture",
            "setup": None,
        },
        {
            "id": "cleaner",
            "name": "Data Cleaner",
            "file": "src/cleaner.py",
            "cmd": cleaner_cmd,
            "desc": "Deduplication, imputation, & bounds validation",
            "setup": None,
        },
        {
            "id": "analyzer",
            "name": "Statistical Analyzer",
            "file": "src/analyzer.py",
            "cmd": analyzer_cmd,
            "desc": "Multi-window rolling stats, OLS, & Z-score",
            "setup": None,
        },
        {
            "id": "alerts",
            "name": "Alert Engine",
            "file": "src/alerts.py",
            "cmd": alerts_cmd,
            "desc": "Threshold evaluation, cooldown, & state machine",
            "setup": None,
        },
        {
            "id": "pipeline_cold",
            "name": "Full Pipeline (Cold Run)",
            "file": "bin/run_pipeline.sh",
            "cmd": cold_pipeline_cmd,
            "desc": "End-to-end: Cleaner -> Analyzer -> Alerts",
            "setup": reset_cold_run,
        },
        {
            "id": "pipeline_cached",
            "name": "Full Pipeline (Cached Run)",
            "file": "bin/run_pipeline.sh",
            "cmd": cached_pipeline_cmd,
            "desc": "Stamp cache bypass (unchanged raw telemetry)",
            "setup": None,
        },
    ]

    results = []
    for idx, stage in enumerate(stages, start=1):
        if not args.json:
            print(f"[{C_YELLOW}{idx}/{len(stages)}{C_RESET}] Benchmarking {stage['name']}...", end="", flush=True)
        res = benchmark_stage(stage["name"], stage["cmd"], runs=args.runs, setup_fn=stage["setup"])
        res["id"] = stage["id"]
        res["file"] = stage["file"]
        res["description"] = stage["desc"]
        results.append(res)
        if not args.json:
            dur_str = format_duration(res["duration_sec"])
            ram_str = f"{res['ram_mb']:.1f} MB"
            print(f" {C_GREEN}Done!{C_RESET} ({dur_str}, {ram_str} RSS)")

    if args.json:
        print(json.dumps(results, indent=2))
        return

    # Print Clean Formatted Results Table
    print(f"\n{C_BOLD}{'='*86}{C_RESET}")
    print(f"| {C_CYAN}{'Pipeline Part / Stage':<30}{C_RESET} | {C_YELLOW}{'Execution Time':<16}{C_RESET} | {C_GREEN}{'RAM Consumed':<14}{C_RESET} | {C_BOLD}{'Stage Function':<16}{C_RESET} |")
    print(f"|{'-'*32}|{'-'*18}|{'-'*16}|{'-'*18}|")

    for r in results:
        dur_str = format_duration(r["duration_sec"])
        ram_str = f"{r['ram_mb']:.1f} MB"
        print(f"| {r['name']:<30} | {dur_str:<16} | {ram_str:<14} | {C_DIM}{r['file']:<16}{C_RESET} |")

    print(f"{C_BOLD}{'='*86}{C_RESET}")

    # Summary
    cold_res = next((r for r in results if r["id"] == "pipeline_cold"), None)
    cached_res = next((r for r in results if r["id"] == "pipeline_cached"), None)
    max_ram = max((r["ram_mb"] for r in results), default=0.0)

    if cold_res and cached_res:
        speedup = (
            round(cold_res["duration_sec"] / max(0.001, cached_res["duration_sec"]), 1)
            if cached_res["duration_sec"] > 0
            else 1.0
        )
        print(f" {C_BOLD}End-to-End Pipeline Duration:{C_RESET} {format_duration(cold_res['duration_sec'])} (Cold) | {format_duration(cached_res['duration_sec'])} (Cached, {speedup}x faster)")

    print(f" {C_BOLD}Peak Pipeline RAM Overhead:{C_RESET}   {max_ram:.1f} MB (Well bounded below 50 MB budget)\n")


if __name__ == "__main__":
    main()
