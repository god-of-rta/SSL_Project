#!/usr/bin/env bash
# run_tests.sh - run test suite and print a pass/fail summary
# Usage: ./run_tests.sh        Exit code 0 only if everything passes.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

PY=""
for c in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python" python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" --version >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "run_tests.sh: python not found" >&2; exit 1; fi

FAILED=()
PASSED=()

echo "==> Python unit tests (pytest)"
if "$PY" -m pytest tests -q; then PASSED+=("pytest"); else FAILED+=("pytest"); fi

for t in tests/*.sh; do
    [ -f "$t" ] || continue
    echo "==> $t"
    if bash "$t"; then PASSED+=("$t"); else FAILED+=("$t"); fi
done

echo
echo "================ SUMMARY ================"
for n in "${PASSED[@]}"; do echo "PASS  $n"; done
for n in "${FAILED[@]}"; do echo "FAIL  $n"; done
echo "========================================="
if [ "${#FAILED[@]}" -eq 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "${#FAILED[@]} suite(s) FAILED"; exit 1