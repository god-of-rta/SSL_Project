# Benchmark plots (owner: P5). Run from the repository root:  gnuplot scripts/plot_benchmarks.gp
# Reads docs/benchmarks/runtime.dat, written by bin/profile_run.py.
# Columns: rows  cleaner_plain  cleaner_fast  analyzer  awk  python_csv  pandas  (seconds)

data = "docs/benchmarks/runtime.dat"
set terminal svg size 800,500 font "Arial,12"
set grid
set key left top

# 1. runtime against row count, one line per implementation
set output "docs/benchmarks/runtime.svg"
set title "Runtime against number of rows"
set xlabel "rows"
set ylabel "seconds"
set format x "%.0s%c"
plot data using 1:2 with linespoints lw 2 title "cleaner (plain Python)", \
     data using 1:3 with linespoints lw 2 title "cleaner (pandas)", \
     data using 1:4 with linespoints lw 2 title "analyzer"

# 2. before / after bars for the largest data size (last line of the file)
stats data using 1 nooutput
last = STATS_records - 1
set output "docs/benchmarks/before_after.svg"
set title "Cleaner: before and after (largest size)"
unset xlabel
unset format
set ylabel "seconds"
set style data histograms
set style fill solid 0.8 border -1
set boxwidth 0.7
set key top right
plot data every ::last::last using 2:xtic(1) title "plain Python (before)", \
     ''   every ::last::last using 3         title "pandas (after)"
