from flask import Flask, jsonify, request, send_from_directory
import subprocess
import json
import csv
import os
import threading
import time

app = Flask(__name__, static_folder='../web', static_url_path='')

# Paths based on repository layout
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
BIN_DIR = os.path.join(os.path.dirname(__file__), '..', 'bin')
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), '..', 'tests', 'fixtures')

# Lock for the background pipeline thread
pipeline_lock = threading.Lock()

def get_file_path(filename, subfolder=""):
    """Helper to check data/ first, then fallback to tests/fixtures/"""
    data_path = os.path.join(DATA_DIR, subfolder, filename) if subfolder else os.path.join(DATA_DIR, filename)
    if os.path.exists(data_path):
        return data_path
    
    fixture_path = os.path.join(FIXTURES_DIR, filename)
    if os.path.exists(fixture_path):
        return fixture_path
        
    return data_path  # To return data path by default if neither exists

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
    # Eventually we will read data/pipeline_status.json and call bin/status.sh
    return jsonify({
        "server_time_epoch": int(time.time()),
        "collector": {"running": False, "pid": None, "last_sample_epoch": None},
        "pipeline": {"last_run_epoch": None, "duration_s": None, "ok": True, "failed_stage": None}
    })

@app.route('/api/system', methods=['GET'])
def get_system():
    sys_info_path = get_file_path('system_info.json')
    if os.path.exists(sys_info_path):
        with open(sys_info_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({})  # Valid empty JSON when file missing

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats_path = get_file_path('stats.json')
    if os.path.exists(stats_path):
        with open(stats_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({})  # Valid empty JSON when file missing[cite: 3]

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    time_range = request.args.get('range', '1h')
    metrics_path = get_file_path('clean_metrics.csv')
    
    columns = []
    rows = []
    
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            reader = csv.reader(f)
            try:
                columns = next(reader)
                for row in reader:
                    parsed_row = []
                    for val in row:
                        if val == '':
                            parsed_row.append(None)  # To handle missing values
                        else:
                            try:
                                parsed_row.append(float(val) if '.' in val else int(val))
                            except ValueError:
                                parsed_row.append(val)
                    rows.append(parsed_row)
            except StopIteration:
                pass
                
    # Downsampling is pending # optimisation is required from my side
    return jsonify({"range": time_range, "step_s": 5, "columns": columns, "rows": rows})

@app.route('/api/alerts', methods=['GET']) 
def get_alerts():
    alerts_path = get_file_path('alerts.csv')
    acks_path = get_file_path('acks.json')
    
    acks = []
    if os.path.exists(acks_path):
        with open(acks_path, 'r') as f:
            try:
                acks = json.load(f).get('acknowledged_ids', [])
            except json.JSONDecodeError:
                pass

    alerts_list = []
    if os.path.exists(alerts_path):
        with open(alerts_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # To convert no. accordingly
                row['id'] = int(row['id']) if row.get('id') else 0
                row['epoch'] = int(row['epoch']) if row.get('epoch') else 0
                row['value'] = float(row['value']) if row.get('value') else None
                row['threshold'] = float(row['threshold']) if row.get('threshold') else None
                
                # Merge acknowledged status
                row['acknowledged'] = 1 if row['id'] in acks else 0
                
                alerts_list.append(row)
                
    # Sort newest first based on epoch
    alerts_list.sort(key=lambda x: x['epoch'], reverse=True)
    # optimisation is required from my side
    return jsonify({"alerts": alerts_list})

# --- Collector Control ---
@app.route('/api/collector/start', methods=['POST'])
def start_collector():
    result = subprocess.run([os.path.join(BIN_DIR, 'start.sh')], capture_output=True)
    return jsonify({"ok": result.returncode == 0, "running": True})

@app.route('/api/collector/stop', methods=['POST'])
def stop_collector():
    result = subprocess.run([os.path.join(BIN_DIR, 'stop.sh')], capture_output=True)
    return jsonify({"ok": result.returncode == 0, "running": False})

@app.route('/api/pipeline/run', methods=['POST'])
def run_pipeline_now():
    if pipeline_lock.locked():
        return jsonify({"ok": False, "error": "Pipeline is already running"})
    
    def trigger_pipeline():
        with pipeline_lock:
            subprocess.run([os.path.join(BIN_DIR, 'run_pipeline.sh')])
            
    threading.Thread(target=trigger_pipeline).start()
    return jsonify({"ok": True})

if __name__ == '__main__':
    # To run on local host
    app.run(host='127.0.0.1', port=5000, debug=True)