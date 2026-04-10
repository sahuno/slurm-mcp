# SLURM Native Error Codes — Comprehensive Reference

**Date:** 2026-04-09
**Purpose:** Document SLURM's built-in error code systems for potential integration into slurm-mcp's error handling layer.

---

## Overview

SLURM has 4 distinct error/status code systems:

1. **Internal API error codes** (`slurm_errno.h`) — 500+ daemon/library errors
2. **Job state codes** — terminal and active states reported by `sacct`/`squeue`
3. **Job reason codes** — ~194 codes explaining why a job is pending or ended
4. **Exit codes** — `exit_code:signal` format from `sacct`

---

## 1. Internal API Error Codes (`slurm_errno.h`)

These are used by SLURM daemons and the C API. They appear in daemon logs and API error responses.

| Range | Category | Key Examples |
|-------|----------|-------------|
| 0 | Success | `SLURM_SUCCESS = 0` |
| 1 | General error | `SLURM_ERROR = 1` |
| 1000-1099 | Communication/protocol | `SLURM_UNEXPECTED_MSG_ERROR = 1000`, `SLURM_COMMUNICATIONS_CONNECTION_ERROR = 1001`, `SLURM_COMMUNICATIONS_SEND_ERROR = 1002` |
| 1800-1899 | Slurmctld communication | `SLURMCTLD_COMMUNICATIONS_CONNECTION_ERROR = 1800`, `SLURMCTLD_COMMUNICATIONS_BACKOFF = 1804` |
| 2000-2199 | Controller (job/partition/QOS) | `ESLURM_INVALID_PARTITION_NAME = 2000`, `ESLURM_ACCESS_DENIED`, `ESLURM_INVALID_QOS = 2066`, `ESLURM_ACCOUNTING_POLICY = 2050`, `ESLURM_BURST_BUFFER_WAIT = 2100` |
| 3000-3099 | SPANK plugin | `ESPANK_ERROR = 3000`, `ESPANK_BAD_ARG = 3001` |
| 4000-4099 | Slurmd (node daemon) | `ESLURMD_KILL_TASK_FAILED = 4001`, `ESLURMD_CREDENTIAL_EXPIRED = 4007`, `ESLURMD_EXECVE_FAILED = 4020`, `ESLURMD_TOOMANYSTEPS = 4025` |
| 6000-6099 | Authentication | `ESLURM_AUTH_CRED_INVALID = 6000`, `ESLURM_AUTH_UNPACK = 6007` |
| 7000-7499 | Database/accounting/federation | `ESLURM_DB_CONNECTION = 7000`, `ESLURM_FED_CLUSTER_MAX_CNT = 7100` |
| 8000-8099 | Plugin/config | `ESLURM_MISSING_TIME_LIMIT = 8000`, `ESLURM_PLUGIN_INVALID = 8002` |
| 9000-9299 | REST API / Data | `ESLURM_REST_INVALID_QUERY = 9000`, `ESLURM_DATA_PATH_NOT_FOUND = 9200` |
| 10000+ | Container/URL/HTTP/TLS | `ESLURM_CONTAINER_NOT_CONFIGURED = 10000`, `ESLURM_URL_UNKNOWN_SCHEME = 11000`, `ESLURM_HTTP_PARSING_FAILURE = 12000`, `ESLURM_TLS_REQUIRED = 13000` |

**Source:** [`slurm/slurm_errno.h`](https://github.com/SchedMD/slurm/blob/master/slurm/slurm_errno.h)
**String mapping:** [`src/common/slurm_errno.c`](https://github.com/SchedMD/slurm/blob/master/src/common/slurm_errno.c)

---

## 2. Job State Codes

Reported by `sacct` (State field) and `squeue` (ST column).

### Terminal States

| State | Abbreviation | Meaning |
|-------|-------------|---------|
| COMPLETED | CD | Finished successfully (exit code 0 on all nodes) |
| FAILED | F | Non-zero exit code or other failure |
| CANCELLED | CA | Cancelled by user or administrator |
| TIMEOUT | TO | Terminated due to reaching time limit |
| OUT_OF_MEMORY | OOM | Exceeded memory limit (cgroup enforcement) |
| NODE_FAIL | NF | Terminated due to node failure |
| PREEMPTED | PR | Terminated due to preemption by higher-priority job |
| BOOT_FAIL | BF | Terminated due to node boot failure |
| DEADLINE | DL | Could not start before configured deadline |

### Active States

| State | Abbreviation | Meaning |
|-------|-------------|---------|
| PENDING | PD | Queued and waiting (has a reason code) |
| RUNNING | R | Allocated resources and executing |
| SUSPENDED | S | Allocated but execution suspended |
| COMPLETING | CG | Finishing cleanup tasks, running epilog |
| CONFIGURING | CF | Waiting for nodes to boot |
| REQUEUED | RQ | Being requeued after failure |
| REQUEUE_HOLD | RH | Requeued but held |
| RESIZING | RS | Job size changing |
| REVOKED | RV | Revoked due to federation sibling |
| SIGNALING | SI | Outgoing signal pending |
| SPECIAL_EXIT | SE | Special situation flag |
| STAGE_OUT | SO | Staging out burst buffer data |
| STOPPED | ST | Received SIGSTOP |

**Source:** [slurm.schedmd.com/job_state_codes.html](https://slurm.schedmd.com/job_state_codes.html)

---

## 3. Job Reason Codes (~194 total)

These explain *why* a PENDING job has not started, or *why* a completed job ended. Shown in the `squeue` REASON column.

### Scheduling Reasons

| Reason | Meaning |
|--------|---------|
| Priority | Higher priority jobs exist for the partition |
| Resources | Requested resources not currently available |
| None | Not yet evaluated in current backfill cycle |
| Dependency | Waiting on another job to complete |
| DependencyNeverSatisfied | Dependency can never be met (depended-on job failed/cancelled) |
| BeginTime | Earliest start time not yet reached |

### Partition and Node Reasons

| Reason | Meaning |
|--------|---------|
| PartitionDown | Partition is DOWN |
| PartitionInactive | Partition is inactive |
| PartitionNodeLimit | Node count outside partition limits |
| PartitionTimeLimit | Time limit exceeds partition maximum |
| PartitionConfig | Generic partition limit violation |
| NodeDown | Required node is down |
| ReqNodeNotAvail | Required nodes not available (reports UnavailableNodes) |
| BadConstraints | Requirements can never be satisfied by any node |

### Account/Association Limit Reasons

| Reason | Meaning |
|--------|---------|
| AssocGrpCpuLimit | Association aggregate CPU limit reached |
| AssocGrpMemLimit | Association aggregate memory limit reached |
| AssocGrpNodeLimit | Association aggregate node limit reached |
| AssocGrpJobsLimit | Association max simultaneous jobs reached |
| AssocGrpSubmitJobsLimit | Association max running+pending jobs reached |
| AssocMaxCpuPerJobLimit | Per-job CPU limit for association |
| AssocMaxMemPerJob | Per-job memory limit for association |
| AssocMaxNodePerJobLimit | Per-job node limit for association |
| AssocMaxWallDurationPerJobLimit | Per-job walltime limit for association |
| AccountNotAllowed | Account not permitted in this partition |
| InvalidAccount | Account does not exist |

*Plus ~30 more AssocGrp* and AssocMax* variants for Billing, Energy, GRES, Licenses, Burst Buffer, etc.*

### QOS Limit Reasons

| Reason | Meaning |
|--------|---------|
| QOSGrpCpuLimit | QOS aggregate CPU limit |
| QOSGrpMemLimit | QOS aggregate memory limit |
| QOSGrpJobsLimit | QOS max simultaneous jobs |
| QOSMaxCpuPerJobLimit | QOS per-job CPU limit |
| QOSMaxJobsPerUserLimit | QOS per-user running job limit |
| QOSMaxMemoryPerJob | QOS per-job memory limit |
| QOSMaxMemoryPerNode | QOS per-node memory limit |
| QOSMaxNodePerJobLimit | QOS per-job node limit |
| QOSMaxWallDurationPerJobLimit | QOS per-job walltime limit |
| QOSMaxSubmitJobPerUserLimit | QOS per-user running+pending limit |
| QOSNotAllowed | QOS not permitted for association/partition |
| QOSResourceLimit | QOS generic resource limit reached |
| QOSTimeLimit | QOS time limit reached |
| QOSUsageThreshold | QOS usage threshold breached |

*Plus ~80 more QOS* variants for per-account, per-user, per-node, and minutes-based limits across all resource types.*

### Job Hold Reasons

| Reason | Meaning |
|--------|---------|
| JobHeldAdmin | Held by system administrator |
| JobHeldUser | Held by user (`scontrol uhold`) |
| JobHoldMaxRequeue | Hit MAX_BATCH_REQUEUE limit |

### Failure/Error Reasons

| Reason | Meaning |
|--------|---------|
| NonZeroExitCode | Job terminated with non-zero exit |
| OutOfMemory | Job failed with OOM |
| TimeLimit | Job exhausted time limit |
| SystemFailure | SLURM system/filesystem/network failure |
| JobLaunchFailure | Could not launch (filesystem, invalid program, etc.) |

### Other Reasons

| Reason | Meaning |
|--------|---------|
| AccountingPolicy | Fallback when other policy reasons don't match |
| Cleaning | Requeued job still cleaning up |
| DeadLine | Cannot meet configured deadline |
| FedJobLock | Waiting for federation cluster sync |
| InactiveLimit | Reached system InactiveLimit |
| InvalidQOS | QOS does not exist |
| JobArrayTaskLimit | Array task concurrency limit reached |
| Licenses | Waiting for a license |
| Prolog | Prolog still running |
| Reservation | Waiting for reservation to start |
| ReservationDeleted | Requested reservation no longer exists |
| SchedDefer | Immediate allocation requested but defer configured |

**Full list:** [slurm.schedmd.com/job_reason_codes.html](https://slurm.schedmd.com/job_reason_codes.html)
**Source code:** `src/common/job_state_reason.c` in the SchedMD/slurm repository

---

## 4. Exit Codes and Signal-Based Termination

### sacct ExitCode Format: `exit_code:signal`

| ExitCode | Meaning |
|----------|---------|
| `0:0` | Success, no signal |
| `1:0` | Script returned error (exit code 1), no signal |
| `0:9` | Killed by SIGKILL (OOM killer or force kill after timeout) |
| `0:15` | Killed by SIGTERM (first signal on timeout/cancel) |
| `137:0` | Bash convention: 128 + 9 (SIGKILL) — seen when script catches signal |

The exit code is an **8-bit unsigned integer (0-255)**. Negative exit codes are displayed as their unsigned equivalent.

### Key Signals in SLURM Context

| Signal | Number | Typical SLURM Cause |
|--------|--------|---------------------|
| SIGTERM | 15 | First signal on timeout or `scancel`. Jobs get ~30s (KillWait) to clean up. |
| SIGKILL | 9 | Sent after KillWait expires, or by kernel OOM killer (uncatchable). |
| SIGCONT | 18 | Sent before SIGTERM to wake suspended processes. |

### SLURM Timeout Kill Sequence

```
SIGCONT -> SIGTERM -> (wait KillWait seconds, default 30) -> SIGKILL
```

### OOM Detection

When using cgroups (`task/cgroup` plugin), SLURM detects OOM kill events and sets the job state to `OUT_OF_MEMORY`. The sacct ExitCode for OOM-killed steps is often `0:9` (SIGKILL from the kernel OOM killer). Some configurations report exit code `7` for the OOM-killed step.

### Bash Exit Code Convention

In bash, when a process is killed by a signal, the exit status is `128 + signal_number`:
- SIGKILL (9) -> exit code 137
- SIGTERM (15) -> exit code 143

SLURM's sacct separates these into the `exit_code:signal` format rather than using the 128+ convention.

### Derived Exit Codes

For `sbatch` jobs, the exit code of the batch script is what SLURM records. If the script itself did not set an exit code but a step within it was killed, the batch step may show a different ExitCode than the individual step.

---

## References

### Official SchedMD Documentation
- [Job Exit Codes](https://slurm.schedmd.com/job_exit_code.html)
- [Job State Codes](https://slurm.schedmd.com/job_state_codes.html)
- [Job Reason Codes](https://slurm.schedmd.com/job_reason_codes.html)
- [sacct manual](https://slurm.schedmd.com/sacct.html)
- [sbatch manual](https://slurm.schedmd.com/sbatch.html)

### Source Code (SchedMD/slurm on GitHub)
- [`slurm/slurm_errno.h`](https://github.com/SchedMD/slurm/blob/master/slurm/slurm_errno.h) — All `ESLURM_*` error code definitions
- [`src/common/slurm_errno.c`](https://github.com/SchedMD/slurm/blob/master/src/common/slurm_errno.c) — Error code to string mapping (500+ entries)
- `src/common/job_state_reason.c` — Job reason code string mappings
