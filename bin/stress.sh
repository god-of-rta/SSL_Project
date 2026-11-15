#!/usr/bin/env bash
# bin/stress.sh - bounded CPU, memory, and GPU load generator
#
# Usage:
#   bin/stress.sh [seconds]
#   bin/stress.sh --duration 30 --cpu 4 --mem 300 --gpu
#
# Options:
#   -d, --duration SECS   Duration in seconds (default: 30, max: 120)
#   -c, --cpu CORES       CPU cores to stress (default: all cores, 0 to disable)
#   -m, --mem MB          Memory in MB to allocate (default: 300, 0 to disable)
#   -g, --gpu             Force GPU stress (auto-detected if supported GPU present)
#       --no-gpu          Disable GPU stress
#   -h, --help            Show this help message
#
# Safety: runs for at most 120 s, uses at most MEM_MB of RAM, and always
# kills everything it started (on finish, Ctrl-C or SIGTERM).

set -e

DEFAULT_SECS=30
MAX_SECS=120
DEFAULT_MEM_MB=300

# Defaults
SECS=""
CPU_CORES=""
MEM_MB=""
GPU_MODE="auto" # "auto", "on", "off"

show_help() {
    cat <<EOF
Usage: bin/stress.sh [OPTIONS] [SECONDS]

Options:
  -d, --duration SECS   Run duration in seconds (default: 30, max: 120)
  -c, --cpu CORES       Number of CPU cores to stress (default: all cores, 0 to skip)
  -m, --mem MB          Amount of RAM to allocate in MB (default: 300, 0 to skip)
  -g, --gpu             Simulate GPU load (auto-detected if NVIDIA/OpenGL exists)
      --no-gpu          Skip GPU stress
  -h, --help            Show this help message

Examples:
  bin/stress.sh 15                      # Stress all cores + 300MB RAM for 15s
  bin/stress.sh --duration 20 --cpu 4   # Stress 4 CPU cores for 20s
  bin/stress.sh --gpu --duration 15     # Stress GPU + CPU + RAM for 15s
  bin/stress.sh --cpu 0 --gpu           # Stress GPU only
EOF
}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        -d|--duration)
            SECS="$2"
            shift 2
            ;;
        -c|--cpu)
            CPU_CORES="$2"
            shift 2
            ;;
        -m|--mem)
            MEM_MB="$2"
            shift 2
            ;;
        -g|--gpu)
            GPU_MODE="on"
            shift
            ;;
        --no-gpu)
            GPU_MODE="off"
            shift
            ;;
        *)
            if [[ -z "$SECS" && "$1" =~ ^[0-9]+$ ]]; then
                SECS="$1"
                shift
            else
                echo "stress.sh: unrecognized option or invalid argument: $1" >&2
                show_help >&2
                exit 1
            fi
            ;;
    esac
done

SECS="${SECS:-$DEFAULT_SECS}"
case "$SECS" in
    ''|*[!0-9]*) echo "stress.sh: duration must be a whole number, got '$SECS'" >&2; exit 1 ;;
esac
if [ "$SECS" -lt 1 ]; then
    echo "stress.sh: duration must be at least 1" >&2; exit 1
fi
if [ "$SECS" -gt "$MAX_SECS" ]; then
    echo "stress.sh: capping $SECS s to the maximum of $MAX_SECS s" >&2
    SECS=$MAX_SECS
fi

TOTAL_CORES=$(env -u OMP_NUM_THREADS nproc --all 2>/dev/null || nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
CPU_CORES="${CPU_CORES:-$TOTAL_CORES}"
case "$CPU_CORES" in
    ''|*[!0-9]*) echo "stress.sh: cpu cores must be a non-negative integer, got '$CPU_CORES'" >&2; exit 1 ;;
esac

MEM_MB="${MEM_MB:-$DEFAULT_MEM_MB}"
case "$MEM_MB" in
    ''|*[!0-9]*) echo "stress.sh: memory must be a non-negative integer, got '$MEM_MB'" >&2; exit 1 ;;
esac

PIDS=()
cleanup() {
    trap - EXIT INT TERM
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "stress.sh: stopped, load removed"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

PY=$(command -v python3 || command -v python || true)

# 1. CPU Load
if [ "$CPU_CORES" -gt 0 ]; then
    for _ in $(seq 1 "$CPU_CORES"); do
        ( while :; do :; done ) &
        PIDS+=($!)
    done
    CPU_MSG="$CPU_CORES CPU core(s)"
else
    CPU_MSG="0 CPU cores (skipped)"
fi

# 2. Memory Load
if [ "$MEM_MB" -gt 0 ]; then
    if [ -n "$PY" ]; then
        "$PY" -c "
import time
buf = bytearray($MEM_MB * 1024 * 1024)
for i in range(0, len(buf), 4096):
    buf[i] = 1
time.sleep($SECS + 5)
" &
        PIDS+=($!)
        MEM_MSG="${MEM_MB} MB RAM"
    else
        MEM_MSG="0 MB RAM (python not found)"
    fi
else
    MEM_MSG="0 MB RAM (skipped)"
fi

# 3. GPU Load
GPU_MSG="GPU load skipped"
if [ "$GPU_MODE" != "off" ]; then
    # Check for NVIDIA CUDA / glxgears capability
    HAS_NVIDIA=0
    if command -v nvidia-smi >/dev/null 2>&1; then
        HAS_NVIDIA=1
    fi

    # Attempt headless Python CUDA stress if NVIDIA driver library exists
    STARTED_GPU=0
    if [ "$HAS_NVIDIA" -eq 1 ] && [ -n "$PY" ]; then
        if "$PY" -c "import ctypes; ctypes.CDLL('libcuda.so.1').cuInit(0)" 2>/dev/null; then
            "$PY" -c "
import ctypes, time, sys
try:
    cuda = ctypes.CDLL('libcuda.so.1')
    if cuda.cuInit(0) != 0: sys.exit(1)
    dev = ctypes.c_int()
    if cuda.cuDeviceGet(ctypes.byref(dev), 0) != 0: sys.exit(1)
    ctx = ctypes.c_void_p()
    if cuda.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev) != 0: sys.exit(1)
    ptr1, ptr2 = ctypes.c_ulonglong(), ctypes.c_ulonglong()
    size = 128 * 1024 * 1024  # 128MB GPU memory
    if cuda.cuMemAlloc_v2(ctypes.byref(ptr1), size) != 0: sys.exit(1)
    if cuda.cuMemAlloc_v2(ctypes.byref(ptr2), size) != 0: sys.exit(1)
    end = time.time() + $SECS + 2
    while time.time() < end:
        cuda.cuMemcpyDtoD_v2(ptr2, ptr1, size)
    cuda.cuMemFree_v2(ptr1)
    cuda.cuMemFree_v2(ptr2)
    cuda.cuCtxDestroy_v2(ctx)
except Exception:
    sys.exit(1)
" &
            PIDS+=($!)
            STARTED_GPU=1
            GPU_MSG="NVIDIA CUDA stress (100% util, 256MB VRAM)"
        fi
    fi

    # Fallback to OpenGL / glxgears if CUDA loop was not started
    if [ "$STARTED_GPU" -eq 0 ] && command -v glxgears >/dev/null 2>&1 && [ -n "${DISPLAY:-$WAYLAND_DISPLAY}" ]; then
        if [ "$HAS_NVIDIA" -eq 1 ]; then
            __GL_SYNC_TO_VBLANK=0 vblank_mode=0 __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
                glxgears -geometry 1x1-200-200 >/dev/null 2>&1 &
            PIDS+=($!)
            STARTED_GPU=1
            GPU_MSG="NVIDIA OpenGL stress (glxgears)"
        else
            vblank_mode=0 glxgears -geometry 1x1-200-200 >/dev/null 2>&1 &
            PIDS+=($!)
            STARTED_GPU=1
            GPU_MSG="OpenGL stress (glxgears)"
        fi
    fi

    if [ "$STARTED_GPU" -eq 0 ] && [ "$GPU_MODE" = "on" ]; then
        GPU_MSG="GPU stress requested but no supported GPU driver found"
    fi
fi

echo "stress.sh: running for ${SECS} s with:"
echo "  - CPU: $CPU_MSG"
echo "  - RAM: $MEM_MSG"
echo "  - GPU: $GPU_MSG"

sleep "$SECS"
exit 0
