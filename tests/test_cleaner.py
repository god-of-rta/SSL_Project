#!/usr/bin/env python3
"""
tests/test_cleaner.py - Unit tests for src/cleaner.py.

Covers:
1. Exact and timestamp duplicates removal
2. Out-of-range and malformed row dropping (percentages > 100 or < 0, temp > 120)
3. Short gap (1 to 3 missing samples) forward-filling (is_filled=1)
4. Long gap (> 3 missing samples) flagging on the first real row (is_gap=1, no invented rows)
5. Rolling-median statistical outlier detection (is_outlier=1, row kept)
6. Empty GPU and temperature fields preserved as empty strings
7. Idempotency (running twice gives identical output)
8. Missing/empty input directory graceful handling
9. Fixture file compatibility (tests/fixtures/raw_metrics.csv if present)
"""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import cleaner  # noqa: E402


def make_raw_row(
    epoch: int,
    cpu_pct: str = "12.5",
    mem_used_pct: str = "45.0",
    mem_avail_mb: str = "8192",
    swap_used_pct: str = "0.0",
    disk_used_pct: str = "60.2",
    load1: str = "0.50",
    load5: str = "0.40",
    load15: str = "0.35",
    net_rx_kbps: str = "10.2",
    net_tx_kbps: str = "5.4",
    cpu_temp_c: str = "52.0",
    gpu_util_pct: str = "8.0",
    gpu_mem_used_pct: str = "15.0",
    gpu_temp_c: str = "48.0",
) -> dict:
    return {
        "epoch": str(epoch),
        "iso_time": cleaner.format_iso_utc(epoch),
        "cpu_pct": cpu_pct,
        "mem_used_pct": mem_used_pct,
        "mem_avail_mb": mem_avail_mb,
        "swap_used_pct": swap_used_pct,
        "disk_used_pct": disk_used_pct,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "net_rx_kbps": net_rx_kbps,
        "net_tx_kbps": net_tx_kbps,
        "cpu_temp_c": cpu_temp_c,
        "gpu_util_pct": gpu_util_pct,
        "gpu_mem_used_pct": gpu_mem_used_pct,
        "gpu_temp_c": gpu_temp_c,
    }


class TestCleaner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.raw_dir = self.base_path / "raw"
        self.clean_dir = self.base_path / "clean"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.output_csv = self.clean_dir / "metrics.csv"
        self.report_json = self.clean_dir / "cleaning_report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_raw_csv(self, filename: str, rows: list) -> Path:
        path = self.raw_dir / filename
        with open(path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cleaner.RAW_COLUMNS, lineterminator="\n")
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        return path

    def read_clean_csv(self) -> list:
        with open(self.output_csv, mode="r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def read_report(self) -> dict:
        with open(self.report_json, mode="r", encoding="utf-8") as f:
            return json.load(f)

    def test_comprehensive_cleaning_pipeline(self) -> None:
        """
        Test duplicate removal, invalid range dropping, short gap filling,
        long gap flagging, outlier spike flagging, and empty GPU preservation.
        """
        base = 1794060000
        rows = [
            # Normal baseline rows (step = 5s)
            make_raw_row(base + 0, cpu_pct="12.0"),
            make_raw_row(base + 5, cpu_pct="13.0"),
            # Duplicate timestamp (should be dropped and counted)
            make_raw_row(base + 5, cpu_pct="13.0"),
            make_raw_row(base + 10, cpu_pct="12.5", gpu_util_pct="", gpu_mem_used_pct="", gpu_temp_c=""),
            # Short gap of 2 missing samples (+15, +20 missing -> jump to +25)
            make_raw_row(base + 25, cpu_pct="14.0", gpu_util_pct="", gpu_mem_used_pct="", gpu_temp_c=""),
            make_raw_row(base + 30, cpu_pct="13.5"),
            # Statistical CPU spike at +35 (should be kept with is_outlier="1")
            make_raw_row(base + 35, cpu_pct="96.0"),
            make_raw_row(base + 40, cpu_pct="13.0"),
            make_raw_row(base + 45, cpu_pct="12.5"),
            make_raw_row(base + 50, cpu_pct="13.0"),
            # Invalid rows: CPU > 100, temp > 120, negative mem_used_pct
            make_raw_row(base + 55, cpu_pct="145.0"),
            make_raw_row(base + 56, cpu_temp_c="135.0"),
            make_raw_row(base + 57, mem_used_pct="-5.0"),
            # Long gap: > 3 missing samples from +50 to +80 (5 missing samples: 55,60,65,70,75)
            make_raw_row(base + 80, cpu_pct="14.0"),
            make_raw_row(base + 85, cpu_pct="13.5"),
        ]

        self.write_raw_csv("metrics_20261108.csv", rows)

        rc = cleaner.main([
            "--input-dir", str(self.raw_dir),
            "--output", str(self.output_csv),
            "--report", str(self.report_json),
        ])
        self.assertEqual(rc, 0)

        clean_rows = self.read_clean_csv()
        report = self.read_report()

        # Verify report counters
        self.assertEqual(report["rows_read"], 15)
        self.assertEqual(report["dropped_duplicates"], 1)
        self.assertEqual(report["dropped_invalid"], 3)
        self.assertEqual(report["filled"], 2)
        self.assertEqual(report["gaps"], 1)
        self.assertEqual(report["outliers"], 1)
        self.assertEqual(report["engine"], "plain")

        # 11 valid unique rows + 2 filled rows = 13 output rows
        self.assertEqual(len(clean_rows), 13)

        # Verify sorted strictly ascending by epoch
        epochs = [int(r["epoch"]) for r in clean_rows]
        self.assertEqual(epochs, sorted(set(epochs)))

        by_epoch = {int(r["epoch"]): r for r in clean_rows}

        # Short gap filled rows at +15 and +20 inherit empty GPU fields from +10
        for fill_ep in (base + 15, base + 20):
            self.assertIn(fill_ep, by_epoch)
            self.assertEqual(by_epoch[fill_ep]["is_filled"], "1")
            self.assertEqual(by_epoch[fill_ep]["is_gap"], "0")
            self.assertEqual(by_epoch[fill_ep]["is_outlier"], "0")
            self.assertEqual(by_epoch[fill_ep]["gpu_util_pct"], "")
            self.assertEqual(by_epoch[fill_ep]["gpu_mem_used_pct"], "")
            self.assertEqual(by_epoch[fill_ep]["gpu_temp_c"], "")

        # Spike row at +35 is kept and flagged is_outlier="1"
        self.assertEqual(by_epoch[base + 35]["cpu_pct"], "96.0")
        self.assertEqual(by_epoch[base + 35]["is_outlier"], "1")
        self.assertEqual(by_epoch[base + 35]["is_filled"], "0")

        # Long gap between +50 and +80: no invented rows, and +80 has is_gap="1"
        self.assertNotIn(base + 55, by_epoch)
        self.assertNotIn(base + 60, by_epoch)
        self.assertEqual(by_epoch[base + 80]["is_gap"], "1")
        self.assertEqual(by_epoch[base + 80]["is_filled"], "0")
        self.assertEqual(by_epoch[base + 85]["is_gap"], "0")

    def test_idempotency_running_twice(self) -> None:
        """Running cleaner.py twice on the same input yields identical CSV and counts."""
        base = 1794061000
        rows = [
            make_raw_row(base + 0, cpu_pct="20.0"),
            make_raw_row(base + 5, cpu_pct="21.0"),
            make_raw_row(base + 15, cpu_pct="22.0"),
        ]
        self.write_raw_csv("metrics_20261108.csv", rows)

        args = [
            "--input-dir", str(self.raw_dir),
            "--output", str(self.output_csv),
            "--report", str(self.report_json),
        ]
        self.assertEqual(cleaner.main(args), 0)
        first_csv = self.output_csv.read_bytes()
        first_report = self.read_report()

        self.assertEqual(cleaner.main(args), 0)
        second_csv = self.output_csv.read_bytes()
        second_report = self.read_report()

        self.assertEqual(first_csv, second_csv)
        for key in ("rows_read", "dropped_duplicates", "dropped_invalid", "filled", "gaps", "outliers", "engine"):
            self.assertEqual(first_report[key], second_report[key])

    def test_empty_or_missing_input_produces_valid_empty_output(self) -> None:
        """Missing input directory must not crash and must produce valid empty outputs."""
        non_existent = self.base_path / "does_not_exist"
        rc = cleaner.main([
            "--input-dir", str(non_existent),
            "--output", str(self.output_csv),
            "--report", str(self.report_json),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue(self.output_csv.is_file())
        self.assertTrue(self.report_json.is_file())

        clean_rows = self.read_clean_csv()
        self.assertEqual(clean_rows, [])

        report = self.read_report()
        self.assertEqual(report["rows_read"], 0)
        self.assertEqual(report["filled"], 0)
        self.assertEqual(report["gaps"], 0)

    def test_fixture_if_present(self) -> None:
        """If tests/fixtures/raw_metrics.csv exists, verify cleaner processes it cleanly."""
        fixture_file = REPO_ROOT / "tests" / "fixtures" / "raw_metrics.csv"
        if not fixture_file.is_file():
            self.skipTest("tests/fixtures/raw_metrics.csv not present yet")

        rc = cleaner.main([
            "--input-dir", str(fixture_file),
            "--output", str(self.output_csv),
            "--report", str(self.report_json),
        ])
        self.assertEqual(rc, 0)
        clean_rows = self.read_clean_csv()
        report = self.read_report()
        self.assertGreater(len(clean_rows), 0)
        self.assertGreaterEqual(report["dropped_duplicates"], 1)
        self.assertGreaterEqual(report["dropped_invalid"], 1)
        self.assertGreaterEqual(report["filled"], 1)
        self.assertGreaterEqual(report["gaps"], 1)
        self.assertGreaterEqual(report["outliers"], 1)

    def test_prune_old_data_deletes_expired_files_and_rows(self) -> None:
        """Retention pruning removes expired raw files, trims boundary rows, and keeps recent data."""
        from datetime import datetime, timezone

        now_ep = int(datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        cutoff_ep = now_ep - 24 * 3600  # 2026-11-11 12:00:00 UTC

        # Old file completely before cutoff (2026-11-01) -> deleted outright
        old_metrics = self.write_raw_csv(
            "metrics_20261101.csv",
            [make_raw_row(cutoff_ep - 10 * 86400)],
        )
        old_procs = self.raw_dir / "processes_20261101.csv"
        old_procs.write_text(
            "epoch,pid,command,cpu_pct,mem_pct,rank\n"
            f"{cutoff_ep - 10 * 86400},101,bash,1.0,0.5,1\n",
            encoding="utf-8",
        )

        # Boundary day file (2026-11-11): 1 expired row + 1 fresh row
        boundary_metrics = self.write_raw_csv(
            "metrics_20261111.csv",
            [make_raw_row(cutoff_ep - 300), make_raw_row(cutoff_ep + 300)],
        )
        boundary_procs = self.raw_dir / "processes_20261111.csv"
        boundary_procs.write_text(
            "epoch,pid,command,cpu_pct,mem_pct,rank\n"
            f"{cutoff_ep - 300},101,old_proc,2.0,0.5,1\n"
            f"{cutoff_ep + 300},102,fresh_proc,3.0,0.5,1\n",
            encoding="utf-8",
        )

        # Alerts CSV with 1 expired + 1 fresh alert
        alerts_csv = self.base_path / "alerts.csv"
        alerts_csv.write_text(
            "id,epoch,iso_time,rule,metric,value,threshold,severity,state,process,acknowledged\n"
            f"1,{cutoff_ep - 60},2026-11-11T11:59:00Z,cpu_high,cpu_pct,90,85,warning,firing,bash,0\n"
            f"2,{cutoff_ep + 60},2026-11-11T12:01:00Z,cpu_high,cpu_pct,91,85,warning,firing,python3,0\n",
            encoding="utf-8",
        )

        # retention_hours=0 must be a no-op
        noop = cleaner.prune_old_data(self.raw_dir, 0, now_epoch=now_ep, alerts_path=alerts_csv)
        self.assertEqual(noop, {"files_deleted": 0, "rows_pruned": 0, "alerts_pruned": 0})
        self.assertTrue(old_metrics.exists())

        stats = cleaner.prune_old_data(self.raw_dir, 24, now_epoch=now_ep, alerts_path=alerts_csv)
        self.assertEqual(stats["files_deleted"], 2)
        self.assertEqual(stats["rows_pruned"], 2)
        self.assertEqual(stats["alerts_pruned"], 1)
        self.assertFalse(old_metrics.exists())
        self.assertFalse(old_procs.exists())

        with open(boundary_metrics, encoding="utf-8") as f:
            kept_m = list(csv.DictReader(f))
        self.assertEqual([int(r["epoch"]) for r in kept_m], [cutoff_ep + 300])

        with open(boundary_procs, encoding="utf-8") as f:
            kept_p = list(csv.DictReader(f))
        self.assertEqual([r["command"] for r in kept_p], ["fresh_proc"])

        with open(alerts_csv, encoding="utf-8") as f:
            kept_a = list(csv.DictReader(f))
        self.assertEqual([r["id"] for r in kept_a], ["2"])


if __name__ == "__main__":
    unittest.main()
