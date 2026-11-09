import csv
import json
import os
import subprocess
import time
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BIN_DIR = os.path.join(REPO_ROOT, 'bin')
DATA_DIR = os.path.join(REPO_ROOT, 'data')

def run_script(script_name, *args):
    cmd = [os.path.join(BIN_DIR, script_name)] + list(args)
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)

def test_collector_lifecycle():
    # 1. Ensure clean initial state
    run_script('stop.sh')
    
    # 2. status.sh when stopped must return 1
    res_status = run_script('status.sh')
    assert res_status.returncode == 1, "status.sh should return 1 when collector is stopped"

    # 3. start.sh starts collector
    res_start = run_script('start.sh', '--interval', '1', '--simulate-temp')
    assert res_start.returncode == 0, f"start.sh failed: {res_start.stderr}"

    try:
        # 4. status.sh when running must return 0
        res_status_running = run_script('status.sh')
        assert res_status_running.returncode == 0, "status.sh should return 0 when collector is running"

        # 5. Second start.sh must return 2 (already running)
        res_start_again = run_script('start.sh')
        assert res_start_again.returncode == 2, "Second start.sh must return exit code 2"

        # 6. Wait for at least one sample to be written (allow time for initial GPU wake-up if hybrid)
        raw_dir = os.path.join(DATA_DIR, 'raw')
        sample_written = False
        for _ in range(20):
            time.sleep(0.5)
            if os.path.isdir(raw_dir):
                m_files = [f for f in os.listdir(raw_dir) if f.startswith('metrics_') and f.endswith('.csv')]
                if m_files:
                    with open(os.path.join(raw_dir, m_files[0]), 'r', encoding='utf-8') as f:
                        if len(list(csv.reader(f))) >= 2:  # header + at least 1 sample row
                            sample_written = True
                            break

        assert sample_written, "Collector did not write a sample within 10 seconds"

    finally:
        # 7. stop.sh must cleanly stop the collector (returns 0)
        res_stop = run_script('stop.sh')
        assert res_stop.returncode == 0, f"stop.sh failed: {res_stop.stderr}"

    # 8. status.sh after stop must return 1
    res_status_stopped = run_script('status.sh')
    assert res_status_stopped.returncode == 1, "status.sh should return 1 after stop.sh"

    # 9. Verify data/system_info.json
    sysinfo_path = os.path.join(DATA_DIR, 'system_info.json')
    assert os.path.isfile(sysinfo_path), "system_info.json should exist"
    with open(sysinfo_path, 'r', encoding='utf-8') as f:
        sysinfo = json.load(f)
    assert 'hostname' in sysinfo
    assert 'kernel' in sysinfo
    assert 'cpu_cores' in sysinfo
    assert 'total_ram_mb' in sysinfo
    assert 'disk_total_gb' in sysinfo
    assert isinstance(sysinfo['cpu_cores'], int)

    # 10. Verify metrics CSV (16 columns)
    raw_dir = os.path.join(DATA_DIR, 'raw')
    assert os.path.isdir(raw_dir), "data/raw directory should exist"
    metric_files = [f for f in os.listdir(raw_dir) if f.startswith('metrics_') and f.endswith('.csv')]
    assert len(metric_files) >= 1, "At least one metrics CSV should be generated"

    metric_file_path = os.path.join(raw_dir, metric_files[0])
    with open(metric_file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        assert len(header) == 16, f"Expected 16 columns in metrics header, got {len(header)}: {header}"
        rows = list(reader)
        assert len(rows) >= 1, "At least one metric sample row should be written"
        for row in rows:
            assert len(row) == 16, f"Expected 16 columns in metric row, got {len(row)}: {row}"

    # 11. Verify processes CSV (6 columns)
    proc_files = [f for f in os.listdir(raw_dir) if f.startswith('processes_') and f.endswith('.csv')]
    assert len(proc_files) >= 1, "At least one processes CSV should be generated"

    proc_file_path = os.path.join(raw_dir, proc_files[0])
    with open(proc_file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        assert len(header) == 6, f"Expected 6 columns in processes header, got {len(header)}: {header}"
        rows = list(reader)
        assert len(rows) >= 1, "At least one processes row should be written"
        for row in rows:
            assert len(row) == 6, f"Expected 6 columns in process row, got {len(row)}: {row}"
