#!/usr/bin/env python3
"""Synthetic raw data generator for SysMon.

Writes data in the standard collector format:
  metrics_YYYYMMDD.csv   (16 columns, one row per sample)
  processes_YYYYMMDD.csv (top 5 processes per sample)

Files are named by the UTC date of their rows. The same --seed always gives
the same output.

Injected problems (so the cleaner has something to do), unless --clean:
  duplicate rows, a short gap (1-3 samples), a long gap (>3 samples),
  out-of-range values, CPU spikes, and stretches of empty GPU fields.

Examples:
  python3 bin/gen_data.py --rows 100000 --out /tmp/g100k --seed 42
  python3 bin/gen_data.py --rows 300 --out tests/fixtures/raw --seed 1
"""
import argparse
import math
import os
import random
import sys
from datetime import datetime, timezone

METRIC_HEADER = (
    "epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,"
    "disk_used_pct,load1,load5,load15,net_rx_kbps,net_tx_kbps,cpu_temp_c,"
    "gpu_util_pct,gpu_mem_used_pct,gpu_temp_c"
)
PROC_HEADER = "epoch,pid,command,cpu_pct,mem_pct,rank"

# 2026-11-01T00:00:00Z
DEFAULT_START = 1793491200
TOTAL_RAM_MB = 15900
CORES = 8
COMMANDS = ["chrome", "firefox", "code", "python3", "gnome-shell", "Xorg",
            "node", "slack", "bash", "pipewire", "systemd", "docker"]


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_key(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%d")


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def plan_faults(rows, rng, enabled):
    """Pick disjoint sample indices for each kind of fault. Returns a dict of sets."""
    plan = {"dup": set(), "short_gap": {}, "long_gap": {}, "invalid": set(),
            "spike": set(), "no_gpu": set()}
    if not enabled or rows < 60:
        return plan
    lo, hi = 20, rows - 20  # keep faults away from the very edges

    gap_blocked = set()
    skip = set()

    def pick_gap(count, length_fn):
        chosen = {}
        max_len = max(4, (hi - lo) // 3)
        for _ in range(max(1, count)):
            length = min(length_fn(), max_len)
            start = None
            max_start = hi - length
            if max_start >= lo:
                for _ in range(50):
                    cand = rng.randint(lo, max_start)
                    if not any(idx in gap_blocked for idx in range(cand - 3, cand + length + 4)):
                        start = cand
                        break
                if start is None:
                    candidates = [
                        s for s in range(lo, max_start + 1)
                        if not any(idx in gap_blocked for idx in range(s - 3, s + length + 4))
                    ]
                    if candidates:
                        start = rng.choice(candidates)
            if start is None:
                continue
            chosen[start] = length
            skip.update(range(start, start + length))
            # Leave >= 3 valid neighbors around gaps so separate gaps never merge
            gap_blocked.update(range(start - 3, start + length + 4))
        return chosen

    plan["long_gap"] = pick_gap(rows // 20000, lambda: rng.randint(8, 30))
    plan["short_gap"] = pick_gap(rows // 3000, lambda: rng.randint(1, 3))

    point_blocked = set(skip)

    def pick_points(count, extra_blocked=()):
        target = max(1, count)
        picks = []
        blocked = point_blocked | set(extra_blocked)
        for _ in range(target * 20):
            if len(picks) >= target:
                break
            cand = rng.randint(lo, hi)
            if cand not in blocked:
                picks.append(cand)
                blocked.add(cand)
                point_blocked.add(cand)
        if len(picks) < target:
            pool = [idx for idx in range(lo, hi + 1) if idx not in blocked]
            if not pool and extra_blocked:
                pool = [idx for idx in range(lo, hi + 1) if idx not in point_blocked]
            need = min(target - len(picks), len(pool))
            if need > 0:
                extra = rng.sample(pool, need)
                picks.extend(extra)
                point_blocked.update(extra)
        return picks

    plan["dup"].update(pick_points(rows // 2000))
    plan["invalid"].update(pick_points(rows // 2500))
    # Keep spikes outside +/- 3 samples of gaps so rolling median has clean neighbors
    plan["spike"].update(pick_points(rows // 1500, extra_blocked=gap_blocked))

    # empty GPU: blocks of 30 samples, about 10% of the data
    for _ in range(max(1, rows // 300)):
        s = rng.randint(0, max(0, rows - 30))
        plan["no_gpu"].update(range(s, s + 30))
    return plan


def main():
    p = argparse.ArgumentParser(description="SysMon synthetic data generator")
    p.add_argument("--rows", type=int, default=1000, help="number of samples to generate")
    p.add_argument("--out", default="data/raw", help="output directory")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--interval", type=int, default=5, help="seconds between samples")
    p.add_argument("--start", type=int, default=DEFAULT_START, help="first epoch (UTC)")
    p.add_argument("--clean", action="store_true", help="do not inject any faults")
    args = p.parse_args()

    if args.rows < 1:
        print("error: --rows must be 1 or more", file=sys.stderr)
        return 1
    if args.interval < 1:
        print("error: --interval must be 1 or more", file=sys.stderr)
        return 1
    try:
        os.makedirs(args.out, exist_ok=True)
    except OSError as e:
        print("error: cannot create %s: %s" % (args.out, e), file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    plan = plan_faults(args.rows, rng, not args.clean)

    handles = {}

    def write(kind, epoch, line, header):
        key = (kind, day_key(epoch))
        fh = handles.get(key)
        if fh is None:
            path = os.path.join(args.out, "%s_%s.csv" % (kind, key[1]))
            fh = open(path, "w", encoding="utf-8", newline="\n")
            fh.write(header + "\n")
            handles[key] = fh
        fh.write(line + "\n")

    disk0 = 55.0 + rng.random() * 10
    disk_per_sample = 0.4 / (86400.0 / args.interval)   # about +0.4% a day
    gpu_util = gpu_mem = gpu_temp = None
    skip = set()
    for start, n in list(plan["short_gap"].items()) + list(plan["long_gap"].items()):
        skip.update(range(start, start + n))

    for i in range(args.rows):
        epoch = args.start + i * args.interval

        if i in skip:
            continue

        t = i * args.interval
        cpu = clamp(28 + 18 * math.sin(2 * math.pi * t / 5400.0)
                    + 8 * math.sin(2 * math.pi * t / 600.0) + rng.gauss(0, 4))
        if i in plan["spike"]:
            cpu = 97 + rng.random() * 3
        mem = clamp(48 + 8 * math.sin(2 * math.pi * t / 21600.0) + rng.gauss(0, 0.6))
        avail = int(TOTAL_RAM_MB * (100 - mem) / 100)
        swap = clamp(2 + 1.5 * math.sin(2 * math.pi * t / 43200.0) + rng.gauss(0, 0.2))
        disk = clamp(disk0 + disk_per_sample * i + rng.gauss(0, 0.01))
        load1 = max(0.0, cpu / 100.0 * CORES * 0.6 + rng.gauss(0, 0.2))
        load5 = max(0.0, load1 * 0.9 + rng.gauss(0, 0.1))
        load15 = max(0.0, load1 * 0.8 + rng.gauss(0, 0.1))
        rx = max(0.0, 400 + 300 * rng.random() + (cpu * 5 if rng.random() < 0.1 else 0))
        tx = max(0.0, 80 + 60 * rng.random())
        if i == 0:
            rx = tx = 0.0
        temp = clamp(38 + 0.45 * cpu + rng.gauss(0, 0.8), 0, 120)

        # GPU is polled every 3 samples; repeat last value in between
        if i % 3 == 0 or gpu_util is None:
            gpu_util = clamp(6 + 5 * rng.random() + (30 * rng.random() if rng.random() < 0.05 else 0))
            gpu_mem = clamp(12 + 3 * rng.random())
            gpu_temp = clamp(42 + 0.2 * gpu_util + rng.gauss(0, 0.6), 0, 120)
        if i in plan["no_gpu"]:
            g = ",,"
        else:
            g = "%.1f,%.1f,%.1f" % (gpu_util, gpu_mem, gpu_temp)

        cpu_s = "%.1f" % cpu
        temp_s = "%.1f" % temp
        if i in plan["invalid"]:
            cpu_s = "%.1f" % (rng.choice([150.0, 250.0, -5.0]))   # out of range
            if rng.random() < 0.5:
                temp_s = "%.1f" % 500.0

        line = "%d,%s,%s,%.1f,%d,%.1f,%.1f,%.2f,%.2f,%.2f,%.1f,%.1f,%s,%s" % (
            epoch, iso(epoch), cpu_s, mem, avail, swap, disk, load1, load5, load15,
            rx, tx, temp_s, g)
        write("metrics", epoch, line, METRIC_HEADER)
        if i in plan["dup"]:
            write("metrics", epoch, line, METRIC_HEADER)   # exact duplicate

        # top 5 processes by CPU
        cpus = sorted((cpu * rng.random() * 0.6 for _ in range(5)), reverse=True)
        names = rng.sample(COMMANDS, 5)
        for rank, (name, c) in enumerate(zip(names, cpus), start=1):
            pid = 1000 + COMMANDS.index(name) * 111
            write("processes", epoch,
                  "%d,%d,%s,%.1f,%.1f,%d" % (epoch, pid, name, c, 1 + rng.random() * 8, rank),
                  PROC_HEADER)

    for fh in handles.values():
        fh.close()
    print("wrote %d samples to %s (%d files)" % (args.rows, args.out, len(handles)))
    return 0


if __name__ == "__main__":
    sys.exit(main())