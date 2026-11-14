SysMon Profiling and Optimization
Owner: P5. Everything here is reproduced by one command:

bin/run_benchmarks.sh            # full run: 100k, 500k, 1M rows, with cProfile (about 8 minutes)
bin/run_benchmarks.sh --quick    # 20k and 100k rows, no cProfile (about 30 seconds)
It writes docs/benchmarks/results.md (all tables), results.json (raw numbers) and runtime.dat (for gnuplot, plots in scripts/plot_benchmarks.gp).

Numbers below come from one reference run (Linux, 2 CPU cores, Python 3.13, pandas 3.0, seed 42). Timings depend on the machine, so before the report is written, run bin/run_benchmarks.sh on the demo laptop and replace the tables with its results.md. The shape of the results (which function dominates, the ratios) stays the same.

1. Method
Data: bin/gen_data.py --rows N --seed 42 for N = 100 000, 500 000 and 1 000 000. The data contains duplicates, gaps, invalid values and spikes, so the cleaner does real work.
Timing: wall-clock time of the whole command (python src/cleaner.py ...), including start-up and file writing. Peak memory comes from the operating system (Linux and macOS only).
Profiling: python -m cProfile -o file.prof script args, then pstats sorted by own time. cProfile slows the program about 3x, so use its percentages, not its seconds.
Correctness check: the optimized cleaner's CSV must be byte-identical to the plain cleaner's CSV. tests/test_cleaner_fast.py checks this on generated data, on a hand-made nasty file, and on empty input. A separate fuzz run (9 kinds of broken input, 3 seeds each, with and without --since, 54 comparisons) found no differences.
2. Baseline (before)
Rows	Cleaner (s)	Analyzer (s)	Cleaner peak memory (MB)	Analyzer peak memory (MB)
100,000	2.36	2.53	222	135
500,000	11.96	12.95	1,036	542
1,000,000	23.22	24.69	2,063	1,050
Both scale linearly (double the rows, double the time), so there is no hidden quadratic step. Memory also grows linearly: the plain cleaner holds every row as a Python dictionary, about 2 KB per row.

3. Bottlenecks (cProfile)
Cleaner (src/cleaner.py), same shape at all three sizes:

Function	Share of time	Why it is slow
flag_outliers_rolling_median	31% (own)	For every row and each of 8 metrics it builds a list of up to 7 values and calls statistics.median twice. That is 7.7 million median calls at 1M rows.
validate_and_normalize_row	14%	One Python function call per row, with about 20 string operations each.
dict.get	13%	Called 82 times per row (81 million at 1M rows), mostly from the outlier test.
csv reader and writer	12%	Row-by-row DictReader and DictWriter.
Analyzer (src/analyzer.py):

Function	Share of time	Why it is slow
parse_float	24%	29 million calls at 1M rows (every cell is parsed through a Python function).
compute_top_processes	11% own, about half cumulative	Reads the processes files, which have 5 rows per sample, so 5 million lines at 1M samples.
csv.__next__	12%	Row-by-row CSV parsing of both input files.
4. Optimizations and results (after)
4.1 Vectorized cleaner (src/cleaner_fast.py)
Validation works on whole columns with numpy instead of one call per row.
The rolling median test uses numpy sliding windows: sort a matrix of 7-value windows once, read off the middle value, and do the second (MAD) pass only for the few rows that could be outliers.
Output is written by joining strings instead of DictWriter.
Business rules are not copied: constants, file discovery and the config reader are imported from cleaner.py. Files with malformed lines fall back to the plain row-by-row validator so those lines are counted exactly as before.
Rows	Plain (s)	Pandas (s)	Speed-up	Identical CSV	Peak memory plain / pandas (MB)
100,000	2.36	1.31	1.8x	yes	222 / 227
500,000	11.96	5.95	2.0x	yes	1,036 / 838
1,000,000	23.22	11.66	2.0x	yes	2,063 / 1,597
Findings:

About 2x faster and about 20% less memory at large sizes, with exactly the same output.
It is not faster on small inputs. Importing pandas costs about 0.3 s and the extra start-up work a little more, so at 20 000 rows the plain cleaner wins (0.51 s against 0.55 s). The fast engine pays off above roughly 50 000 rows. The dashboard pipeline runs on small data every 10 s, so it should keep the plain engine; the pandas engine is for big back-fills and for the benchmark.
The remaining time is mostly work that cannot be vectorized cheaply: turning 19 columns of numbers back into text for each output row, and reading the files.
4.2 Incremental processing (--since)
Both cleaner.py and analyzer.py already accept --since EPOCH. Measured with --since set to the newest hour of data:

Rows	Cleaner full (s)	Cleaner --since (s)	Analyzer full (s)	Analyzer --since (s)
100,000	2.36	0.76	2.53	1.04
500,000	11.96	3.85	12.95	4.79
1,000,000	23.22	7.23	24.69	9.79
About 3x faster for the cleaner and 2.5x for the analyzer, but not as much as it could be: the cleaner still reads and validates every raw row before it checks --since, and the analyzer still scans every process line. The time that remains is that scan. A further improvement (P2 and P1 code, not done here) is to skip raw files whose UTC date is entirely before the --since date, which would make the cost depend on the new data only.

4.3 Downsampling in /api/metrics
Done by P4 in server.py (1 hour raw, 24 hours as 1-minute averages, 7 days as 10-minute averages). P4 records the before and after timings in their own section.

4.4 Unix tools comparison: mean CPU per hour
The same statistic computed with awk, a plain Python csv loop, and pandas, on the raw metrics files (seconds):

Rows	awk	Python csv	pandas	Same result
100,000	0.06	0.19	0.07	yes
500,000	0.33	1.14	0.31	yes
1,000,000	0.67	2.77	0.61	yes
The awk one-liner is as fast as pandas and about 4x faster than a Python loop. The reason is that awk does one pass with no per-row object creation. Python is slower here because of the interpreter overhead per row, which is exactly what the cleaner profile showed. The price of awk is that its output has no data validation or flags: it handles one statistic well, not the whole cleaning pipeline (duplicates, gaps, outlier flags, reports).

5. Conclusions
 1.The cost of both stages is per-row Python overhead (function calls, dictionaries, statistics.median), and it scales linearly. Vectorizing removes most of it: 2x for the cleaner.
 2.The biggest remaining wins are in the analyzer (P1), which the profile shows is dominated by parse_float and by reading the processes files. Parsing numeric columns in bulk and aggregating processes in one pass would be the next change.
 3.Incremental processing gives a larger real-world gain than faster code, because the dashboard only needs the newest data. Skipping old raw files would make it near-constant.
 4.For the demo (small data), none of this matters: at 20,000 rows each stage takes about half a second. The optimizations are about scaling to weeks of data.
6. Not measured here
Collector overhead (CPU and memory of collector.sh, /proc against top): it needs a Linux machine with the collector running. It is measured separately with P2.