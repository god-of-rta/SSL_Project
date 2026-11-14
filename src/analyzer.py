#!/usr/bin/env python3
"""SysMon Analyzer Module

Reads cleaned metrics CSV and process logs, calculates summary statistics across
time windows (1h, 24h, 7d, all), computes hourly averages, ranks top processes,
fits disk-full linear forecast, identifies statistical anomalies (z-score), and
emits data/stats.json atomically.
"""

import argparse
import csv
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

METRIC_COLUMNS = [
    "cpu_pct",
    "mem_used_pct",
    "swap_used_pct",
    "disk_used_pct",
    "load1",
    "net_rx_kbps",
    "net_tx_kbps",
    "cpu_temp_c",
    "gpu_util_pct",
    "gpu_mem_used_pct",
    "gpu_temp_c",
]


def parse_float(val: Any) -> Optional[float]:
    """Parse a float from string or number; return None if empty, null, or invalid."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "null":
        return None
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def calculate_percentile(values: List[float], percentile: float) -> float:
    """Calculate percentile for a list of floats (0 to 100)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1


def calculate_std(values: List[float], mean: float) -> float:
    """Calculate sample standard deviation (ddof=1) or 0.0 if len <= 1."""
    if len(values) <= 1:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(max(0.0, variance))


def compute_metric_stats(values: List[float]) -> Optional[Dict[str, float]]:
    """Compute mean, min, max, p95, std for a list of values."""
    if not values:
        return None
    mean_val = sum(values) / len(values)
    min_val = min(values)
    max_val = max(values)
    p95_val = calculate_percentile(values, 95.0)
    std_val = calculate_std(values, mean_val)

    return {
        "mean": round(mean_val, 2),
        "min": round(min_val, 2),
        "max": round(max_val, 2),
        "p95": round(p95_val, 2),
        "std": round(std_val, 2),
    }


def compute_disk_forecast(points: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Compute disk-full forecast using linear regression of disk_used_pct vs time in days.

    points: list of (epoch, disk_used_pct).
    """
    if len(points) < 2:
        return {"days_to_full": None, "slope_pct_per_day": 0.0}

    epochs = [p[0] for p in points]
    disks = [p[1] for p in points]

    min_epoch = min(epochs)
    x_days = [(e - min_epoch) / 86400.0 for e in epochs]
    y = disks

    n = len(points)
    x_mean = sum(x_days) / n
    y_mean = sum(y) / n

    numerator = sum((x_days[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    denominator = sum((x_days[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0 or math.isclose(denominator, 0.0):
        slope = 0.0
    else:
        slope = numerator / denominator

    slope_pct_per_day = round(slope, 2)

    if slope <= 0 or math.isclose(slope, 0.0):
        days_to_full = None
    else:
        latest_disk = y[-1]
        if latest_disk >= 100.0:
            days_to_full = 0.0
        else:
            remaining = 100.0 - latest_disk
            days_to_full = round(remaining / slope, 1)

    return {
        "days_to_full": days_to_full,
        "slope_pct_per_day": slope_pct_per_day,
    }


def compute_anomalies(metric_series: Dict[str, List[float]]) -> Dict[str, int]:
    """Compute z-score anomalies count per metric (|z| > 3.0)."""
    anomalies: Dict[str, int] = {}
    for metric, values in metric_series.items():
        if not values:
            continue
        n = len(values)
        if n < 3:
            anomalies[metric] = 0
            continue
        mean_val = sum(values) / n
        std_val = calculate_std(values, mean_val)
        if std_val == 0 or math.isclose(std_val, 0.0):
            anomalies[metric] = 0
            continue

        anomaly_count = sum(1 for x in values if abs(x - mean_val) / std_val > 3.0)
        anomalies[metric] = anomaly_count

    return anomalies


def compute_top_processes(
    processes_dir: str,
    latest_epoch: Optional[int],
    window_s: int = 3600,
    top_n: int = 5,
    cpu_cores: int = 1,
) -> List[Dict[str, Any]]:
    """Compute top processes by average CPU and memory over recent window.
    
    Includes avg_cpu (raw ps average) and avg_cpu_normalized (scaled to total
    system capacity across cores, matching desktop system monitors like Resources).
    """
    if not os.path.isdir(processes_dir):
        return []

    pattern = os.path.join(processes_dir, "processes_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return []

    records_by_command: Dict[str, List[Tuple[float, float]]] = {}

    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    epoch_val = parse_float(row.get("epoch"))
                    cmd = (row.get("command") or "").strip()
                    cpu = parse_float(row.get("cpu_pct"))
                    mem = parse_float(row.get("mem_pct"))

                    if not cmd or cpu is None:
                        continue
                    if mem is None:
                        mem = 0.0

                    if latest_epoch is not None and epoch_val is not None:
                        if epoch_val < (latest_epoch - window_s):
                            continue

                    if cmd not in records_by_command:
                        records_by_command[cmd] = []
                    records_by_command[cmd].append((cpu, mem))
        except Exception:
            continue

    if not records_by_command:
        return []

    summaries = []
    cores = cpu_cores if (isinstance(cpu_cores, (int, float)) and cpu_cores > 0) else 1
    for cmd, samples in records_by_command.items():
        avg_cpu = round(sum(s[0] for s in samples) / len(samples), 2)
        avg_mem = round(sum(s[1] for s in samples) / len(samples), 2)
        avg_cpu_normalized = round(min(100.0, avg_cpu / cores), 2)
        summaries.append({
            "command": cmd,
            "avg_cpu": avg_cpu,
            "avg_cpu_normalized": avg_cpu_normalized,
            "avg_mem": avg_mem,
        })

    # Sort descending by avg_cpu, then avg_mem
    summaries.sort(key=lambda x: (x["avg_cpu"], x["avg_mem"]), reverse=True)
    return summaries[:top_n]


def analyze(
    metrics_path: str,
    processes_dir: str,
    output_path: str,
    since_epoch: Optional[int] = None,
    system_info_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute analysis pipeline and write stats.json atomically."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    empty_stats: Dict[str, Any] = {
        "generated_at": generated_at,
        "latest_epoch": None,
        "windows": {
            "1h": {},
            "24h": {},
            "7d": {},
            "all": {},
        },
        "hourly": {
            "cpu_pct": [],
        },
        "top_processes": [],
        "disk_forecast": {
            "days_to_full": None,
            "slope_pct_per_day": 0.0,
        },
        "anomalies": {},
        "data_quality": {
            "gaps": 0,
            "filled": 0,
        },
    }

    if not os.path.exists(metrics_path) or os.path.getsize(metrics_path) == 0:
        write_atomic_json(empty_stats, output_path)
        return empty_stats

    rows: List[Dict[str, Any]] = []
    gap_count = 0
    filled_count = 0

    try:
        with open(metrics_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                epoch_val = parse_float(row.get("epoch"))
                if epoch_val is None:
                    continue
                epoch_int = int(epoch_val)

                if since_epoch is not None and epoch_int < since_epoch:
                    continue

                is_gap = int(parse_float(row.get("is_gap")) or 0)
                is_filled = int(parse_float(row.get("is_filled")) or 0)

                if is_gap == 1:
                    gap_count += 1
                if is_filled == 1:
                    filled_count += 1

                parsed_row: Dict[str, Any] = {
                    "epoch": epoch_int,
                    "is_gap": is_gap,
                    "is_filled": is_filled,
                }
                for metric in METRIC_COLUMNS:
                    parsed_row[metric] = parse_float(row.get(metric))

                rows.append(parsed_row)
    except Exception as e:
        sys.stderr.write(f"Warning: error reading metrics file {metrics_path}: {e}\n")
        write_atomic_json(empty_stats, output_path)
        return empty_stats

    if not rows:
        write_atomic_json(empty_stats, output_path)
        return empty_stats

    rows.sort(key=lambda r: r["epoch"])
    latest_epoch = rows[-1]["epoch"]

    window_defs = {
        "1h": 3600,
        "24h": 86400,
        "7d": 7 * 86400,
        "all": None,
    }

    windows: Dict[str, Dict[str, Any]] = {}
    for win_name, win_duration in window_defs.items():
        windows[win_name] = {}
        if win_duration is None:
            win_rows = rows
        else:
            cutoff = latest_epoch - win_duration
            win_rows = [r for r in rows if r["epoch"] >= cutoff]

        for metric in METRIC_COLUMNS:
            # Skip rows where is_gap == 1
            vals = [
                r[metric]
                for r in win_rows
                if r["is_gap"] != 1 and r[metric] is not None
            ]
            stats = compute_metric_stats(vals)
            windows[win_name][metric] = stats

    # Hourly averages for cpu_pct
    hourly_buckets: Dict[int, List[float]] = {}
    for r in rows:
        if r["is_gap"] == 1 or r["cpu_pct"] is None:
            continue
        hour_epoch = (r["epoch"] // 3600) * 3600
        if hour_epoch not in hourly_buckets:
            hourly_buckets[hour_epoch] = []
        hourly_buckets[hour_epoch].append(r["cpu_pct"])

    hourly_list = []
    for h_epoch in sorted(hourly_buckets.keys()):
        h_vals = hourly_buckets[h_epoch]
        h_iso = datetime.fromtimestamp(h_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
        hourly_list.append({
            "hour": h_iso,
            "mean": round(sum(h_vals) / len(h_vals), 2),
        })

    # Disk forecast: points of (epoch, disk_used_pct)
    disk_points = [
        (float(r["epoch"]), r["disk_used_pct"])
        for r in rows
        if r["disk_used_pct"] is not None
    ]
    disk_forecast = compute_disk_forecast(disk_points)

    # Anomalies across all valid metric data points (skipping is_gap)
    full_metric_series: Dict[str, List[float]] = {}
    for metric in METRIC_COLUMNS:
        full_metric_series[metric] = [
            r[metric]
            for r in rows
            if r["is_gap"] != 1 and r[metric] is not None
        ]
    anomalies = compute_anomalies(full_metric_series)

    # Resolve cpu_cores from system_info.json if available
    cpu_cores = 1
    candidates = []
    if system_info_path:
        candidates.append(system_info_path)
    candidates.extend([
        os.path.join(os.path.dirname(os.path.abspath(output_path)), "system_info.json"),
        os.path.join(os.path.dirname(os.path.abspath(processes_dir)), "system_info.json"),
        os.path.join("data", "system_info.json"),
    ])
    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    info = json.load(f)
                    cores = info.get("cpu_cores")
                    if isinstance(cores, (int, float)) and cores > 0:
                        cpu_cores = int(cores)
                        break
            except Exception:
                pass

    if cpu_cores <= 1:
        detected = os.cpu_count()
        if detected and detected > 1:
            cpu_cores = detected

    # Top processes
    top_processes = compute_top_processes(processes_dir, latest_epoch, cpu_cores=cpu_cores)

    stats_output: Dict[str, Any] = {
        "generated_at": generated_at,
        "latest_epoch": latest_epoch,
        "windows": windows,
        "hourly": {
            "cpu_pct": hourly_list,
        },
        "top_processes": top_processes,
        "disk_forecast": disk_forecast,
        "anomalies": anomalies,
        "data_quality": {
            "gaps": gap_count,
            "filled": filled_count,
        },
    }

    write_atomic_json(stats_output, output_path)
    return stats_output


def write_atomic_json(data: Dict[str, Any], target_path: str) -> None:
    """Write JSON atomically by writing to temporary file in same folder then renaming."""
    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)
    temp_path = f"{target_path}.tmp.{os.getpid()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(temp_path, target_path)
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise e


def main() -> None:
    """CLI entrypoint for analyzer."""
    parser = argparse.ArgumentParser(description="SysMon Analyzer")
    parser.add_argument(
        "--input",
        default="data/clean/metrics.csv",
        help="Path to cleaned metrics CSV",
    )
    parser.add_argument(
        "--processes-dir",
        default="data/raw",
        help="Path to directory with raw processes_*.csv files",
    )
    parser.add_argument(
        "--output",
        default="data/stats.json",
        help="Path to write output stats.json",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Optional epoch timestamp to process data incrementally",
    )
    parser.add_argument(
        "--system-info",
        default=None,
        help="Optional path to system_info.json for core normalization",
    )

    args = parser.parse_args()

    try:
        analyze(
            metrics_path=args.input,
            processes_dir=args.processes_dir,
            output_path=args.output,
            since_epoch=args.since,
            system_info_path=args.system_info,
        )
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"Error during analysis: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
