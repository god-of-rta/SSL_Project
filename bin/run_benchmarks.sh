ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PY=""
for c in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python" python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import pandas" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "run_benchmarks.sh: need Python with pandas (pip install -r requirements.txt)" >&2; exit 1; fi

if [ "$1" = "--quick" ]; then
    "$PY" bin/profile_run.py --sizes 20000 100000 --no-profile || exit 1
else
    "$PY" bin/profile_run.py || exit 1
fi

if command -v gnuplot >/dev/null 2>&1; then
    gnuplot scripts/plot_benchmarks.gp && echo "plots: docs/benchmarks/runtime.svg, before_after.svg"
else
    echo "gnuplot not installed, skipped the plots (data file: docs/benchmarks/runtime.dat)"
fi
echo "Done. Tables are in docs/benchmarks/results.md"