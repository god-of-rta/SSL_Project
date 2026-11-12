#!/usr/bin/env bash
# bin/stress.sh - bounded CPU + memory load to trigger alerts in the demo (owner: P5)
#
# Usage: bin/stress.sh [seconds]     (default 30, hard maximum 120)
#
# Safety: runs for at most 120 s, uses at most MEM_MB of RAM, and always
# kills everything it started (on finish, Ctrl-C or SIGTERM).

DEFAULT_SECS=30
MAX_SECS=120
MEM_MB=300          # hard cap on the memory load

SECS="${1:-$DEFAULT_SECS}"
case "$SECS" in
    ''|*[!0-9]*) echo "stress.sh: seconds must be a whole number, got '$SECS'" >&2; exit 1 ;;
esac
if [ "$SECS" -lt 1 ]; then
    echo "stress.sh: seconds must be at least 1" >&2; exit 1
fi
if [ "$SECS" -gt "$MAX_SECS" ]; then
    echo "stress.sh: capping $SECS s to the maximum of $MAX_SECS s" >&2
    SECS=$MAX_SECS
fi

PIDS=()
cleanup() {
    trap - EXIT INT TERM
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
    echo "stress.sh: stopped, load removed"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CORES=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)

# CPU load: one busy loop per core
for _ in $(seq 1 "$CORES"); do
    ( while :; do :; done ) &
    PIDS+=($!)
done

# Memory load: hold MEM_MB of RAM (needs python3 or python; skipped if neither exists)
PY=$(command -v python3 || command -v python || true)
if [ -n "$PY" ]; then
    "$PY" -c "
import time
buf = bytearray($MEM_MB * 1024 * 1024)
for i in range(0, len(buf), 4096):   # touch every page so it is really used
    buf[i] = 1
time.sleep($SECS + 5)
" &
    PIDS+=($!)
else
    echo "stress.sh: no python found, skipping the memory load" >&2
fi

echo "stress.sh: loading $CORES CPU core(s) and ${MEM_MB} MB of RAM for ${SECS} s"
sleep "$SECS"
exit 0