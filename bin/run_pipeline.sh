#!/usr/bin/env bash
# SysMon Pipeline Runner (P1: Integrator + Analysis)
# Runs cleaner, analyzer, alerts in order.
# Stops at the first failure, logs to data/pipeline.log,
# and atomically writes data/pipeline_status.json.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

DATA_DIR="$ROOT_DIR/data"
CLEAN_DIR="$DATA_DIR/clean"
RAW_DIR="$DATA_DIR/raw"
CONFIG_DIR="$ROOT_DIR/config"
LOG_FILE="$DATA_DIR/pipeline.log"
STATUS_FILE="$DATA_DIR/pipeline_status.json"

mkdir -p "$DATA_DIR" "$CLEAN_DIR" "$RAW_DIR"

# Select Python binary (prefer repo venv if available)
if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

START_EPOCH=$("$PYTHON_BIN" -c "import time; print(f'{time.time():.4f}')")
START_INT=${START_EPOCH%.*}

echo "=== Pipeline run started at $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===" >> "$LOG_FILE"

FAILED_STAGE=""
STAGE_OK=true

# Stage 1: Cleaner
if [ -f "$ROOT_DIR/src/cleaner.py" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running cleaner..." >> "$LOG_FILE"
    if ! "$PYTHON_BIN" "$ROOT_DIR/src/cleaner.py" \
        --input-dir "$RAW_DIR" \
        --output "$CLEAN_DIR/metrics.csv" \
        --report "$CLEAN_DIR/cleaning_report.json" >> "$LOG_FILE" 2>&1; then
        FAILED_STAGE="cleaner"
        STAGE_OK=false
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Cleaner stage failed" >> "$LOG_FILE"
    fi
else
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Cleaner stage skipped (src/cleaner.py not present)" >> "$LOG_FILE"
fi

# Stage 2: Analyzer
if [ "$STAGE_OK" = true ]; then
    if [ -f "$ROOT_DIR/src/analyzer.py" ]; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running analyzer..." >> "$LOG_FILE"
        if ! "$PYTHON_BIN" "$ROOT_DIR/src/analyzer.py" \
            --input "$CLEAN_DIR/metrics.csv" \
            --processes-dir "$RAW_DIR" \
            --output "$DATA_DIR/stats.json" >> "$LOG_FILE" 2>&1; then
            FAILED_STAGE="analyzer"
            STAGE_OK=false
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Analyzer stage failed" >> "$LOG_FILE"
        fi
    else
        FAILED_STAGE="analyzer"
        STAGE_OK=false
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Analyzer stage missing (src/analyzer.py not found)" >> "$LOG_FILE"
    fi
fi

# Stage 3: Alerts
if [ "$STAGE_OK" = true ]; then
    if [ -f "$ROOT_DIR/src/alerts.py" ]; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running alerts..." >> "$LOG_FILE"
        THRESHOLDS_CONF="$CONFIG_DIR/thresholds.conf"
        if [ ! -f "$THRESHOLDS_CONF" ]; then
            # Initialize defaults if alerts module supports it
            "$PYTHON_BIN" "$ROOT_DIR/src/alerts.py" --init-defaults >> "$LOG_FILE" 2>&1 || true
        fi
        if ! "$PYTHON_BIN" "$ROOT_DIR/src/alerts.py" \
            --input "$CLEAN_DIR/metrics.csv" \
            --processes-dir "$RAW_DIR" \
            --thresholds "$THRESHOLDS_CONF" \
            --state "$DATA_DIR/alert_state.json" \
            --alerts "$DATA_DIR/alerts.csv" >> "$LOG_FILE" 2>&1; then
            FAILED_STAGE="alerts"
            STAGE_OK=false
            echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Alerts stage failed" >> "$LOG_FILE"
        fi
    else
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Alerts stage skipped (src/alerts.py not present)" >> "$LOG_FILE"
    fi
fi

END_EPOCH=$("$PYTHON_BIN" -c "import time; print(f'{time.time():.4f}')")
DURATION_S=$("$PYTHON_BIN" -c "print(round(max(0.0, float($END_EPOCH) - float($START_EPOCH)), 2))")

# Format status JSON atomically
STATUS_TMP="$STATUS_FILE.tmp.$$"
if [ "$STAGE_OK" = true ]; then
    FAILED_JSON="null"
    EXIT_CODE=0
else
    FAILED_JSON="\"$FAILED_STAGE\""
    EXIT_CODE=1
fi

cat <<EOF > "$STATUS_TMP"
{
  "last_run_epoch": $START_INT,
  "duration_s": $DURATION_S,
  "ok": $STAGE_OK,
  "failed_stage": $FAILED_JSON
}
EOF

mv "$STATUS_TMP" "$STATUS_FILE"

echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Pipeline finished with exit code $EXIT_CODE (duration: ${DURATION_S}s)" >> "$LOG_FILE"
exit "$EXIT_CODE"
