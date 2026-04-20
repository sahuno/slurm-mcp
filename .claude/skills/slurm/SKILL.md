---
name: slurm
description: |
  Pre-submission intelligence for SLURM HPC jobs. Classifies a computational
  task into a resource profile (Q1-Q4), matches it against available partitions
  via the slurm-mcp MCP, and generates a validated #SBATCH script before the
  job enters the queue. Use this skill proactively whenever the user asks to:
  submit a SLURM job, run a bioinformatics tool on the cluster (STAR, dorado,
  hifiasm, Kraken2, AlphaFold, etc.), choose a partition for a task, write or
  fix an sbatch script, estimate resources for a computational task, or
  convert "run X on the cluster" into a concrete submission. Also trigger when
  the user mentions #SBATCH, sbatch, srun, --partition, --mem, --time,
  --gres=gpu, job array, or names a partition from their cluster.
  Do NOT trigger for: diagnosing an already-failed job (use slurm_diagnose_job
  tool directly), listing or cancelling existing jobs, or Snakemake/Nextflow
  pipelines (those have their own skills — this skill is for ad-hoc single
  jobs or job arrays, not multi-rule DAGs).
version: 0.1.0
author: Samuel Ahuno (ekwame001@gmail.com)
---

# SLURM Pre-Submission Skill

Turn "run X on the cluster" into a right-sized, validated SLURM submission.
The skill sits on top of the [slurm-mcp](https://github.com/sahuno/slurm-mcp)
MCP tools and resources — it does the *thinking* (classification, partition
matching, script assembly); the MCP does the *acting* (submit, validate,
query).

## When to Use This Skill

**Use when** the user:
- Describes a computational task and wants it running on SLURM
- Asks "what resources should I request for X?"
- Asks "what partition should I use?"
- Wants an `#SBATCH` script generated for a tool
- Wants to submit a job array over N samples

**Don't use when:**
- A job already failed → call `slurm_diagnose_job` directly instead
- The task is a multi-rule pipeline → use the `snakemake` skill instead
- The user just wants to list/cancel jobs → call the MCP tools directly

## Core Workflow

The skill runs four steps in order. Skip a step only if the user has already
specified its output.

### Step 1 — Classify the task (Q1-Q4)

Match the task to one of the four quadrants on the Time × Memory plane.
See `references/resource_profiles.md` for the full tool-to-quadrant lookup.

| Quadrant | Profile | Example tools |
|----------|---------|---------------|
| Q1 | Low-mem, low-time | quick utilities, small conversions |
| Q2 | Low-mem, high-time | BLAST, BEAST, modkit pileup, phasing |
| Q3 | High-mem, low-time | STAR, Kraken2, featureCounts, MACS2 |
| Q4 | High-mem, high-time | assembly, dorado, AlphaFold, joint genotyping |

The quadrant dictates the resource *shape* (CPUs, memory, runtime order of
magnitude, GPU yes/no). The specific numbers come from the tool-specific row
in `references/resource_profiles.md`.

### Step 2 — Pick the partition

Read the current cluster state, match against the task's requirements:

```
slurm://partition-limits   → per-partition max CPUs, memory, time, GPUs
slurm://partitions         → real-time availability
```

Algorithm in `references/partition_selection.md`. Key rule: **tightest fit
wins**. A 30-min STAR job on a 7-day partition is wasteful scheduling.

### Step 3 — Generate the script

Assemble an `#SBATCH` script from the templates in
`references/script_templates.md`. Template choice:

- **Base** — single node, CPU-only
- **Container** — adds `singularity exec --bind` wrapper
- **GPU** — adds `--gres=gpu:N` + `nvidia-smi` diagnostic
- **Job array** — `--array=1-N`, reads sample from list via `$SLURM_ARRAY_TASK_ID`
- **Multi-node** — adds `--ntasks-per-node`, wraps command in `srun`

Every generated script MUST include the logging + completion-marker block
(see CLAUDE.md §2 Logging and Audit Trail).

### Step 4 — Validate before submitting

Always run `test_only=true` or `dry_run=true` through `slurm_submit_job`
before the real submission. This catches:
- Invalid partition for the account
- Resource requests exceeding partition limits
- Missing account / malformed directives

Only submit for real after validation passes. If validation fails, refine
Step 2 or Step 3 and re-validate — never submit a failing script.

## MCP Tools and Resources Available

| Tool | Use |
|------|-----|
| `slurm_submit_job(..., dry_run=True)` | Step 4 validation |
| `slurm_submit_job(...)` | Final submission |
| `slurm_submit_batch(scripts, chain=True)` | Multi-script submission with `afterok` chaining |
| `slurm_queue_info` | Check real-time availability for a partition |
| `slurm_node_info` | Find nodes with enough free resources right now |

| Resource | Use |
|----------|-----|
| `slurm://partition-limits` | Step 2 matching — **read at every activation** |
| `slurm://partitions` | Step 2 real-time state |

**Never embed partition limits in this skill.** Cluster configurations
change. Always read `slurm://partition-limits` at activation time.

## Core Principles

1. **Validate before submitting.** Always `test_only` or `dry_run` first.
2. **Match partition to job, not job to partition.** Tightest fit wins.
3. **Don't over-request.** 2× safety margin, not 10×.
4. **Job arrays > many individual submissions.** `--array=1-N` is cheaper
   for the scheduler and gives one job ID to manage.
5. **Every script logs.** Start time, hostname, resources, command, exit
   code, completion marker. Missing marker = job didn't finish.
6. **GPU jobs are special.** Only request GPUs for tools that use them
   (dorado, AlphaFold, GROMACS, ML training). GPUs are scarce.
7. **Containers need bind mounts.** Every I/O path must be `--bind`-covered.
8. **Read partition limits, not hardcoded numbers.** Cluster config changes.
9. **Separate code from results.** Run outputs go in
   `results/{date}_{genome}_{description}/` with one config per run.

## Reference Files

Read these during execution:

| File | When to read |
|------|-------------|
| `references/resource_profiles.md` | Step 1 — classifying the task, getting per-tool numbers |
| `references/partition_selection.md` | Step 2 — matching a task to a partition |
| `references/script_templates.md` | Step 3 — assembling the #SBATCH script |
| `evals/phase1_cases.md` | Self-check: does my output match the expected shape? |

## Phase 1 Scope (v0.1.0)

This version covers **pre-submission only**: classify → pick partition →
generate script → validate. Post-failure orchestration (calling
`slurm_diagnose_job`, proposing bumped-resource resubmits) is Phase 2 and
will be added only if Phase 1 usage reveals a gap. For a failed job today,
call `slurm_diagnose_job` directly.

## Authoritative Source for Resource Numbers

The tool-to-resource mapping in `references/resource_profiles.md` is derived
from `docs/job_resource_profiles.md` in the slurm-mcp repo. When in doubt
or when a tool isn't in the skill's lookup, **read the docs file** —
it is more comprehensive and is the source of truth. Keep the skill's
lookup lean and delegate to the doc for edge cases.
