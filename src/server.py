from flask import Flask, jsonify, request, send_from_directory
import subprocess
import json
import csv
import os
import threading
import time
import tempfile
import configparser
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
    """Locates files in data/ subfolders first, falling back to tests/fixtures/."""
    data_path = os.path.join(DATA_DIR, subfolder, filename) if subfolder else os.path.join(DATA_DIR, filename)
    if os.path.exists(data_path):
        return data_path

    fixture_prefix = f"{subfolder}_{filename}" if subfolder else filename
    fixture_path = os.path.join(FIXTURES_DIR, fixture_prefix)
    if os.path.exists(fixture_path):
        return fixture_path

    fixture_direct = os.path.join(FIXTURES_DIR, filename)
    if os.path.exists(fixture_direct):
        return fixture_direct

    return data_path

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

    # Read last sample epoch (check clean metrics first)
    last_sample_epoch = None
    clean_metrics_path = get_file_path('metrics.csv', 'clean')
    
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

    if os.path.exists(clean_metrics_path):
        last_sample_epoch = get_last_epoch_from_file(clean_metrics_path)

    # Fallback to raw data if clean metrics is empty or doesn't exist
    if last_sample_epoch is None:
        raw_dir = os.path.join(DATA_DIR, 'raw')
        if os.path.exists(raw_dir):
            raw_files = sorted(glob.glob(os.path.join(raw_dir, 'metrics_*.csv')))
            if raw_files:
                last_sample_epoch = get_last_epoch_from_file(raw_files[-1])

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
                return jsonify(json.load(f))
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

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    time_range = request.args.get('range', '1h')
    metrics_path = get_file_path('metrics.csv', 'clean')

    if not os.path.exists(metrics_path):
        return jsonify({"range": time_range, "step_s": 5, "columns": [], "rows": []})

    try:
        df = pd.read_csv(metrics_path)
    except Exception:
        return jsonify({"range": time_range, "step_s": 5, "columns": [], "rows": []})

    if df.empty:
        return jsonify({"range": time_range, "step_s": 5, "columns": df.columns.tolist(), "rows": []})

    latest_epoch = df['epoch'].max()

    # Determine step and window per downsampling 
    if time_range == '24h':
        window_seconds = 86400
        step_s = 60
        bucket_size = '1min'
    elif time_range == '7d':
        window_seconds = 604800
        step_s = 600
        bucket_size = '10min'
    else:  # 1h defaults
        window_seconds = 3600
        step_s = 5
        bucket_size = None

    cutoff_epoch = latest_epoch - window_seconds
    df = df[df['epoch'] >= cutoff_epoch]

    if bucket_size and not df.empty:
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
        df_resampled['epoch'] = df_resampled.index.astype('int64') // 10**9
        df = df_resampled.reset_index(drop=True)
        
        # Reorder columns to match original CSV structure
        df = df[['epoch', 'iso_time'] + [c for c in df.columns if c not in ('epoch', 'iso_time')]]

    # Clean up for JSON serialization
    df = df.replace({np.nan: None})
    columns = df.columns.tolist()
    numeric_cols = df.select_dtypes(include=['float64']).columns
    df[numeric_cols] = df[numeric_cols].round(2)

    rows = df.values.tolist()
    return jsonify({"range": time_range, "step_s": step_s, "columns": columns, "rows": rows})

# --- Alert Endpoints ---

@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    alerts_path = get_file_path('alerts.csv')
    acks_path = get_file_path('acks.json')

    acks = []
    if os.path.exists(acks_path):
        try:
            with open(acks_path, 'r') as f:
                acks = json.load(f).get('acknowledged_ids', [])
        except Exception:
            pass

    alerts_list = []
    if os.path.exists(alerts_path):
        try:
            with open(alerts_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    alert_id = int(row['id']) if row.get('id') else 0
                    alerts_list.append({
                        "id": alert_id,
                        "epoch": int(row['epoch']) if row.get('epoch') else 0,
                        "iso_time": row.get('iso_time', ''),
                        "rule": row.get('rule', ''),
                        "metric": row.get('metric', ''),
                        "value": float(row['value']) if row.get('value') not in (None, '') else None,
                        "threshold": float(row['threshold']) if row.get('threshold') not in (None, '') else None,
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
    req_data = request.get_json(silent=True) or {}
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
            if isinstance(aid, int):
                existing_acks.add(aid)

    atomic_write_json(acks_path, {"acknowledged_ids": sorted(list(existing_acks))})
    return jsonify({"ok": True})

@app.route('/api/alerts/test', methods=['POST'])
def test_alert():
    alerts_script = os.path.join(BASE_DIR, 'src', 'alerts.py')
    if not os.path.exists(alerts_script):
        return jsonify({"ok": False, "error": "src/alerts.py script not found"}), 500

    try:
        res = subprocess.run(['python3', alerts_script, '--test'], capture_output=True, text=True)
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Failed to execute test alert"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# --- Collector & Pipeline Control Endpoints ---

@app.route('/api/collector/start', methods=['POST'])
def start_collector():
    start_script = os.path.join(BIN_DIR, 'start.sh')
    if not os.path.exists(start_script):
        return jsonify({"ok": False, "running": False, "error": "start.sh script not found"}), 500

    try:
        res = subprocess.run(['bash', start_script], capture_output=True, text=True)
        is_ok = (res.returncode == 0)
        return jsonify({"ok": is_ok, "running": is_ok})
    except Exception as e:
        return jsonify({"ok": False, "running": False, "error": str(e)}), 500

@app.route('/api/collector/stop', methods=['POST'])
def stop_collector():
    stop_script = os.path.join(BIN_DIR, 'stop.sh')
    if not os.path.exists(stop_script):
        return jsonify({"ok": False, "running": False, "error": "stop.sh script not found"}), 500

    try:
        res = subprocess.run(['bash', stop_script], capture_output=True, text=True)
        is_ok = (res.returncode == 0)
        return jsonify({"ok": is_ok, "running": not is_ok})
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
        res = subprocess.run(['bash', pipeline_script], capture_output=True, text=True)
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Pipeline execution failed"}), 500
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

@app.route('/api/config', methods=['GET'])
def get_config():
    sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    thresholds_path = os.path.join(CONFIG_DIR, 'thresholds.conf')

    sysmon_data = {
        "interval_s": 5,
        "pipeline_interval_s": 10,
        "simulate_temp": False,
        "collect_gpu": True,
        "gpu_poll_every": 3
    }

    if os.path.exists(sysmon_path):
        cfg = configparser.ConfigParser()
        cfg.read(sysmon_path)
        if cfg.has_section('sysmon'):
            sec = cfg['sysmon']
            sysmon_data["interval_s"] = sec.getint('interval_s', fallback=5)
            sysmon_data["pipeline_interval_s"] = sec.getint('pipeline_interval_s', fallback=10)
            sysmon_data["simulate_temp"] = sec.getboolean('simulate_temp', fallback=False)
            sysmon_data["collect_gpu"] = sec.getboolean('collect_gpu', fallback=True)
            sysmon_data["gpu_poll_every"] = sec.getint('gpu_poll_every', fallback=3)

    thresholds_data = {}
    if os.path.exists(thresholds_path):
        cfg = configparser.ConfigParser()
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
    payload = request.get_json(silent=True) or {}
    sysmon_path = os.path.join(CONFIG_DIR, 'sysmon.conf')
    thresholds_path = os.path.join(CONFIG_DIR, 'thresholds.conf')

    if 'sysmon' in payload:
        s_cfg = payload['sysmon']
        cfg = configparser.ConfigParser()
        if os.path.exists(sysmon_path):
            cfg.read(sysmon_path)
        if not cfg.has_section('sysmon'):
            cfg.add_section('sysmon')

        if 'interval_s' in s_cfg:
            val = s_cfg['interval_s']
            if not isinstance(val, int) or val <= 0:
                return jsonify({"ok": False, "error": "interval_s must be a positive integer", "field": "sysmon.interval_s"}), 400
            cfg['sysmon']['interval_s'] = str(val)

        if 'pipeline_interval_s' in s_cfg:
            val = s_cfg['pipeline_interval_s']
            if not isinstance(val, int) or val <= 0:
                return jsonify({"ok": False, "error": "pipeline_interval_s must be a positive integer", "field": "sysmon.pipeline_interval_s"}), 400
            cfg['sysmon']['pipeline_interval_s'] = str(val)

        if 'simulate_temp' in s_cfg:
            cfg['sysmon']['simulate_temp'] = str(bool(s_cfg['simulate_temp'])).lower()

        if 'collect_gpu' in s_cfg:
            cfg['sysmon']['collect_gpu'] = str(bool(s_cfg['collect_gpu'])).lower()

        if 'gpu_poll_every' in s_cfg:
            val = s_cfg['gpu_poll_every']
            if not isinstance(val, int) or val <= 0:
                return jsonify({"ok": False, "error": "gpu_poll_every must be a positive integer", "field": "sysmon.gpu_poll_every"}), 400
            cfg['sysmon']['gpu_poll_every'] = str(val)

        atomic_write_config(sysmon_path, cfg)

    if 'thresholds' in payload:
        t_cfg = payload['thresholds']
        cfg = configparser.ConfigParser()
        if os.path.exists(thresholds_path):
            cfg.read(thresholds_path)

        for rule_name, rule_body in t_cfg.items():
            if rule_body is None:
                if rule_name == 'no_data':
                    return jsonify({"ok": False, "error": "no_data rule cannot be deleted", "field": "thresholds.no_data"}), 400
                if cfg.has_section(rule_name):
                    cfg.remove_section(rule_name)
                continue

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
                    if not isinstance(val, int) or val <= 0:
                        return jsonify({"ok": False, "error": "max_age_s must be a positive integer", "field": "thresholds.no_data.max_age_s"}), 400
                    cfg[rule_name]['max_age_s'] = str(val)
                if 'severity' in rule_body:
                    if rule_body['severity'] not in ('warning', 'critical'):
                        return jsonify({"ok": False, "error": "severity must be warning or critical", "field": "thresholds.no_data.severity"}), 400
                    cfg[rule_name]['severity'] = rule_body['severity']
                if 'enabled' in rule_body:
                    cfg[rule_name]['enabled'] = str(bool(rule_body['enabled'])).lower()
            else:
                if 'metric' in rule_body:
                    if rule_body['metric'] not in ALLOWED_METRICS:
                        return jsonify({"ok": False, "error": f"Invalid metric name '{rule_body['metric']}'", "field": f"thresholds.{rule_name}.metric"}), 400
                    cfg[rule_name]['metric'] = rule_body['metric']

                if 'op' in rule_body:
                    if rule_body['op'] not in ALLOWED_OPS:
                        return jsonify({"ok": False, "error": f"Invalid operator '{rule_body['op']}'", "field": f"thresholds.{rule_name}.op"}), 400
                    cfg[rule_name]['op'] = rule_body['op']

                if 'threshold' in rule_body:
                    val = rule_body['threshold']
                    if not isinstance(val, (int, float)):
                        return jsonify({"ok": False, "error": "threshold must be a number", "field": f"thresholds.{rule_name}.threshold"}), 400
                    cfg[rule_name]['threshold'] = str(val)

                if 'sustained' in rule_body:
                    val = rule_body['sustained']
                    if not isinstance(val, int) or val < 1:
                        return jsonify({"ok": False, "error": "sustained must be an integer >= 1", "field": f"thresholds.{rule_name}.sustained"}), 400
                    cfg[rule_name]['sustained'] = str(val)

                if 'cooldown_s' in rule_body:
                    val = rule_body['cooldown_s']
                    if not isinstance(val, int) or val < 0:
                        return jsonify({"ok": False, "error": "cooldown_s must be an integer >= 0", "field": f"thresholds.{rule_name}.cooldown_s"}), 400
                    cfg[rule_name]['cooldown_s'] = str(val)

                if 'severity' in rule_body:
                    if rule_body['severity'] not in ('warning', 'critical'):
                        return jsonify({"ok": False, "error": "severity must be warning or critical", "field": f"thresholds.{rule_name}.severity"}), 400
                    cfg[rule_name]['severity'] = rule_body['severity']

                if 'enabled' in rule_body:
                    cfg[rule_name]['enabled'] = str(bool(rule_body['enabled'])).lower()

        atomic_write_config(thresholds_path, cfg)

    return jsonify({"ok": True})

@app.route('/api/config/profile', methods=['POST'])
def set_config_profile():
    req_data = request.get_json(silent=True) or {}
    profile = req_data.get('profile')

    if profile not in ('default', 'demo'):
        return jsonify({"ok": False, "error": "Profile must be 'default' or 'demo'", "field": "profile"}), 400

    alerts_script = os.path.join(BASE_DIR, 'src', 'alerts.py')
    if not os.path.exists(alerts_script):
        return jsonify({"ok": False, "error": "src/alerts.py script not found"}), 500

    try:
        res = subprocess.run(
            ['python3', alerts_script, '--init-defaults', '--profile', profile, '--force'],
            capture_output=True, text=True
        )
        if res.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": res.stderr.strip() or "Failed to apply profile"}), 500
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
                cfg = configparser.ConfigParser()
                try:
                    cfg.read(sysmon_path)
                    if cfg.has_section('sysmon'):
                        interval_s = cfg['sysmon'].getint('pipeline_interval_s', fallback=10)
                except Exception:
                    pass
            
            time.sleep(interval_s)

            pipeline_script = os.path.join(BIN_DIR, 'run_pipeline.sh')
            if os.path.exists(pipeline_script):
                if pipeline_lock.acquire(blocking=False):
                    try:
                        subprocess.run(['bash', pipeline_script], capture_output=True, text=True)
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
        parser = configparser.ConfigParser()
        try:
            parser.read(conf_path)
            if parser.has_section('sysmon'):
                server_port = parser['sysmon'].getint('port', fallback=5000)
        except Exception:
            pass

    start_pipeline_timer()
    app.run(host='127.0.0.1', port=server_port, debug=True)