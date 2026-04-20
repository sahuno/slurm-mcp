# Script Templates — #SBATCH Generation

Every generated script must include:

1. An accurate `#SBATCH` header block from the matching template.
2. The **standard logging preamble** — session header with timestamps.
3. **Input validation** appropriate to the command (files exist, paths writable).
4. The actual command.
5. **Exit code capture + completion marker** on the very last line.

Substitute `{placeholders}` from the task spec. Never leave a placeholder
in the final script.

---

## Base Template (single-node, CPU-only)

```bash
#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --account={account}
#SBATCH --partition={partition}
#SBATCH --nodes=1
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem_gb}G
#SBATCH --time={time_formatted}
#SBATCH --output={log_dir}/%x_%j.out
#SBATCH --error={log_dir}/%x_%j.err

set -euo pipefail

# ── Logging ──
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== JOB START ==="
log "Job ID:     $SLURM_JOB_ID"
log "Job name:   $SLURM_JOB_NAME"
log "Node:       $(hostname)"
log "CPUs:       $SLURM_CPUS_PER_TASK"
log "Memory:     ${SLURM_MEM_PER_NODE:-${SLURM_MEM_PER_CPU:-unknown}} MB"
log "Partition:  $SLURM_JOB_PARTITION"
log "Work dir:   $(pwd)"

# ── Input validation ──
# {generated from the command's expected inputs — see patterns below}

# ── Main command ──
{command}

# ── Exit handling ──
EXIT_CODE=$?
log "Exit code: $EXIT_CODE"
log "=== DONE: {name} completed ==="
exit $EXIT_CODE
```

**Time format.** SLURM accepts `HH:MM:SS` or `D-HH:MM:SS`. Convert:
- `2.0h` → `02:00:00`
- `30min` → `00:30:00`
- `48h` → `2-00:00:00`
- `7d`  → `7-00:00:00`

---

## Container Template (Singularity / Apptainer)

Add the `singularity exec --bind` wrapper around the command. Bind mounts
must cover **every** input and output directory the command touches.

```bash
# ── Container config ──
IMAGE={image_path}
BINDS="/data1/greenbab,/scratch,$(pwd)"   # add every I/O root

# ── Main command ──
singularity exec --bind "$BINDS" "$IMAGE" \
    {command}
```

Rules:
- Never use `:latest` tags — pin the exact image version in `{image_path}`.
- `BINDS` must include both the *input* parent directory and the *output*
  parent directory. Compute nodes do not have the same mounts as login nodes.
- If the tool needs GPU + container, add `--nv` to `singularity exec`.
- If a Snakemake rule uses the `singularity:` directive, the shell block
  does NOT need `singularity exec` — that's for standalone scripts only.

---

## GPU Template

```bash
#SBATCH --gres=gpu:{gpu_type}:{gpu_count}   # or just gpu:{count} if no type pref

# ... rest of standard #SBATCH block ...

# ── GPU diagnostic at job start ──
log "=== GPU DIAGNOSTIC ==="
nvidia-smi
log "CUDA_VISIBLE_DEVICES: ${CUDA_VISIBLE_DEVICES:-unset}"
log "====================="

# ── Main command ──
{command}
```

**Anti-pattern to flag**: Setting `--mem={total}G` on a GPU job can conflict
on some clusters. If the GPU partition complains, try `--mem-per-cpu=` or
omit `--mem` (lets SLURM allocate per the partition default). See
`~/.claude/CLAUDE.md` §6 GPU note.

---

## Job Array Template

For N independent invocations of the same command on different inputs.

```bash
#SBATCH --array=1-{N}%{max_concurrent}     # %K caps simultaneous runs

# ... rest of standard #SBATCH block ...
# NOTE: --time and --mem apply PER ARRAY ELEMENT, not to the whole array.

# ── Array element setup ──
SAMPLE_LIST={path_to_sample_list}          # one sample per line
SAMPLE=$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$SAMPLE_LIST")

log "Array task ID: $SLURM_ARRAY_TASK_ID"
log "Sample:        $SAMPLE"

# Per-sample output dir
OUT={output_root}/${SAMPLE}
mkdir -p "$OUT"

# ── Main command (parameterized by $SAMPLE) ──
{command_using_$SAMPLE_and_$OUT}
```

When to use `%K` throttling:
- DB-heavy tools (Kraken2 full DB): cap at number of nodes with enough RAM.
- I/O-heavy tools on shared storage: cap to avoid swamping the filesystem.
- No reason to throttle for ordinary CPU-bound work.

---

## Multi-Node Template

Only for commands that can actually use multiple nodes (MPI, Spark,
distributed deep learning). Most bioinformatics tools are single-node.

```bash
#SBATCH --nodes={num_nodes}
#SBATCH --ntasks-per-node={tasks_per_node}
#SBATCH --cpus-per-task={cpus_per_task}

# ... rest of standard #SBATCH block ...

# ── Main command (wrap in srun for multi-node) ──
srun {command}
```

Confirm with the user that the command is genuinely distributed before
using this template — an MPI binary is not the same as a tool that
"supports parallelism."

---

## Input Validation Patterns

Insert relevant checks from this list in the `# ── Input validation ──`
block:

```bash
# File must exist
[[ -f "$INPUT" ]] || { log "ERROR: Input not found: $INPUT"; exit 1; }

# Directory must exist
[[ -d "$REF_DIR" ]] || { log "ERROR: Ref dir not found: $REF_DIR"; exit 1; }

# File must be non-empty
[[ -s "$INPUT" ]] || { log "ERROR: Input is empty: $INPUT"; exit 1; }

# Output parent must be writable
OUT_PARENT=$(dirname "$OUTPUT")
[[ -w "$OUT_PARENT" ]] || { log "ERROR: Cannot write to: $OUT_PARENT"; exit 1; }
mkdir -p "$OUT_PARENT"

# BAM index must exist alongside BAM
[[ -f "${BAM}.bai" || -f "${BAM%.bam}.bai" || -f "${BAM}.csi" ]] || { log "ERROR: No index for $BAM"; exit 1; }

# Reference FASTA index
[[ -f "${REF}.fai" ]] || { log "ERROR: Missing FASTA index: ${REF}.fai"; exit 1; }

# Container image exists
[[ -f "$IMAGE" ]] || { log "ERROR: Container image not found: $IMAGE"; exit 1; }
```

Only include checks relevant to the task — don't bloat every script.

---

## Log Directory Handling

`{log_dir}` in the header should be:
- An **absolute path** (SLURM resolves it from the submission directory
  otherwise, which is fragile).
- Created **before** submission, or the `#SBATCH --output`/`--error`
  write will fail silently.

Typical:
```
{log_dir} = {project_root}/results/{date}_{genome}_{description}/logs
```

The skill should `mkdir -p "{log_dir}"` before calling `slurm_submit_job`.

---

## Pre-Submission Validation Checklist

Before passing the script to `slurm_submit_job(dry_run=True)`:

- [ ] All `{placeholders}` replaced with concrete values
- [ ] `{time_formatted}` is `HH:MM:SS` or `D-HH:MM:SS`, not `2h` or `1d`
- [ ] `{mem_gb}G` has the `G` suffix
- [ ] Log directory exists
- [ ] Input validation block matches the command's actual inputs
- [ ] Last line is `exit $EXIT_CODE`, not just the command
- [ ] Completion marker `=== DONE: ... ===` is present
- [ ] For container jobs: `BINDS` covers both input and output paths
- [ ] For GPU jobs: `--gres=gpu:N` is set and `nvidia-smi` diagnostic is included
- [ ] For arrays: `--array=1-N` matches the sample list length
