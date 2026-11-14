import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleaner as plain  # noqa: E402

RAW = plain.RAW_COLUMNS
CLEAN = plain.CLEAN_COLUMNS
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def iso_from_epochs(epochs):
    """int array -> list of 'YYYY-MM-DDTHH:MM:SSZ' strings (UTC)."""
    if len(epochs) == 0:
        return []
    return list(pd.to_datetime(np.asarray(epochs, dtype="int64"), unit="s", utc=True).strftime(ISO_FMT))


def parse_floats(values, allow_empty=False):
    """Object array of strings -> (float array, parsed_ok mask).

    Fast path: numpy converts the whole column with Python's own float(), so
    every digit matches the plain cleaner. If any text is not a number, a
    slower per-value path marks just those values as not parsed (NaN).
    allow_empty: an empty string is a legal 'no value' and also gives NaN.
    """
    if allow_empty:
        values = np.where(values == "", "nan", values)
    try:
        arr = values.astype(float)
    except (ValueError, TypeError):
        arr = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float, copy=True)
        good = ~np.isnan(arr)
        if good.any():
            arr[good] = values[good].astype(float)
    return arr, ~np.isnan(arr)


def fmt1(values):
    """float array -> list of strings with 1 decimal ('%.1f' == f'{v:.1f}')."""
    return ["%.1f" % v for v in values.tolist()]


# --------------------------------------------------------------------------
# reading + validating one raw file
# --------------------------------------------------------------------------
def load_file_plain(path):
    """Slow but exact path, used for files with malformed lines."""
    rows_read = invalid = 0
    good = []
    with open(path, mode="r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not all(c in reader.fieldnames for c in RAW):
            for _ in reader:
                rows_read += 1
                invalid += 1
            return rows_read, invalid, pd.DataFrame(columns=RAW, dtype=object)
        for raw_row in reader:
            rows_read += 1
            cleaned = plain.validate_and_normalize_row(raw_row)
            if cleaned is None:
                invalid += 1
            else:
                good.append(cleaned)
    return rows_read, invalid, pd.DataFrame(good, columns=RAW, dtype=object)


def load_file(path):
    """Return (rows_read, dropped_invalid, DataFrame of valid rows as normalized strings)."""
    with open(path, "rb") as fh:
        blob = fh.read()
    try:
        raw = pd.read_csv(io.BytesIO(blob), dtype=object, keep_default_na=False, na_values=[],
                          on_bad_lines="error", encoding="utf-8")
    except pd.errors.EmptyDataError:
        return 0, 0, pd.DataFrame(columns=RAW, dtype=object)
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError):
        return load_file_plain(path)

    if not isinstance(raw.index, pd.RangeIndex):      # pandas guessed an index column: malformed
        return load_file_plain(path)
    n = len(raw)
    if not all(c in raw.columns for c in RAW):        # bad header
        return n, n, pd.DataFrame(columns=RAW, dtype=object)
    if n == 0:
        return 0, 0, pd.DataFrame(columns=RAW, dtype=object)

    # stripping spaces is slow, so only do it when the file has any whitespace at all
    needs_strip = (b" " in blob) or (b"\t" in blob) or (b"\r" in blob)
    ok = ~raw[RAW].isna().to_numpy().any(axis=1)      # short line -> missing field -> invalid
    a = {}
    for c in RAW:
        col = raw[c].str.strip() if needs_strip else raw[c]
        a[c] = col.to_numpy(dtype=object)

    # epoch: positive integer written without sign, spaces or leading zeros
    ep_ok = pd.Series(a["epoch"]).str.fullmatch(r"[1-9][0-9]{0,17}").to_numpy(dtype=bool, na_value=False)
    ok &= ep_ok
    epoch = np.zeros(n, dtype="int64")
    epoch[ok] = a["epoch"][ok].astype("int64")

    # iso_time: keep as written, or build from the epoch when empty
    iso = a["iso_time"].copy()
    need_iso = (iso == "") & ok
    if need_iso.any():
        iso[need_iso] = iso_from_epochs(epoch[need_iso])

    num = {}
    # required percentages: 0..100, 1 decimal
    for c in plain.REQUIRED_PCT_COLS:
        arr, parsed = parse_floats(a[c])
        ok &= parsed & (arr >= 0.0) & (arr <= 100.0)
        num[c] = arr
    # mem_avail_mb: non-negative whole number
    arr, parsed = parse_floats(a["mem_avail_mb"])
    ok &= parsed & np.isfinite(arr) & (arr >= 0.0) & (arr == np.floor(arr))
    num["mem_avail_mb"] = arr
    # loads and network: non-negative; loads keep their original text, network gets 1 decimal
    for c in plain.NON_NEG_FLOAT_COLS:
        arr, parsed = parse_floats(a[c])
        ok &= parsed & (arr >= 0.0)
        num[c] = arr
    # optional columns: empty is fine, otherwise must be in range
    nonempty = {}
    for c, hi in [(c, 120.0) for c in plain.OPTIONAL_TEMP_COLS] + [(c, 100.0) for c in plain.OPTIONAL_PCT_COLS]:
        ne = a[c] != ""
        arr, parsed = parse_floats(a[c], allow_empty=True)
        ok &= ~ne | (parsed & (arr >= 0.0) & (arr <= hi))
        num[c], nonempty[c] = arr, ne

    # build normalized strings for the valid rows only
    keep = np.nonzero(ok)[0]
    df = pd.DataFrame(index=range(len(keep)), columns=RAW, dtype=object)
    df["epoch"] = a["epoch"][keep]
    df["iso_time"] = iso[keep]
    for c in plain.REQUIRED_PCT_COLS:
        df[c] = fmt1(num[c][keep])
    df["mem_avail_mb"] = ["%d" % v for v in num["mem_avail_mb"][keep].tolist()]
    for c in plain.NON_NEG_FLOAT_COLS:
        df[c] = a[c][keep] if not c.startswith("net_") else fmt1(num[c][keep])
    for c in plain.OPTIONAL_TEMP_COLS + plain.OPTIONAL_PCT_COLS:
        ne = nonempty[c][keep]
        vals = np.array(fmt1(np.where(ne, num[c][keep], 0.0)), dtype=object)
        vals[~ne] = ""
        df[c] = vals
    return n, int(n - len(keep)), df[RAW]


# --------------------------------------------------------------------------
# outliers: rolling median test, vectorized (same maths as the plain version)
# --------------------------------------------------------------------------
def window_median(sorted_w, count):
    """Median of each row of an already row-sorted matrix (NaN sorted last)."""
    rows = np.arange(sorted_w.shape[0])
    lo = sorted_w[rows, np.maximum(count - 1, 0) // 2]
    hi = sorted_w[rows, count // 2]
    return (lo + hi) / 2.0


def flag_outliers(df, is_filled, radius=3):
    n = len(df)
    flags = np.zeros(n, dtype=bool)
    if n < 3:
        return flags
    for col, min_delta in plain.OUTLIER_MIN_DELTA.items():
        strs = df[col]
        nonempty = (strs != "").to_numpy()
        values = np.full(n, np.nan)
        if nonempty.any():
            values[nonempty] = strs.to_numpy(dtype=object)[nonempty].astype(float)
        usable = np.where(is_filled, np.nan, values)       # filled rows never count
        padded = np.concatenate([np.full(radius, np.nan), usable, np.full(radius, np.nan)])
        win = np.stack([padded[k:k + n] for k in range(2 * radius + 1)], axis=1)
        count = (~np.isnan(win)).sum(axis=1)
        cand = ~is_filled & ~np.isnan(values) & (count >= 3)
        med = window_median(np.sort(win, axis=1), count)
        dev = np.abs(values - med)
        cand &= dev >= min_delta
        idx = np.nonzero(cand)[0]
        if len(idx) == 0:
            continue
        sub = np.sort(np.abs(win[idx] - med[idx, None]), axis=1)
        mad = window_median(sub, count[idx])
        thr = np.maximum(min_delta, 3.5 * np.maximum(mad, 1.0))
        flags[idx[dev[idx] >= thr]] = True
    return flags


# --------------------------------------------------------------------------
# the whole cleaning job
# --------------------------------------------------------------------------
def clean_metrics_fast(input_dir, default_step=5, since_epoch=None):
    files = plain.discover_input_files(Path(input_dir))
    rows_read = dropped_invalid = 0
    frames = []
    for path in files:
        try:
            rr, inv, df = load_file(path)
        except OSError as exc:
            print("Error reading %s: %s" % (path, exc), file=sys.stderr)
            raise
        rows_read += rr
        dropped_invalid += inv
        if len(df):
            frames.append(df)

    if frames:
        allrows = pd.concat(frames, ignore_index=True)
    else:
        allrows = pd.DataFrame(columns=RAW, dtype=object)
    ep = allrows["epoch"].to_numpy(dtype=object).astype("int64") if len(allrows) else np.zeros(0, "int64")

    if since_epoch is not None:
        mask = ep >= since_epoch
        allrows, ep = allrows[mask].reset_index(drop=True), ep[mask]

    # drop duplicate epochs (first one wins) and sort by epoch
    ep_sorted, first_idx = np.unique(ep, return_index=True)
    dropped_duplicates = int(len(ep) - len(first_idx))
    real = allrows.iloc[first_idx].reset_index(drop=True)
    n = len(real)

    # gaps
    step = plain.infer_step_seconds([int(x) for x in ep_sorted[:2]], default_step) if n < 2 else None
    if n >= 2:
        diffs = np.diff(ep_sorted)
        vals, first, counts = np.unique(diffs, return_index=True, return_counts=True)
        best = np.nonzero(counts == counts.max())[0]
        step = max(1, int(vals[best[np.argmin(first[best])]]))
        dt = diffs
        missing = np.where(dt > step, np.rint(dt / step).astype("int64") - 1, 0)
    else:
        diffs = np.zeros(0, "int64")
        missing = np.zeros(0, "int64")
    short = (missing >= 1) & (missing <= 3)
    long_gap = missing > 3

    is_gap = np.zeros(n, dtype=bool)
    is_gap[1:] = long_gap

    # forward-filled rows for short gaps
    prev_idx = np.repeat(np.nonzero(short)[0], missing[short])
    total = len(prev_idx)
    if total:
        cnt = missing[short]
        starts = np.cumsum(cnt) - cnt
        m = np.arange(total) - np.repeat(starts, cnt) + 1
        mm = np.repeat(cnt, cnt)
        p_ep = ep_sorted[prev_idx]
        c_ep = ep_sorted[prev_idx + 1]
        fill_ep = p_ep + m * step
        bad = fill_ep >= c_ep
        fill_ep[bad] = p_ep[bad] + ((c_ep[bad] - p_ep[bad]) * m[bad]) // (mm[bad] + 1)
        fills = real.iloc[prev_idx].reset_index(drop=True)
        fills["epoch"] = [str(x) for x in fill_ep.tolist()]
        fills["iso_time"] = iso_from_epochs(fill_ep)
        order_key = np.concatenate([np.arange(n) * 4 + 3, (prev_idx + 1) * 4 + (m - 1)])
        combined = pd.concat([real, fills], ignore_index=True)
        is_filled = np.concatenate([np.zeros(n, bool), np.ones(total, bool)])
        gap_all = np.concatenate([is_gap, np.zeros(total, bool)])
        perm = np.argsort(order_key, kind="stable")
        combined = combined.iloc[perm].reset_index(drop=True)
        is_filled, gap_all = is_filled[perm], gap_all[perm]
    else:
        combined, is_filled, gap_all = real, np.zeros(n, bool), is_gap

    outlier = flag_outliers(combined, is_filled)
    out = combined.copy()
    out["is_filled"] = np.where(is_filled, "1", "0")
    out["is_gap"] = np.where(gap_all, "1", "0")
    out["is_outlier"] = np.where(outlier, "1", "0")

    report = {
        "generated_at": datetime.now(timezone.utc).strftime(ISO_FMT),
        "rows_read": rows_read,
        "dropped_duplicates": dropped_duplicates,
        "dropped_invalid": dropped_invalid,
        "filled": int(total),
        "gaps": int(long_gap.sum()),
        "outliers": int(outlier.sum()),
        "engine": "pandas",
    }
    return out[CLEAN], report


def csv_text(df):
    """Whole CSV as text. Joining strings is ~6x faster than DataFrame.to_csv, but
    only valid while no cell needs CSV quoting; otherwise use pandas' own writer."""
    iso = df["iso_time"].to_numpy(dtype=object)
    joined = "".join(iso.tolist())
    needs_quotes = any(ch in joined for ch in (",", '"', "\n", "\r"))
    if needs_quotes:
        return df.to_csv(index=False, lineterminator="\n")
    cols = [df[c].to_numpy(dtype=object) for c in CLEAN]
    lines = [",".join(CLEAN)]
    lines.extend(map(",".join, zip(*cols)))
    return "\n".join(lines) + "\n"


def write_csv_atomic(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    try:
        with open(tmp, mode="w", encoding="utf-8", newline="") as f:
            f.write(csv_text(df))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def main(argv=None):
    p = argparse.ArgumentParser(description="SysMon raw metrics cleaner (pandas engine)")
    p.add_argument("--input-dir", default="data/raw")
    p.add_argument("--output", default="data/clean/metrics.csv")
    p.add_argument("--report", default="data/clean/cleaning_report.json")
    p.add_argument("--since", type=int, default=None, help="only keep rows with epoch >= this")
    p.add_argument("--engine", default="pandas", help=argparse.SUPPRESS)   # accepted so cleaner.py can hand over its own arguments
    args = p.parse_args(argv)
    try:
        root = plain.get_repo_root()

        def resolve(x):
            x = Path(x)
            return x if x.is_absolute() else root / x

        df, report = clean_metrics_fast(resolve(args.input_dir), plain.read_config_interval(root), args.since)
        write_csv_atomic(resolve(args.output), df)
        plain.atomic_write_json(resolve(args.report), report)
        return 0
    except Exception as exc:  # noqa: BLE001
        print("Error in cleaner_fast.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())