# Error Code Interpreter Design for slurm-mcp

**Date:** 2026-04-10
**Status:** Implementation planned (Level 1 + Level 2)

---

## Problem

When a SLURM job fails, slurm-mcp returns raw strings like `state: "FAILED"`, `exit_code: "0:9"`, `reason: "QOSMaxJobsPerUserLimit"`. Claude (the LLM) can often interpret these, but it's guessing — it doesn't have structured knowledge of what each code means or what to do about it.

SLURM has ~194 reason codes, 12+ terminal states, and a signal-based exit code system. Most HPC users only encounter a handful, but the long tail causes confusion.

---

## Design: Three Levels of Ambition

### Level 1: Static Lookup Table (simplest) — IMPLEMENTING

A Python dict mapping known states, reasons, and signal codes to human-readable explanations + suggested actions.

```python
{"OUT_OF_MEMORY": {
    "explanation": "Job exceeded cgroup memory limit",
    "action": "Increase --mem (check actual usage with sacct MaxRSS)"
}}
```

Lives in `slurm_cli.py` and enriches the return dicts from `job_status()` and `job_resources()`. Cheap, no external calls, covers the ~20 most common failure modes.

**Coverage target:** The most common failure states, reason codes, and signal numbers that HPC users in computational biology encounter.

### Level 2: Context-Aware Diagnosis (medium) — IMPLEMENTING

When a job fails, automatically pull `sacct` resource data (MaxRSS, elapsed time, exit code) and compare against what was requested.

Example: job requested 64G, MaxRSS was 63.8G -> "OOM: job used 99.7% of allocated memory, increase to 96G (1.5x)".

This reuses the `job_resources()` data we already collect — we add comparison logic and resource-aware suggestions.

**Key comparisons:**
- Memory: MaxRSS vs requested `--mem` (detect near-limit and over-limit)
- Time: Elapsed vs requested `--time` (detect timeout proximity)
- Exit code: Parse `exit_code:signal` format, map signals to causes

### Level 3: Pattern-Based Advisor (most ambitious) — DEFERRED

Track failure patterns across jobs using the audit log. If a user's last 3 jobs on `componc_cpu` all hit `TIMEOUT`, suggest partition changes.

This ties into the `/slurm` skill design (shelved in `docs/slurm_skill_design_20260303.md`). Revisit when the skill system is built.

---

## Implementation Plan

### New function: `diagnose_job(job_id, config)` in `slurm_cli.py`

1. Pull state + exit code + reason from `sacct`
2. Look up static explanation from the lookup tables
3. Parse `exit_code:signal` and map signal to cause
4. Compare requested vs actual resources (Level 2)
5. Return structured dict:

```python
{
    "job_id": "12345",
    "state": "OUT_OF_MEMORY",
    "exit_code": "0:9",
    "explanation": "Job exceeded cgroup memory limit and was killed by the kernel OOM killer",
    "suggested_action": "Increase --mem to 96G (1.5x current 64G). Actual peak usage was 63.8G.",
    "resource_comparison": {
        "memory": {"requested": "64G", "actual_peak": "63.8G", "utilization": 0.997},
        "time": {"requested": "04:00:00", "elapsed": "02:15:33", "utilization": 0.564},
    },
    "signal": {"number": 9, "name": "SIGKILL", "cause": "Kernel OOM killer or force kill after timeout grace period"},
    "severity": "error"
}
```

### New MCP tool: `slurm_diagnose_job` in `server.py`

Exposes `diagnose_job()` as an MCP tool so Claude can call it directly when a user asks "why did my job fail?"

### Enrichment of existing tools

`job_status()` and `job_resources()` gain optional `explanation` and `suggested_action` fields when the job is in a terminal failure state.

---

## What We're NOT Building

- No daemon or background monitoring (that's Level 3 territory)
- No automatic resubmission (too risky without user confirmation)
- No custom SLURM plugin (we only read, never modify SLURM internals)
- No ML-based prediction (premature — static rules cover 90% of cases)
