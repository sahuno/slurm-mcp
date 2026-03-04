"""SLURM CLI wrapper — subprocess-based interface to sbatch, squeue, sacct, scancel, sinfo, scontrol.

Author: Samuel Ahuno
Date: 2026-02-21
Purpose: Thin Python wrappers around SLURM CLI commands with structured output parsing.
"""

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SlurmConfig:
    """Safety guardrails and defaults for SLURM operations."""

    default_account: str = os.environ.get("SLURM_DEFAULT_ACCOUNT", "")
    default_partition: str = os.environ.get("SLURM_DEFAULT_PARTITION", "")
    default_nodes: int = int(os.environ.get("SLURM_DEFAULT_NODES", "1"))
    default_ntasks_per_node: int = int(os.environ.get("SLURM_DEFAULT_NTASKS_PER_NODE", "1"))
    default_cpus_per_task: int = int(os.environ.get("SLURM_DEFAULT_CPUS_PER_TASK", "8"))
    default_mem: str = os.environ.get("SLURM_DEFAULT_MEM", "64G")
    default_time: str = os.environ.get("SLURM_DEFAULT_TIME", "04:00:00")
    log_dir: str = os.environ.get(
        "SLURM_LOG_DIR",
        os.path.expanduser("~/slurm_logs"),
    )
    audit_log: str = os.environ.get(
        "SLURM_AUDIT_LOG",
        os.path.expanduser("~/slurm_logs/audit.jsonl"),
    )

    # Safety limits
    max_cpus: int = int(os.environ.get("SLURM_MAX_CPUS", "64"))
    max_mem_gb: int = int(os.environ.get("SLURM_MAX_MEM_GB", "256"))
    max_time_hours: int = int(os.environ.get("SLURM_MAX_TIME_HOURS", "168"))
    max_gpus: int = int(os.environ.get("SLURM_MAX_GPUS", "4"))
    max_concurrent_jobs: int = int(os.environ.get("SLURM_MAX_CONCURRENT", "50"))

    # Allowed partitions (empty = all allowed)
    allowed_partitions: list[str] = field(default_factory=list)

    # Per-partition resource limits (loaded from JSON profile)
    partition_limits: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        env_partitions = os.environ.get("SLURM_ALLOWED_PARTITIONS", "")
        if env_partitions and not self.allowed_partitions:
            self.allowed_partitions = [
                p.strip() for p in env_partitions.split(",") if p.strip()
            ]

        limits_path = os.environ.get("SLURM_PARTITION_LIMITS", "")
        if limits_path and not self.partition_limits:
            self.partition_limits = self._load_partition_limits(limits_path)

    @staticmethod
    def _load_partition_limits(path: str) -> dict[str, dict]:
        """Load per-partition resource limits from a JSON profile.

        Args:
            path: Path to the partition limits JSON file.

        Returns:
            Dict mapping partition names to their resource limits.
            Empty dict on any failure.
        """
        try:
            with open(path) as f:
                data = json.load(f)
            partitions = data.get("partitions", {})
            if not isinstance(partitions, dict):
                logger.warning("Partition limits file has invalid 'partitions' field: %s", path)
                return {}
            logger.info("Loaded partition limits for %d partitions from %s", len(partitions), path)
            return partitions
        except FileNotFoundError:
            logger.warning("Partition limits file not found: %s", path)
            return {}
        except json.JSONDecodeError as e:
            logger.warning("Malformed JSON in partition limits file %s: %s", path, e)
            return {}
        except OSError as e:
            logger.warning("Could not read partition limits file %s: %s", path, e)
            return {}

    def get_partition_limits(self, partition: str) -> Optional[dict]:
        """Get resource limits for a specific partition.

        Args:
            partition: Partition name.

        Returns:
            Dict with partition limits, or None if partition not found.
        """
        return self.partition_limits.get(partition)


def _run_cmd(
    cmd: list[str], timeout: int = 30, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def _parse_mem_gb(mem_str: str) -> float:
    """Parse memory string like '8G', '16384M', '1T' to GB."""
    mem_str = mem_str.strip().upper()
    if mem_str.endswith("T"):
        return float(mem_str[:-1]) * 1024
    if mem_str.endswith("G"):
        return float(mem_str[:-1])
    if mem_str.endswith("M"):
        return float(mem_str[:-1]) / 1024
    if mem_str.endswith("K"):
        return float(mem_str[:-1]) / (1024 * 1024)
    # Assume MB if no suffix
    return float(mem_str) / 1024


def _parse_time_hours(time_str: str) -> float:
    """Parse SLURM time string like '24:00:00', '2-12:00:00', '1:00' to hours."""
    time_str = time_str.strip()
    days = 0
    if "-" in time_str:
        day_part, time_str = time_str.split("-", 1)
        days = int(day_part)

    parts = time_str.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        hours, minutes, seconds = 0, int(parts[0]), int(parts[1])
    elif len(parts) == 1:
        hours, minutes, seconds = 0, int(parts[0]), 0
    else:
        raise ValueError(f"Cannot parse time: {time_str}")

    return days * 24 + hours + minutes / 60 + seconds / 3600


def _validate_resources(
    config: SlurmConfig,
    ntasks_per_node: int,
    mem: str,
    time: str,
    partition: str,
    gpu: str,
) -> list[str]:
    """Validate requested resources against safety limits. Returns list of violations.

    Uses per-partition limits when available, falling back to global config limits.
    """
    violations = []

    # Look up per-partition limits if available
    part_limits = config.get_partition_limits(partition) if partition else None
    limit_source = f"partition '{partition}'" if part_limits else "global"

    # CPU check
    effective_max_cpus = part_limits["max_cpus_per_node"] if part_limits else config.max_cpus
    if ntasks_per_node > effective_max_cpus:
        violations.append(
            f"ntasks-per-node ({ntasks_per_node}) exceeds {limit_source} limit ({effective_max_cpus})"
        )

    # Memory check
    mem_gb = _parse_mem_gb(mem)
    effective_max_mem_gb = part_limits["max_mem_gb"] if part_limits else config.max_mem_gb
    if mem_gb > effective_max_mem_gb:
        violations.append(
            f"Memory ({mem} = {mem_gb:.1f}G) exceeds {limit_source} limit ({effective_max_mem_gb}G)"
        )

    # Time check
    time_hours = _parse_time_hours(time)
    effective_max_time = part_limits["time_limit_hours"] if part_limits else config.max_time_hours
    if time_hours > effective_max_time:
        violations.append(
            f"Time ({time} = {time_hours:.1f}h) exceeds {limit_source} limit ({effective_max_time}h)"
        )

    # GPU check
    if gpu:
        gpu_match = re.search(r"(\d+)", gpu)
        n_gpus = int(gpu_match.group(1)) if gpu_match else 1

        if part_limits:
            # Check if partition has GPU nodes at all
            if not part_limits.get("has_gpu_nodes", False):
                violations.append(
                    f"GPUs requested ({n_gpus}) but partition '{partition}' has no GPU nodes"
                )
            else:
                effective_max_gpus = part_limits.get("max_gpus_per_node", config.max_gpus)
                if n_gpus > effective_max_gpus:
                    violations.append(
                        f"GPUs ({n_gpus}) exceeds {limit_source} limit ({effective_max_gpus})"
                    )
        else:
            if n_gpus > config.max_gpus:
                violations.append(
                    f"GPUs ({n_gpus}) exceeds {limit_source} limit ({config.max_gpus})"
                )

    # Partition allowlist check
    if partition and config.allowed_partitions:
        if partition not in config.allowed_partitions:
            violations.append(
                f"Partition '{partition}' not in allowed list: {config.allowed_partitions}"
            )

    return violations


def _audit_log(config: SlurmConfig, action: str, details: dict) -> None:
    """Append an entry to the audit log."""
    log_path = Path(config.audit_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "user": os.environ.get("USER", "unknown"),
        "action": action,
        **details,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ============================================================================
# SLURM operations
# ============================================================================


def submit_job(
    config: SlurmConfig,
    script: str = "",
    job_name: str = "mcp_job",
    nodes: int = 0,
    ntasks_per_node: int = 0,
    cpus: int = 0,
    mem: str = "",
    time: str = "",
    partition: str = "",
    gpu: str = "",
    dependency: str = "",
    wrap: str = "",
    extra_args: Optional[list[str]] = None,
    dry_run: bool = False,
    test_only: bool = False,
) -> dict:
    """Submit a job to SLURM via sbatch.

    Args:
        config: SLURM safety configuration.
        script: Path to the job script.
        job_name: Job name (--job-name).
        nodes: Number of nodes (--nodes). 0 = use default.
        ntasks_per_node: Tasks per node (--ntasks-per-node). 0 = use default.
        cpus: CPUs per task (--cpus-per-task). 0 = not set (use ntasks_per_node instead).
        mem: Memory (--mem). Empty = use default.
        time: Wall time (--time). Empty = use default.
        partition: Partition (--partition). Empty = use default.
        gpu: GPU spec (--gres=gpu:X). Empty = no GPU.
        dependency: Dependency spec (--dependency=...).
        wrap: Inline command (--wrap). Mutually exclusive with script.
        extra_args: Additional sbatch arguments.
        dry_run: If True, return the command without executing.
        test_only: If True, run sbatch --test-only to validate with the scheduler without submitting.

    Returns:
        Dict with job_id, job_name, command, and submit_time.
    """
    # Apply defaults
    nodes = nodes or config.default_nodes
    ntasks_per_node = ntasks_per_node or config.default_ntasks_per_node
    cpus = cpus or config.default_cpus_per_task
    mem = mem or config.default_mem
    time = time or config.default_time
    partition = partition or config.default_partition

    # Validate
    if not script and not wrap:
        raise ValueError("Either 'script' or 'wrap' must be provided")
    if script and wrap:
        raise ValueError("Provide either 'script' or 'wrap', not both")
    if script and not Path(script).is_file():
        raise FileNotFoundError(f"Script not found: {script}")

    violations = _validate_resources(config, ntasks_per_node, mem, time, partition, gpu)
    if violations:
        raise ValueError(
            f"Resource validation failed:\n" + "\n".join(f"  - {v}" for v in violations)
        )

    # Build log directory
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build sbatch command
    cmd = ["sbatch"]
    if config.default_account:
        cmd.extend(["--account", config.default_account])
    cmd.extend(["--job-name", job_name])
    cmd.extend(["--nodes", str(nodes)])
    cmd.extend(["--ntasks-per-node", str(ntasks_per_node)])
    cmd.extend(["--cpus-per-task", str(cpus)])
    cmd.extend(["--mem", mem])
    cmd.extend(["--time", time])
    cmd.extend(["--output", str(log_dir / f"{job_name}_%j.out")])
    cmd.extend(["--error", str(log_dir / f"{job_name}_%j.err")])

    if partition:
        cmd.extend(["--partition", partition])
    if gpu:
        cmd.extend([f"--gres=gpu:{gpu}"])
    if dependency:
        cmd.extend([f"--dependency={dependency}"])
    if extra_args:
        cmd.extend(extra_args)
    if wrap:
        cmd.extend(["--wrap", wrap])
    else:
        cmd.append(script)

    if dry_run:
        return {
            "dry_run": True,
            "command": " ".join(cmd),
            "job_name": job_name,
            "resources": {
                "nodes": nodes,
                "ntasks_per_node": ntasks_per_node,
                "cpus_per_task": cpus,
                "mem": mem,
                "time": time,
                "partition": partition,
                "gpu": gpu,
            },
        }

    # Test-only: ask SLURM to validate without actually submitting
    if test_only:
        test_cmd = cmd.copy()
        # Insert --test-only right after 'sbatch'
        test_cmd.insert(1, "--test-only")
        result = _run_cmd(test_cmd, timeout=30, check=False)
        feasible = result.returncode == 0
        output = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
        return {
            "test_only": True,
            "feasible": feasible,
            "command": " ".join(test_cmd),
            "slurm_response": output,
            "job_name": job_name,
            "resources": {
                "nodes": nodes,
                "ntasks_per_node": ntasks_per_node,
                "cpus_per_task": cpus,
                "mem": mem,
                "time": time,
                "partition": partition,
                "gpu": gpu,
            },
        }

    # Submit
    result = _run_cmd(cmd, timeout=30)
    stdout = result.stdout.strip()

    # Parse job ID from "Submitted batch job 12345"
    match = re.search(r"Submitted batch job (\d+)", stdout)
    if not match:
        raise RuntimeError(f"Could not parse job ID from sbatch output: {stdout}")

    job_id = match.group(1)

    _audit_log(config, "submit", {
        "job_id": job_id,
        "job_name": job_name,
        "command": " ".join(cmd),
        "script": script or f"--wrap={wrap}",
    })

    return {
        "job_id": job_id,
        "job_name": job_name,
        "command": " ".join(cmd),
        "submit_time": datetime.now().isoformat(),
        "log_stdout": str(log_dir / f"{job_name}_{job_id}.out"),
        "log_stderr": str(log_dir / f"{job_name}_{job_id}.err"),
    }


def job_status(job_ids: list[str]) -> list[dict]:
    """Check status of one or more jobs via squeue and sacct.

    Args:
        job_ids: List of SLURM job IDs.

    Returns:
        List of dicts with job_id, name, state, elapsed, node, exit_code.
    """
    if not job_ids:
        return []

    jobs_str = ",".join(job_ids)

    # Try squeue first (for running/pending jobs)
    squeue_fmt = "%i|%j|%T|%P|%M|%l|%R|%D|%C"
    result = _run_cmd(
        ["squeue", "-j", jobs_str, f"--format={squeue_fmt}", "--noheader"],
        check=False,
    )

    active_jobs = {}
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("|")
            if len(parts) >= 9:
                active_jobs[parts[0].strip()] = {
                    "job_id": parts[0].strip(),
                    "name": parts[1].strip(),
                    "state": parts[2].strip(),
                    "partition": parts[3].strip(),
                    "elapsed": parts[4].strip(),
                    "time_limit": parts[5].strip(),
                    "reason_or_node": parts[6].strip(),
                    "nodes": parts[7].strip(),
                    "cpus": parts[8].strip(),
                }

    # Use sacct for completed/failed jobs not in squeue
    missing = [jid for jid in job_ids if jid not in active_jobs]
    completed_jobs = {}
    if missing:
        sacct_fmt = "JobID,JobName,State,ExitCode,Elapsed,MaxRSS,CPUTime,NodeList"
        result = _run_cmd(
            [
                "sacct", "-j", ",".join(missing),
                f"--format={sacct_fmt}",
                "--noheader", "--parsable2",
            ],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                parts = line.strip().split("|")
                if len(parts) >= 8:
                    jid = parts[0].strip()
                    # Skip sub-steps like "12345.batch"
                    if "." in jid:
                        continue
                    completed_jobs[jid] = {
                        "job_id": jid,
                        "name": parts[1].strip(),
                        "state": parts[2].strip(),
                        "exit_code": parts[3].strip(),
                        "elapsed": parts[4].strip(),
                        "max_rss": parts[5].strip(),
                        "cpu_time": parts[6].strip(),
                        "node": parts[7].strip(),
                    }

    # Merge results in input order
    results = []
    for jid in job_ids:
        if jid in active_jobs:
            results.append(active_jobs[jid])
        elif jid in completed_jobs:
            results.append(completed_jobs[jid])
        else:
            results.append({"job_id": jid, "state": "UNKNOWN", "error": "Job not found"})

    return results


def list_jobs(state: str = "", user: str = "", limit: int = 25) -> dict:
    """List jobs for the current user via squeue.

    Args:
        state: Filter by state (RUNNING, PENDING, COMPLETED, etc.). Empty = all.
        user: Username. Empty = current user.
        limit: Maximum number of jobs to return. 0 = all.

    Returns:
        Dict with total_matching count and jobs list (capped at limit).
    """
    user = user or os.environ.get("USER", "")
    fmt = "%i|%j|%T|%P|%M|%l|%R|%D|%C"
    cmd = ["squeue", "-u", user, f"--format={fmt}", "--noheader"]
    if state:
        cmd.extend(["--states", state])

    result = _run_cmd(cmd, check=False)
    jobs = []
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("|")
            if len(parts) >= 9:
                jobs.append({
                    "job_id": parts[0].strip(),
                    "name": parts[1].strip(),
                    "state": parts[2].strip(),
                    "partition": parts[3].strip(),
                    "elapsed": parts[4].strip(),
                    "time_limit": parts[5].strip(),
                    "reason_or_node": parts[6].strip(),
                    "nodes": parts[7].strip(),
                    "cpus": parts[8].strip(),
                })

    total = len(jobs)
    if limit > 0:
        jobs = jobs[:limit]
    return {"total_matching": total, "shown": len(jobs), "jobs": jobs}


def job_logs(
    job_id: str,
    config: SlurmConfig,
    log_type: str = "both",
    tail_lines: int = 50,
) -> dict:
    """Read stdout/stderr log files for a job.

    Tries to find log files via scontrol, then falls back to the configured log_dir.

    Args:
        job_id: SLURM job ID.
        config: SLURM config (for log_dir).
        log_type: "stdout", "stderr", or "both".
        tail_lines: Number of lines to return from the end of each log.

    Returns:
        Dict with stdout and/or stderr content.
    """
    stdout_path = None
    stderr_path = None

    # Try scontrol to get exact paths
    result = _run_cmd(
        ["scontrol", "show", "job", job_id],
        check=False,
    )
    if result.returncode == 0:
        for match in re.finditer(r"StdOut=(\S+)", result.stdout):
            stdout_path = match.group(1)
        for match in re.finditer(r"StdErr=(\S+)", result.stdout):
            stderr_path = match.group(1)

    # Fallback: search log_dir for matching files
    log_dir = Path(config.log_dir)
    if not stdout_path:
        candidates = sorted(log_dir.glob(f"*_{job_id}.out"))
        stdout_path = str(candidates[0]) if candidates else None
    if not stderr_path:
        candidates = sorted(log_dir.glob(f"*_{job_id}.err"))
        stderr_path = str(candidates[0]) if candidates else None

    output = {}
    MAX_LOG_CHARS = 8_000

    def _read_tail(filepath: Optional[str], n: int) -> str:
        if not filepath or not Path(filepath).is_file():
            return f"[Log file not found: {filepath}]"
        try:
            with open(filepath) as f:
                lines = f.readlines()
            content = "".join(lines[-n:] if n > 0 else lines)
            if len(content) > MAX_LOG_CHARS:
                total_chars = len(content)
                content = f"[TRUNCATED — showing last {MAX_LOG_CHARS} of {total_chars} chars]\n" + content[-MAX_LOG_CHARS:]
            return content
        except PermissionError:
            return f"[Permission denied: {filepath}]"

    if log_type in ("stdout", "both"):
        output["stdout_path"] = stdout_path or "not found"
        output["stdout"] = _read_tail(stdout_path, tail_lines)

    if log_type in ("stderr", "both"):
        output["stderr_path"] = stderr_path or "not found"
        output["stderr"] = _read_tail(stderr_path, tail_lines)

    return output


def cancel_job(job_id: str, config: SlurmConfig) -> dict:
    """Cancel a job via scancel.

    Args:
        job_id: SLURM job ID.
        config: SLURM config (for audit log).

    Returns:
        Dict with cancelled status and message.
    """
    result = _run_cmd(["scancel", job_id], check=False)

    success = result.returncode == 0
    msg = "Job cancelled" if success else f"scancel failed: {result.stderr.strip()}"

    _audit_log(config, "cancel", {"job_id": job_id, "success": success, "message": msg})

    return {"job_id": job_id, "cancelled": success, "message": msg}


def job_resources(job_id: str) -> dict:
    """Get resource usage for a completed job via sacct.

    Args:
        job_id: SLURM job ID.

    Returns:
        Dict with max_rss, elapsed, cpu_time, exit_code, state.
    """
    fmt = "JobID,JobName,State,ExitCode,Elapsed,MaxRSS,MaxVMSize,CPUTime,AveRSS,NNodes,NCPUS,TotalCPU"
    result = _run_cmd(
        ["sacct", "-j", job_id, f"--format={fmt}", "--noheader", "--parsable2"],
        check=False,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return {"job_id": job_id, "error": "No accounting data found"}

    # Parse the main job entry (skip .batch, .extern sub-steps)
    main_entry = None
    batch_entry = None
    for line in result.stdout.strip().split("\n"):
        parts = line.strip().split("|")
        if len(parts) < 12:
            continue
        jid = parts[0].strip()
        if jid == job_id:
            main_entry = parts
        elif jid == f"{job_id}.batch":
            batch_entry = parts

    if not main_entry:
        return {"job_id": job_id, "error": "Job ID not found in sacct output"}

    # MaxRSS is usually in the .batch step, not the main entry
    max_rss = main_entry[5].strip()
    if not max_rss and batch_entry:
        max_rss = batch_entry[5].strip()

    return {
        "job_id": job_id,
        "name": main_entry[1].strip(),
        "state": main_entry[2].strip(),
        "exit_code": main_entry[3].strip(),
        "elapsed": main_entry[4].strip(),
        "max_rss": max_rss,
        "max_vmsize": main_entry[6].strip(),
        "cpu_time": main_entry[7].strip(),
        "nodes": main_entry[9].strip(),
        "cpus": main_entry[10].strip(),
        "total_cpu": main_entry[11].strip(),
    }


def queue_info(partition: str = "") -> list[dict]:
    """Show partition/queue information via sinfo.

    Args:
        partition: Specific partition name. Empty = all partitions.

    Returns:
        List of partition info dicts.
    """
    fmt = "%P|%a|%l|%D|%T|%c|%m"
    cmd = ["sinfo", f"--format={fmt}", "--noheader"]
    if partition:
        cmd.extend(["--partition", partition])

    result = _run_cmd(cmd, check=False)
    partitions = []

    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split("|")
            if len(parts) >= 7:
                partitions.append({
                    "partition": parts[0].strip().rstrip("*"),
                    "default": parts[0].strip().endswith("*"),
                    "available": parts[1].strip(),
                    "time_limit": parts[2].strip(),
                    "nodes": parts[3].strip(),
                    "state": parts[4].strip(),
                    "cpus_per_node": parts[5].strip(),
                    "memory_mb": parts[6].strip(),
                })

    return partitions


def node_info(
    partition: str = "",
    state: str = "",
    min_mem_free_gb: float = 0,
    min_cpus_free: int = 0,
    limit: int = 25,
) -> dict:
    """Show per-node resource availability via sinfo.

    Args:
        partition: Filter by partition name. Empty = all partitions.
        state: Filter by node state (idle, mixed, allocated). Empty = all.
        min_mem_free_gb: Only return nodes with at least this much free memory (GB).
        min_cpus_free: Only return nodes with at least this many free CPUs.
        limit: Maximum nodes to return (sorted by free_mem_gb desc). 0 = all.

    Returns:
        Dict with total_matching count and nodes list (capped at limit).
    """
    # %N=nodename, %P=partition, %e=free_mem_mb, %m=total_mem_mb, %C=cpus(A/I/O/T), %T=state, %O=load
    fmt = "%N|%P|%e|%m|%C|%T|%O"
    cmd = ["sinfo", "-N", f"--format={fmt}", "--noheader"]
    if partition:
        cmd.extend(["--partition", partition])

    result = _run_cmd(cmd, check=False)
    nodes = []

    if result.returncode != 0 or not result.stdout.strip():
        return {"total_matching": 0, "shown": 0, "nodes": []}

    seen = set()
    for line in result.stdout.strip().split("\n"):
        parts = line.strip().split("|")
        if len(parts) < 7:
            continue

        nodename = parts[0].strip()
        # sinfo -N can list a node multiple times (once per partition);
        # keep only the first occurrence to avoid duplicates
        if nodename in seen:
            continue
        seen.add(nodename)

        part_name = parts[1].strip().rstrip("*")
        free_mem_mb_str = parts[2].strip()
        total_mem_mb_str = parts[3].strip()
        cpus_str = parts[4].strip()  # format: Allocated/Idle/Other/Total
        node_state = parts[5].strip()
        load_str = parts[6].strip()

        # Parse free memory
        try:
            free_mem_mb = int(free_mem_mb_str)
        except ValueError:
            free_mem_mb = 0
        free_mem_gb = free_mem_mb / 1024

        # Parse total memory
        try:
            total_mem_mb = int(total_mem_mb_str)
        except ValueError:
            total_mem_mb = 0
        total_mem_gb = total_mem_mb / 1024

        # Parse CPU allocation (A/I/O/T)
        cpus_allocated = 0
        cpus_idle = 0
        cpus_total = 0
        cpu_parts = cpus_str.split("/")
        if len(cpu_parts) == 4:
            try:
                cpus_allocated = int(cpu_parts[0])
                cpus_idle = int(cpu_parts[1])
                cpus_total = int(cpu_parts[3])
            except ValueError:
                pass

        # Parse load
        try:
            load = float(load_str)
        except ValueError:
            load = 0.0

        # Apply filters
        if state and node_state.lower() != state.lower():
            continue
        if min_mem_free_gb > 0 and free_mem_gb < min_mem_free_gb:
            continue
        if min_cpus_free > 0 and cpus_idle < min_cpus_free:
            continue

        nodes.append({
            "node": nodename,
            "partition": part_name,
            "state": node_state,
            "free_mem_gb": round(free_mem_gb, 1),
            "total_mem_gb": round(total_mem_gb, 1),
            "cpus_allocated": cpus_allocated,
            "cpus_idle": cpus_idle,
            "cpus_total": cpus_total,
            "load": round(load, 2),
        })

    # Sort by free memory descending
    nodes.sort(key=lambda n: n["free_mem_gb"], reverse=True)
    total = len(nodes)
    if limit > 0:
        nodes = nodes[:limit]
    return {"total_matching": total, "shown": len(nodes), "nodes": nodes}
