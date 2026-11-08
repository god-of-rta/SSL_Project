#!/usr/bin/env bash
# bin/start.sh - Start collector.sh in the background with nohup
# Exit codes: 0 = started, 2 = already running, 1 = error

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

FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval)
            if [[ $# -lt 2 ]] || ! [[ "$2" =~ ^[0-9]+$ ]] || [[ "$2" -le 0 ]]; then
                echo "Error: --interval requires a positive integer" >&2
                exit 1
            fi
            FORWARD_ARGS+=("--interval" "$2")
            shift 2
            ;;
        --interval=*)
            val="${1#*=}"
            if ! [[ "$val" =~ ^[0-9]+$ ]] || [[ "$val" -le 0 ]]; then
                echo "Error: --interval requires a positive integer" >&2
                exit 1
            fi
            FORWARD_ARGS+=("--interval" "$val")
            shift
            ;;
        --simulate-temp)
            FORWARD_ARGS+=("--simulate-temp")
            shift
            ;;
        *)
            echo "Error: Unknown argument '$1'" >&2
            exit 1
            ;;
    esac
done

if "$SCRIPT_DIR/status.sh"; then
    echo "Collector is already running." >&2
    exit 2
fi

DATA_PATH="$REPO_ROOT/$DATA_DIR"
mkdir -p "$DATA_PATH/raw"
LOG_FILE="$DATA_PATH/collector.log"
PID_FILE="$DATA_PATH/collector.pid"

nohup "$SCRIPT_DIR/collector.sh" "${FORWARD_ARGS[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
NEW_PID=$!
printf "%d\n" "$NEW_PID" > "$PID_FILE"

# Brief pause to verify the process did not immediately fail
sleep 0.2
if ! "$SCRIPT_DIR/status.sh"; then
    echo "Error: Collector failed to start. Check $LOG_FILE." >&2
    exit 1
fi

exit 0