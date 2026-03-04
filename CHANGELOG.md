# CHANGELOG

## 2026-03-04 — Partition-Aware Resource Limits

**Motivation:** The MCP server previously enforced hardcoded global limits
(`SLURM_MAX_CPUS=64`, `SLURM_MAX_MEM_GB=256`, etc.) that don't reflect actual
partition constraints. On heterogeneous clusters, partitions range from 2h/56
CPUs to 60 days/128 CPUs/8 GPUs. Requesting resources above a partition's real
limit causes silent sbatch failures. This change generates a per-partition
profile during setup and uses it for smarter validation at runtime.

---

### 1. `setup.sh` — Partition limits generation + default validation

**Files:** `setup.sh`

Added `generate_partition_limits()` bash function that:
1. Queries `sinfo -h -o '%P|%l|%c|%m|%G|%D'` for all partitions
2. Pipes output to an inline Python aggregator that computes per-partition:
   max CPUs/node, max memory, time limit, GPU types/counts, node counts
3. Writes a timestamped JSON file to `~/.slurm-mcp/partition_limits_YYYYMMDD_HHMMSS.json`
4. Creates a `partition_limits_latest.json` symlink

Added post-prompt validation block that compares user-entered defaults (memory,
time, CPUs) against the selected partition's real limits. If any exceed the
partition max, the user is warned and offered auto-adjustment.

The MCP config block now includes:
- `SLURM_PARTITION_LIMITS` env var pointing to the symlink
- Dynamic `SLURM_MAX_*` values derived from the selected partition (instead of
  hardcoded 64/256/168/4)

---

### 2. `slurm_cli.py` — Per-partition validation

**Files:** `src/slurm_mcp/slurm_cli.py`

`SlurmConfig` dataclass changes:
- Added `partition_limits: dict[str, dict]` field
- Added `_load_partition_limits(path)` static method — reads JSON, returns
  `data["partitions"]`, catches `FileNotFoundError`/`JSONDecodeError`/`OSError`
- Added `get_partition_limits(partition)` helper — returns dict or None
- `__post_init__` auto-loads from `SLURM_PARTITION_LIMITS` env var

`_validate_resources()` changes:
- Looks up per-partition limits first, falls back to global `config.max_*`
- Error messages include the limit source (`"partition 'componc_cpu'"` vs
  `"global"`)
- New check: GPUs requested on a partition with `has_gpu_nodes=False` → violation

---

### 3. `server.py` — Startup log + MCP resource

**Files:** `src/slurm_mcp/server.py`

- Added startup log line: `"Partition-aware limits loaded for N partitions"`
- Added `slurm://partition-limits` MCP resource exposing the loaded partition
  limits as JSON (lets Claude inspect available partitions and their constraints)

---

### 4. Tests — 15 new tests (59 → 74 total)

**Files:** `tests/test_slurm_mcp.py`

Partition limits loading (7 tests):
- Load valid JSON, missing file, malformed JSON, env var loading, no env var,
  known partition lookup, unknown partition lookup

Per-partition validation (8 tests):
- CPU exceeds partition limit, memory exceeds, time exceeds, GPU on CPU-only
  partition, unknown partition falls back to global, within limits passes,
  large GPU within limit, GPU count exceeds limit

All 74 tests pass.

---

### Summary of API surface changes

| Location | Change | Backwards-compatible? |
|----------|--------|-----------------------|
| `SlurmConfig.partition_limits` | New field (default `{}`) | Yes |
| `SlurmConfig._load_partition_limits()` | New static method | Additive |
| `SlurmConfig.get_partition_limits()` | New method | Additive |
| `_validate_resources()` | Uses per-partition limits when available | Yes — falls back to global |
| `slurm://partition-limits` | New MCP resource | Additive |

---

## 2026-02-28 — `test_only` Parameter: Server-Side Job Validation

**Motivation:** The existing `dry_run` parameter builds and returns the sbatch
command without ever calling SLURM. This is useful for inspecting the command,
but it cannot tell you whether the scheduler would actually accept the job (valid
partition, sufficient resources, correct account, estimated start time). The
`--test-only` flag built into sbatch does exactly this — it asks the scheduler to
validate without creating a job. This change exposes that capability as a
first-class parameter.

---

### 1. New `test_only` parameter — `submit_job()`

**Files:** `src/slurm_mcp/slurm_cli.py`

Added `test_only: bool = False` to `submit_job()`. When `True`:

1. Inserts `--test-only` into the sbatch command (after `sbatch`, before other flags)
2. Executes the command with `check=False` (non-zero exit = infeasible, not an error)
3. Captures both stdout and stderr from SLURM's response
4. Returns a structured result without attempting to parse a job ID
5. Does **not** write to the audit log (no job was created)

```python
# Return shape when test_only=True
{
    "test_only": True,
    "feasible": True,           # True if sbatch returned 0
    "command": "sbatch --test-only --account ... script.sh",
    "slurm_response": "Job 12345 to start at 2026-03-01T10:00:00 ...",
    "job_name": "my_job",
    "resources": {
        "nodes": 1,
        "cpus_per_task": 8,
        "mem": "64G",
        ...
    },
}
```

The three-tier validation flow is now:

| Mode | Calls SLURM? | Creates job? | Use case |
|------|-------------|-------------|----------|
| `dry_run=True` | No | No | "Show me the command" |
| `test_only=True` | Yes | No | "Ask SLURM if this would work" |
| Neither | Yes | Yes | Actual submission |

---

### 2. MCP tool layer updated

**Files:** `src/slurm_mcp/server.py`

- `slurm_submit_job`: Added `test_only: bool = False` parameter, passed through
  to `submit_job()`.
- `slurm_submit_batch`: Added `test_only: bool = False` parameter, passed through
  to each `submit_job()` call in the loop.

Docstrings updated to document the new parameter.

---

### 3. Test suite expanded — 4 new tests (55 → 59 total)

**Files:** `tests/test_slurm_mcp.py`

| Test | What it verifies |
|------|------------------|
| `test_test_only_feasible` | `feasible=True` when sbatch returns 0, `--test-only` in command, no `job_id` key |
| `test_test_only_infeasible` | `feasible=False` when sbatch returns 1, error message in `slurm_response` |
| `test_test_only_does_not_audit_log` | Audit log file is not created for test-only calls |
| `test_slurm_submit_job_test_only_json` | MCP round-trip: tool returns valid JSON with correct shape |

All 59 tests pass.

---

### Summary of API surface changes

| Location | Change | Backwards-compatible? |
|----------|--------|-----------------------|
| `slurm_cli.submit_job()` | Added `test_only: bool = False` param | Yes — default preserves old behaviour |
| `server.slurm_submit_job()` | Added `test_only: bool = False` param | Yes — default preserves old behaviour |
| `server.slurm_submit_batch()` | Added `test_only: bool = False` param | Yes — default preserves old behaviour |

---

## 2026-02-26 — Context Reduction Refactor

**Motivation:** MCP tool responses and schema descriptions are injected into the
model's context window on every API call. On a busy cluster, unbounded list
responses (`squeue`, `sinfo`) and pretty-printed JSON were consuming
disproportionate tokens. This refactor reduces context footprint without
removing any functionality.

---

### 1. Compact JSON output — all tools

**Files:** `src/slurm_mcp/server.py`

Removed `indent=2` from every `json.dumps()` call across all tool handlers.
Pretty-printing is cosmetic for machine-to-machine communication and adds
roughly 30–50% overhead to list responses (whitespace = tokens).

```python
# Before
return json.dumps(result, indent=2)

# After
return json.dumps(result)
```

Affected tools: `slurm_submit_job`, `slurm_job_status`, `slurm_list_jobs`,
`slurm_job_logs`, `slurm_cancel_job`, `slurm_job_resources`, `slurm_queue_info`,
`slurm_node_info`, `slurm_submit_batch`.

---

### 2. Result pagination — `slurm_list_jobs` and `slurm_node_info`

**Files:** `src/slurm_mcp/server.py`, `src/slurm_mcp/slurm_cli.py`

Added a `limit: int = 25` parameter to both tools and their underlying CLI
functions. Without a cap, a busy cluster could return thousands of jobs or
hundreds of nodes in a single tool call.

The response envelope now includes `total_matching` and `shown` so the model
knows when results have been truncated:

```python
# list_jobs return shape (slurm_cli.py, list_jobs())
{"total_matching": 47, "shown": 25, "jobs": [...]}

# node_info return shape (slurm_cli.py, node_info())
{"total_matching": 83, "shown": 25, "nodes": [...]}
```

**Return type change:** Both functions changed from returning `list[dict]` to
`dict`. The early-exit path in `node_info` (empty `sinfo` output) was also
updated to return the same dict shape instead of `[]`:

```python
# slurm_cli.py — node_info(), early exit on empty sinfo output
# Before (returned bare list, crashed server on dict key access)
if result.returncode != 0 or not result.stdout.strip():
    return nodes  # was []

# After
if result.returncode != 0 or not result.stdout.strip():
    return {"total_matching": 0, "shown": 0, "nodes": []}
```

`node_info` results remain sorted by `free_mem_gb` descending before the cap
is applied, so the top-N by available memory is always returned.

---

### 3. Log truncation hard cap — `slurm_job_logs`

**Files:** `src/slurm_mcp/slurm_cli.py`

Added an 8,000-character hard cap inside `_read_tail()` (the private helper
inside `job_logs()`). A verbose job log (e.g. a 10,000-line STAR alignment log)
would previously consume the entire context window when `tail_lines=0`.

```python
# slurm_cli.py — job_logs(), _read_tail() helper
MAX_LOG_CHARS = 8_000

content = "".join(lines[-n:] if n > 0 else lines)
if len(content) > MAX_LOG_CHARS:
    total_chars = len(content)
    content = (
        f"[TRUNCATED — showing last {MAX_LOG_CHARS} of {total_chars} chars]\n"
        + content[-MAX_LOG_CHARS:]
    )
return content
```

The cap applies after the `tail_lines` slice, so the `tail_lines=50` default
will rarely hit it. It acts as a safety net for `tail_lines=0` (full file).

---

### 4. `slurm_queue_info` promoted to MCP Resource

**Files:** `src/slurm_mcp/server.py`

Partition information changes at most once per cluster maintenance window.
Exposing it as an MCP Resource (rather than a Tool) allows MCP clients to cache
it and avoids injecting a tool schema slot into every request.

```python
# server.py — new Resource alongside the existing Tool
@mcp.resource("slurm://partitions")
def slurm_partitions_resource() -> str:
    """All SLURM partitions with availability, node counts, and resource limits."""
    ...
```

The `slurm_queue_info` tool is retained for querying a **specific** partition
by name. Its docstring was updated to point users toward the resource for
all-partition queries.

---

### 5. Trimmed tool docstrings

**Files:** `src/slurm_mcp/server.py`

FastMCP uses the Python docstring as the `description` field in the tool
schema, which is sent to the model on every API call. Verbose `Args:` blocks
were replaced with single- or two-line summaries that preserve the format
hints the model genuinely needs (non-obvious sentinel values, mutually
exclusive parameters, string formats).

| Tool | Before (approx. tokens) | After (approx. tokens) |
|------|------------------------|------------------------|
| `slurm_submit_job` | ~75 | ~25 |
| `slurm_job_status` | ~20 | ~15 |
| `slurm_list_jobs` | ~25 | ~15 |
| `slurm_job_logs` | ~30 | ~15 |
| `slurm_cancel_job` | ~15 | ~10 |
| `slurm_job_resources` | ~15 | ~12 |
| `slurm_node_info` | ~55 | ~15 |
| `slurm_submit_batch` | ~50 | ~15 |

Parameters that retain inline format hints in the summary:
- `slurm_submit_job`: `gpu` string format (`'1'`), `dependency` syntax
  (`'afterok:12345'`), `script`/`wrap` mutual exclusivity
- `slurm_job_logs`: `tail_lines=0` full-file sentinel, 8000-char cap note
- `slurm_list_jobs`: valid `state` enum values

---

### 6. Test suite updated

**Files:** `tests/test_slurm_mcp.py`

All 12 tests that tested `list_jobs` and `node_info` were updated to use the
new dict return shape. Key patterns changed:

```python
# TestListJobs — Before
jobs = list_jobs()
assert len(jobs) == 2
assert jobs[0]["state"] == "RUNNING"

# After
result = list_jobs()
assert result["total_matching"] == 2
assert result["jobs"][0]["state"] == "RUNNING"
```

```python
# TestNodeInfo — Before
nodes = node_info()
assert len(nodes) == 3
assert nodes[0]["node"] == "node02"

# After
result = node_info()
assert result["total_matching"] == 3
assert result["nodes"][0]["node"] == "node02"
```

```python
# TestMCPToolRoundTrip — Before
assert result["count"] == 1   # old key name

# After
assert result["total_matching"] == 1
```

Tests for the empty-output paths were updated from bare list equality to dict
key access:

```python
# Before
assert node_info() == []

# After
assert node_info()["nodes"] == []
```

All 55 tests pass after these updates.

---

### Summary of API surface changes

| Location | Change | Backwards-compatible? |
|----------|--------|-----------------------|
| `slurm_cli.list_jobs()` | Returns `dict` instead of `list` | No — callers must use `result["jobs"]` |
| `slurm_cli.node_info()` | Returns `dict` instead of `list` | No — callers must use `result["nodes"]` |
| `server.slurm_list_jobs()` | Added `limit: int = 25` param | Yes — default preserves old behaviour |
| `server.slurm_node_info()` | Added `limit: int = 25` param | Yes — default preserves old behaviour |
| `server.slurm_job_logs()` | Logs hard-capped at 8000 chars | Yes — only affects very large logs |
| `server.slurm_partitions_resource` | New MCP Resource at `slurm://partitions` | Additive |
