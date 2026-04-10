"""SLURM MCP Server — submit, monitor, and manage SLURM jobs from Claude Code.

Author: Samuel Ahuno
Date: 2026-02-21
Purpose: MCP server exposing SLURM operations as tools for Claude Code integration.

Tools:
    slurm_submit_job    — Submit a script or inline command to SLURM
    slurm_job_status    — Check status of one or more jobs
    slurm_list_jobs     — List all user's active/recent jobs
    slurm_job_logs      — Read stdout/stderr from a job
    slurm_cancel_job    — Cancel a running or pending job
    slurm_job_resources — Get resource usage for a completed job
    slurm_diagnose_job  — Diagnose why a job failed with explanation and fix suggestions
    slurm_queue_info    — Show partition/queue availability
    slurm_submit_batch  — Submit multiple scripts at once

Usage:
    python -m slurm_mcp.server          # stdio transport (for Claude Code)
    slurm-mcp                           # via entry point after pip install
"""

import json
import logging
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .slurm_cli import SlurmConfig, submit_job, job_status, list_jobs, job_logs
from .slurm_cli import cancel_job, job_resources, queue_info, node_info, diagnose_job

# All logging to stderr — stdout is reserved for MCP JSON-RPC protocol
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Initialize MCP server and SLURM config
mcp = FastMCP("slurm-mcp")
config = SlurmConfig()


# ============================================================================
# Tool 1: Submit a job
# ============================================================================


@mcp.tool()
def slurm_submit_job(
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
    extra_args: str = "",
    dry_run: bool = False,
    test_only: bool = False,
) -> str:
    """Submit a job script or inline command to SLURM via sbatch.

    Provide script (path to .sh) OR wrap (inline bash command), not both.
    gpu: number as string e.g. '1'. dependency: e.g. 'afterok:12345'.
    dry_run=True returns the sbatch command without submitting.
    test_only=True runs sbatch --test-only to validate with the SLURM scheduler without submitting.
    """
    extra = extra_args.split() if extra_args else None

    try:
        result = submit_job(
            config=config,
            script=script,
            job_name=job_name,
            nodes=nodes,
            ntasks_per_node=ntasks_per_node,
            cpus=cpus,
            mem=mem,
            time=time,
            partition=partition,
            gpu=gpu,
            dependency=dependency,
            wrap=wrap,
            extra_args=extra,
            dry_run=dry_run,
            test_only=test_only,
        )
        logger.info("Submitted job: %s", result.get("job_id", "dry-run"))
        return json.dumps(result)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        logger.error("Submit failed: %s", e)
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 2: Check job status
# ============================================================================


@mcp.tool()
def slurm_job_status(job_ids: str) -> str:
    """Check status of one or more SLURM jobs. job_ids: comma-separated (e.g. '12345,12346')."""
    ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    if not ids:
        return json.dumps({"error": "No job IDs provided"})

    try:
        results = job_status(ids)
        return json.dumps(results)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 3: List user's jobs
# ============================================================================


@mcp.tool()
def slurm_list_jobs(state: str = "", limit: int = 25) -> str:
    """List SLURM jobs for the current user. state: RUNNING|PENDING|COMPLETED|FAILED|empty=all. Returns total_matching + capped list."""
    try:
        result = list_jobs(state=state.upper() if state else "", limit=limit)
        if not result["jobs"]:
            return json.dumps({"message": "No jobs found" + (f" with state={state}" if state else ""), "jobs": []})
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 4: Read job logs
# ============================================================================


@mcp.tool()
def slurm_job_logs(
    job_id: str,
    log_type: str = "both",
    tail_lines: int = 50,
) -> str:
    """Read job log files. log_type: stdout|stderr|both. tail_lines=0 returns full file (hard-capped at 8000 chars)."""
    if log_type not in ("stdout", "stderr", "both"):
        return json.dumps({"error": f"Invalid log_type '{log_type}'. Use 'stdout', 'stderr', or 'both'"})

    try:
        result = job_logs(
            job_id=job_id,
            config=config,
            log_type=log_type,
            tail_lines=tail_lines,
        )
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 5: Cancel a job
# ============================================================================


@mcp.tool()
def slurm_cancel_job(job_id: str) -> str:
    """Cancel a running or pending SLURM job by job_id."""
    try:
        result = cancel_job(job_id=job_id, config=config)
        logger.info("Cancel job %s: %s", job_id, result["message"])
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 6: Job resource usage
# ============================================================================


@mcp.tool()
def slurm_job_resources(job_id: str) -> str:
    """Get resource usage (MaxRSS, CPU time, elapsed, exit_code) for a completed SLURM job."""
    try:
        result = job_resources(job_id=job_id)
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 7: Diagnose job failure
# ============================================================================


@mcp.tool()
def slurm_diagnose_job(job_id: str) -> str:
    """Diagnose why a SLURM job failed. Returns human-readable explanation, suggested fix, exit code analysis, and resource usage comparison (requested vs actual)."""
    try:
        result = diagnose_job(job_id=job_id, config=config)
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Resource 1: Partition / queue info (read-only, cacheable)
# ============================================================================


@mcp.resource("slurm://partitions")
def slurm_partitions_resource() -> str:
    """All SLURM partitions with availability, node counts, and resource limits."""
    try:
        partitions = queue_info(partition="")
        return json.dumps({"count": len(partitions), "partitions": partitions})
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


@mcp.resource("slurm://partition-limits")
def slurm_partition_limits_resource() -> str:
    """Per-partition resource limits (max CPUs, memory, time, GPUs) loaded from the partition profile."""
    if not config.partition_limits:
        return json.dumps({"message": "No partition limits loaded. Run setup.sh to generate the partition profile.", "partitions": {}})
    return json.dumps({"count": len(config.partition_limits), "partitions": config.partition_limits})


# Tool kept for querying a specific partition by name
@mcp.tool()
def slurm_queue_info(partition: str = "") -> str:
    """Show SLURM partition availability. Leave partition empty for all. Use slurm://partitions resource instead when possible."""
    try:
        partitions = queue_info(partition=partition)
        if not partitions:
            return json.dumps({"message": "No partition information available", "partitions": []})
        return json.dumps({"count": len(partitions), "partitions": partitions})
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 8: Per-node resource availability
# ============================================================================


@mcp.tool()
def slurm_node_info(
    partition: str = "",
    state: str = "",
    min_mem_free_gb: float = 0,
    min_cpus_free: int = 0,
    limit: int = 25,
) -> str:
    """Show per-node free memory, idle CPUs, and load. Results sorted by free_mem_gb desc, capped at limit."""
    try:
        result = node_info(
            partition=partition,
            state=state,
            min_mem_free_gb=min_mem_free_gb,
            min_cpus_free=min_cpus_free,
            limit=limit,
        )
        if not result["nodes"]:
            return json.dumps({
                "message": "No nodes match the specified criteria",
                "nodes": [],
                "filters": {
                    "partition": partition or "(all)",
                    "state": state or "(all)",
                    "min_mem_free_gb": min_mem_free_gb,
                    "min_cpus_free": min_cpus_free,
                },
            })
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# ============================================================================
# Tool 9: Submit multiple scripts
# ============================================================================


@mcp.tool()
def slurm_submit_batch(
    scripts: str,
    chain: bool = False,
    job_prefix: str = "batch",
    nodes: int = 0,
    ntasks_per_node: int = 0,
    cpus: int = 0,
    mem: str = "",
    time: str = "",
    partition: str = "",
    dry_run: bool = False,
    test_only: bool = False,
) -> str:
    """Submit multiple scripts at once. chain=True links them as afterok dependencies in order. test_only=True validates with SLURM without submitting."""
    script_list = [s.strip() for s in scripts.split(",") if s.strip()]
    if not script_list:
        return json.dumps({"error": "No scripts provided"})

    results = []
    prev_job_id = None

    for i, script_path in enumerate(script_list):
        dep = f"afterok:{prev_job_id}" if chain and prev_job_id else ""
        name = f"{job_prefix}_{i+1:02d}"

        try:
            result = submit_job(
                config=config,
                script=script_path,
                job_name=name,
                nodes=nodes,
                ntasks_per_node=ntasks_per_node,
                cpus=cpus,
                mem=mem,
                time=time,
                partition=partition,
                dependency=dep,
                dry_run=dry_run,
                test_only=test_only,
            )
            result["script"] = script_path
            result["step"] = i + 1
            if dep:
                result["depends_on"] = prev_job_id
            results.append(result)

            if not dry_run:
                prev_job_id = result.get("job_id")
                logger.info("Submitted batch step %d: job %s (%s)", i + 1, prev_job_id, script_path)

        except (ValueError, FileNotFoundError, RuntimeError) as e:
            results.append({
                "script": script_path,
                "step": i + 1,
                "error": str(e),
            })
            if chain:
                # Stop chain on failure
                break

    return json.dumps({"submitted": len([r for r in results if "error" not in r]), "total": len(script_list), "jobs": results})


# ============================================================================
# Entry point
# ============================================================================


def main() -> None:
    """Run the SLURM MCP server with stdio transport."""
    logger.info("Starting SLURM MCP server")
    logger.info("Config: partition=%s, nodes=%d, ntasks_per_node=%d, mem=%s, time=%s",
                config.default_partition or "(system default)",
                config.default_nodes, config.default_ntasks_per_node,
                config.default_mem, config.default_time)
    logger.info("Limits: max_cpus=%d, max_mem=%dG, max_time=%dh, max_gpus=%d",
                config.max_cpus, config.max_mem_gb, config.max_time_hours, config.max_gpus)
    if config.partition_limits:
        logger.info("Partition-aware limits loaded for %d partitions", len(config.partition_limits))
    logger.info("Log dir: %s", config.log_dir)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
