#!/usr/bin/env bash
# bin/stop.sh - Stop running collector.sh cleanly
# Exit codes: 0 = stopped or was not running, 1 = error

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

DATA_DIR="data"
CONFIG_FILE="$REPO_ROOT/config/sysmon.conf"
if [[ -f "$CONFIG_FILE" ]]; then
    cfg_dir="$(awk -F'=' '/^[[:space:]]*data_dir[[:space:]]*=/ {
        val=$2; sub(/[#;].*$/, "", val); gsub(/^[[:space:]]+|[[:space:]]+$/, "", val); print val
    }' "$CONFIG_FILE")"
    [[ -n "$cfg_dir" ]] && DATA_DIR="$cfg_dir"
fi

PID_FILE="$REPO_ROOT/$DATA_DIR/collector.pid"
LOCK_FILE="$REPO_ROOT/$DATA_DIR/collector.lock"

if ! "$SCRIPT_DIR/status.sh"; then
    rm -f "$PID_FILE" "$LOCK_FILE"
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$PID" ]]; then
    rm -f "$PID_FILE" "$LOCK_FILE"
    exit 0
fi

if ! kill -TERM "$PID" 2>/dev/null; then
    rm -f "$PID_FILE" "$LOCK_FILE"
    exit 0
fi

# Wait up to 5 seconds for clean termination
for _ in {1..50}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE" "$LOCK_FILE"
        exit 0
    fi
    sleep 0.1
done

# Fallback SIGKILL if stuck
if kill -0 "$PID" 2>/dev/null; then
    kill -KILL "$PID" 2>/dev/null || true
    sleep 0.1
fi

if kill -0 "$PID" 2>/dev/null; then
    echo "Error: Failed to stop collector process $PID" >&2
    exit 1
fi

rm -f "$PID_FILE" "$LOCK_FILE"
exit 0