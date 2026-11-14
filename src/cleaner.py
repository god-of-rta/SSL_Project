#!/usr/bin/env python3
"""
src/cleaner.py - SysMon plain-Python data cleaner (Task P2-6).

Reads raw metrics CSV files, validates ranges, deduplicates and sorts by epoch,
forward-fills short gaps (<= 3 samples), flags long gaps (> 3 samples), flags
rolling-median statistical outliers, and atomically writes data/clean/metrics.csv
and data/clean/cleaning_report.json.
"""

import argparse
import configparser
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RAW_COLUMNS = [
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
]

CLEAN_COLUMNS = RAW_COLUMNS + ["is_filled", "is_gap", "is_outlier"]

REQUIRED_PCT_COLS = ["cpu_pct", "mem_used_pct", "swap_used_pct", "disk_used_pct"]
OPTIONAL_PCT_COLS = ["gpu_util_pct", "gpu_mem_used_pct"]
OPTIONAL_TEMP_COLS = ["cpu_temp_c", "gpu_temp_c"]
NON_NEG_FLOAT_COLS = ["load1", "load5", "load15", "net_rx_kbps", "net_tx_kbps"]

# Minimum deviation from rolling median to consider a value a spike
OUTLIER_MIN_DELTA = {
    "cpu_pct": 25.0,
    "mem_used_pct": 25.0,
    "swap_used_pct": 25.0,
    "disk_used_pct": 20.0,
    "cpu_temp_c": 15.0,
    "gpu_util_pct": 30.0,
    "gpu_mem_used_pct": 30.0,
    "gpu_temp_c": 15.0,
}


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_config_interval(repo_root: Path) -> int:
    conf_path = repo_root / "config" / "sysmon.conf"
    if not conf_path.is_file():
        return 5
    parser = configparser.ConfigParser()
    try:
        parser.read(conf_path, encoding="utf-8")
        return max(1, int(parser.get("sysmon", "interval_s", fallback="5")))
    except Exception:
        return 5


def format_iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover_input_files(input_path: Path) -> List[Path]:
    if not input_path.exists():
        return []
    if input_path.is_file():
        return [input_path]

    files = sorted(input_path.glob("metrics_*.csv"))
    if files:
        return files

    # Fallback for fixture directories containing raw_metrics.csv
    fallback = sorted(input_path.glob("raw_metrics*.csv"))
    return fallback


def validate_and_normalize_row(raw_row: Dict[str, Optional[str]]) -> Optional[Dict[str, str]]:
    """
    Validate a single CSV DictReader row.
    Returns a normalized dict of string values if valid, or None if invalid.
    """
    if None in raw_row:
        # Extra unlabelled columns -> malformed line
        return None

    for col in RAW_COLUMNS:
        if col not in raw_row or raw_row[col] is None:
            return None

    norm: Dict[str, str] = {}

    # 1. epoch (positive integer)
    epoch_str = raw_row["epoch"].strip()
    try:
        epoch_val = int(epoch_str)
        if epoch_val <= 0 or str(epoch_val) != epoch_str:
            return None
        norm["epoch"] = str(epoch_val)
    except ValueError:
        return None

    # 2. iso_time (non-empty string; normalized to UTC ISO if needed)
    iso_str = raw_row["iso_time"].strip()
    if not iso_str:
        iso_str = format_iso_utc(epoch_val)
    norm["iso_time"] = iso_str

    # 3. Required percentage columns (0.0 to 100.0, 1 decimal place)
    for col in REQUIRED_PCT_COLS:
        val_str = raw_row[col].strip()
        if not val_str:
            return None
        try:
            val = float(val_str)
        except ValueError:
            return None
        if not (0.0 <= val <= 100.0):
            return None
        norm[col] = f"{val:.1f}"

    # 4. mem_avail_mb (non-negative integer)
    mem_avail_str = raw_row["mem_avail_mb"].strip()
    if not mem_avail_str:
        return None
    try:
        mem_avail_flt = float(mem_avail_str)
        if mem_avail_flt < 0 or not mem_avail_flt.is_integer():
            return None
        norm["mem_avail_mb"] = str(int(mem_avail_flt))
    except ValueError:
        return None

    # 5. Load averages and network rates (non-negative floats)
    for col in NON_NEG_FLOAT_COLS:
        val_str = raw_row[col].strip()
        if not val_str:
            return None
        try:
            val = float(val_str)
        except ValueError:
            return None
        if val < 0.0:
            return None
        if col.startswith("net_"):
            norm[col] = f"{val:.1f}"
        else:
            # Preserve load precision cleanly while ensuring valid float
            norm[col] = val_str

    # 6. Optional temperature columns (empty OR 0.0 to 120.0, 1 decimal place)
    for col in OPTIONAL_TEMP_COLS:
        val_str = raw_row[col].strip()
        if val_str == "":
            norm[col] = ""
        else:
            try:
                val = float(val_str)
            except ValueError:
                return None
            if not (0.0 <= val <= 120.0):
                return None
            norm[col] = f"{val:.1f}"

    # 7. Optional GPU percentage columns (empty OR 0.0 to 100.0, 1 decimal place)
    for col in OPTIONAL_PCT_COLS:
        val_str = raw_row[col].strip()
        if val_str == "":
            norm[col] = ""
        else:
            try:
                val = float(val_str)
            except ValueError:
                return None
            if not (0.0 <= val <= 100.0):
                return None
            norm[col] = f"{val:.1f}"

    return norm


def infer_step_seconds(epochs: List[int], default_step: int) -> int:
    if len(epochs) < 2:
        return default_step
    diffs = [epochs[i] - epochs[i - 1] for i in range(1, len(epochs)) if epochs[i] > epochs[i - 1]]
    if not diffs:
        return default_step
    counts = Counter(diffs)
    most_common_diff, _ = counts.most_common(1)[0]
    return max(1, int(most_common_diff))


def flag_outliers_rolling_median(rows: List[Dict[str, str]], window_radius: int = 3) -> int:
    """
    Flag statistical outliers on real rows using a rolling median & MAD test.
    Never deletes rows; sets is_outlier = "1" or "0".
    Returns total count of rows flagged as outliers.
    """
    n = len(rows)
    if n == 0:
        return 0

    for r in rows:
        r["is_outlier"] = "0"

    if n < 3:
        return 0

    # Extract float values per metric for fast window slicing
    metric_series: Dict[str, List[Optional[float]]] = {}
    for col in OUTLIER_MIN_DELTA:
        series: List[Optional[float]] = []
        for r in rows:
            val_str = r.get(col, "")
            series.append(float(val_str) if val_str != "" else None)
        metric_series[col] = series

    outlier_count = 0
    for i, r in enumerate(rows):
        # Forward-filled rows are synthetic copies, not new statistical spikes
        if r.get("is_filled") == "1":
            continue

        lo = max(0, i - window_radius)
        hi = min(n, i + window_radius + 1)

        is_row_outlier = False
        for col, min_delta in OUTLIER_MIN_DELTA.items():
            val = metric_series[col][i]
            if val is None:
                continue

            win_vals = [
                metric_series[col][j]
                for j in range(lo, hi)
                if metric_series[col][j] is not None and rows[j].get("is_filled") != "1"
            ]
            if len(win_vals) < 3:
                continue

            med = statistics.median(win_vals)
            abs_dev = abs(val - med)
            if abs_dev < min_delta:
                continue

            mad = statistics.median([abs(x - med) for x in win_vals])
            threshold = max(min_delta, 3.5 * max(mad, 1.0))
            if abs_dev >= threshold:
                is_row_outlier = True
                break

        if is_row_outlier:
            r["is_outlier"] = "1"
            outlier_count += 1

    return outlier_count


def clean_metrics(
    input_dir: Path,
    default_step: int = 5,
    since_epoch: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], Dict[str, object]]:
    files = discover_input_files(input_dir)

    rows_read = 0
    dropped_duplicates = 0
    dropped_invalid = 0
    valid_by_epoch: Dict[int, Dict[str, str]] = {}

    for csv_path in files:
        try:
            with open(csv_path, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or not all(c in reader.fieldnames for c in RAW_COLUMNS):
                    # File has invalid/missing header; count any non-empty data lines as invalid
                    for _ in reader:
                        rows_read += 1
                        dropped_invalid += 1
                    continue

                for raw_row in reader:
                    rows_read += 1
                    cleaned = validate_and_normalize_row(raw_row)
                    if cleaned is None:
                        dropped_invalid += 1
                        continue

                    ep = int(cleaned["epoch"])
                    if since_epoch is not None and ep < since_epoch:
                        continue

                    if ep in valid_by_epoch:
                        dropped_duplicates += 1
                        continue

                    valid_by_epoch[ep] = cleaned
        except OSError as exc:
            print(f"Error reading {csv_path}: {exc}", file=sys.stderr)
            raise

    sorted_epochs = sorted(valid_by_epoch.keys())
    step = infer_step_seconds(sorted_epochs, default_step)

    expanded_rows: List[Dict[str, str]] = []
    filled_count = 0
    gaps_count = 0

    for idx, ep in enumerate(sorted_epochs):
        curr_row = dict(valid_by_epoch[ep])
        curr_row["is_filled"] = "0"
        curr_row["is_gap"] = "0"
        curr_row["is_outlier"] = "0"

        if idx > 0:
            prev_ep = sorted_epochs[idx - 1]
            prev_row = valid_by_epoch[prev_ep]
            dt = ep - prev_ep
            missing_samples = int(round(dt / step)) - 1 if dt > step else 0

            if 1 <= missing_samples <= 3:
                # Short gap: forward-fill up to 3 missing samples
                for m in range(1, missing_samples + 1):
                    fill_ep = prev_ep + m * step
                    if fill_ep >= ep:
                        fill_ep = prev_ep + int((dt * m) // (missing_samples + 1))
                    fill_row = dict(prev_row)
                    fill_row["epoch"] = str(fill_ep)
                    fill_row["iso_time"] = format_iso_utc(fill_ep)
                    fill_row["is_filled"] = "1"
                    fill_row["is_gap"] = "0"
                    fill_row["is_outlier"] = "0"
                    expanded_rows.append(fill_row)
                    filled_count += 1
            elif missing_samples > 3:
                # Long gap: do not invent rows; mark the first real row after the gap
                curr_row["is_gap"] = "1"
                gaps_count += 1

        expanded_rows.append(curr_row)

    outliers_count = flag_outliers_rolling_median(expanded_rows)

    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows_read": rows_read,
        "dropped_duplicates": dropped_duplicates,
        "dropped_invalid": dropped_invalid,
        "filled": filled_count,
        "gaps": gaps_count,
        "outliers": outliers_count,
        "engine": "plain",
    }

    return expanded_rows, report


def atomic_write_csv(output_path: Path, rows: List[Dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp_path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CLEAN_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_write_json(report_path: Path, data: Dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_name(f".{report_path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp_path, mode="w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, report_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


RAW_DATED_RE = re.compile(r"^(?:metrics|processes)_(\d{4})(\d{2})(\d{2})\.csv$")


def _prune_csv_rows_by_epoch(
    csv_path: Path, cutoff_epoch: int, delete_if_empty: bool = False
) -> Tuple[int, bool]:
    """Filter out rows with epoch < cutoff_epoch from a CSV file.

    Returns (rows_pruned, file_deleted). Never rewrites the file unless
    at least one row is actually dropped.
    """
    if not csv_path.is_file():
        return 0, False

    with open(csv_path, mode="r", encoding="utf-8", errors="replace", newline="") as f:
        header = f.readline()
        if not header:
            return 0, False
        cols = [c.strip() for c in header.strip("\r\n").split(",")]
        try:
            epoch_idx = cols.index("epoch")
        except ValueError:
            return 0, False

        kept_lines: List[str] = []
        pruned_count = 0
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            parts = line.split(",")
            if epoch_idx >= len(parts):
                kept_lines.append(line)
                continue
            try:
                ep = int(float(parts[epoch_idx].strip()))
            except ValueError:
                kept_lines.append(line)
                continue
            if ep < cutoff_epoch:
                pruned_count += 1
            else:
                kept_lines.append(line if line.endswith("\n") else line + "\n")

    if pruned_count == 0:
        return 0, False

    if delete_if_empty and not kept_lines:
        csv_path.unlink()
        return pruned_count, True

    tmp_path = csv_path.with_name(f".{csv_path.name}.prune.{os.getpid()}")
    try:
        with open(tmp_path, mode="w", encoding="utf-8", newline="") as out_f:
            out_f.write(header if header.endswith("\n") else header + "\n")
            out_f.writelines(kept_lines)
        os.replace(tmp_path, csv_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return pruned_count, False


def prune_old_data(
    raw_dir: Path,
    retention_hours: int,
    now_epoch: Optional[int] = None,
    alerts_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Delete or trim raw metrics/processes CSVs and alerts older than retention_hours.

    When retention_hours <= 0, auto-deletion is disabled and nothing is modified.
    """
    stats = {"files_deleted": 0, "rows_pruned": 0, "alerts_pruned": 0}
    if retention_hours <= 0:
        return stats

    now_ts = int(now_epoch if now_epoch is not None else time.time())
    cutoff_epoch = now_ts - retention_hours * 3600
    today_str = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y%m%d")

    if raw_dir.is_dir():
        for entry in sorted(raw_dir.iterdir()):
            if not entry.is_file():
                continue
            match = RAW_DATED_RE.match(entry.name)
            if not match:
                continue
            y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            file_date_str = f"{y:04d}{m:02d}{d:02d}"
            try:
                day_start = int(datetime(y, m, d, 0, 0, 0, tzinfo=timezone.utc).timestamp())
                day_end = int(datetime(y, m, d, 23, 59, 59, tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue

            if day_end < cutoff_epoch:
                entry.unlink()
                stats["files_deleted"] += 1
            elif day_start < cutoff_epoch:
                pruned, deleted = _prune_csv_rows_by_epoch(
                    entry, cutoff_epoch, delete_if_empty=(file_date_str < today_str)
                )
                stats["rows_pruned"] += pruned
                if deleted:
                    stats["files_deleted"] += 1

    if alerts_path is not None and alerts_path.is_file():
        alerts_pruned, _ = _prune_csv_rows_by_epoch(
            alerts_path, cutoff_epoch, delete_if_empty=False
        )
        stats["alerts_pruned"] = alerts_pruned

    return stats


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SysMon raw metrics cleaner")
    parser.add_argument(
        "--input-dir",
        default="data/raw",
        help="Directory containing raw metrics_*.csv files (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        default="data/clean/metrics.csv",
        help="Output path for cleaned CSV (default: data/clean/metrics.csv)",
    )
    parser.add_argument(
        "--report",
        default="data/clean/cleaning_report.json",
        help="Output path for cleaning report JSON (default: data/clean/cleaning_report.json)",
    )
    parser.add_argument(
        "--engine",
        choices=["plain", "pandas"],
        default="plain",
        help="Cleaning engine to use (default: plain)",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Optional minimum epoch timestamp for incremental processing",
    )
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=0,
        help="Auto-delete raw CSVs and rows older than N hours (0 disables, default: 0)",
    )
    parser.add_argument(
        "--alerts",
        default=None,
        help="Optional alerts.csv path to prune expired alert rows alongside raw data",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(argv)
        repo_root = get_repo_root()

        input_dir = Path(args.input_dir)
        if not input_dir.is_absolute():
            input_dir = repo_root / input_dir

        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path

        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = repo_root / report_path

        prune_stats = None
        if args.retention_hours > 0:
            alerts_path = None
            if args.alerts:
                alerts_path = Path(args.alerts)
                if not alerts_path.is_absolute():
                    alerts_path = repo_root / alerts_path
            prune_stats = prune_old_data(
                raw_dir=input_dir,
                retention_hours=args.retention_hours,
                alerts_path=alerts_path,
            )

        default_step = read_config_interval(repo_root)
        rows, report = clean_metrics(
            input_dir=input_dir,
            default_step=default_step,
            since_epoch=args.since,
        )
        report["engine"] = args.engine
        if prune_stats is not None:
            report["retention"] = prune_stats

        atomic_write_csv(output_path, rows)
        atomic_write_json(report_path, report)
        return 0
    except Exception as exc:
        print(f"Error in cleaner.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
