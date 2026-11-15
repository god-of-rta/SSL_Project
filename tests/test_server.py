import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch

# Add the root directory to sys.path so we can import src.server
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.server import app, pipeline_lock

@pytest.fixture
def client(monkeypatch):
    """Provides a safe Flask test client with mocked file paths and blocked shell execution."""
    app.config['TESTING'] = True
    
    # Safe temporary directory for write operations to make sure data isn't corrupted
    with tempfile.TemporaryDirectory() as temp_dir:
        # Redirect all server write paths to the safe temp folder
        monkeypatch.setattr('src.server.DATA_DIR', temp_dir)
        monkeypatch.setattr('src.server.CONFIG_DIR', temp_dir)
        
        # Intercept subprocess.run to prevent real shell scripts from executing
        with patch('src.server.subprocess.run') as mock_run:
            # To tell the fake subprocess to always simulate a success (exit code 0)
            mock_run.return_value.returncode = 0
            
            # Ensure the pipeline lock is released before every test
            if pipeline_lock.locked():
                pipeline_lock.release()
                
            with app.test_client() as client:
                yield client

# --- 1. Status and Read-Only Endpoints ---

def test_get_status(client):
    """Test that /api/status returns the correct JSON structure."""
    response = client.get('/api/status')
    assert response.status_code == 200
    data = response.get_json()
    assert "server_time_epoch" in data
    assert "collector" in data
    assert "pipeline" in data

def test_get_system(client):
    response = client.get('/api/system')
    assert response.status_code == 200
    assert isinstance(response.get_json(), dict)

def test_get_stats(client):
    response = client.get('/api/stats')
    assert response.status_code == 200
    assert isinstance(response.get_json(), dict)

def test_get_metrics(client):
    response = client.get('/api/metrics?range=1h')
    assert response.status_code == 200
    data = response.get_json()
    assert "range" in data
    assert "columns" in data
    assert "rows" in data

def test_get_alerts(client):
    response = client.get('/api/alerts')
    assert response.status_code == 200
    data = response.get_json()
    assert "alerts" in data
    assert isinstance(data["alerts"], list)

def test_get_config(client):
    response = client.get('/api/config')
    assert response.status_code == 200
    data = response.get_json()
    assert "sysmon" in data
    assert "thresholds" in data

# --- 2. Collector & Pipeline Control Endpoints ---

def test_collector_start(client):
    # Mocking intercepts start.sh so it won't actually run on our PC
    with patch('src.server.os.path.exists', return_value=True):
        response = client.post('/api/collector/start')
        data = response.get_json()
        assert data["ok"] is True
        assert data["running"] is True

def test_collector_stop(client):
    with patch('src.server.os.path.exists', return_value=True):
        response = client.post('/api/collector/stop')
        data = response.get_json()
        assert data["ok"] is True
        assert data["running"] is False

def test_pipeline_run(client):
    with patch('src.server.os.path.exists', return_value=True):
        response = client.post('/api/pipeline/run')
        data = response.get_json()
        assert data["ok"] is True

def test_pipeline_already_running(client):
    """Test that triggering a pipeline run while locked returns a 400 error."""
    with patch('src.server.os.path.exists', return_value=True):
        # Manually lock the thread to simulate a running pipeline
        pipeline_lock.acquire()
        response = client.post('/api/pipeline/run')
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

# --- 3. Configuration Validation & Profile Tests ---

def test_config_validation_bad_interval(client):
    payload = {"sysmon": {"interval_s": -5}}
    response = client.post('/api/config', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert data["field"] == "sysmon.interval_s"

def test_config_validation_invalid_metric(client):
    payload = {"thresholds": {"bad_rule": {"metric": "not_a_real_metric"}}}
    response = client.post('/api/config', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert "thresholds.bad_rule.metric" in data["field"]

def test_config_validation_invalid_operator(client):
    payload = {"thresholds": {"bad_op_rule": {"op": "=="}}}
    response = client.post('/api/config', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert "thresholds.bad_op_rule.op" in data["field"]

def test_config_delete_rule(client):
    """Test that setting a rule body to null deletes it (except no_data)."""
    payload = {"thresholds": {"cpu_high": None}}
    response = client.post('/api/config', json=payload)
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

def test_config_delete_nodata_forbidden(client):
    """Test that attempting to delete the no_data rule returns a 400 error."""
    payload = {"thresholds": {"no_data": None}}
    response = client.post('/api/config', json=payload)
    assert response.status_code == 400
    assert response.get_json()["ok"] is False

def test_config_profile_validation(client):
    payload = {"profile": "unknown_profile"}
    response = client.post('/api/config/profile', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert data["field"] == "profile"

def test_config_profile_valid(client):
    with patch('src.server.os.path.exists', return_value=True):
        payload = {"profile": "demo"}
        response = client.post('/api/config/profile', json=payload)
        assert response.status_code == 200
        assert response.get_json()["ok"] is True

# --- 4. Alert Actions ---

def test_alert_ack(client):
    payload = {"ids": [1, 2, 3]}
    response = client.post('/api/alerts/ack', json=payload)
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

def test_alert_ack_all(client):
    payload = {"all": True}
    response = client.post('/api/alerts/ack', json=payload)
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

def test_test_alert(client):
    with patch('src.server.os.path.exists', return_value=True):
        response = client.post('/api/alerts/test')
        assert response.get_json()["ok"] is True


def test_config_retention_hours_validation_and_persistence(client):
    """Test saving valid retention_hours (including 0 to disable) and rejecting negative values."""
    bad = client.post('/api/config', json={"sysmon": {"retention_hours": -1}})
    assert bad.status_code == 400
    assert bad.get_json()["field"] == "sysmon.retention_hours"

    ok_save = client.post('/api/config', json={"sysmon": {"retention_hours": 0}})
    assert ok_save.status_code == 200
    assert ok_save.get_json()["ok"] is True

    cfg = client.get('/api/config').get_json()
    assert cfg["sysmon"]["retention_hours"] == 0


def test_alerts_clear(client, tmp_path):
    """Test clearing resolved alerts and clearing all alerts."""
    alerts_file = tmp_path / "alerts.csv"
    alerts_file.write_text(
        "id,epoch,iso_time,rule,metric,value,threshold,severity,state,process,acknowledged\n"
        "1,100,2026-11-14T00:00:00Z,cpu_high,cpu_pct,90,85,warning,firing,bash,0\n"
        "2,105,2026-11-14T00:00:05Z,cpu_high,cpu_pct,40,85,warning,resolved,bash,0\n"
    )
    with patch('src.server.get_file_path', side_effect=lambda f: str(tmp_path / f)):
        # Clear resolved
        res = client.post('/api/alerts/clear', json={"scope": "resolved"})
        assert res.status_code == 200
        assert res.get_json()["cleared"] == 1
        assert res.get_json()["remaining"] == 1

        # Clear all
        res_all = client.post('/api/alerts/clear', json={"scope": "all"})
        assert res_all.status_code == 200
        assert res_all.get_json()["cleared"] == 1
        assert res_all.get_json()["remaining"] == 0


def test_alerts_toggle(client):
    """Test toggling alerts_enabled state."""
    res1 = client.post('/api/alerts/toggle', json={"enabled": False})
    assert res1.status_code == 200
    assert res1.get_json()["alerts_enabled"] is False

    res2 = client.post('/api/alerts/toggle')
    assert res2.status_code == 200
    assert res2.get_json()["alerts_enabled"] is True