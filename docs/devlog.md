# slurm-mcp Development Log

---

## 2026-02-28 — Teaching the MCP to "Ask Before Submitting"

### The problem

When Claude Code submits a SLURM job through the MCP, there are two ways it
can fail:

1. **Before submission** — the local safety limits catch it (too much memory,
   disallowed partition, etc.). This is what `_validate_resources()` does.
2. **After submission** — SLURM itself rejects the job. Wrong account, partition
   is down for maintenance, requested node configuration doesn't exist, etc.

The existing `dry_run=True` only catches category 1. It builds the command and
returns it, but never talks to the scheduler. So you can get a perfectly
constructed sbatch command that SLURM immediately rejects.

Meanwhile, sbatch has had `--test-only` forever. It asks the scheduler to
validate the request and estimate a start time, without actually creating a job.
The problem: passing `--test-only` via `extra_args` would hit the job ID parser
(`re.search(r"Submitted batch job (\d+)", stdout)`) and raise a RuntimeError,
because `--test-only` doesn't produce that output.

### The fix

Added `test_only: bool = False` as a first-class parameter to `submit_job()`.
When enabled:

- `--test-only` is inserted into the command after `sbatch`
- The command is executed with `check=False` so we capture failures gracefully
- Both stdout and stderr are returned as `slurm_response`
- A `feasible` boolean tells you whether the scheduler said yes or no
- No audit log entry is written (nothing was created)
- No job ID parsing is attempted

This gives a clean three-tier validation flow:

```
dry_run     →  "Here's the command I would run"       (instant, offline)
test_only   →  "SLURM says this would work/fail"      (calls scheduler, no job)
(default)   →  "Job 12345 submitted"                  (real submission)
```

### Design decisions

**Why not just fix `extra_args="--test-only"`?** Because the response shape is
fundamentally different. A test-only call returns SLURM's validation message, not
a job ID. Jamming it into the existing code path would require ugly conditional
parsing. A dedicated code path is cleaner and testable.

**Why `check=False`?** When `--test-only` determines the job is infeasible,
sbatch returns a non-zero exit code. That's not an error in our context — it's
the expected "no" answer. We capture it and set `feasible=False`.

**Why not audit log test-only calls?** The audit log tracks actual state changes
(jobs created, jobs cancelled). Test-only is a read-only query. Logging it would
add noise without value.

**Position of `--test-only` in the command:** Inserted at index 1 (right after
`sbatch`) rather than appended, to match the conventional placement and avoid
any ambiguity about whether it's a flag for the script.

### Testing

Four new tests cover the feature:

- Feasible job → `feasible=True`, SLURM response includes estimated start time
- Infeasible job → `feasible=False`, SLURM error message captured
- No audit log written for test-only calls
- MCP round-trip returns valid JSON with correct shape

All 59 tests pass (up from 55).

### Usage

From Claude Code, just add `test_only=True` to any submission:

```
# Via the MCP tool:
slurm_submit_job(script="/path/to/job.sh", mem="128G", test_only=True)

# Response:
{
  "test_only": true,
  "feasible": true,
  "slurm_response": "Job 12345 to start at 2026-03-01T10:00:00 using 8 processors on node01 in partition componc_cpu",
  "resources": { "mem": "128G", ... }
}
```

If `feasible` comes back `false`, you know to adjust resources before wasting a
submission attempt.

---
