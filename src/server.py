from flask import Flask, jsonify, request, send_from_directory
import subprocess
import json
import csv
import io
import os
import sys
import math
import threading
import time
import tempfile
import configparser

# Limit BLAS/OMP threads before importing pandas/numpy so multi-core CPUs don't spin thread pools
for _env_var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_env_var, "1")

import pandas as pd
import numpy as np
import glob

app = Flask(__name__, static_folder='../web', static_url_path='')

# Base directory paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA_DIR = os.path.join(BASE_DIR, 'data')
BIN_DIR = os.path.join(BASE_DIR, 'bin')
CONFIG_DIR = os.path.join(BASE_DIR, 'config')
FIXTURES_DIR = os.path.join(BASE_DIR, 'tests', 'fixtures')

# Global lock for pipeline execution
pipeline_lock = threading.Lock()

# --- Helper Functions ---

def get_file_path(filename, subfolder=""):
    """Locates files in data/ subfolders, falling back to tests/fixtures/ only when opted in."""
    data_path = os.path.join(DATA_DIR, subfolder, filename) if subfolder else os.path.join(DATA_DIR, filename)
    if os.path.exists(data_path):
        return data_path

    if os.environ.get('SYSMON_USE_FIXTURES') == '1':
        fixture_prefix = f"{subfolder}_{filename}" if subfolder else filename
        fixture_path = os.path.join(FIXTURES_DIR, fixture_prefix)
        if os.path.exists(fixture_path):
            return fixture_path

        fixture_direct = os.path.join(FIXTURES_DIR, filename)
        if os.path.exists(fixture_direct):
            return fixture_direct

    return data_path

def error_response(message, status=400, field=None):
    """Builds a Section 9 JSON error response."""
    body = {"ok": False, "error": message}
    if field:
        body["field"] = field
    return jsonify(body), status

def parse_json_object():
    """Parses request body as a JSON object; returns (dict, None) or (None, error_tuple)."""
    if not request.get_data(cache=True):
        return {}, None
    try:
        data = request.get_json(force=False, silent=False)
    except Exception:
        return None, error_response("Invalid JSON body", 400)
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, error_response("Request body must be a JSON object", 400)
    return data, None

def is_strict_int(val):
    return isinstance(val, int) and not isinstance(val, bool)

def is_strict_number(val):
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)

def atomic_write_json(filepath, data):
    """Writes JSON atomically with guaranteed temp file cleanup."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    dir_name = os.path.dirname(filepath)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
            json.dump(data, tf, indent=2)
            temp_name = tf.name
        
        os.replace(temp_name, filepath)
        temp_name = None 
    finally:
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

def atomic_write_config(filepath, config_parser):
    """Writes INI config files atomically with guaranteed temp file cleanup."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    dir_name = os.path.dirname(filepath)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
            config_parser.write(tf)
            temp_name = tf.name
            
        os.replace(temp_name, filepath)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

def atomic_write_text(filepath, text):
    """Writes raw text files atomically with guaranteed temp file cleanup."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    dir_name = os.path.dirname(filepath)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
            tf.write(text)
            temp_name = tf.name
        os.replace(temp_name, filepath)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

# --- Error Handlers ---

@app.errorhandler(404)
def not_found(e):
    return jsonify({"ok": False, "error": "Route or resource not found"}), 404

@app.errorhandler(400)
def bad_request(e):
    err_msg = getattr(e, 'description', 'Bad request')
    return jsonify({"ok": False, "error": err_msg}), 400

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"ok": False, "error": "Internal server error"}), 500

# --- Static File Serving ---

@app.route('/')
@app.route('/dashboard')
@app.route('/dashboard.html')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

# --- System & Status Endpoints ---

@app.route('/api/status', methods=['GET'])
def get_status():
    server_time = int(time.time())
    
    collector_running = False
    collector_pid = None
    status_script = os.path.join(BIN_DIR, 'status.sh')
    
    if os.path.exists(status_script):
        try:
            res = subprocess.run(
                ['bash', status_script],
                capture_output=True,
                text=True,
                timeout=2
            )
            collector_running = (res.returncode == 0)
        except subprocess.TimeoutExpired:
            collector_running = False
        except Exception:
            collector_running = False

    pid_file = os.path.join(DATA_DIR, 'collector.pid')
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                collector_pid = int(f.read().strip())
        except (ValueError, OSError):
            pass

    pipeline_info = {"last_run_epoch": None, "duration_s": None, "ok": True, "failed_stage": None}
    pipe_status_path = get_file_path('pipeline_status.json')
    if os.path.exists(pipe_status_path):
        try:
            with open(pipe_status_path, 'r') as f:
                pipeline_info = json.load(f)
        except Exception:
            pass

    # Read last sample epoch from clean metrics and raw data (take newest valid epoch)
    def get_last_epoch_from_file(filepath):
        try:
            with open(filepath, 'rb') as f:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b'\n':
                    f.seek(-2, os.SEEK_CUR)
                last_line = f.readline().decode()
                return int(last_line.split(',')[0])
        except Exception:
            return None

    epoch_candidates = []
    clean_metrics_path = get_file_path('metrics.csv', 'clean')
    if os.path.exists(clean_metrics_path):
        clean_ep = get_last_epoch_from_file(clean_metrics_path)
        if clean_ep is not None:
            epoch_candidates.append(clean_ep)

    raw_dir = os.path.join(DATA_DIR, 'raw')
    if os.path.exists(raw_dir):
        raw_files = sorted(glob.glob(os.path.join(raw_dir, 'metrics_*.csv')))
        if raw_files:
            raw_ep = get_last_epoch_from_file(raw_files[-1])
            if raw_ep is not None:
                epoch_candidates.append(raw_ep)

    last_sample_epoch = max(epoch_candidates) if epoch_candidates else None

    return jsonify({
        "server_time_epoch": server_time,
        "collector": {
            "running": collector_running,
            "pid": collector_pid,
            "last_sample_epoch": last_sample_epoch
        },
        "pipeline": pipeline_info
    })

@app.route('/api/system', methods=['GET'])
def get_system():
    sys_info_path = get_file_path('system_info.json')
    if os.path.exists(sys_info_path):
        try:
            with open(sys_info_path, 'r') as f:
                data = json.load(f)
                cores = data.get('cpu_cores')
                if not cores or cores <= 1:
                    real_cores = os.cpu_count()
                    if real_cores and real_cores > 1:
                        data['cpu_cores'] = real_cores
                return jsonify(data)
        except Exception:
            pass
    return jsonify({})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats_path = get_file_path('stats.json')
    if os.path.exists(stats_path):
        try:
            with open(stats_path, 'r') as f:
                return jsonify(json.load(f))
        except Exception:
            pass
    return jsonify({})

# --- Optimized Metrics Endpoint ---

RANGE_SPEC = {
    '1h': (3600, 5, None),
    '24h': (86400, 60, '1min'),
    '7d': (604800, 600, '10min'),
}

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    time_range = request.args.get('range', '1h')
    if time_range not in RANGE_SPEC:
        return error_response("range must be one of '1h', '24h', '7d'", 400, field="range")

    window_seconds, step_s, bucket_size = RANGE_SPEC[time_range]
    metrics_path = get_file_path('metrics.csv', 'clean')

    if not os.path.exists(metrics_path):
        return jsonify({"range": time_range, "step_s": step_s, "columns": [], "rows": []})

    try:
        df = pd.read_csv(metrics_path)
    except Exception:
        return jsonify({"range": time_range, "step_s": step_s, "columns": [], "rows": []})

    if df.empty or 'epoch' not in df.columns:
        return jsonify({"range": time_range, "step_s": step_s, "columns": df.columns.tolist(), "rows": []})

    df = df.copy()
    df['epoch'] = pd.to_numeric(df['epoch'], errors='coerce')
    df = df.dropna(subset=['epoch'])
    if df.empty:
        return jsonify({"range": time_range, "step_s": step_s, "columns": df.columns.tolist(), "rows": []})

    df['epoch'] = df['epoch'].astype('int64')
    df = df.sort_values('epoch')
    latest_epoch = int(df['epoch'].max())

    # For range=1h (live dashboard window), seamlessly append any fresh raw samples
    # written by the collector that have not yet been processed by the background cleaner.
    if time_range == '1h':
        raw_dir = os.path.join(DATA_DIR, 'raw')
        if os.path.isdir(raw_dir):
            raw_files = sorted(glob.glob(os.path.join(raw_dir, 'metrics_*.csv')))
            if raw_files:
                try:
                    raw_df = pd.read_csv(raw_files[-1])
                    if not raw_df.empty and 'epoch' in raw_df.columns:
                        raw_df['epoch'] = pd.to_numeric(raw_df['epoch'], errors='coerce')
                        raw_df = raw_df.dropna(subset=['epoch'])
                        raw_df['epoch'] = raw_df['epoch'].astype('int64')
                        new_raw = raw_df[raw_df['epoch'] > latest_epoch].copy()
                        if not new_raw.empty:
                            for flag_col in ('is_filled', 'is_gap', 'is_outlier'):
                                if flag_col not in new_raw.columns:
                                    new_raw[flag_col] = 0
                            df = pd.concat([df, new_raw], ignore_index=True)
                            df = df.sort_values('epoch')
                            latest_epoch = int(df['epoch'].max())
                except Exception:
                    pass

    cutoff_epoch = latest_epoch - window_seconds
    df = df[df['epoch'] >= cutoff_epoch].copy()

    int_cols = {'epoch', 'is_filled', 'is_gap', 'is_outlier'}

    if bucket_size and not df.empty:
        for col in df.columns:
            if col not in ('epoch', 'iso_time'):
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df['datetime'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
        df = df.set_index('datetime')

        # Custom aggregation: mean for numbers, max for flags, last for strings
        agg_funcs = {}
        for col in df.columns:
            if col in ['is_filled', 'is_gap', 'is_outlier']:
                agg_funcs[col] = 'max'
            elif col == 'iso_time':
                agg_funcs[col] = 'last'
            elif col != 'epoch':
                agg_funcs[col] = 'mean'

        df_resampled = df.resample(bucket_size).agg(agg_funcs).dropna(subset=['iso_time'])
        epoch_zero = pd.Timestamp('1970-01-01', tz='UTC')
        df_resampled['epoch'] = (df_resampled.index - epoch_zero).total_seconds().astype('int64')
        df = df_resampled.reset_index(drop=True)

        # Reorder columns to match original CSV structure
        df = df[['epoch', 'iso_time'] + [c for c in df.columns if c not in ('epoch', 'iso_time')]]

    columns = df.columns.tolist()
    clean_cols = {}
    for col in columns:
        if col == 'iso_time':
            clean_cols[col] = [None if pd.isna(v) else str(v) for v in df[col]]
        elif col in int_cols:
            clean_cols[col] = [None if pd.isna(v) else int(v) for v in pd.to_numeric(df[col], errors='coerce')]
        else:
            vals = pd.to_numeric(df[col], errors='coerce')
            clean_cols[col] = [
                None if (pd.isna(v) or not math.isfinite(float(v))) else round(float(v), 2)
                for v in vals
            ]

    rows = [list(r) for r in zip(*(clean_cols[c] for c in columns))] if columns else []
    return jsonify({"range": time_range, "step_s": step_s, "columns": columns, "rows": rows})

# --- Alert Endpoints ---

@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    alerts_path = get_file_path('alerts.csv')
    acks_path = get_file_path('acks.json')

    acks = set()
    if os.path.exists(acks_path):
        try:
            with open(acks_path, 'r') as f:
                acks = set(json.load(f).get('acknowledged_ids', []))
        except Exception:
            pass

    alerts_list = []
    if os.path.exists(alerts_path):
        try:
            with open(alerts_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        alert_id = int(row['id']) if row.get('id') else 0
                        epoch_val = int(row['epoch']) if row.get('epoch') else 0
                        val = float(row['value']) if row.get('value') not in (None, '') else None
                        thr = float(row['threshold']) if row.get('threshold') not in (None, '') else None
                    except (ValueError, TypeError):
                        continue
                    alerts_list.append({
                        "id": alert_id,
                        "epoch": epoch_val,
                        "iso_time": row.get('iso_time', ''),
                        "rule": row.get('rule', ''),
                        "metric": row.get('metric', ''),
                        "value": val,
                        "threshold": thr,
                        "severity": row.get('severity', ''),
                        "state": row.get('state', ''),
                        "process": row.get('process', ''),
                        "acknowledged": 1 if alert_id in acks else 0
                    })
        except Exception:
            pass

    alerts_list.sort(key=lambda x: x['epoch'], reverse=True)
    return jsonify({"alerts": alerts_list})

@app.route('/api/alerts/ack', methods=['POST'])
def ack_alerts():
    req_data, err = parse_json_object()
    if err:
        return err

    if 'all' in req_data and not isinstance(req_data['all'], bool):
        return error_response("all must be a boolean", 400, field="all")
    if 'ids' in req_data:
        ids_val = req_data['ids']
        if not isinstance(ids_val, list) or not all(is_strict_int(x) for x in ids_val):
            return error_response("ids must be a list of integers", 400, field="ids")

    acks_path = os.path.join(DATA_DIR, 'acks.json')

    existing_acks = set()
    if os.path.exists(acks_path):
        try:
            with open(acks_path, 'r') as f:
                existing_acks = set(json.load(f).get('acknowledged_ids', []))
        except Exception:
            pass

    if req_data.get('all') is True:
        alerts_path = get_file_path('alerts.csv')
        if os.path.exists(alerts_path):
            try:
                with open(alerts_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('id'):
                            existing_acks.add(int(row['id']))
            except Exception:
                pass
    elif isinstance(req_data.get('ids'), list):
        for aid in req_data['ids']:
            if is_strict_int(aid):
                existing_acks.add(aid)

    atomic_write_json(acks_path, {"acknowledged_ids": sorted(list(existing_acks))})
    return jsonify({"ok": True})

@app.route('/api/alerts/test', methods=['POST'])
def test_alert():
    alerts_script = os.path.join(BASE_DIR, 'src', 'alerts.py')
    if not os.path.exists(alerts_script):
        return jsonify({"ok": False, "error": "src/alerts.py script not found"}), 500

    try:
        res = subprocess.run([sys.executable, alerts_script, '--test'], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Failed to execute test alert"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Test alert timed out"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/alerts/clear', methods=['POST'])
def clear_alerts():
    """Clear alerts history. scope='resolved' removes resolved alerts; scope='all' resets all."""
    req_data = {}
    if request.is_json and request.data:
        parsed, err = parse_json_object()
        if err:
            return err
        req_data = parsed

    scope = req_data.get('scope', 'resolved')
    if scope not in ('resolved', 'all'):
        return error_response("scope must be 'resolved' or 'all'", 400, field="scope")

    alerts_path = get_file_path('alerts.csv')
    if not os.path.exists(alerts_path):
        return jsonify({"ok": True, "cleared": 0, "remaining": 0})

    header = ["id", "epoch", "iso_time", "rule", "metric", "value", "threshold",
              "severity", "state", "process", "acknowledged"]
    remaining_rows = []
    cleared_count = 0

    try:
        with open(alerts_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or header
            for row in reader:
                st = (row.get('state') or '').strip().lower()
                if scope == 'resolved' and st == 'resolved':
                    cleared_count += 1
                elif scope == 'all':
                    cleared_count += 1
                else:
                    remaining_rows.append(row)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to read alerts: {e}"}), 500

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator='\n')
    writer.writeheader()
    for r in remaining_rows:
        writer.writerow(r)

    atomic_write_text(alerts_path, out.getvalue())

    if scope == 'all':
        acks_path = get_file_path('acks.json')
        if os.path.exists(acks_path):
            atomic_write_json(acks_path, {"acknowledged_ids": []})
        state_path = get_file_path('alert_state.json')
        if os.path.exists(state_path):
            atomic_write_json(state_path, {"last_epoch": None, "rules": {}})

    return jsonify({"ok": True, "cleared": cleared_count, "remaining": len(remaining_rows)})

@app.route('/api/alerts/toggle', methods=['POST'])
def toggle_alerts():
    """Toggle alerts_enabled between True and False."""
    req_data = {}
    if request.is_json and request.data:
        parsed, err = parse_json_object()
        if err:
            return err
        req_data = parsed

    sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    cfg = configparser.ConfigParser(interpolation=None)
    if os.path.exists(sysmon_path):
        cfg.read(sysmon_path)
    if not cfg.has_section('sysmon'):
        cfg.add_section('sysmon')

    cur_enabled = cfg['sysmon'].getboolean('alerts_enabled', fallback=True)
    if 'enabled' in req_data:
        new_enabled = bool(req_data['enabled'])
    else:
        new_enabled = not cur_enabled

    cfg['sysmon']['alerts_enabled'] = str(new_enabled).lower()
    atomic_write_config(sysmon_path, cfg)
    return jsonify({"ok": True, "alerts_enabled": new_enabled})

# --- Collector & Pipeline Control Endpoints ---

@app.route('/api/collector/start', methods=['POST'])
def start_collector():
    start_script = os.path.join(BIN_DIR, 'start.sh')
    if not os.path.exists(start_script):
        return jsonify({"ok": False, "running": False, "error": "start.sh script not found"}), 500

    try:
        res = subprocess.run(['bash', start_script], capture_output=True, text=True, timeout=15)
        if res.returncode in (0, 2):
            return jsonify({"ok": True, "running": True})
        err_msg = res.stderr.strip() or res.stdout.strip() or "Failed to start collector"
        return jsonify({"ok": False, "running": False, "error": err_msg}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "running": False, "error": "Collector start timed out"}), 500
    except Exception as e:
        return jsonify({"ok": False, "running": False, "error": str(e)}), 500

@app.route('/api/collector/stop', methods=['POST'])
def stop_collector():
    stop_script = os.path.join(BIN_DIR, 'stop.sh')
    if not os.path.exists(stop_script):
        return jsonify({"ok": False, "running": False, "error": "stop.sh script not found"}), 500

    try:
        res = subprocess.run(['bash', stop_script], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return jsonify({"ok": True, "running": False})
        err_msg = res.stderr.strip() or res.stdout.strip() or "Failed to stop collector"
        return jsonify({"ok": False, "running": True, "error": err_msg}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "running": True, "error": "Collector stop timed out"}), 500
    except Exception as e:
        return jsonify({"ok": False, "running": True, "error": str(e)}), 500

@app.route('/api/pipeline/run', methods=['POST'])
def run_pipeline_now():
    pipeline_script = os.path.join(BIN_DIR, 'run_pipeline.sh')
    if not os.path.exists(pipeline_script):
        return jsonify({"ok": False, "error": "bin/run_pipeline.sh script not found"}), 500

    acquired = pipeline_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"ok": False, "error": "Pipeline is already running"}), 400

    try:
        res = subprocess.run(['bash', pipeline_script], capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Pipeline execution failed"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Pipeline execution timed out"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        pipeline_lock.release()

# --- Configuration Endpoints ---

ALLOWED_METRICS = {
    "cpu_pct", "mem_used_pct", "swap_used_pct", "disk_used_pct",
    "load1", "cpu_temp_c", "gpu_util_pct", "gpu_mem_used_pct", "gpu_temp_c"
}
ALLOWED_OPS = {">", ">=", "<", "<="}
SYSMON_INT_BOUNDS = {
    "interval_s": (1, 86400),
    "pipeline_interval_s": (1, 86400),
    "gpu_poll_every": (1, 1000),
    "retention_hours": (0, 87600),
}
METRIC_BOUNDS = {
    "cpu_pct": (0, 100),
    "mem_used_pct": (0, 100),
    "swap_used_pct": (0, 100),
    "disk_used_pct": (0, 100),
    "gpu_util_pct": (0, 100),
    "gpu_mem_used_pct": (0, 100),
    "load1": (0, 10000),
    "cpu_temp_c": (-50, 200),
    "gpu_temp_c": (-50, 200),
}

@app.route('/api/config', methods=['GET'])
def get_config():
    sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    thresholds_path = os.path.join(CONFIG_DIR, 'thresholds.conf')

    sysmon_data = {
        "interval_s": 5,
        "pipeline_interval_s": 10,
        "simulate_temp": False,
        "collect_gpu": True,
        "gpu_poll_every": 3,
        "retention_hours": 168,
        "alerts_enabled": True
    }

    if os.path.exists(sysmon_path):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(sysmon_path)
        if cfg.has_section('sysmon'):
            sec = cfg['sysmon']
            sysmon_data["interval_s"] = sec.getint('interval_s', fallback=5)
            sysmon_data["pipeline_interval_s"] = sec.getint('pipeline_interval_s', fallback=10)
            sysmon_data["simulate_temp"] = sec.getboolean('simulate_temp', fallback=False)
            sysmon_data["collect_gpu"] = sec.getboolean('collect_gpu', fallback=True)
            sysmon_data["gpu_poll_every"] = sec.getint('gpu_poll_every', fallback=3)
            sysmon_data["retention_hours"] = sec.getint('retention_hours', fallback=168)
            sysmon_data["alerts_enabled"] = sec.getboolean('alerts_enabled', fallback=True)

    thresholds_data = {}
    if os.path.exists(thresholds_path):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(thresholds_path)
        for section in cfg.sections():
            sec = cfg[section]
            if section == 'no_data':
                thresholds_data[section] = {
                    "max_age_s": sec.getint('max_age_s', fallback=60),
                    "severity": sec.get('severity', fallback='critical'),
                    "enabled": sec.getboolean('enabled', fallback=True)
                }
            else:
                thresholds_data[section] = {
                    "metric": sec.get('metric', fallback=''),
                    "op": sec.get('op', fallback='>'),
                    "threshold": sec.getfloat('threshold', fallback=0.0),
                    "sustained": sec.getint('sustained', fallback=1),
                    "cooldown_s": sec.getint('cooldown_s', fallback=600),
                    "severity": sec.get('severity', fallback='warning'),
                    "enabled": sec.getboolean('enabled', fallback=True)
                }

    return jsonify({"sysmon": sysmon_data, "thresholds": thresholds_data})

@app.route('/api/config', methods=['POST'])
def save_config():
    payload, err = parse_json_object()
    if err:
        return err

    sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    thresholds_path = os.path.join(CONFIG_DIR, 'thresholds.conf')
    pending_writes = []

    if 'sysmon' in payload:
        s_cfg = payload['sysmon']
        if not isinstance(s_cfg, dict):
            return error_response("sysmon must be an object", 400, field="sysmon")

        cfg = configparser.ConfigParser(interpolation=None)
        if os.path.exists(sysmon_path):
            cfg.read(sysmon_path)
        if not cfg.has_section('sysmon'):
            cfg.add_section('sysmon')

        for key, (lo, hi) in SYSMON_INT_BOUNDS.items():
            if key in s_cfg:
                val = s_cfg[key]
                if not is_strict_int(val) or val < lo or val > hi:
                    return error_response(f"{key} must be an integer between {lo} and {hi}", 400, field=f"sysmon.{key}")
                cfg['sysmon'][key] = str(val)

        for key in ('simulate_temp', 'collect_gpu', 'alerts_enabled'):
            if key in s_cfg:
                val = s_cfg[key]
                if not isinstance(val, bool):
                    return error_response(f"{key} must be a boolean", 400, field=f"sysmon.{key}")
                cfg['sysmon'][key] = str(val).lower()

        pending_writes.append((sysmon_path, cfg))

    if 'thresholds' in payload:
        t_cfg = payload['thresholds']
        if not isinstance(t_cfg, dict):
            return error_response("thresholds must be an object", 400, field="thresholds")

        cfg = configparser.ConfigParser(interpolation=None)
        if os.path.exists(thresholds_path):
            cfg.read(thresholds_path)

        for rule_name, rule_body in t_cfg.items():
            if not isinstance(rule_name, str) or not rule_name.strip() or rule_name == 'DEFAULT' or any(c in rule_name for c in '[]\r\n'):
                return error_response("Invalid rule name", 400, field=f"thresholds.{rule_name}")

            if rule_body is None:
                if rule_name == 'no_data':
                    return error_response("no_data rule cannot be deleted", 400, field="thresholds.no_data")
                if cfg.has_section(rule_name):
                    cfg.remove_section(rule_name)
                continue

            if not isinstance(rule_body, dict):
                return error_response("Rule configuration must be an object", 400, field=f"thresholds.{rule_name}")

            if not cfg.has_section(rule_name):
                cfg.add_section(rule_name)
                # Apply defaults for entirely new rules to ensure INI integrity
                if rule_name != 'no_data':
                    cfg[rule_name]['metric'] = 'cpu_pct'
                    cfg[rule_name]['op'] = '>'
                    cfg[rule_name]['threshold'] = '0.0'
                    cfg[rule_name]['sustained'] = '1'
                    cfg[rule_name]['cooldown_s'] = '600'
                    cfg[rule_name]['severity'] = 'warning'
                    cfg[rule_name]['enabled'] = 'true'
                else:
                    cfg[rule_name]['max_age_s'] = '60'
                    cfg[rule_name]['severity'] = 'critical'
                    cfg[rule_name]['enabled'] = 'true'

            if rule_name == 'no_data':
                if 'max_age_s' in rule_body:
                    val = rule_body['max_age_s']
                    if not is_strict_int(val) or val < 1 or val > 604800:
                        return error_response("max_age_s must be an integer between 1 and 604800", 400, field="thresholds.no_data.max_age_s")
                    cfg[rule_name]['max_age_s'] = str(val)
                if 'severity' in rule_body:
                    if rule_body['severity'] not in ('warning', 'critical'):
                        return error_response("severity must be warning or critical", 400, field="thresholds.no_data.severity")
                    cfg[rule_name]['severity'] = rule_body['severity']
                if 'enabled' in rule_body:
                    if not isinstance(rule_body['enabled'], bool):
                        return error_response("enabled must be a boolean", 400, field="thresholds.no_data.enabled")
                    cfg[rule_name]['enabled'] = str(rule_body['enabled']).lower()
            else:
                if 'metric' in rule_body:
                    if rule_body['metric'] not in ALLOWED_METRICS:
                        return error_response(f"Invalid metric name '{rule_body['metric']}'", 400, field=f"thresholds.{rule_name}.metric")
                    cfg[rule_name]['metric'] = rule_body['metric']

                if 'op' in rule_body:
                    if rule_body['op'] not in ALLOWED_OPS:
                        return error_response(f"Invalid operator '{rule_body['op']}'", 400, field=f"thresholds.{rule_name}.op")
                    cfg[rule_name]['op'] = rule_body['op']

                if 'threshold' in rule_body:
                    val = rule_body['threshold']
                    if not is_strict_number(val):
                        return error_response("threshold must be a finite number", 400, field=f"thresholds.{rule_name}.threshold")
                    eff_metric = cfg[rule_name].get('metric', '')
                    lo, hi = METRIC_BOUNDS.get(eff_metric, (-1e6, 1e6))
                    if val < lo or val > hi:
                        return error_response(f"threshold must be between {lo} and {hi}", 400, field=f"thresholds.{rule_name}.threshold")
                    cfg[rule_name]['threshold'] = str(val)

                for key, (lo, hi) in (('sustained', (1, 1000)), ('cooldown_s', (0, 604800))):
                    if key in rule_body:
                        val = rule_body[key]
                        if not is_strict_int(val) or val < lo or val > hi:
                            return error_response(f"{key} must be an integer between {lo} and {hi}", 400, field=f"thresholds.{rule_name}.{key}")
                        cfg[rule_name][key] = str(val)

                if 'severity' in rule_body:
                    if rule_body['severity'] not in ('warning', 'critical'):
                        return error_response("severity must be warning or critical", 400, field=f"thresholds.{rule_name}.severity")
                    cfg[rule_name]['severity'] = rule_body['severity']

                if 'enabled' in rule_body:
                    if not isinstance(rule_body['enabled'], bool):
                        return error_response("enabled must be a boolean", 400, field=f"thresholds.{rule_name}.enabled")
                    cfg[rule_name]['enabled'] = str(rule_body['enabled']).lower()

        pending_writes.append((thresholds_path, cfg))

    for path, cfg in pending_writes:
        atomic_write_config(path, cfg)

    return jsonify({"ok": True})

@app.route('/api/config/profile', methods=['POST'])
def set_config_profile():
    req_data, err = parse_json_object()
    if err:
        return err

    profile = req_data.get('profile')

    if profile not in ('default', 'demo'):
        return jsonify({"ok": False, "error": "Profile must be 'default' or 'demo'", "field": "profile"}), 400

    alerts_script = os.path.join(BASE_DIR, 'src', 'alerts.py')
    if not os.path.exists(alerts_script):
        return jsonify({"ok": False, "error": "src/alerts.py script not found"}), 500

    try:
        if profile == 'default':
            sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
            default_sysmon = (
                "[sysmon]\n"
                "interval_s = 5\n"
                "data_dir = data\n"
                "top_n = 5\n"
                "pipeline_interval_s = 10\n"
                "port = 5000\n"
                "simulate_temp = false\n"
                "collect_gpu = true\n"
                "gpu_poll_every = 3\n"
                "retention_hours = 168\n"
                "alerts_enabled = true\n"
            )
            atomic_write_text(sysmon_path, default_sysmon)

        res = subprocess.run(
            [sys.executable, alerts_script, '--init-defaults', '--profile', profile, '--force'],
            capture_output=True, text=True, timeout=15
        )
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Failed to apply profile"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Applying profile timed out"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# --- Background Pipeline Timer ---

def start_pipeline_timer():
    """Runs the pipeline script periodically in a background thread."""
    def loop():
        while True:
            interval_s = 10
            sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
            if os.path.exists(sysmon_path):
                cfg = configparser.ConfigParser(interpolation=None)
                try:
                    cfg.read(sysmon_path)
                    if cfg.has_section('sysmon'):
                        interval_s = max(1, cfg['sysmon'].getint('pipeline_interval_s', fallback=10))
                except Exception:
                    pass
            
            time.sleep(interval_s)

            pipeline_script = os.path.join(BIN_DIR, 'run_pipeline.sh')
            if os.path.exists(pipeline_script):
                if pipeline_lock.acquire(blocking=False):
                    try:
                        subprocess.run(['bash', pipeline_script], capture_output=True, text=True, timeout=120)
                    except Exception:
                        pass
                    finally:
                        pipeline_lock.release()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

if __name__ == '__main__':
    server_port = 5000
    conf_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    if os.path.exists(conf_path):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(conf_path)
            if parser.has_section('sysmon'):
                server_port = parser['sysmon'].getint('port', fallback=5000)
        except Exception:
            pass

    start_pipeline_timer()
    app.run(host='127.0.0.1', port=server_port, debug=False)