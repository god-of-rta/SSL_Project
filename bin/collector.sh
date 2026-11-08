#!/usr/bin/env bash
# bin/collector.sh - SysMon background metrics collector (v1: core 16-column metrics)

set -u
export LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Defaults from INTERFACES.md Section 7.1
INTERVAL_S=5
DATA_DIR="data"
TOP_N=5
SIMULATE_TEMP="false"
COLLECT_GPU="true"
GPU_POLL_EVERY=3

CONFIG_FILE="$REPO_ROOT/config/sysmon.conf"

parse_config() {
    [[ -f "$CONFIG_FILE" ]] || return 0
    while IFS='=' read -r raw_key raw_val; do
        # Strip comments and whitespace
        raw_key="${raw_key%%[#;]*}"
        raw_key="${raw_key#"${raw_key%%[![:space:]]*}"}"
        raw_key="${raw_key%"${raw_key##*[![:space:]]}"}"
        [[ -z "$raw_key" || "$raw_key" == \[*\] ]] && continue

        raw_val="${raw_val%%[#;]*}"
        raw_val="${raw_val#"${raw_val%%[![:space:]]*}"}"
        raw_val="${raw_val%"${raw_val##*[![:space:]]}"}"

        case "$raw_key" in
            interval_s)     [[ -n "$raw_val" ]] && INTERVAL_S="$raw_val" ;;
            data_dir)       [[ -n "$raw_val" ]] && DATA_DIR="$raw_val" ;;
            top_n)          [[ -n "$raw_val" ]] && TOP_N="$raw_val" ;;
            simulate_temp)  [[ -n "$raw_val" ]] && SIMULATE_TEMP="$raw_val" ;;
            collect_gpu)    [[ -n "$raw_val" ]] && COLLECT_GPU="$raw_val" ;;
            gpu_poll_every) [[ -n "$raw_val" ]] && GPU_POLL_EVERY="$raw_val" ;;
        esac
    done < "$CONFIG_FILE"
}

parse_config

# CLI overrides
while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval)
            INTERVAL_S="$2"
            shift 2
            ;;
        --interval=*)
            INTERVAL_S="${1#*=}"
            shift
            ;;
        --simulate-temp)
            SIMULATE_TEMP="true"
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

RAW_DIR="$REPO_ROOT/$DATA_DIR/raw"
mkdir -p "$RAW_DIR"

METRICS_HEADER="epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,net_rx_kbps,net_tx_kbps,cpu_temp_c,gpu_util_pct,gpu_mem_used_pct,gpu_temp_c"

read_cpu_stat() {
    awk '/^cpu / {
        idle = $5 + $6
        total = $2 + $3 + $4 + $5 + $6 + $7 + $8 + $9
        print total, idle
        exit
    }' /proc/stat
}

read_net_bytes() {
    awk -F'[: ]+' '
    NR > 2 && $1 != "" && $1 != "lo" {
        rx += $2
        tx += $10
    }
    END {
        printf "%.0f %.0f\n", rx, tx
    }' /proc/net/dev
}

read_memory() {
    awk '
    /^MemTotal:/     { mem_total = $2 }
    /^MemAvailable:/ { mem_avail = $2 }
    /^SwapTotal:/    { swap_total = $2 }
    /^SwapFree:/     { swap_free = $2 }
    END {
        if (mem_total > 0) {
            mem_used_pct = ((mem_total - mem_avail) / mem_total) * 100.0
        } else {
            mem_used_pct = 0.0
        }
        mem_avail_mb = int(mem_avail / 1024)
        if (swap_total > 0) {
            swap_used_pct = ((swap_total - swap_free) / swap_total) * 100.0
        } else {
            swap_used_pct = 0.0
        }
        printf "%.1f %d %.1f\n", mem_used_pct, mem_avail_mb, swap_used_pct
    }' /proc/meminfo
}

read_disk_pct() {
    df -P / 2>/dev/null | awk 'NR==2 {
        if ($2 > 0) {
            printf "%.1f\n", ($3 / $2) * 100.0
        } else {
            print "0.0"
        }
    }'
}

read_loadavg() {
    awk '{ printf "%.2f %.2f %.2f\n", $1, $2, $3 }' /proc/loadavg
}

PROCESSES_HEADER="epoch,pid,command,cpu_pct,mem_pct,rank"

read_cpu_temp() {
    local cpu_load="${1:-10.0}"

    if [[ "$SIMULATE_TEMP" == "true" ]]; then
        awk -v cpu="$cpu_load" -v seed="$RANDOM" 'BEGIN {
            srand(seed)
            jitter = (rand() - 0.5) * 3.0
            temp = 42.0 + (cpu * 0.42) + jitter
            if (temp < 35.0) temp = 35.0
            if (temp > 98.0) temp = 98.0
            printf "%.1f\n", temp
        }'
        return 0
    fi

    # 1. Try /sys/class/thermal/thermal_zone*/temp (pick hottest zone)
    local hottest=""
    local f raw_val
    for f in /sys/class/thermal/thermal_zone*/temp; do
        [[ -r "$f" ]] || continue
        raw_val="$(cat "$f" 2>/dev/null || true)"
        if [[ "$raw_val" =~ ^[0-9]+$ ]] && [[ "$raw_val" -gt 0 ]]; then
            hottest="$(awk -v raw="$raw_val" -v max="$hottest" 'BEGIN {
                c = (raw > 1000) ? (raw / 1000.0) : raw
                if (c > 0 && c < 120) {
                    if (max == "" || c > max) printf "%.1f", c
                    else printf "%s", max
                } else {
                    printf "%s", max
                }
            }')"
        fi
    done

    if [[ -n "$hottest" ]]; then
        printf "%s\n" "$hottest"
        return 0
    fi

    # 2. Fallback to lm-sensors if installed
    if command -v sensors >/dev/null 2>&1; then
        hottest="$(sensors 2>/dev/null | awk '
            /Package id 0:|Tctl:|Tdie:|Core [0-9]+:|temp1:/ {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^\+[0-9]+(\.[0-9]+)?°C/) {
                        val = $i
                        gsub(/^\+|°C.*/, "", val)
                        val += 0
                        if (val > 0 && val < 120 && (max == "" || val > max)) {
                            max = val
                        }
                    }
                }
            }
            END {
                if (max != "") printf "%.1f\n", max
            }
        ')"
        if [[ -n "$hottest" ]]; then
            printf "%s\n" "$hottest"
            return 0
        fi
    fi

    # 3. Unavailable -> empty string
    printf ""
}

collect_top_processes() {
    local epoch="$1"
    local date_str="$2"
    local proc_file="$RAW_DIR/processes_${date_str}.csv"

    if [[ ! -f "$proc_file" ]]; then
        printf "%s\n" "$PROCESSES_HEADER" > "$proc_file"
    fi

    ps -eo pid=,comm=,%cpu=,%mem= --sort=-%cpu 2>/dev/null | awk -v ep="$epoch" -v top_n="$TOP_N" '
    NR <= top_n {
        pid = $1
        cpu = $3 + 0
        mem = $4 + 0
        # Reconstruct comm in case it contains spaces
        cmd = $2
        gsub(/[, \t\r\n]+/, "_", cmd)
        printf "%s,%s,%s,%.1f,%.1f,%d\n", ep, pid, cmd, cpu, mem, NR
    }' >> "$proc_file"
}

RUNNING=1
trap 'RUNNING=0' SIGTERM SIGINT

# Initial baseline read so the first logged sample has a valid CPU delta
read -r PREV_CPU_TOTAL PREV_CPU_IDLE <<< "$(read_cpu_stat)"
read -r PREV_NET_RX PREV_NET_TX <<< "$(read_net_bytes)"
PREV_EPOCH="$(date -u +%s)"
FIRST_SAMPLE=1

while [[ "$RUNNING" -eq 1 ]]; do
    sleep "$INTERVAL_S" &
    wait $! 2>/dev/null || true
    [[ "$RUNNING" -eq 0 ]] && break

    EPOCH="$(date -u +%s)"
    ISO_TIME="$(date -u -d "@$EPOCH" +"%Y-%m-%dT%H:%M:%SZ")"
    DATE_STR="$(date -u -d "@$EPOCH" +"%Y%m%d")"

    # CPU delta
    read -r CURR_CPU_TOTAL CURR_CPU_IDLE <<< "$(read_cpu_stat)"
    CPU_PCT="$(awk -v t1="$PREV_CPU_TOTAL" -v i1="$PREV_CPU_IDLE" \
                   -v t2="$CURR_CPU_TOTAL" -v i2="$CURR_CPU_IDLE" 'BEGIN {
        dt = t2 - t1
        di = i2 - i1
        if (dt > 0) {
            busy = ((dt - di) / dt) * 100.0
            if (busy < 0) busy = 0.0
            if (busy > 100) busy = 100.0
            printf "%.1f", busy
        } else {
            printf "0.0"
        }
    }')"
    PREV_CPU_TOTAL="$CURR_CPU_TOTAL"
    PREV_CPU_IDLE="$CURR_CPU_IDLE"

    # Memory & Swap
    read -r MEM_USED_PCT MEM_AVAIL_MB SWAP_USED_PCT <<< "$(read_memory)"

    # Disk
    DISK_USED_PCT="$(read_disk_pct)"
    [[ -z "$DISK_USED_PCT" ]] && DISK_USED_PCT="0.0"

    # Load average
    read -r LOAD1 LOAD5 LOAD15 <<< "$(read_loadavg)"

    # Network rate (0.0 on first sample per INTERFACES.md 3.1)
    read -r CURR_NET_RX CURR_NET_TX <<< "$(read_net_bytes)"
    DT=$(( EPOCH - PREV_EPOCH ))
    if [[ "$FIRST_SAMPLE" -eq 1 || "$DT" -le 0 ]]; then
        NET_RX_KBPS="0.0"
        NET_TX_KBPS="0.0"
        FIRST_SAMPLE=0
    else
        read -r NET_RX_KBPS NET_TX_KBPS <<< "$(awk -v rx1="$PREV_NET_RX" -v tx1="$PREV_NET_TX" \
            -v rx2="$CURR_NET_RX" -v tx2="$CURR_NET_TX" -v dt="$DT" 'BEGIN {
            drx = rx2 - rx1; if (drx < 0) drx = 0
            dtx = tx2 - tx1; if (dtx < 0) dtx = 0
            printf "%.1f %.1f\n", (drx * 8.0) / (1000.0 * dt), (dtx * 8.0) / (1000.0 * dt)
        }')"
    fi
    PREV_NET_RX="$CURR_NET_RX"
    PREV_NET_TX="$CURR_NET_TX"
    PREV_EPOCH="$EPOCH"

    # Placeholders for P2-3 (temp) and P2-4 (GPU)
    CPU_TEMP_C="$(read_cpu_temp "$CPU_PCT")"
    GPU_UTIL_PCT=""
    GPU_MEM_USED_PCT=""
    GPU_TEMP_C=""
    # Top N Processes
    collect_top_processes "$EPOCH" "$DATE_STR"

    METRICS_FILE="$RAW_DIR/metrics_${DATE_STR}.csv"
    if [[ ! -f "$METRICS_FILE" ]]; then
        printf "%s\n" "$METRICS_HEADER" > "$METRICS_FILE"
    fi

    printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
        "$EPOCH" "$ISO_TIME" "$CPU_PCT" "$MEM_USED_PCT" "$MEM_AVAIL_MB" \
        "$SWAP_USED_PCT" "$DISK_USED_PCT" "$LOAD1" "$LOAD5" "$LOAD15" \
        "$NET_RX_KBPS" "$NET_TX_KBPS" "$CPU_TEMP_C" \
        "$GPU_UTIL_PCT" "$GPU_MEM_USED_PCT" "$GPU_TEMP_C" >> "$METRICS_FILE"
done
