from flask import Flask, jsonify, request, send_from_directory
import subprocess
import json
import os
import threading
import time

app = Flask(__name__, static_folder='../web', static_url_path='')

# Paths based on repository layout
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
BIN_DIR = os.path.join(os.path.dirname(__file__), '..', 'bin')
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')

# Lock for the background pipeline thread
pipeline_lock = threading.Lock()

# --- Static File Serving ---
@app.route('/')
def serve_index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

# --- Read-Only Data Endpoints ---
@app.route('/api/status', methods=['GET'])
def get_status():
    # To read data/pipeline_status.json and call bin/status.sh
    return jsonify({
        "server_time_epoch": int(time.time()),
        "collector": {"running": False, "pid": None, "last_sample_epoch": None},
        "pipeline": {"last_run_epoch": None, "duration_s": None, "ok": True, "failed_stage": None}
    })

@app.route('/api/system', methods=['GET'])
def get_system():
    sys_info_path = os.path.join(DATA_DIR, 'system_info.json')
    if os.path.exists(sys_info_path):
        with open(sys_info_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats_path = os.path.join(DATA_DIR, 'stats.json')
    if os.path.exists(stats_path):
        with open(stats_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({})

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    time_range = request.args.get('range', '1h')
    # to implement reading from data/clean/metrics.csv and downsampling
    return jsonify({"range": time_range, "step_s": 5, "columns": [], "rows": []})

@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    # to read data/alerts.csv and data/acks.json
    return jsonify({"alerts": []})

# --- Collector Control ---
@app.route('/api/collector/start', methods=['POST'])
def start_collector():
    # Execute bin/start.sh
    result = subprocess.run([os.path.join(BIN_DIR, 'start.sh')], capture_output=True)
    return jsonify({"ok": result.returncode == 0, "running": True})

@app.route('/api/collector/stop', methods=['POST'])
def stop_collector():
    # Execute bin/stop.sh
    result = subprocess.run([os.path.join(BIN_DIR, 'stop.sh')], capture_output=True)
    return jsonify({"ok": result.returncode == 0, "running": False})

@app.route('/api/pipeline/run', methods=['POST'])
def run_pipeline_now():
    if pipeline_lock.locked():
        return jsonify({"ok": False, "error": "Pipeline is already running"})
    
    # Normally this is triggered by the background thread, but this forces an immediate run
    def trigger_pipeline():
        with pipeline_lock:
            subprocess.run([os.path.join(BIN_DIR, 'run_pipeline.sh')])
            
    threading.Thread(target=trigger_pipeline).start()
    return jsonify({"ok": True})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)