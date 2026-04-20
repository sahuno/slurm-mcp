# SLURM Skill Design — Brainstorm

**Date**: 2026-03-03
**Status**: Draft — for review before building with skill-creator
**Context**: Based on lessons learned building slurm-mcp (MCP tools, partition-
aware validation, job resource profiles Q1-Q4).

---

## Update — 2026-04-19

The error-code interpreter (`slurm_diagnose_job`, Level 1 + Level 2) has
shipped. That changes the skill's scope:

- **§3.3 Failure Diagnostician** and **§7 Failure Pattern Database** are now
  *superseded by the MCP tool*. The pattern matching (state → explanation,
  signal → cause, MaxRSS vs requested → OOM verdict) lives in `slurm_cli.py`
  as `JOB_STATE_INFO`, `JOB_REASON_INFO`, `SIGNAL_INFO`, and the
  `diagnose_job()` function. The skill should **call** `slurm_diagnose_job`,
  not re-encode that knowledge.
- The skill's real unfilled gap is **pre-submission intelligence** — nothing
  today classifies a task, picks a partition, or generates a `#SBATCH`
  header. That's where this skill earns its keep.
- **§3.4 Partition Navigator** folds into the Advisor. "Pick a partition" is
  a step inside "submit this job," not a separate mode.

### Revised scope (phased)

1. **Phase 1 — Resource Advisor + Script Generator.** Classify task
   (Q1-Q4) → read `slurm://partition-limits` → pick tightest-fit partition →
   generate `#SBATCH` script → validate with `dry_run` / `test_only` before
   real submit. Ship this first.
2. **Phase 2 — Failure-orchestration wrapper (thin).** Call
   `slurm_diagnose_job`, read the verdict, propose a bumped-resource
   resubmit script, confirm with user. Only build after Phase 1 gets real
   use — the tool may already be enough on its own.
3. **Deferred — dedicated Partition Navigator, Level 3 pattern advisor.**
   Revisit if Phase 1/2 reveal a gap.

### Key risk

The Q1-Q4 task→resource mapping (§4) becomes a second source of domain
knowledge alongside CLAUDE.md and `docs/job_resource_profiles.md`. **Drift
risk**: the skill says 50G for STAR, the profiles doc says 40G, reality
moves on, the two disagree. **Mitigation**: keep the mapping in one place —
either the skill prompt reads `docs/job_resource_profiles.md` at activation,
or we treat the skill as the authoritative copy and delete the duplicate
from the profiles doc. Decide before building.

---

## 1. The Core Insight

**MCP tools** = low-level operations ("submit this job", "check that status")
**Skill** = intelligence layer ("given what you're trying to do, here's the
right way to submit it")

The MCP tools are the hands. The skill is the brain. Without the skill, Claude
has to rediscover partition limits, job profiles, and troubleshooting knowledge
every conversation. With the skill, it's loaded and ready when triggered.

---

## 2. What We've Learned That the Skill Should Encode

### 2a. Job classification is actionable
The Q1-Q4 resource profiles (see `docs/job_resource_profiles.md`) map task
descriptions to concrete resource recommendations. If Claude can classify a
user's task into a quadrant, it can recommend specific resource ranges *before*
the user ever types a number.

### 2b. Partition selection is a matching problem
We built partition-limits (`~/.slurm-mcp/partition_limits_latest.json`) for
validation. But a skill can use it *proactively* — read
`slurm://partition-limits`, match job requirements against available partitions,
and recommend the best fit. Not just "is this valid?" but "what should you use?"

### 2c. Most SLURM failures are preventable
From CLAUDE.md troubleshooting and our experience:
- Wrong partition for the job type
- Memory underestimated (OOM kill at hour 23)
- Time limit too short
- GPU requested on CPU-only partition
- `SLURM_MEM_PER_NODE` vs `SLURM_MEM_PER_CPU` conflict (Snakemake 9)
- Missing account, missing bind mounts in containers

A skill catches these *before* submission.

### 2d. The failure-diagnosis-fix loop is formulaic
Job fails → read logs → pattern match exit code/error → suggest fix →
resubmit. This is a decision tree, perfect for a skill.

### 2e. Run organization has rules
From CLAUDE.md: `results/{version}_{genome}_{subset}/` with derived `FIGDIR`,
`LOGDIR`. One config per run. The skill enforces this structure.

---

## 3. Skill Architecture: `/slurm`

One unified skill, multiple modes triggered by context.

### Mode 1: Resource Advisor
**Trigger context**: User describes a computational task.

```
User: "I need to run STAR alignment on 20 RNA-seq samples"

Skill thinks:
  → Classification: Q3 (High-Mem, Low-Time)
  → Per-sample: 8-16 CPUs, 40-50G RAM, 20-40 min
  → Strategy: Job array (--array=1-20), not 20 separate submissions
  → Read slurm://partition-limits
  → Best partition: shortest time limit with >=50G RAM
  → Anti-pattern: don't use 7-day partition for 40-min jobs
```

### Mode 2: Script Generator
**Trigger context**: User wants to submit a job or asks for a job script.

Produces:
- `#SBATCH` headers matched to task profile
- Proper logging (mirrors CLAUDE.md logging requirements)
- Input validation (files exist, paths writable)
- The actual command
- Exit code capture + completion marker
- Uses `slurm_submit_job` tool with `dry_run=true` first for validation

### Mode 3: Failure Diagnostician  **[SUPERSEDED — see Update 2026-04-19]**

**Status**: Replaced by the `slurm_diagnose_job` MCP tool. That tool already
runs state lookup, exit-code/signal parsing, and requested-vs-actual
resource comparison. The skill's role here shrinks to: call the tool, read
the verdict, and (Phase 2) propose a bumped-resource resubmit script.

Original design preserved below for historical context:

**Trigger context**: "Job failed", "OOM", "TIMEOUT", error messages, job IDs
mentioned with problems.

```
Skill:
  → slurm_job_status(job_id) — get state + exit code
  → slurm_job_logs(job_id) — read stderr
  → slurm_job_resources(job_id) — get MaxRSS vs requested
  → Pattern match against failure database (Section 7)
  → Generate fix + resubmit command
```

### Mode 4: Partition Navigator  **[FOLDED INTO MODE 1 — see Update 2026-04-19]**
**Trigger context**: "What partition should I use?", "Show me available
resources", choosing where to submit.

```
Skill:
  → Read slurm://partition-limits resource
  → Cross-reference with job quadrant
  → Rank: prefer tightest fit (don't waste big partitions on small jobs)
  → Show: table of matching partitions with headroom
```

---

## 4. Skill Content: Job Resource Knowledge Base

Compressed decision tree from `docs/job_resource_profiles.md`. The skill
embeds this as a lookup so Claude doesn't need to read the file every time.

```
IF task mentions {STAR, BWA, minimap2, HISAT2, bowtie2, alignment, align}
  → Q3: 8-16 CPU, 40-50G, 20-60 min, no GPU
  → Strategy: per-sample parallelism, job array if multi-sample

IF task mentions {assembly, hifiasm, flye, canu, verkko, spades}
  → Q4: 32-64 CPU, 200-500G, 12h-days, no GPU
  → Strategy: single large job, longest-time partition

IF task mentions {dorado, basecalling, basecall, pod5}
  → Q4: 4-8 CPU, 16-32G, 1-4 GPU, 4-24h
  → Strategy: GPU partition required, match model to chemistry

IF task mentions {BLAST, repeatmasker, phasing, VEP, annotation}
  → Q2: 1-2 CPU, 4-8G, 12-48h, no GPU
  → Strategy: long-time CPU partition, array if multi-query

IF task mentions {featureCounts, salmon, htseq, quantification}
  → Q3: 4-8 CPU, 16-32G, 5-30 min, no GPU
  → Strategy: per-sample, fast — cpushort-friendly

IF task mentions {DESeq2, edgeR, limma, DGE, differential expression}
  → Q3: 4-8 CPU, 8-32G, 5-30 min, no GPU
  → Strategy: single job, minimal resources

IF task mentions {CellRanger, STARsolo, scRNA, single-cell}
  → Q3/Q4 boundary: 16 CPU, 64-128G, 2-6h, no GPU
  → Strategy: single job per sample, then integration step

IF task mentions {Seurat, Scanpy, integration, harmony, scVI}
  → Q4: 8-16 CPU, 64-256G, 2-8h, maybe 1 GPU for scVI
  → Strategy: single job, high-memory node

IF task mentions {Kraken2, MetaPhlAn, metagenomic classification}
  → Q3: 8-16 CPU, 64-256G, 10-60 min, no GPU
  → Strategy: poster child of Q3 — huge RAM, fast runtime

IF task mentions {metaSPAdes, MEGAHIT, metagenome assembly}
  → Q4: 16-32 CPU, 256G-1T, 24-72h, no GPU
  → Strategy: largest-memory nodes only, may exceed most partitions

IF task mentions {AlphaFold, ESM, protein structure, folding}
  → Q4: 8-16 CPU, 64-256G, 1-4 GPU, 2-24h
  → Strategy: GPU partition with long time limit

IF task mentions {GROMACS, AMBER, NAMD, molecular dynamics, MD simulation}
  → Q4: 8-16 CPU, 32-64G, 1-4 GPU, days-weeks
  → Strategy: longest-time GPU partition

IF task mentions {modkit, pileup, methylation, bedMethyl}
  → Q2: 1-2 CPU, 4-8G, 2-8h, no GPU
  → Strategy: I/O-bound, long-time CPU partition

IF task mentions {samtools sort, mark duplicates, picard, BAM processing}
  → Q3: 4-8 CPU, 16-64G, 15-90 min, no GPU
  → Strategy: per-sample, fast

IF task mentions {GATK, HaplotypeCaller, variant calling, GenotypeGVCFs}
  → Q3 per-interval or Q4 joint: depends on mode
  → If per-sample/interval: 4-8 CPU, 16-32G, 30min-2h
  → If joint genotyping (100+ samples): 8-16 CPU, 64-256G, 12-48h

IF task mentions {download, prefetch, SRA, transfer, wget, curl}
  → Q2: 1 CPU, 2-4G, hours-days, no GPU
  → Strategy: network-bound, minimal compute

IF task mentions {BEAST, MrBayes, MCMC, phylogenetics, RAxML}
  → Q2: 1-2 CPU, 4-8G, days-weeks, no GPU
  → Strategy: longest-time partition available

IF task mentions {MACS2, MACS3, peak calling, ChIP-seq, ATAC-seq}
  → Q3: 2-4 CPU, 8-16G, 10-30 min, no GPU
  → Strategy: fast, cpushort-friendly

IF task mentions {Hi-C, HiC-Pro, Juicer, cooler, contact matrix}
  → Q4: 8-16 CPU, 64-256G, 8-24h, no GPU
  → Strategy: memory scales with resolution^2

IF task mentions {PLINK, SAIGE, REGENIE, GWAS, biobank}
  → Q4: 8-16 CPU, 64-256G, 4-24h, no GPU
  → Strategy: scales with cohort size
```

---

## 5. Skill Content: Partition Selection Logic

```
1. Read slurm://partition-limits
2. Filter: time_limit_hours >= job_time AND max_mem_gb >= job_mem
3. If GPU needed: filter has_gpu_nodes == true
4. Rank remaining by tightness of fit:
   - PREFER partition where job uses 25-75% of max resources
   - AVOID partitions where job uses <10% (wasteful)
   - AVOID partitions where job uses >90% (no headroom for spikes)
5. If multiple equal: prefer partition with more total_nodes (shorter queue)
6. Show the recommendation with reasoning
```

### Anti-patterns to flag
- Requesting a 7-day partition for a 30-min job
- Requesting GPU partition for CPU-only work
- Requesting 1 TB on a 503G-max partition
- Requesting 8h on a 2h-max partition
- Using the user's default partition when a better fit exists

---

## 6. Skill Content: Script Template

```bash
#!/bin/bash
#SBATCH --job-name={name}
#SBATCH --account={from_config}
#SBATCH --partition={recommended_partition}
#SBATCH --nodes=1
#SBATCH --cpus-per-task={from_profile}
#SBATCH --mem={from_profile}
#SBATCH --time={from_profile}
#SBATCH --output={log_dir}/%x_%j.out
#SBATCH --error={log_dir}/%x_%j.err

set -euo pipefail

# ── Logging ──
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === JOB START ==="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Job ID:    $SLURM_JOB_ID"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Job name:  $SLURM_JOB_NAME"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Node:      $(hostname)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] CPUs:      $SLURM_CPUS_PER_TASK"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Memory:    ${SLURM_MEM_PER_NODE:-unknown} MB"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Partition:  $SLURM_JOB_PARTITION"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Work dir:  $(pwd)"
echo ""

# ── Input validation ──
# {skill generates these based on the command's expected inputs}
# Example:
# [[ -f "$INPUT_BAM" ]] || { echo "ERROR: Input BAM not found: $INPUT_BAM"; exit 1; }

# ── Main command ──
{command}

# ── Exit handling ──
EXIT_CODE=$?
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Exit code: $EXIT_CODE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === DONE: {name} completed ==="
exit $EXIT_CODE
```

### Template variations
- **Container jobs**: Add `singularity exec --bind /data1/greenbab {image}` wrapper,
  verify bind paths cover all I/O directories
- **Job arrays**: Add `#SBATCH --array=1-N`, read sample from list file using
  `$SLURM_ARRAY_TASK_ID`
- **GPU jobs**: Add `#SBATCH --gres=gpu:{type}:{count}`, add `nvidia-smi`
  diagnostic at start
- **Multi-node jobs**: Add `#SBATCH --ntasks-per-node`, use `srun` for the
  command

---

## 7. Skill Content: Failure Pattern Database  **[SUPERSEDED — see Update 2026-04-19]**

**Status**: This database is now implemented in `slurm_cli.py` as
`JOB_STATE_INFO`, `JOB_REASON_INFO`, and `SIGNAL_INFO`, consumed by
`diagnose_job()`. The skill should call `slurm_diagnose_job` rather than
re-encode these patterns. Kept below as the historical source material that
informed the tool's lookup tables.



### Exit code patterns

```
EXIT 137 (SIGKILL / OOM)
  → Almost always out of memory.
  → Diagnostic: slurm_job_resources → compare MaxRSS vs requested mem.
  → Fix: increase --mem by 50-100%.
  → If tool has fixed memory floor (STAR index=32G, Kraken2 DB=64-256G),
    the floor is the minimum — can't go below it.
  → Resubmit command with increased memory.

STATE=TIMEOUT, EXIT 0
  → Job was killed at time limit.
  → Diagnostic: sacct elapsed vs limit.
  → If elapsed ≈ limit: job was still running, needs more time.
  → If elapsed << limit: job was hung (different problem — check logs).
  → Fix: increase --time. If close to partition max, switch partition.

EXIT 1 + "No space left on device"
  → /tmp or local scratch full.
  → Fix: set TMPDIR to a scratch directory with more space.
  → Or add #SBATCH --tmp={size} to reserve local disk.

EXIT 1 + "module: command not found"
  → Compute node has different shell or no module system.
  → Fix: use full path to binary, or use container instead.

EXIT 1 + "Permission denied" on output path
  → Container bind mount missing, or filesystem not mounted on
    compute node.
  → Fix: verify --bind covers all I/O paths.

EXIT 1 + "CUDA error" or "no CUDA-capable device"
  → Job landed on node without GPU, or GPU not allocated.
  → Fix: verify --gres=gpu:N is set. Check partition has_gpu_nodes.

SLURM_MEM_PER_NODE vs SLURM_MEM_PER_CPU conflict
  → Snakemake 9 + SLURM executor specific.
  → Fix: use mem_mb_per_cpu in resources, set mem_mb: 0 in profile
    default-resources, unset SLURM_MEM_PER_NODE in coordinator script.

STATE=FAILED, EXIT 0 (contradictory)
  → Often: job exceeded memory limit but the process exited 0 before
    SLURM could record the OOM. Check MaxRSS.

STATE=NODE_FAIL
  → Node crashed during execution. Not your fault.
  → Fix: resubmit. Add --requeue to auto-requeue on node failure.
```

### Log pattern matching

```
"Killed"                    → OOM (kernel OOM killer)
"Bus error"                 → Likely memory corruption or NFS issue
"Segmentation fault"        → Bug in tool, or corrupted input
"java.lang.OutOfMemoryError"→ JVM heap too small (GATK/Picard: -Xmx)
"bad_alloc"                 → C++ OOM
"MemoryError"               → Python OOM
"cannot allocate memory"    → System-level OOM
"exceeded memory limit"     → SLURM cgroup enforcement
```

---

## 8. Skill Content: Principles

```
1. VALIDATE BEFORE SUBMITTING
   Always dry_run or test_only first. Catch problems before they enter
   the queue and waste time.

2. MATCH PARTITION TO JOB, NOT JOB TO PARTITION
   Don't default to the biggest partition. Use the tightest fit.
   A 30-min STAR alignment on a 7-day partition wastes scheduling
   priority and may queue longer.

3. DON'T OVER-REQUEST RESOURCES
   2x safety margin, not 10x. Over-requesting wastes allocation and
   may increase queue wait time. Use the Q1-Q4 profiles as baselines.

4. JOB ARRAYS > MANY INDIVIDUAL SUBMISSIONS
   If running the same command on N samples, use --array=1-N.
   Cheaper for the scheduler, easier to manage, one job ID.

5. SEPARATE CODE FROM RESULTS
   Pipeline code is reusable. Run outputs go in:
   results/{version}_{genome}_{subset}/
   One config per run. Derive FIGDIR and LOGDIR from output_dir.

6. EVERY JOB MUST LOG
   Start time, hostname, resources, command, exit code, completion
   marker. If the completion marker is missing, the job didn't finish.

7. READ THE ERROR BEFORE GUESSING THE FIX
   Check slurm_job_logs and slurm_job_resources before suggesting
   changes. The error message tells you what happened.

8. USE PARTITION LIMITS, NOT HARDCODED NUMBERS
   Read slurm://partition-limits for actual constraints. Cluster
   configurations change — hardcoded values go stale.

9. GPU JOBS ARE SPECIAL
   Only request GPUs when the tool actually uses them (dorado,
   AlphaFold, GROMACS, deep learning). GPUs are scarce — never
   request them for CPU-only work.

10. CONTAINERS NEED BIND MOUNTS
    Compute nodes may not have the same mounts as login nodes.
    Every input and output path must be explicitly bound.
```

---

## 9. Available MCP Tools (what the skill can call)

The skill has access to these slurm-mcp tools:

| Tool | Use in skill |
|------|-------------|
| `slurm_submit_job` | Submit with dry_run=true for validation, then real submit |
| `slurm_job_status` | Check state + exit code for diagnosis |
| `slurm_job_logs` | Read stdout/stderr for failure pattern matching |
| `slurm_job_resources` | Get MaxRSS/elapsed for OOM/timeout diagnosis |
| `slurm_list_jobs` | Show user's current jobs for context |
| `slurm_cancel_job` | Cancel and resubmit with fixed resources |
| `slurm_queue_info` | Real-time partition availability |
| `slurm_node_info` | Find nodes with enough free resources right now |
| `slurm_submit_batch` | Multi-script submission with chaining |

And these MCP resources:

| Resource | Use in skill |
|----------|-------------|
| `slurm://partitions` | Real-time partition state |
| `slurm://partition-limits` | Per-partition max CPUs, memory, time, GPUs |

---

## 10. Skill Trigger Design

### Option A: One unified `/slurm` skill
**Trigger description**: "Use when the user wants to submit SLURM jobs, choose
resources or partitions for computational tasks, diagnose failed jobs, or
manage cluster workloads."

Mode detected from user message context:
- Mentions a tool/pipeline + "run" / "submit" → Resource Advisor + Script Gen
- Mentions a job ID + "failed" / "error" / "OOM" → Failure Diagnostician
- Asks about partitions / resources / "where should I" → Partition Navigator

### Option B: Split skills
- `/slurm-submit` — Resource Advisor + Script Generator
- `/slurm-diagnose` — Failure Diagnostician
- `/slurm-partitions` — Partition Navigator

### Option C: One skill + internal routing (recommended)
Single `/slurm` trigger, broad activation. Skill prompt includes routing
instructions:

```
Determine which mode applies based on the user's message:
- If they describe a task to run → ADVISOR mode
- If they mention a failed job → DIAGNOSE mode
- If they ask about partitions/resources → NAVIGATE mode
- If they want a script written → GENERATE mode
Multiple modes can activate simultaneously.
```

**Recommendation**: Option C. One trigger, internal routing. Users don't need
to remember multiple slash commands, and the skill can combine modes (e.g.,
diagnose a failure AND generate a fixed resubmission script).

---

## 11. Open Questions

1. **Skill size**: The full content (knowledge base + failure DB + templates +
   principles) may be large. Should we compress aggressively, or is the skill
   prompt budget generous enough?

2. **Dynamic vs static knowledge**: The Q1-Q4 profiles are relatively stable,
   but partition limits change when the cluster is reconfigured. Should the
   skill always read `slurm://partition-limits` at runtime, or embed a
   snapshot?
   → Recommendation: always read at runtime. The MCP resource exists for
   this purpose.

3. **Scope boundary**: Where does the skill end and CLAUDE.md begin? The skill
   should handle SLURM-specific intelligence. CLAUDE.md handles broader
   conventions (logging, naming, figure formats).

4. **Testing**: How do we evaluate the skill? Possible eval cases:
   - "Run STAR on 20 samples" → expect Q3 classification, job array, ~50G mem
   - "My job 12345 got OOM killed" → expect log check, MaxRSS comparison, mem
     increase suggestion
   - "What partition for a week-long BEAST run?" → expect longest-time
     partition recommendation
   - "Submit dorado basecalling" → expect GPU partition, correct model flag

5. **Iteration**: Start with Resource Advisor + Script Generator (proactive),
   then add Failure Diagnostician (reactive) once the proactive side is
   validated?

---

## 12. Next Steps  *(revised 2026-04-19)*

**Decided:**
- Trigger approach: **Option C** — one `/slurm` skill, internal routing.
- Scope: **phased**. Phase 1 (Advisor + Script Generator) first; Phase 2
  (failure-orchestration wrapper around `slurm_diagnose_job`) only if
  usage reveals a gap.

**Open decisions (needed before building):**
- [ ] Confirm skill should always read `slurm://partition-limits` at
      runtime (no embedded snapshot).

**Build tasks:**
- [ ] Use skill-creator to scaffold `/slurm` with Phase 1 content only.
- [ ] Write Phase 1 eval cases (see §11.4 — STAR on 20 samples, dorado
      basecalling, week-long BEAST run).
- [ ] Use on real jobs for 1-2 weeks before deciding on Phase 2.
- [ ] Only then: decide whether Phase 2 adds value over calling
      `slurm_diagnose_job` directly.
