import argparse
import csv
import filecmp
import json
import os
import platform
import pstats
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PY = sys.executable
CLEANER = os.path.join(ROOT, "src", "cleaner.py")
FAST = os.path.join(ROOT, "src", "cleaner_fast.py")
ANALYZER = os.path.join(ROOT, "src", "analyzer.py")
GEN = os.path.join(ROOT, "bin", "gen_data.py")


def run(cmd):
    """Run a command, return (wall_seconds, None). Stops with a message on failure."""
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True)
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        sys.exit("command failed: %s\n%s" % (" ".join(cmd), proc.stderr.decode(errors="replace")[-800:]))
    return wall, None


def run_with_memory(cmd):
    """Like run(), but also reports this command's own peak memory (MB).
    Peak memory is only available on Linux/macOS (os.wait4); on Windows it is None."""
    if not hasattr(os, "wait4"):
        return run(cmd)
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, status, usage = os.wait4(proc.pid, 0)
    wall = time.perf_counter() - start
    proc.returncode = os.waitstatus_to_exitcode(status)
    if proc.returncode != 0:
        sys.exit("command failed (exit %d): %s" % (proc.returncode, " ".join(cmd)))
    scale = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0   # ru_maxrss: bytes on macOS, KB on Linux
    return wall, usage.ru_maxrss / scale


def profile_top(script, args, prof_path, top=6):
    """Run cProfile on a script and return (total_seconds, [top functions by own time])."""
    cmd = [PY, "-m", "cProfile", "-o", prof_path, script] + args
    subprocess.run(cmd, check=True, capture_output=True)
    st = pstats.Stats(prof_path)
    total = st.total_tt
    rows = []
    for (fname, line, name), (cc, nc, tt, ct, _) in sorted(st.stats.items(), key=lambda kv: -kv[1][2])[:top]:
        short = "%s:%d(%s)" % (os.path.basename(fname), line, name)
        rows.append({"function": short, "calls": nc, "own_s": round(tt, 3),
                     "cumulative_s": round(ct, 3), "pct_of_total": round(100 * tt / total, 1)})
    return round(total, 2), rows


def newest_epoch(raw_dir):
    best = 0
    for name in sorted(os.listdir(raw_dir)):
        if name.startswith("metrics_"):
            with open(os.path.join(raw_dir, name), newline="") as fh:
                for rec in csv.DictReader(fh):
                    best = max(best, int(rec["epoch"]))
    return best


# ---- one statistic three ways: mean CPU per hour --------------------------
def hourly_awk(raw_dir):
    files = sorted(os.path.join(raw_dir, f) for f in os.listdir(raw_dir) if f.startswith("metrics_"))
    prog = 'FNR>1 && $3>=0 && $3<=100 {h=int($1/3600); s[h]+=$3; n[h]++} END{for(h in s) printf "%d,%.2f\\n", h, s[h]/n[h]}'
    out = subprocess.run(["awk", "-F,", prog] + files, capture_output=True, text=True, check=True).stdout
    return {int(a): float(b) for a, b in (ln.split(",") for ln in out.split())}


def hourly_python(raw_dir):
    s, n = defaultdict(float), defaultdict(int)
    for f in sorted(os.listdir(raw_dir)):
        if f.startswith("metrics_"):
            with open(os.path.join(raw_dir, f), newline="") as fh:
                for rec in csv.DictReader(fh):
                    try:
                        v = float(rec["cpu_pct"])
                        h = int(rec["epoch"]) // 3600
                    except ValueError:
                        continue
                    if 0 <= v <= 100:
                        s[h] += v
                        n[h] += 1
    return {h: round(s[h] / n[h], 2) for h in s}


def hourly_pandas(raw_dir):
    import pandas as pd
    frames = [pd.read_csv(os.path.join(raw_dir, f), usecols=["epoch", "cpu_pct"])
              for f in sorted(os.listdir(raw_dir)) if f.startswith("metrics_")]
    df = pd.concat(frames)
    df["epoch"] = pd.to_numeric(df["epoch"], errors="coerce")
    df["cpu_pct"] = pd.to_numeric(df["cpu_pct"], errors="coerce")
    df = df[(df.cpu_pct >= 0) & (df.cpu_pct <= 100)].dropna()
    g = df.groupby(df.epoch.astype("int64") // 3600).cpu_pct.mean().round(2)
    return {int(k): float(v) for k, v in g.items()}


def timed(fn, *a):
    t = time.perf_counter()
    res = fn(*a)
    return time.perf_counter() - t, res


def main():
    ap = argparse.ArgumentParser(description="SysMon profiling and benchmarks")
    ap.add_argument("--sizes", type=int, nargs="+", default=[100000, 500000, 1000000])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "benchmarks"))
    ap.add_argument("--no-profile", action="store_true", help="skip cProfile runs (faster)")
    ap.add_argument("--keep", action="store_true", help="keep the generated data")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    res = {"machine": {"python": platform.python_version(), "platform": platform.platform(),
                       "cpu_count": os.cpu_count()}, "seed": args.seed, "sizes": {}}
    try:
        import pandas
        res["machine"]["pandas"] = pandas.__version__
    except ImportError:
        sys.exit("pandas is needed for the fast cleaner: pip install pandas")

    work = tempfile.mkdtemp(prefix="sysmon_bench_")
    try:
        for n in args.sizes:
            print("=== %d rows ===" % n, flush=True)
            raw = os.path.join(work, "raw_%d" % n)
            subprocess.run([PY, GEN, "--rows", str(n), "--out", raw, "--seed", str(args.seed)],
                           check=True, capture_output=True)
            r = {}
            clean_p, rep_p = os.path.join(work, "plain.csv"), os.path.join(work, "plain.json")
            clean_f, rep_f = os.path.join(work, "fast.csv"), os.path.join(work, "fast.json")
            stats = os.path.join(work, "stats.json")

            w, m = run_with_memory([PY, CLEANER, "--input-dir", raw, "--output", clean_p, "--report", rep_p])
            r["cleaner_plain"] = {"wall_s": round(w, 2), "peak_mb": m and round(m)}
            w, m = run_with_memory([PY, FAST, "--input-dir", raw, "--output", clean_f, "--report", rep_f])
            r["cleaner_fast"] = {"wall_s": round(w, 2), "peak_mb": m and round(m)}
            r["cleaner_fast"]["identical_csv"] = filecmp.cmp(clean_p, clean_f, shallow=False)
            r["cleaner_fast"]["speedup"] = round(r["cleaner_plain"]["wall_s"] / r["cleaner_fast"]["wall_s"], 2)
            print("  cleaner  plain %.1fs  fast %.1fs  identical=%s" % (
                r["cleaner_plain"]["wall_s"], r["cleaner_fast"]["wall_s"], r["cleaner_fast"]["identical_csv"]), flush=True)

            a_args = ["--input", clean_p, "--processes-dir", raw, "--output", stats]
            w, m = run_with_memory([PY, ANALYZER] + a_args)
            r["analyzer"] = {"wall_s": round(w, 2), "peak_mb": m and round(m)}
            print("  analyzer %.1fs" % w, flush=True)

            # incremental: only the newest hour of data
            since = str(newest_epoch(raw) - 3600)
            w1, _ = run([PY, CLEANER, "--input-dir", raw, "--output", os.path.join(work, "i.csv"),
                         "--report", os.path.join(work, "i.json"), "--since", since])
            w2, _ = run([PY, ANALYZER, "--input", os.path.join(work, "i.csv"), "--processes-dir", raw,
                         "--output", os.path.join(work, "i_stats.json"), "--since", since])
            r["incremental_last_hour"] = {"cleaner_s": round(w1, 2), "analyzer_s": round(w2, 2)}

            # one statistic: mean CPU per hour
            stat = {}
            if shutil.which("awk"):
                t, a = timed(hourly_awk, raw)
                stat["awk_s"] = round(t, 2)
            t, b = timed(hourly_python, raw)
            stat["python_csv_s"] = round(t, 2)
            t, c = timed(hourly_pandas, raw)
            stat["pandas_s"] = round(t, 2)
            # the three tools round differently in the 2nd decimal, so compare with a tolerance
            close = lambda x, y: x.keys() == y.keys() and all(abs(x[h] - y[h]) < 0.011 for h in x)  # noqa: E731
            stat["same_result"] = close(b, c) and ("awk_s" not in stat or close(a, b))
            r["mean_cpu_per_hour"] = stat

            if not args.no_profile:
                for label, script, sargs in (
                        ("cleaner_plain", CLEANER, ["--input-dir", raw, "--output", clean_p, "--report", rep_p]),
                        ("analyzer", ANALYZER, a_args)):
                    total, top = profile_top(script, sargs, os.path.join(work, label + ".prof"))
                    r[label]["profile_total_s"] = total
                    r[label]["top_functions"] = top
                    print("  profiled %s (%.1fs under cProfile)" % (label, total), flush=True)
            res["sizes"][str(n)] = r
    finally:
        if args.keep:
            print("data kept in", work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2)
    write_markdown(res, os.path.join(args.out, "results.md"))
    write_dat(res, os.path.join(args.out, "runtime.dat"))
    print("\nwrote results.json, results.md, runtime.dat to", args.out)
    return 0


def write_dat(res, path):
    """gnuplot data: rows  cleaner_plain  cleaner_fast  analyzer  awk  python  pandas"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# rows cleaner_plain cleaner_fast analyzer awk python_csv pandas\n")
        for n, r in res["sizes"].items():
            s = r["mean_cpu_per_hour"]
            fh.write("%s %s %s %s %s %s %s\n" % (n, r["cleaner_plain"]["wall_s"], r["cleaner_fast"]["wall_s"],
                                                 r["analyzer"]["wall_s"], s.get("awk_s", "NaN"),
                                                 s["python_csv_s"], s["pandas_s"]))


def write_markdown(res, path):
    m = res["machine"]
    L = ["# Benchmark results (generated by bin/profile_run.py)", "",
         "Machine: %s, Python %s, pandas %s, %s CPUs. Seed %d." % (
             m["platform"], m["python"], m.get("pandas", "?"), m["cpu_count"], res["seed"]), "",
         "## Wall time (seconds)", "",
         "| Rows | Cleaner plain | Cleaner pandas | Speed-up | Identical output | Analyzer |",
         "|---:|---:|---:|---:|:---:|---:|"]
    for n, r in res["sizes"].items():
        L.append("| %s | %s | %s | %sx | %s | %s |" % (
            format(int(n), ","), r["cleaner_plain"]["wall_s"], r["cleaner_fast"]["wall_s"],
            r["cleaner_fast"]["speedup"], "yes" if r["cleaner_fast"]["identical_csv"] else "NO",
            r["analyzer"]["wall_s"]))
    L += ["", "## Peak memory (MB, where measurable)", "",
          "| Rows | Cleaner plain | Cleaner pandas | Analyzer |", "|---:|---:|---:|---:|"]
    for n, r in res["sizes"].items():
        L.append("| %s | %s | %s | %s |" % (format(int(n), ","), r["cleaner_plain"]["peak_mb"],
                                            r["cleaner_fast"]["peak_mb"], r["analyzer"]["peak_mb"]))
    L += ["", "## Incremental run (only the newest hour, `--since`)", "",
          "| Rows | Cleaner (full) | Cleaner (--since) | Analyzer (full) | Analyzer (--since) |",
          "|---:|---:|---:|---:|---:|"]
    for n, r in res["sizes"].items():
        i = r["incremental_last_hour"]
        L.append("| %s | %s | %s | %s | %s |" % (format(int(n), ","), r["cleaner_plain"]["wall_s"], i["cleaner_s"],
                                                 r["analyzer"]["wall_s"], i["analyzer_s"]))
    L += ["", "## One statistic (mean CPU per hour) three ways, seconds", "",
          "| Rows | awk | Python csv | pandas | Same result |", "|---:|---:|---:|---:|:---:|"]
    for n, r in res["sizes"].items():
        s = r["mean_cpu_per_hour"]
        L.append("| %s | %s | %s | %s | %s |" % (format(int(n), ","), s.get("awk_s", "n/a"), s["python_csv_s"],
                                                 s["pandas_s"], "yes" if s["same_result"] else "NO"))
    for label in ("cleaner_plain", "analyzer"):
        for n, r in res["sizes"].items():
            if "top_functions" not in r[label]:
                continue
            L += ["", "## cProfile, %s, %s rows (%.1f s under the profiler)" % (
                label.replace("_", " "), format(int(n), ","), r[label]["profile_total_s"]), "",
                  "| Function | Calls | Own time (s) | Cumulative (s) | % of total |", "|---|---:|---:|---:|---:|"]
            for f in r[label]["top_functions"]:
                L.append("| `%s` | %s | %s | %s | %s |" % (f["function"], format(f["calls"], ","), f["own_s"],
                                                         f["cumulative_s"], f["pct_of_total"]))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())