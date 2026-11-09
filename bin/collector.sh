#!/usr/bin/env bash
# bin/collector.sh - SysMon background metrics & process collector

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
        raw_key="${raw_key//$'\r'/}"
        raw_val="${raw_val//$'\r'/}"
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval)
            INTERVAL_S="${2:-}"
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
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if ! [[ "$INTERVAL_S" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_S" -le 0 ]]; then
    echo "Invalid interval_s: $INTERVAL_S" >&2
    exit 1
fi

if ! [[ "$GPU_POLL_EVERY" =~ ^[0-9]+$ ]] || [[ "$GPU_POLL_EVERY" -le 0 ]]; then
    GPU_POLL_EVERY=3
fi

DATA_PATH="$REPO_ROOT/$DATA_DIR"
RAW_DIR="$DATA_PATH/raw"
LOCK_FILE="$DATA_PATH/collector.lock"
PID_FILE="$DATA_PATH/collector.pid"
LOG_FILE="$DATA_PATH/collector.log"
SYSINFO_FILE="$DATA_PATH/system_info.json"

mkdir -p "$RAW_DIR"

log_msg() {
    printf "[%s] %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >> "$LOG_FILE"
}

# Acquire exclusive lock (flock on Linux, PID-lock fallback on Git Bash/MSYS2)
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log_msg "ERROR: Another collector instance holds $LOCK_FILE. Exiting."
        echo "Collector is already running (lock held)." >&2
        exit 2
    fi
else
    if [[ -f "$LOCK_FILE" ]]; then
        EXISTING_PID="$(tr -d '\r\n ' < "$LOCK_FILE" 2>/dev/null || true)"
        if [[ "$EXISTING_PID" =~ ^[0-9]+$ ]] && [[ "$EXISTING_PID" -ne "$$" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
            log_msg "ERROR: Another collector instance (PID $EXISTING_PID) holds $LOCK_FILE. Exiting."
            echo "Collector is already running (lock held)." >&2
            exit 2
        fi
    fi
    printf "%d\n" "$$" > "$LOCK_FILE"
fi

printf "%d\n" "$$" > "$PID_FILE"

cleanup() {
    log_msg "INFO: Collector (PID $$) stopping cleanly."
    if [[ -f "$PID_FILE" ]] && [[ "$(tr -d '\r\n ' < "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
        rm -f "$PID_FILE"
    fi
    rm -f "$LOCK_FILE"
}
RUNNING=1
trap 'RUNNING=0' SIGTERM SIGINT
trap 'cleanup' EXIT

# --- GPU Detection (INTERFACES.md Section 3.4) ---
GPU_VENDOR="none"
GPU_NAME=""
AMD_CARD_DIR=""

detect_gpu() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        local nv_name
        nv_name="$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits -i 0 2>/dev/null | tr -d '\r' | head -n 1 || true)"
        nv_name="${nv_name#"${nv_name%%[![:space:]]*}"}"
        nv_name="${nv_name%"${nv_name##*[![:space:]]}"}"
        if [[ -n "$nv_name" && ! "$nv_name" =~ "NVIDIA-SMI has failed" && ! "$nv_name" =~ "Unable to" ]]; then
            GPU_VENDOR="nvidia"
            GPU_NAME="$nv_name"
            return 0
        fi
    fi

    local card_path
    for card_path in /sys/class/drm/card*/device; do
        if [[ -r "$card_path/gpu_busy_percent" ]]; then
            GPU_VENDOR="amd"
            AMD_CARD_DIR="$card_path"
            if command -v lspci >/dev/null 2>&1; then
                GPU_NAME="$(lspci 2>/dev/null | tr -d '\r' | grep -iE "VGA|3D|Display" | grep -iE "AMD|ATI|Radeon" | head -n 1 | sed -E 's/^[^:]+: [^:]+: //' || true)"
            fi
            [[ -z "$GPU_NAME" ]] && GPU_NAME="AMD Radeon GPU"
            return 0
        fi
    done

    GPU_VENDOR="none"
    GPU_NAME=""
}

detect_gpu

json_escape() {
    local s="${1//$'\r'/}"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf "%s" "$s"
}

# --- Write data/system_info.json (INTERFACES.md Section 3.5) ---
write_system_info() {
    local collected_at hostname_str kernel_str cpu_model cpu_cores total_ram_mb disk_total_gb cpu_temp_max_c
    collected_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    hostname_str="$( (hostname 2>/dev/null || uname -n 2>/dev/null || echo "localhost") | tr -d '\r' )"
    kernel_str="$( (uname -r 2>/dev/null | cut -d'-' -f1 || echo "unknown") | tr -d '\r' )"

    cpu_model="$(awk -F': ' '/^model name/ { gsub(/\r/, "", $2); print $2; exit }' /proc/cpuinfo 2>/dev/null || true)"
    [[ -z "$cpu_model" ]] && cpu_model="$( (uname -p 2>/dev/null || echo "Unknown CPU") | tr -d '\r' )"
    cpu_cores="$( (nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo 1) | tr -d '\r' )"

    total_ram_mb="$(awk '/^MemTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo 2>/dev/null || echo 0)"
    disk_total_gb="$(df -P -k / 2>/dev/null | awk 'NR==2 { printf "%d", $2 / (1024 * 1024) }' || echo 0)"

    cpu_temp_max_c="null"
    local hw_file raw_limit
    for hw_file in /sys/class/hwmon/*/temp1_max /sys/class/hwmon/*/temp1_crit; do
        [[ -r "$hw_file" ]] || continue
        raw_limit="$(tr -d '\r\n' < "$hw_file" 2>/dev/null || true)"
        if [[ "$raw_limit" =~ ^[0-9]+$ ]] && [[ "$raw_limit" -gt 0 ]]; then
            local deg=$(( raw_limit > 1000 ? raw_limit / 1000 : raw_limit ))
            if [[ "$deg" -ge 50 && "$deg" -le 130 ]]; then
                cpu_temp_max_c="$deg"
                break
            fi
        fi
    done

    local gpu_json="null"
    if [[ "$GPU_VENDOR" != "none" ]]; then
        gpu_json="$(printf '{ "vendor": "%s", "name": "%s" }' \
            "$(json_escape "$GPU_VENDOR")" "$(json_escape "$GPU_NAME")")"
    fi

    local tmp_file="${SYSINFO_FILE}.tmp.$$"
    cat > "$tmp_file" <<SYS_EOF
{
  "collected_at": "$collected_at",
  "hostname": "$(json_escape "$hostname_str")",
  "kernel": "$(json_escape "$kernel_str")",
  "cpu_model": "$(json_escape "$cpu_model")",
  "cpu_cores": $cpu_cores,
  "total_ram_mb": $total_ram_mb,
  "disk_total_gb": $disk_total_gb,
  "cpu_temp_max_c": $cpu_temp_max_c,
  "gpu": $gpu_json
}
SYS_EOF
    mv -f "$tmp_file" "$SYSINFO_FILE"
}

write_system_info
log_msg "INFO: Collector started (PID $$, interval=${INTERVAL_S}s, simulate_temp=${SIMULATE_TEMP}, gpu=${GPU_VENDOR})."

METRICS_HEADER="epoch,iso_time,cpu_pct,mem_used_pct,mem_avail_mb,swap_used_pct,disk_used_pct,load1,load5,load15,net_rx_kbps,net_tx_kbps,cpu_temp_c,gpu_util_pct,gpu_mem_used_pct,gpu_temp_c"
PROCESSES_HEADER="epoch,pid,command,cpu_pct,mem_pct,rank"

read_cpu_stat() {
    if [[ -r /proc/stat ]]; then
        awk '/^cpu / {
            idle = $5 + $6
            total = $2 + $3 + $4 + $5 + $6 + $7 + $8 + $9
            print total, idle
            found = 1
            exit
        }
        END { if (!found) print "0 0" }' /proc/stat
    else
        echo "0 0"
    fi
}

read_net_bytes() {
    if [[ -r /proc/net/dev ]]; then
        awk -F'[: ]+' '
        NR > 2 && $1 != "" && $1 != "lo" {
            rx += $2
            tx += $10
        }
        END {
            printf "%.0f %.0f\n", rx, tx
        }' /proc/net/dev
    elif command -v netstat >/dev/null 2>&1; then
        # Fallback for Git Bash / Windows netstat -e
        netstat -e 2>/dev/null | tr -d '\r' | awk '
        /^Bytes/ {
            printf "%.0f %.0f\n", $2, $3
            found = 1
            exit
        }
        END {
            if (!found) print "0 0"
        }'
    else
        echo "0 0"
    fi
}

read_memory() {
    if [[ -r /proc/meminfo ]]; then
        awk '
        /^MemTotal:/     { mem_total = $2 }
        /^MemAvailable:/ { mem_avail = $2; has_avail = 1 }
        /^MemFree:/      { mem_free = $2 }
        /^Buffers:/      { buffers = $2 }
        /^Cached:/       { cached = $2 }
        /^SwapTotal:/    { swap_total = $2 }
        /^SwapFree:/     { swap_free = $2 }
        END {
            if (!has_avail) mem_avail = mem_free + buffers + cached
            mem_used_pct = (mem_total > 0) ? ((mem_total - mem_avail) / mem_total) * 100.0 : 0.0
            if (mem_used_pct < 0) mem_used_pct = 0.0
            if (mem_used_pct > 100) mem_used_pct = 100.0
            mem_avail_mb = int(mem_avail / 1024)
            swap_used_pct = (swap_total > 0) ? ((swap_total - swap_free) / swap_total) * 100.0 : 0.0
            if (swap_used_pct < 0) swap_used_pct = 0.0
            if (swap_used_pct > 100) swap_used_pct = 100.0
            printf "%.1f %d %.1f\n", mem_used_pct, mem_avail_mb, swap_used_pct
        }' /proc/meminfo
    else
        echo "0.0 0 0.0"
    fi
}

read_disk_pct() {
    df -P / 2>/dev/null | tr -d '\r' | awk 'NR==2 {
        if ($2 > 0) printf "%.1f\n", ($3 / $2) * 100.0
        else print "0.0"
    }'
}

read_loadavg() {
    if [[ -r /proc/loadavg ]]; then
        awk '{ printf "%.2f %.2f %.2f\n", $1, $2, $3 }' /proc/loadavg
    else
        echo "0.00 0.00 0.00"
    fi
}

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

    local hottest=""
    local f raw_val
    for f in /sys/class/thermal/thermal_zone*/temp; do
        [[ -r "$f" ]] || continue
        raw_val="$(tr -d '\r\n' < "$f" 2>/dev/null || true)"
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

    if command -v sensors >/dev/null 2>&1; then
        hottest="$(sensors 2>/dev/null | tr -d '\r' | awk '
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

    printf ""
}

poll_gpu_metrics() {
    if [[ "$COLLECT_GPU" != "true" || "$GPU_VENDOR" == "none" ]]; then
        printf ",,"
        return 0
    fi

    if [[ "$GPU_VENDOR" == "nvidia" ]]; then
        local nv_out
        nv_out="$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits -i 0 2>/dev/null | tr -d '\r' | head -n 1 || true)"
        if [[ -n "$nv_out" ]]; then
            awk -F',' '{
                gsub(/[[:space:]]+/, "", $1)
                gsub(/[[:space:]]+/, "", $2)
                gsub(/[[:space:]]+/, "", $3)
                gsub(/[[:space:]]+/, "", $4)
                util = ($1 ~ /^[0-9.]+$/) ? sprintf("%.1f", $1 + 0) : ""
                mem = ($2 ~ /^[0-9.]+$/ && $3 ~ /^[0-9.]+$/ && $3 > 0) ? sprintf("%.1f", ($2 / $3) * 100.0) : ""
                temp = ($4 ~ /^[0-9.]+$/) ? sprintf("%.1f", $4 + 0) : ""
                printf "%s,%s,%s", util, mem, temp
            }' <<< "$nv_out"
            return 0
        fi
    elif [[ "$GPU_VENDOR" == "amd" && -n "$AMD_CARD_DIR" ]]; then
        local busy_raw vram_used vram_total temp_raw
        busy_raw="$(tr -d '\r\n' < "$AMD_CARD_DIR/gpu_busy_percent" 2>/dev/null || true)"
        vram_used="$(tr -d '\r\n' < "$AMD_CARD_DIR/mem_info_vram_used" 2>/dev/null || true)"
        vram_total="$(tr -d '\r\n' < "$AMD_CARD_DIR/mem_info_vram_total" 2>/dev/null || true)"
        temp_raw=""
        local tf
        for tf in "$AMD_CARD_DIR"/hwmon/hwmon*/temp1_input; do
            if [[ -r "$tf" ]]; then
                temp_raw="$(tr -d '\r\n' < "$tf" 2>/dev/null || true)"
                break
            fi
        done
        awk -v b="$busy_raw" -v vu="$vram_used" -v vt="$vram_total" -v tr="$temp_raw" 'BEGIN {
            util = (b ~ /^[0-9.]+$/) ? sprintf("%.1f", b + 0) : ""
            mem = (vu ~ /^[0-9.]+$/ && vt ~ /^[0-9.]+$/ && vt > 0) ? sprintf("%.1f", (vu / vt) * 100.0) : ""
            temp = (tr ~ /^[0-9.]+$/ && tr > 0) ? sprintf("%.1f", (tr > 1000 ? tr / 1000.0 : tr)) : ""
            printf "%s,%s,%s", util, mem, temp
        }'
        return 0
    fi

    printf ",,"
}

collect_top_processes() {
    local epoch="$1"
    local date_str="$2"
    local proc_file="$RAW_DIR/processes_${date_str}.csv"

    if [[ ! -f "$proc_file" ]]; then
        printf "%s\n" "$PROCESSES_HEADER" > "$proc_file"
    fi

    local ps_out
    if ps_out="$(ps -eo pid=,comm=,%cpu=,%mem= --sort=-%cpu 2>/dev/null)" && [[ -n "$ps_out" ]]; then
        awk -v ep="$epoch" -v top_n="$TOP_N" '
        NR <= top_n {
            pid = $1
            cpu = $3 + 0
            mem = $4 + 0
            cmd = $2
            gsub(/[, \t\r\n]+/, "_", cmd)
            printf "%s,%s,%s,%.1f,%.1f,%d\n", ep, pid, cmd, cpu, mem, NR
        }' <<< "$ps_out" >> "$proc_file"
    else
        # Fallback for Git Bash / MSYS2 ps where -eo and --sort are unsupported
        ps -ef 2>/dev/null | tr -d '\r' | awk -v ep="$epoch" -v top_n="$TOP_N" '
        NR > 1 && count < top_n {
            count++
            pid = $2
            cmd = $NF
            sub(/^.*\//, "", cmd)
            gsub(/[, \t\r\n]+/, "_", cmd)
            if (cmd == "") cmd = "process"
            printf "%s,%s,%s,0.0,0.0,%d\n", ep, pid, cmd, count
        }' >> "$proc_file"
    fi
}

read -r PREV_CPU_TOTAL PREV_CPU_IDLE <<< "$(read_cpu_stat)"
read -r PREV_NET_RX PREV_NET_TX <<< "$(read_net_bytes)"
PREV_EPOCH="$(date -u +%s)"
FIRST_SAMPLE=1
SAMPLE_COUNT=0
LAST_GPU_FIELDS=",,"

while [[ "$RUNNING" -eq 1 ]]; do
    sleep "$INTERVAL_S" &
    wait $! 2>/dev/null || true
    [[ "$RUNNING" -eq 0 ]] && break

    EPOCH="$(date -u +%s)"
    ISO_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    DATE_STR="$(date -u +"%Y%m%d")"

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

    # Network rate
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

    # CPU Temperature
    CPU_TEMP_C="$(read_cpu_temp "$CPU_PCT")"

    # GPU Polling every gpu_poll_every samples
    if [[ "$COLLECT_GPU" != "true" || "$GPU_VENDOR" == "none" ]]; then
        LAST_GPU_FIELDS=",,"
    elif (( SAMPLE_COUNT % GPU_POLL_EVERY == 0 )); then
        LAST_GPU_FIELDS="$(poll_gpu_metrics)"
    fi
    SAMPLE_COUNT=$(( SAMPLE_COUNT + 1 ))

    METRICS_FILE="$RAW_DIR/metrics_${DATE_STR}.csv"
    if [[ ! -f "$METRICS_FILE" ]]; then
        printf "%s\n" "$METRICS_HEADER" > "$METRICS_FILE"
    fi

    printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
        "$EPOCH" "$ISO_TIME" "$CPU_PCT" "$MEM_USED_PCT" "$MEM_AVAIL_MB" \
        "$SWAP_USED_PCT" "$DISK_USED_PCT" "$LOAD1" "$LOAD5" "$LOAD15" \
        "$NET_RX_KBPS" "$NET_TX_KBPS" "$CPU_TEMP_C" "$LAST_GPU_FIELDS" >> "$METRICS_FILE"

    collect_top_processes "$EPOCH" "$DATE_STR"
done
