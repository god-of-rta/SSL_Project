#!/usr/bin/env bash
# bin/status.sh - Check if collector.sh is running (0 = running, 1 = not running)

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

if [[ ! -f "$PID_FILE" ]]; then
    exit 1
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$PID" ]] || ! [[ "$PID" =~ ^[0-9]+$ ]]; then
    rm -f "$PID_FILE"
    exit 1
fi

if kill -0 "$PID" 2>/dev/null; then
    # Verify it is not a recycled PID from an unrelated process
    CMDLINE="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || ps -p "$PID" -o args= 2>/dev/null || true)"
    if [[ "$CMDLINE" == *"collector.sh"* ]]; then
        exit 0
    fi
fi

# Stale PID file detected
rm -f "$PID_FILE"
exit 1