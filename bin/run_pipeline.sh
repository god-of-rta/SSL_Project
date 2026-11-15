#!/usr/bin/env bash
# SysMon Pipeline Runner
# Runs cleaner, analyzer, alerts in order.
# Stops at the first failure, logs to data/pipeline.log,
# and atomically writes data/pipeline_status.json.

set -u

# Avoid spawning full-core OpenBLAS/OMP thread pools per short-lived Python subprocess
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

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

read_pipeline_state() {
    "$PYTHON_BIN" -c '
import configparser, datetime, os, re, sys, time

raw_dir, conf_path, alerts_path = sys.argv[1], sys.argv[2], sys.argv[3]
now_ts = time.time()
ret_h = 168
if os.path.isfile(conf_path):
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(conf_path, encoding="utf-8")
        ret_h = max(0, int(cfg.get("sysmon", "retention_hours", fallback="168")))
    except Exception:
        ret_h = 168

cutoff = int(now_ts) - ret_h * 3600 if ret_h > 0 else 0
may_expire = False
dated_re = re.compile(r"^(?:metrics|processes)_(\d{4})(\d{2})(\d{2})\.csv$")

def first_row_expired(path, cut):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            hdr = f.readline()
            if not hdr:
                return False
            cols = [c.strip() for c in hdr.strip("\r\n").split(",")]
            if "epoch" not in cols:
                return False
            idx = cols.index("epoch")
            for line in f:
                if not line.strip():
                    continue
                parts = line.split(",")
                if idx < len(parts):
                    try:
                        return int(float(parts[idx].strip())) < cut
                    except ValueError:
                        return False
                return False
    except OSError:
        return False
    return False

items = []
if os.path.isdir(raw_dir):
    for e in sorted(os.scandir(raw_dir), key=lambda x: x.name):
        if e.is_file() and e.name.endswith(".csv") and (e.name.startswith("metrics_") or e.name.startswith("processes_")):
            st = e.stat()
            items.append(f"{e.name}:{st.st_size}:{st.st_mtime_ns}")
            if ret_h > 0 and not may_expire:
                m = dated_re.match(e.name)
                if m:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    try:
                        d_start = int(datetime.datetime(y, mo, d, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
                        d_end = int(datetime.datetime(y, mo, d, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp())
                        if d_end < cutoff or (d_start < cutoff and first_row_expired(e.path, cutoff)):
                            may_expire = True
                    except ValueError:
                        pass

if ret_h > 0 and not may_expire and os.path.isfile(alerts_path):
    if first_row_expired(alerts_path, cutoff):
        may_expire = True

print(f"{now_ts:.4f}|{ret_h}|{1 if may_expire else 0}|" + ";".join(items))
' "$RAW_DIR" "$CONFIG_DIR/sysmon.conf" "$DATA_DIR/alerts.csv"
}

START_INFO="$(read_pipeline_state)"
START_EPOCH="${START_INFO%%|*}"
REST="${START_INFO#*|}"
RETENTION_HOURS="${REST%%|*}"
REST="${REST#*|}"
MAY_EXPIRE="${REST%%|*}"
RAW_SIG="${REST#*|}"
START_INT=${START_EPOCH%.*}
STAMP_FILE="$DATA_DIR/.pipeline_raw_stamp"
RAW_UNCHANGED=false
if [ "$MAY_EXPIRE" = "0" ] && [ -f "$STAMP_FILE" ] && [ -f "$CLEAN_DIR/metrics.csv" ] && [ -f "$DATA_DIR/stats.json" ] && [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$RAW_SIG" ]; then
    RAW_UNCHANGED=true
fi

echo "=== Pipeline run started at $(date -u +'%Y-%m-%dT%H:%M:%SZ') ===" >> "$LOG_FILE"

FAILED_STAGE=""
STAGE_OK=true

if [ "$RAW_UNCHANGED" = true ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Raw inputs unchanged; skipping cleaner and analyzer" >> "$LOG_FILE"
else
    # Stage 1: Cleaner
    if [ -f "$ROOT_DIR/src/cleaner.py" ]; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Running cleaner..." >> "$LOG_FILE"
        if ! "$PYTHON_BIN" "$ROOT_DIR/src/cleaner.py" \
            --input-dir "$RAW_DIR" \
            --output "$CLEAN_DIR/metrics.csv" \
            --report "$CLEAN_DIR/cleaning_report.json" \
            --retention-hours "$RETENTION_HOURS" \
            --alerts "$DATA_DIR/alerts.csv" >> "$LOG_FILE" 2>&1; then
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

    if [ "$STAGE_OK" = true ]; then
        if [ "$MAY_EXPIRE" = "1" ]; then
            POST_INFO="$(read_pipeline_state)"
            RAW_SIG="${POST_INFO##*|}"
        fi
        printf "%s" "$RAW_SIG" > "$STAMP_FILE"
    fi
fi

# Stage 3: Alerts
if [ "$STAGE_OK" = true ]; then
    ALERTS_ENABLED=true
    if [ -f "$CONFIG_DIR/sysmon.conf" ]; then
        CONF_ALERTS=$(grep -E "^\s*alerts_enabled\s*=" "$CONFIG_DIR/sysmon.conf" | tail -n1 | cut -d'=' -f2 | tr -d ' ' || true)
        if [ "$CONF_ALERTS" = "false" ]; then
            ALERTS_ENABLED=false
        fi
    fi

    if [ "$ALERTS_ENABLED" = false ]; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Alerts stage skipped (alerts_enabled = false in sysmon.conf)" >> "$LOG_FILE"
    elif [ -f "$ROOT_DIR/src/alerts.py" ]; then
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
