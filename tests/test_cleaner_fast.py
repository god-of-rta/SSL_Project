import filecmp
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("pandas")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
GEN = os.path.join(ROOT, "bin", "gen_data.py")
PLAIN = os.path.join(ROOT, "src", "cleaner.py")
FAST = os.path.join(ROOT, "src", "cleaner_fast.py")
HEADER = ("epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,"
          "net_rx_kbps,net_tx_kbps,cpu_temp_c,gpu_util_pct,gpu_mem_used_pct,gpu_temp_c")


def clean_with(script, raw_dir, out_dir, tag, since=None):
    csv_path = os.path.join(str(out_dir), tag + ".csv")
    rep_path = os.path.join(str(out_dir), tag + ".json")
    cmd = [sys.executable, script, "--input-dir", str(raw_dir), "--output", csv_path, "--report", rep_path]
    if since is not None:
        cmd += ["--since", str(since)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    with open(rep_path) as fh:
        report = json.load(fh)
    report.pop("generated_at")
    return csv_path, report


def assert_same(raw_dir, tmp_path, since=None):
    csv_a, rep_a = clean_with(PLAIN, raw_dir, tmp_path, "plain", since)
    csv_b, rep_b = clean_with(FAST, raw_dir, tmp_path, "fast", since)
    assert rep_a.pop("engine") == "plain" and rep_b.pop("engine") == "pandas"
    assert rep_a == rep_b
    assert filecmp.cmp(csv_a, csv_b, shallow=False), "cleaned CSV differs from the plain cleaner"
    return rep_b


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    d = tmp_path_factory.mktemp("raw")
    subprocess.run([sys.executable, GEN, "--rows", "6000", "--out", str(d), "--seed", "7"], check=True,
                   capture_output=True)
    return d


def test_same_output_on_generated_data(generated, tmp_path):
    report = assert_same(generated, tmp_path)
    # the generator injects every kind of problem, so the report must show them
    assert report["dropped_duplicates"] > 0 and report["dropped_invalid"] > 0
    assert report["filled"] > 0 and report["outliers"] > 0


def test_same_output_with_since(generated, tmp_path):
    assert_same(generated, tmp_path, since=1793491200 + 3 * 3600)


def test_same_output_on_hand_made_nasty_file(tmp_path):
    """Spaces, bad numbers, short and long lines, a long gap, a spike, a quoted time."""
    raw = tmp_path / "raw"
    raw.mkdir()
    base = ",50.0,40.0,8000,1.0,60.0,1.00,1.00,1.00,10.0,5.0,45.0,,,"
    lines = [HEADER]
    ep = 1793491200
    for i in range(60):
        ep += 5
        lines.append("%d,2026-11-01T00:00:00Z,%s" % (ep, "20.0" if i != 30 else "99.0") + base[5:])
    lines.append("%d,2026-11-01T00:05:00Z, 20.0 ,40.0,8000,1.0,60.0,1.5,1,1,10.0,5.0,45.0,,," % (ep + 5))  # spaces
    lines.append("%d,x,abc,40.0,8000,1.0,60.0,1,1,1,10.0,5.0,45.0,,," % (ep + 10))                      # bad number
    lines.append("%d,x,150,40.0,8000,1.0,60.0,1,1,1,10.0,5.0,45.0,,," % (ep + 15))                      # out of range
    lines.append("%d,x,20.0,40.0,8000.5,1.0,60.0,1,1,1,10.0,5.0,45.0,,," % (ep + 20))                   # float mem
    lines.append("%d,x,20.0" % (ep + 25))                                                                # too short
    lines.append("%d,x,20.0,40.0,8000,1.0,60.0,1,1,1,10.0,5.0,45.0,,,,extra" % (ep + 30))               # too long
    lines.append("%d,,20.0,40.0,8000,1.0,60.0,1,1,1,10.0,5.0,45.0,,," % (ep + 600))                     # empty time, gap
    lines.append(lines[5])                                                                               # duplicate
    (raw / "metrics_20261101.csv").write_text("\n".join(lines) + "\n")
    report = assert_same(raw, tmp_path)
    assert report["dropped_invalid"] >= 4 and report["dropped_duplicates"] == 1 and report["gaps"] == 1


def test_same_output_with_quoted_time_cell(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    row = '%d,"2026-11-01, odd",20.0,40.0,8000,1.0,60.0,1,1,1,10.0,5.0,45.0,,,'
    (raw / "metrics_20261101.csv").write_text(
        HEADER + "\n" + "\n".join(row % (1793491200 + 5 * i) for i in range(10)) + "\n")
    assert_same(raw, tmp_path)


@pytest.mark.parametrize("content", ["", HEADER + "\n", "a,b,c\n1,2,3\n"])
def test_empty_and_header_only_and_bad_header(tmp_path, content):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "metrics_20261101.csv").write_text(content)
    assert_same(raw, tmp_path)


def test_no_input_files_gives_valid_empty_output(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    report = assert_same(raw, tmp_path)
    assert report["rows_read"] == 0
    with open(os.path.join(str(tmp_path), "fast.csv")) as fh:
        assert fh.read().startswith("epoch,iso_time,")


def test_running_twice_gives_identical_output(generated, tmp_path):
    a, _ = clean_with(FAST, generated, tmp_path, "run1")
    b, _ = clean_with(FAST, generated, tmp_path, "run2")
    assert filecmp.cmp(a, b, shallow=False)