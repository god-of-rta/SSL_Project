#!/usr/bin/env bash
# bin/demo_benchmarks.sh - Run SysMon live performance benchmarks demo

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PY=""
for c in "$ROOT/.venv/bin/python3" "$ROOT/.venv/bin/python" "$ROOT/venv/bin/python" python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "Error: Python 3 not found. Please create a virtual environment first." >&2
    exit 1
fi

exec "$PY" "$ROOT/bin/demo_benchmarks.py" "$@"
