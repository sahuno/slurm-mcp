#!/usr/bin/env bash
# setup.sh — Interactive setup for slurm-mcp
# Author: Samuel Ahuno
# Date: 2026-02-28
# Purpose: Detect SLURM environment, install the package, and configure ~/.claude.json

set -euo pipefail

CLAUDE_JSON="$HOME/.claude.json"

echo "============================================"
echo "  slurm-mcp setup"
echo "============================================"
echo ""

# ── 1. Check prerequisites ──────────────────────────────────────────────

# Check that we're on a SLURM cluster
if ! command -v sbatch &>/dev/null; then
    echo "ERROR: sbatch not found. This tool requires a SLURM cluster."
    echo "       Make sure you're on a login node with SLURM commands available."
    exit 1
fi

if ! command -v claude &>/dev/null; then
    echo "WARNING: 'claude' CLI not found in PATH."
    echo "         Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code"
    echo ""
fi

# ── 2. Detect defaults ──────────────────────────────────────────────────

# Python path
DETECTED_PYTHON="$(which python3 2>/dev/null || echo "")"

# SLURM accounts — multi-strategy fallback chain
DETECTED_ACCOUNTS=()
ACCOUNT_SOURCE=""

# Strategy 1: sacctmgr associations (works on accounting-enabled clusters)
if command -v sacctmgr &>/dev/null; then
    while IFS= read -r acct; do
        [ -n "$acct" ] && DETECTED_ACCOUNTS+=("$acct")
    done < <(sacctmgr -n show assoc user="$USER" format=Account%-30 2>/dev/null | xargs -n1 | sort -u)
    [ ${#DETECTED_ACCOUNTS[@]} -gt 0 ] && ACCOUNT_SOURCE="sacctmgr"
fi

# Strategy 2: SLURM_DEFAULT_ACCOUNT environment variable
if [ ${#DETECTED_ACCOUNTS[@]} -eq 0 ] && [ -n "${SLURM_DEFAULT_ACCOUNT:-}" ]; then
    DETECTED_ACCOUNTS+=("$SLURM_DEFAULT_ACCOUNT")
    ACCOUNT_SOURCE="environment"
fi

# Strategy 3: Parse existing ~/.claude.json for a previously configured account
if [ ${#DETECTED_ACCOUNTS[@]} -eq 0 ] && [ -f "$CLAUDE_JSON" ] && command -v jq &>/dev/null; then
    PREV_ACCOUNT="$(jq -r '.mcpServers.slurm.env.SLURM_DEFAULT_ACCOUNT // empty' "$CLAUDE_JSON" 2>/dev/null || true)"
    if [ -n "$PREV_ACCOUNT" ]; then
        DETECTED_ACCOUNTS+=("$PREV_ACCOUNT")
        ACCOUNT_SOURCE="previous config"
    fi
fi

# Log directory
DETECTED_LOG_DIR="$HOME/slurm_logs"

echo "Detected environment:"
echo "  Python:      ${DETECTED_PYTHON:-<not found>}"
if [ ${#DETECTED_ACCOUNTS[@]} -gt 0 ]; then
    echo "  Account(s):  ${DETECTED_ACCOUNTS[*]} (via $ACCOUNT_SOURCE)"
else
    echo "  Account(s):  <not found — will ask>"
fi
echo "  Log dir:     $DETECTED_LOG_DIR"
echo ""

# ── 3. Prompt user to confirm/override ──────────────────────────────────

# ── Python ──
read -rp "Python path [$DETECTED_PYTHON]: " PYTHON_PATH
PYTHON_PATH="${PYTHON_PATH:-$DETECTED_PYTHON}"

if [ -z "$PYTHON_PATH" ] || [ ! -x "$PYTHON_PATH" ]; then
    echo "ERROR: Python not found at '$PYTHON_PATH'. Please provide a valid path."
    exit 1
fi

# ── SLURM account ──
if [ ${#DETECTED_ACCOUNTS[@]} -eq 0 ]; then
    echo "Could not auto-detect your SLURM account."
    echo "  Hint: Ask your PI or cluster admin, or check your group's SLURM docs."
    echo "  Common patterns: lab name, PI last name, or group ID."
    echo ""
    while true; do
        read -rp "SLURM account: " SLURM_ACCOUNT
        if [ -n "$SLURM_ACCOUNT" ]; then
            break
        fi
        echo "  A SLURM account is required. Please enter one."
    done
elif [ ${#DETECTED_ACCOUNTS[@]} -eq 1 ]; then
    echo "Found 1 SLURM account: ${DETECTED_ACCOUNTS[0]} (via $ACCOUNT_SOURCE)"
    read -rp "SLURM account [${DETECTED_ACCOUNTS[0]}]: " SLURM_ACCOUNT
    SLURM_ACCOUNT="${SLURM_ACCOUNT:-${DETECTED_ACCOUNTS[0]}}"
else
    echo "Found ${#DETECTED_ACCOUNTS[@]} SLURM accounts (via $ACCOUNT_SOURCE):"
    for i in "${!DETECTED_ACCOUNTS[@]}"; do
        echo "  $((i+1))) ${DETECTED_ACCOUNTS[$i]}"
    done
    echo ""
    read -rp "Select account [1]: " ACCT_CHOICE
    ACCT_CHOICE="${ACCT_CHOICE:-1}"
    if [[ "$ACCT_CHOICE" =~ ^[0-9]+$ ]] && [ "$ACCT_CHOICE" -ge 1 ] && [ "$ACCT_CHOICE" -le ${#DETECTED_ACCOUNTS[@]} ]; then
        SLURM_ACCOUNT="${DETECTED_ACCOUNTS[$((ACCT_CHOICE-1))]}"
    else
        echo "ERROR: Invalid selection '$ACCT_CHOICE'."
        exit 1
    fi
fi
echo "  Using account: $SLURM_ACCOUNT"
echo ""

# ── Partition (filtered by account access) ──
USER_PARTITIONS=()
PARTITION_SOURCE=""

if command -v sacctmgr &>/dev/null; then
    while IFS= read -r part; do
        [ -n "$part" ] && USER_PARTITIONS+=("$part")
    done < <(sacctmgr -n show assoc user="$USER" account="$SLURM_ACCOUNT" format=Partition%-30 2>/dev/null | xargs -n1 | sort -u)
fi

# Remove empty string entries (sacctmgr returns "" for account-level assocs with no partition)
CLEAN_PARTITIONS=()
for p in "${USER_PARTITIONS[@]}"; do
    [ -n "$p" ] && CLEAN_PARTITIONS+=("$p")
done
USER_PARTITIONS=("${CLEAN_PARTITIONS[@]+"${CLEAN_PARTITIONS[@]}"}")

if [ ${#USER_PARTITIONS[@]} -gt 0 ]; then
    PARTITION_SOURCE="sacctmgr"
fi

# Track whether we fell back to sinfo (determines selection UI)
SINFO_FALLBACK=false

if [ ${#USER_PARTITIONS[@]} -eq 0 ]; then
    # Fallback: show all cluster partitions via sinfo
    echo "Could not determine partitions for account '$SLURM_ACCOUNT' via sacctmgr."
    echo ""
    if command -v sinfo &>/dev/null; then
        while IFS= read -r part; do
            part_clean=$(echo "$part" | tr -d '*')
            [ -n "$part_clean" ] && USER_PARTITIONS+=("$part_clean")
        done < <(sinfo -h -o "%P" 2>/dev/null | sort -u)
        [ ${#USER_PARTITIONS[@]} -gt 0 ] && PARTITION_SOURCE="sinfo" && SINFO_FALLBACK=true
    fi
fi

if [ ${#USER_PARTITIONS[@]} -eq 0 ]; then
    # No partitions found at all — manual entry
    while true; do
        read -rp "Default partition (could not auto-detect): " SLURM_PARTITION
        if [ -n "$SLURM_PARTITION" ]; then
            break
        fi
        echo "  A partition is required. Please enter one."
    done
elif [ ${#USER_PARTITIONS[@]} -eq 1 ]; then
    echo "Found 1 partition: ${USER_PARTITIONS[0]}"
    read -rp "Default partition [${USER_PARTITIONS[0]}]: " SLURM_PARTITION
    SLURM_PARTITION="${SLURM_PARTITION:-${USER_PARTITIONS[0]}}"
elif [ "$SINFO_FALLBACK" = true ] && [ ${#USER_PARTITIONS[@]} -gt 10 ]; then
    # Many partitions from sinfo — use type-to-select with column display
    echo "Available cluster partitions (${#USER_PARTITIONS[@]} total):"
    echo ""
    printf '%s\n' "${USER_PARTITIONS[@]}" | column 2>/dev/null || printf '%s\n' "${USER_PARTITIONS[@]}"
    echo ""
    while true; do
        read -rp "Type your default partition name: " SLURM_PARTITION
        if [ -z "$SLURM_PARTITION" ]; then
            echo "  A partition is required. Please enter one."
            continue
        fi
        # Validate against the list
        VALID=false
        for p in "${USER_PARTITIONS[@]}"; do
            if [ "$p" = "$SLURM_PARTITION" ]; then
                VALID=true
                break
            fi
        done
        if [ "$VALID" = true ]; then
            break
        else
            echo "  '$SLURM_PARTITION' is not in the partition list. Please check spelling and try again."
        fi
    done
else
    # Manageable number of partitions — numbered list (sacctmgr-filtered or small sinfo list)
    echo "Partitions available for account '$SLURM_ACCOUNT' (via $PARTITION_SOURCE):"
    for i in "${!USER_PARTITIONS[@]}"; do
        echo "  $((i+1))) ${USER_PARTITIONS[$i]}"
    done
    echo ""
    read -rp "Select default partition [1]: " PART_CHOICE
    PART_CHOICE="${PART_CHOICE:-1}"
    if [[ "$PART_CHOICE" =~ ^[0-9]+$ ]] && [ "$PART_CHOICE" -ge 1 ] && [ "$PART_CHOICE" -le ${#USER_PARTITIONS[@]} ]; then
        SLURM_PARTITION="${USER_PARTITIONS[$((PART_CHOICE-1))]}"
    else
        echo "ERROR: Invalid selection '$PART_CHOICE'."
        exit 1
    fi
fi
echo "  Using partition: $SLURM_PARTITION"
echo ""

# ── Validate account + partition with sbatch --test-only ──
if command -v sbatch &>/dev/null; then
    # Check if --test-only works on this cluster.
    # Strategy: run a bare test first. If it fails, run with user values.
    #   - If both fail with same "Invalid account" error → cluster doesn't support --test-only, skip.
    #   - If bare fails but parameterized succeeds → validation passed.
    #   - If bare succeeds but parameterized fails → user has bad combo, show error.
    #   - If both succeed → validation passed.
    echo "Validating account + partition combination..."

    if BARE_TEST_ERR="$(sbatch --test-only --wrap="hostname" -n1 2>&1)"; then
        BARE_TEST_RC=0
    else
        BARE_TEST_RC=$?
    fi

    if PARAM_TEST_ERR="$(sbatch --test-only --wrap="hostname" --partition="$SLURM_PARTITION" --account="$SLURM_ACCOUNT" -n1 2>&1)"; then
        PARAM_TEST_RC=0
    else
        PARAM_TEST_RC=$?
    fi

    if [ $PARAM_TEST_RC -eq 0 ]; then
        echo "  Validation passed: account='$SLURM_ACCOUNT' partition='$SLURM_PARTITION'"
    elif [ $BARE_TEST_RC -ne 0 ] && [ $PARAM_TEST_RC -ne 0 ]; then
        # Both fail — cluster likely doesn't support --test-only validation
        echo "  Skipping validation (sbatch --test-only not usable on this cluster)."
        echo "  Proceeding with account='$SLURM_ACCOUNT' partition='$SLURM_PARTITION'."
        echo "  If jobs fail later, re-run setup with corrected values."
    else
        # Bare test passed but parameterized failed — user's combo is actually bad
        echo "  WARNING: Validation failed for account='$SLURM_ACCOUNT' partition='$SLURM_PARTITION'."
        echo "  Scheduler said: $PARAM_TEST_ERR"
        echo ""
        MAX_VALIDATE_ATTEMPTS=3
        VALIDATE_ATTEMPT=0
        while true; do
            VALIDATE_ATTEMPT=$((VALIDATE_ATTEMPT + 1))
            if [ "$VALIDATE_ATTEMPT" -gt "$MAX_VALIDATE_ATTEMPTS" ]; then
                echo ""
                echo "  Reached $MAX_VALIDATE_ATTEMPTS failed attempts."
                read -rp "  Continue anyway with current settings? [y/N]: " FORCE_CONTINUE
                if [[ "${FORCE_CONTINUE,,}" =~ ^y ]]; then
                    echo "  Proceeding with unvalidated settings."
                    break
                else
                    echo "  Aborting. Fix your SLURM account/partition and re-run setup."
                    exit 1
                fi
            fi
            echo "  You can re-enter account and/or partition (attempt $VALIDATE_ATTEMPT/$MAX_VALIDATE_ATTEMPTS)."
            read -rp "  New account [$SLURM_ACCOUNT]: " NEW_ACCOUNT
            SLURM_ACCOUNT="${NEW_ACCOUNT:-$SLURM_ACCOUNT}"
            read -rp "  New partition [$SLURM_PARTITION]: " NEW_PARTITION
            SLURM_PARTITION="${NEW_PARTITION:-$SLURM_PARTITION}"
            if RETRY_ERR="$(sbatch --test-only --wrap="hostname" --partition="$SLURM_PARTITION" --account="$SLURM_ACCOUNT" -n1 2>&1)"; then
                echo "  Validation passed: account='$SLURM_ACCOUNT' partition='$SLURM_PARTITION'"
                break
            else
                echo ""
                echo "  WARNING: Still failing for account='$SLURM_ACCOUNT' partition='$SLURM_PARTITION'."
                echo "  Scheduler said: $RETRY_ERR"
            fi
        done
    fi
    echo ""
fi

# ── Generate partition limits profile ──────────────────────────────────
generate_partition_limits() {
    # Query sinfo and aggregate per-partition resource limits into a JSON profile.
    # Writes timestamped file + symlink under ~/.slurm-mcp/.
    local outdir="$HOME/.slurm-mcp"
    local timestamp
    timestamp="$(date +%Y%m%d_%H%M%S)"
    local outfile="$outdir/partition_limits_${timestamp}.json"
    local symlink="$outdir/partition_limits_latest.json"

    mkdir -p "$outdir"

    if ! command -v sinfo &>/dev/null; then
        echo "  Skipping partition limits generation (sinfo not found)."
        return 1
    fi

    local sinfo_output
    sinfo_output="$(sinfo -h -o '%P|%l|%c|%m|%G|%D' 2>/dev/null)" || {
        echo "  Skipping partition limits generation (sinfo query failed)."
        return 1
    }

    if [ -z "$sinfo_output" ]; then
        echo "  Skipping partition limits generation (sinfo returned no data)."
        return 1
    fi

    # Pipe raw sinfo output to Python for aggregation (Python is verified available at this point)
    "$PYTHON_PATH" -c '
import sys, json, re
from datetime import datetime
from collections import defaultdict

partitions = defaultdict(lambda: {
    "time_limit_hours": 0,
    "max_cpus_per_node": 0,
    "max_mem_mb": 0,
    "max_mem_gb": 0,
    "total_nodes": 0,
    "gpu_types": {},
    "max_gpus_per_node": 0,
    "has_gpu_nodes": False,
    "has_cpu_only_nodes": False,
})

def parse_time_limit(tl):
    """Parse SLURM time limit like D-HH:MM:SS, HH:MM:SS, or UNLIMITED."""
    tl = tl.strip()
    if tl.upper() == "INFINITE" or tl.upper() == "UNLIMITED":
        return 8760  # 365 days
    days = 0
    if "-" in tl:
        day_part, tl = tl.split("-", 1)
        days = int(day_part)
    parts = tl.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), int(parts[1])
    elif len(parts) == 1:
        h, m, s = 0, int(parts[0]), 0
    else:
        return 0
    return days * 24 + h + m / 60 + s / 3600

def parse_gres(gres_str):
    """Parse GRES string like gpu:a100:4(S:0-1,3),gpu:l40s:2 into {type: max_count}."""
    gpus = {}
    if not gres_str or gres_str.strip() in ("(null)", ""):
        return gpus
    for entry in gres_str.split(","):
        entry = entry.strip()
        # Remove socket affinity like (S:0-1,3) -- but careful with commas in split above
        entry = re.sub(r"\(S:[^)]*\)", "", entry)
        parts = entry.split(":")
        if len(parts) >= 2 and parts[0] == "gpu":
            if len(parts) >= 3:
                gpu_type = parts[1]
                try:
                    count = int(parts[2])
                except ValueError:
                    count = 1
            else:
                gpu_type = "generic"
                try:
                    count = int(parts[1])
                except ValueError:
                    gpu_type = parts[1]
                    count = 1
            gpus[gpu_type] = max(gpus.get(gpu_type, 0), count)
    return gpus

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    fields = line.split("|")
    if len(fields) < 6:
        continue

    part_name = fields[0].strip().rstrip("*")
    time_limit = fields[1].strip()
    cpus = fields[2].strip().rstrip("+")
    mem_mb = fields[3].strip().rstrip("+")
    gres = fields[4].strip()
    node_count = fields[5].strip()

    p = partitions[part_name]

    # Time limit (partition-level, same across rows)
    tl_hours = parse_time_limit(time_limit)
    if tl_hours > p["time_limit_hours"]:
        p["time_limit_hours"] = tl_hours

    # Max CPUs per node (take MAX across rows)
    try:
        c = int(cpus)
        if c > p["max_cpus_per_node"]:
            p["max_cpus_per_node"] = c
    except ValueError:
        pass

    # Max memory (take MAX across rows)
    try:
        m = int(mem_mb)
        if m > p["max_mem_mb"]:
            p["max_mem_mb"] = m
            p["max_mem_gb"] = int(m / 1024)
    except ValueError:
        pass

    # Node count (SUM across rows)
    try:
        p["total_nodes"] += int(node_count)
    except ValueError:
        pass

    # GPU types
    gpu_info = parse_gres(gres)
    if gpu_info:
        p["has_gpu_nodes"] = True
        for gtype, gcount in gpu_info.items():
            if gtype not in p["gpu_types"]:
                p["gpu_types"][gtype] = {"max_per_node": gcount}
            else:
                p["gpu_types"][gtype]["max_per_node"] = max(
                    p["gpu_types"][gtype]["max_per_node"], gcount
                )
            if gcount > p["max_gpus_per_node"]:
                p["max_gpus_per_node"] = gcount
    else:
        if gres.strip() in ("(null)", ""):
            p["has_cpu_only_nodes"] = True

# Round time limits to reasonable precision
for pname, pdata in partitions.items():
    tl = pdata["time_limit_hours"]
    if tl == int(tl):
        pdata["time_limit_hours"] = int(tl)
    else:
        pdata["time_limit_hours"] = round(tl, 2)

output = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "generated_by": "slurm-mcp setup.sh",
    "partitions": dict(partitions),
}
json.dump(output, sys.stdout, indent=2)
print()
' <<< "$sinfo_output" > "$outfile" 2>/dev/null

    if [ $? -ne 0 ] || [ ! -s "$outfile" ]; then
        echo "  WARNING: Failed to generate partition limits profile."
        rm -f "$outfile"
        return 1
    fi

    # Verify valid JSON
    if ! "$PYTHON_PATH" -c "import json; json.load(open('$outfile'))" 2>/dev/null; then
        echo "  WARNING: Generated partition limits file is not valid JSON."
        rm -f "$outfile"
        return 1
    fi

    # Create/update symlink
    ln -sf "$outfile" "$symlink"

    local n_partitions
    n_partitions="$("$PYTHON_PATH" -c "import json; print(len(json.load(open('$outfile'))['partitions']))" 2>/dev/null || echo "?")"
    echo "  Generated partition limits for $n_partitions partitions: $outfile"

    PARTITION_LIMITS_FILE="$symlink"
    return 0
}

echo "Generating partition resource limits profile..."
PARTITION_LIMITS_FILE=""
generate_partition_limits
echo ""

# ── Remaining defaults ──
read -rp "Log directory [$DETECTED_LOG_DIR]: " LOG_DIR
LOG_DIR="${LOG_DIR:-$DETECTED_LOG_DIR}"

read -rp "Default memory [64G]: " DEFAULT_MEM
DEFAULT_MEM="${DEFAULT_MEM:-64G}"

read -rp "Default wall time [04:00:00]: " DEFAULT_TIME
DEFAULT_TIME="${DEFAULT_TIME:-04:00:00}"

read -rp "Default CPUs per task [8]: " DEFAULT_CPUS
DEFAULT_CPUS="${DEFAULT_CPUS:-8}"

# ── Validate defaults against partition limits ──
SLURM_MAX_CPUS="64"
SLURM_MAX_MEM_GB="256"
SLURM_MAX_TIME_HOURS="168"
SLURM_MAX_GPUS="4"

if [ -n "$PARTITION_LIMITS_FILE" ] && [ -f "$PARTITION_LIMITS_FILE" ]; then
    # Read the selected partition's limits from the JSON file
    PART_MAX_CPUS="$("$PYTHON_PATH" -c "
import json, sys
d = json.load(open('$PARTITION_LIMITS_FILE'))
p = d.get('partitions', {}).get('$SLURM_PARTITION', {})
print(p.get('max_cpus_per_node', ''))
" 2>/dev/null || echo "")"

    PART_MAX_MEM_GB="$("$PYTHON_PATH" -c "
import json, sys
d = json.load(open('$PARTITION_LIMITS_FILE'))
p = d.get('partitions', {}).get('$SLURM_PARTITION', {})
print(p.get('max_mem_gb', ''))
" 2>/dev/null || echo "")"

    PART_MAX_TIME="$("$PYTHON_PATH" -c "
import json, sys
d = json.load(open('$PARTITION_LIMITS_FILE'))
p = d.get('partitions', {}).get('$SLURM_PARTITION', {})
print(p.get('time_limit_hours', ''))
" 2>/dev/null || echo "")"

    PART_MAX_GPUS="$("$PYTHON_PATH" -c "
import json, sys
d = json.load(open('$PARTITION_LIMITS_FILE'))
p = d.get('partitions', {}).get('$SLURM_PARTITION', {})
print(p.get('max_gpus_per_node', ''))
" 2>/dev/null || echo "")"

    if [ -n "$PART_MAX_CPUS" ] && [ "$PART_MAX_CPUS" != "0" ]; then
        # Set dynamic max values from partition profile
        SLURM_MAX_CPUS="$PART_MAX_CPUS"
        SLURM_MAX_MEM_GB="${PART_MAX_MEM_GB:-256}"
        SLURM_MAX_TIME_HOURS="${PART_MAX_TIME:-168}"
        SLURM_MAX_GPUS="${PART_MAX_GPUS:-4}"

        echo "Partition '$SLURM_PARTITION' resource limits:"
        echo "  Max CPUs/node:  $SLURM_MAX_CPUS"
        echo "  Max memory:     ${SLURM_MAX_MEM_GB}G"
        echo "  Max wall time:  ${SLURM_MAX_TIME_HOURS}h"
        echo "  Max GPUs/node:  $SLURM_MAX_GPUS"
        echo ""

        # Validate user-entered defaults against partition limits
        NEEDS_ADJUST=false
        SUGGESTED_CPUS="$DEFAULT_CPUS"
        SUGGESTED_MEM="$DEFAULT_MEM"
        SUGGESTED_TIME="$DEFAULT_TIME"

        # Check CPUs
        if [ "$DEFAULT_CPUS" -gt "$SLURM_MAX_CPUS" ] 2>/dev/null; then
            # Suggest min(8, partition_max)
            if [ "$SLURM_MAX_CPUS" -lt 8 ]; then
                SUGGESTED_CPUS="$SLURM_MAX_CPUS"
            else
                SUGGESTED_CPUS="8"
            fi
            echo "  WARNING: Default CPUs ($DEFAULT_CPUS) exceeds partition max ($SLURM_MAX_CPUS)."
            echo "           Suggested: $SUGGESTED_CPUS"
            NEEDS_ADJUST=true
        fi

        # Check memory — parse user input to GB for comparison
        USER_MEM_GB="$("$PYTHON_PATH" -c "
mem = '$DEFAULT_MEM'.strip().upper()
if mem.endswith('T'): print(int(float(mem[:-1]) * 1024))
elif mem.endswith('G'): print(int(float(mem[:-1])))
elif mem.endswith('M'): print(int(float(mem[:-1]) / 1024))
else: print(int(float(mem) / 1024))
" 2>/dev/null || echo "0")"

        if [ "$USER_MEM_GB" -gt "$SLURM_MAX_MEM_GB" ] 2>/dev/null; then
            # Suggest ~25% of partition max
            SUGGESTED_MEM_GB=$(( SLURM_MAX_MEM_GB / 4 ))
            SUGGESTED_MEM="${SUGGESTED_MEM_GB}G"
            echo "  WARNING: Default memory (${DEFAULT_MEM} = ${USER_MEM_GB}G) exceeds partition max (${SLURM_MAX_MEM_GB}G)."
            echo "           Suggested: $SUGGESTED_MEM"
            NEEDS_ADJUST=true
        fi

        # Check time — parse user input to hours for comparison
        USER_TIME_HOURS="$("$PYTHON_PATH" -c "
import sys
t = '$DEFAULT_TIME'.strip()
days = 0
if '-' in t:
    dp, t = t.split('-', 1)
    days = int(dp)
parts = t.split(':')
if len(parts) == 3: h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
elif len(parts) == 2: h, m, s = 0, int(parts[0]), int(parts[1])
else: h, m, s = 0, int(parts[0]), 0
total = days * 24 + h + m / 60 + s / 3600
print(int(total) if total == int(total) else round(total, 2))
" 2>/dev/null || echo "0")"

        SLURM_MAX_TIME_INT="${SLURM_MAX_TIME_HOURS%.*}"
        USER_TIME_INT="${USER_TIME_HOURS%.*}"
        if [ "$USER_TIME_INT" -gt "$SLURM_MAX_TIME_INT" ] 2>/dev/null; then
            # Suggest min(4, partition_max) hours
            if [ "$SLURM_MAX_TIME_INT" -lt 4 ]; then
                SUGGESTED_TIME_H="$SLURM_MAX_TIME_INT"
            else
                SUGGESTED_TIME_H="4"
            fi
            SUGGESTED_TIME="$(printf '%02d:00:00' "$SUGGESTED_TIME_H")"
            echo "  WARNING: Default time (${DEFAULT_TIME} = ${USER_TIME_HOURS}h) exceeds partition max (${SLURM_MAX_TIME_HOURS}h)."
            echo "           Suggested: $SUGGESTED_TIME"
            NEEDS_ADJUST=true
        fi

        if [ "$NEEDS_ADJUST" = true ]; then
            echo ""
            read -rp "  Auto-adjust defaults to suggested values? [Y/n]: " ADJUST_CHOICE
            if [[ ! "${ADJUST_CHOICE,,}" =~ ^n ]]; then
                DEFAULT_CPUS="$SUGGESTED_CPUS"
                DEFAULT_MEM="$SUGGESTED_MEM"
                DEFAULT_TIME="$SUGGESTED_TIME"
                echo "  Adjusted: CPUs=$DEFAULT_CPUS, memory=$DEFAULT_MEM, time=$DEFAULT_TIME"
            else
                echo "  Keeping user-entered defaults (may cause sbatch rejections)."
            fi
        fi
    else
        echo "  Partition '$SLURM_PARTITION' not found in limits profile; using global defaults."
    fi
fi

echo ""

# ── 4. Install the package ──────────────────────────────────────────────

echo "Installing slurm-mcp..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$PYTHON_PATH" -m pip install -e "$SCRIPT_DIR" --quiet
echo "  Installed."
echo ""

# Verify import works
if ! "$PYTHON_PATH" -c "import slurm_mcp" 2>/dev/null; then
    echo "ERROR: 'import slurm_mcp' failed. Check your Python environment."
    exit 1
fi

# ── 5. Write ~/.claude.json ─────────────────────────────────────────────

# Create log directory
mkdir -p "$LOG_DIR"

# Build the MCP server config block
MCP_BLOCK=$(cat <<EOF
{
  "type": "stdio",
  "command": "$PYTHON_PATH",
  "args": ["-m", "slurm_mcp.server"],
  "env": {
    "SLURM_DEFAULT_ACCOUNT": "$SLURM_ACCOUNT",
    "SLURM_DEFAULT_PARTITION": "$SLURM_PARTITION",
    "SLURM_DEFAULT_NODES": "1",
    "SLURM_DEFAULT_NTASKS_PER_NODE": "1",
    "SLURM_DEFAULT_CPUS_PER_TASK": "$DEFAULT_CPUS",
    "SLURM_DEFAULT_MEM": "$DEFAULT_MEM",
    "SLURM_DEFAULT_TIME": "$DEFAULT_TIME",
    "SLURM_LOG_DIR": "$LOG_DIR",
    "SLURM_AUDIT_LOG": "$LOG_DIR/audit.jsonl",
    "SLURM_MAX_CPUS": "$SLURM_MAX_CPUS",
    "SLURM_MAX_MEM_GB": "$SLURM_MAX_MEM_GB",
    "SLURM_MAX_TIME_HOURS": "$SLURM_MAX_TIME_HOURS",
    "SLURM_MAX_GPUS": "$SLURM_MAX_GPUS",
    "SLURM_PARTITION_LIMITS": "${PARTITION_LIMITS_FILE:-}"
  }
}
EOF
)

# If ~/.claude.json exists, merge; otherwise create from scratch
if [ -f "$CLAUDE_JSON" ]; then
    # Check if jq is available for safe JSON merging
    if command -v jq &>/dev/null; then
        # Back up existing config
        cp "$CLAUDE_JSON" "${CLAUDE_JSON}.bak.$(date +%Y%m%d_%H%M%S)"
        echo "  Backed up existing $CLAUDE_JSON"

        # Merge the slurm server into mcpServers
        jq --argjson slurm "$MCP_BLOCK" '.mcpServers.slurm = $slurm' "$CLAUDE_JSON" > "${CLAUDE_JSON}.tmp" \
            && mv "${CLAUDE_JSON}.tmp" "$CLAUDE_JSON"
        echo "  Updated $CLAUDE_JSON (merged slurm into mcpServers)"
    else
        echo ""
        echo "WARNING: jq not found. Cannot safely merge into existing $CLAUDE_JSON."
        echo ""
        echo "Add this manually to the \"mcpServers\" section of $CLAUDE_JSON:"
        echo ""
        echo "  \"slurm\": $MCP_BLOCK"
        echo ""
        echo "Or install jq and re-run this script."
    fi
else
    # Create new file
    cat > "$CLAUDE_JSON" <<OUTER
{
  "mcpServers": {
    "slurm": $MCP_BLOCK
  }
}
OUTER
    echo "  Created $CLAUDE_JSON"
fi

# ── 6. Done ─────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code (exit and relaunch)"
echo "  2. Ask Claude: \"List my running SLURM jobs\""
echo ""
echo "Config:  $CLAUDE_JSON"
echo "Logs:    $LOG_DIR"
echo ""
