"""Tests for SLURM MCP server — uses mocked subprocess calls (no real SLURM needed).

Author: Samuel Ahuno
Date: 2026-02-21
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from slurm_mcp.slurm_cli import (
    SlurmConfig,
    _parse_mem_gb,
    _parse_time_hours,
    _validate_resources,
    submit_job,
    job_status,
    list_jobs,
    job_logs,
    cancel_job,
    job_resources,
    queue_info,
    node_info,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def config(tmp_path):
    """Create a test SlurmConfig with temp directories."""
    return SlurmConfig(
        default_partition="test_queue",
        default_nodes=1,
        default_ntasks_per_node=1,
        default_cpus_per_task=8,
        default_mem="64G",
        default_time="4:00:00",
        log_dir=str(tmp_path / "logs"),
        audit_log=str(tmp_path / "logs" / "audit.jsonl"),
        max_cpus=32,
        max_mem_gb=128,
        max_time_hours=72,
        max_gpus=2,
        max_concurrent_jobs=20,
        allowed_partitions=["test_queue", "gpu_queue"],
    )


@pytest.fixture
def mock_script(tmp_path):
    """Create a temporary script file."""
    script = tmp_path / "test_job.sh"
    script.write_text("#!/bin/bash\necho hello\n")
    script.chmod(0o755)
    return str(script)


# ============================================================================
# Unit tests: parsing helpers
# ============================================================================


class TestParseMemGb:
    def test_gigabytes(self):
        assert _parse_mem_gb("8G") == 8.0

    def test_megabytes(self):
        assert _parse_mem_gb("16384M") == 16.0

    def test_terabytes(self):
        assert _parse_mem_gb("1T") == 1024.0

    def test_kilobytes(self):
        assert abs(_parse_mem_gb("1048576K") - 1.0) < 0.001

    def test_no_suffix_assumes_mb(self):
        assert abs(_parse_mem_gb("8192") - 8.0) < 0.001


class TestParseTimeHours:
    def test_hms(self):
        assert _parse_time_hours("24:00:00") == 24.0

    def test_days_hms(self):
        assert _parse_time_hours("2-12:00:00") == 60.0

    def test_minutes_seconds(self):
        assert _parse_time_hours("30:00") == 0.5

    def test_minutes_only(self):
        assert _parse_time_hours("60") == 1.0


# ============================================================================
# Unit tests: validation
# ============================================================================


class TestValidateResources:
    def test_valid_resources(self, config):
        violations = _validate_resources(config, ntasks_per_node=4, mem="16G", time="8:00:00", partition="test_queue", gpu="")
        assert violations == []

    def test_cpu_exceeded(self, config):
        violations = _validate_resources(config, ntasks_per_node=64, mem="8G", time="1:00:00", partition="test_queue", gpu="")
        assert any("ntasks-per-node" in v for v in violations)

    def test_mem_exceeded(self, config):
        violations = _validate_resources(config, ntasks_per_node=2, mem="256G", time="1:00:00", partition="test_queue", gpu="")
        assert any("Memory" in v for v in violations)

    def test_time_exceeded(self, config):
        violations = _validate_resources(config, ntasks_per_node=2, mem="8G", time="4-00:00:00", partition="test_queue", gpu="")
        assert any("Time" in v for v in violations)

    def test_gpu_exceeded(self, config):
        violations = _validate_resources(config, ntasks_per_node=2, mem="8G", time="1:00:00", partition="gpu_queue", gpu="4")
        assert any("GPUs" in v for v in violations)

    def test_disallowed_partition(self, config):
        violations = _validate_resources(config, ntasks_per_node=2, mem="8G", time="1:00:00", partition="forbidden", gpu="")
        assert any("Partition" in v for v in violations)

    def test_allowed_partition(self, config):
        violations = _validate_resources(config, ntasks_per_node=2, mem="8G", time="1:00:00", partition="gpu_queue", gpu="")
        assert violations == []

    def test_multiple_violations(self, config):
        violations = _validate_resources(config, ntasks_per_node=100, mem="500G", time="10-00:00:00", partition="forbidden", gpu="8")
        assert len(violations) >= 4


# ============================================================================
# Integration tests: submit_job
# ============================================================================


class TestSubmitJob:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_submit_script(self, mock_run, config, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Submitted batch job 12345\n", returncode=0
        )
        result = submit_job(config=config, script=mock_script, job_name="test_job")
        assert result["job_id"] == "12345"
        assert result["job_name"] == "test_job"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_submit_wrap(self, mock_run, config):
        mock_run.return_value = MagicMock(
            stdout="Submitted batch job 99999\n", returncode=0
        )
        result = submit_job(config=config, wrap="echo hello", job_name="wrap_job")
        assert result["job_id"] == "99999"

    def test_submit_no_script_no_wrap(self, config):
        with pytest.raises(ValueError, match="Either 'script' or 'wrap'"):
            submit_job(config=config, job_name="bad_job")

    def test_submit_both_script_and_wrap(self, config, mock_script):
        with pytest.raises(ValueError, match="not both"):
            submit_job(config=config, script=mock_script, wrap="echo hi", job_name="bad")

    def test_submit_missing_script(self, config):
        with pytest.raises(FileNotFoundError):
            submit_job(config=config, script="/nonexistent/script.sh", job_name="bad")

    def test_submit_resource_violation(self, config, mock_script):
        with pytest.raises(ValueError, match="Resource validation failed"):
            submit_job(config=config, script=mock_script, ntasks_per_node=999, job_name="too_big")

    def test_dry_run(self, config, mock_script):
        result = submit_job(config=config, script=mock_script, job_name="dry", dry_run=True)
        assert result["dry_run"] is True
        assert "sbatch" in result["command"]
        assert mock_script in result["command"]

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_test_only_feasible(self, mock_run, config, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Job 12345 to start at 2026-03-01T10:00:00 using 8 processors on node01 in partition test_queue\n",
            stderr="",
            returncode=0,
        )
        result = submit_job(config=config, script=mock_script, job_name="testonly", test_only=True)
        assert result["test_only"] is True
        assert result["feasible"] is True
        assert "--test-only" in result["command"]
        assert "job_id" not in result
        assert "12345" in result["slurm_response"]

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_test_only_infeasible(self, mock_run, config, mock_script):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="sbatch: error: Batch job submission failed: Requested node configuration is not available\n",
            returncode=1,
        )
        result = submit_job(config=config, script=mock_script, job_name="testonly_bad", test_only=True)
        assert result["test_only"] is True
        assert result["feasible"] is False
        assert "--test-only" in result["command"]
        assert "not available" in result["slurm_response"]

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_test_only_does_not_audit_log(self, mock_run, config, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Job 12345 to start at 2026-03-01T10:00:00\n",
            stderr="",
            returncode=0,
        )
        submit_job(config=config, script=mock_script, job_name="testonly_audit", test_only=True)
        audit_path = Path(config.audit_log)
        assert not audit_path.exists()

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_audit_log_written(self, mock_run, config, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Submitted batch job 11111\n", returncode=0
        )
        submit_job(config=config, script=mock_script, job_name="audit_test")
        audit_path = Path(config.audit_log)
        assert audit_path.exists()
        entries = audit_path.read_text().strip().split("\n")
        last = json.loads(entries[-1])
        assert last["action"] == "submit"
        assert last["job_id"] == "11111"


# ============================================================================
# Integration tests: job_status
# ============================================================================


class TestJobStatus:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_running_job(self, mock_run):
        # squeue returns the job, sacct not called
        mock_run.return_value = MagicMock(
            stdout="12345|my_job|RUNNING|seville|1:23:45|24:00:00|node01|1|4\n",
            returncode=0,
        )
        results = job_status(["12345"])
        assert len(results) == 1
        assert results[0]["state"] == "RUNNING"
        assert results[0]["name"] == "my_job"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_completed_job(self, mock_run):
        # squeue returns empty (job done), sacct returns data
        def side_effect(cmd, **kwargs):
            if "squeue" in cmd:
                return MagicMock(stdout="", returncode=0)
            elif "sacct" in cmd:
                return MagicMock(
                    stdout="12345|my_job|COMPLETED|0:0|2:30:00|4096K|8:00:00|node01\n",
                    returncode=0,
                )
            return MagicMock(stdout="", returncode=1)

        mock_run.side_effect = side_effect
        results = job_status(["12345"])
        assert results[0]["state"] == "COMPLETED"
        assert results[0]["exit_code"] == "0:0"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_unknown_job(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        results = job_status(["99999"])
        assert results[0]["state"] == "UNKNOWN"

    def test_empty_input(self):
        assert job_status([]) == []


# ============================================================================
# Integration tests: list_jobs
# ============================================================================


class TestListJobs:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_list_all(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "100|job_a|RUNNING|seville|0:30:00|4:00:00|node01|1|2\n"
                "101|job_b|PENDING|seville|0:00:00|8:00:00|(Priority)|1|4\n"
            ),
            returncode=0,
        )
        result = list_jobs()
        assert result["total_matching"] == 2
        assert result["jobs"][0]["state"] == "RUNNING"
        assert result["jobs"][1]["state"] == "PENDING"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_empty_queue(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = list_jobs()
        assert result["jobs"] == []


# ============================================================================
# Integration tests: job_logs
# ============================================================================


class TestJobLogs:
    def test_read_existing_logs(self, config, tmp_path):
        log_dir = Path(config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create mock log files
        (log_dir / "test_12345.out").write_text("line1\nline2\nline3\n")
        (log_dir / "test_12345.err").write_text("warning: something\n")

        with patch("slurm_mcp.slurm_cli._run_cmd") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=f"StdOut={log_dir}/test_12345.out\nStdErr={log_dir}/test_12345.err\n",
                returncode=0,
            )
            result = job_logs("12345", config=config)

        assert "line3" in result["stdout"]
        assert "warning" in result["stderr"]

    def test_missing_logs(self, config):
        with patch("slurm_mcp.slurm_cli._run_cmd") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=1)
            result = job_logs("99999", config=config)
        assert "not found" in result["stdout_path"] or "not found" in result["stdout"].lower()


# ============================================================================
# Integration tests: cancel_job
# ============================================================================


class TestCancelJob:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_cancel_success(self, mock_run, config):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        result = cancel_job("12345", config=config)
        assert result["cancelled"] is True

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_cancel_failure(self, mock_run, config):
        mock_run.return_value = MagicMock(
            stdout="", stderr="scancel: error: Invalid job id 99999", returncode=1
        )
        result = cancel_job("99999", config=config)
        assert result["cancelled"] is False


# ============================================================================
# Integration tests: job_resources
# ============================================================================


class TestJobResources:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_completed_job(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "12345|my_job|COMPLETED|0:0|1:30:00|2048K|4096K|3:00:00|2048K|1|4|2:45:00\n"
                "12345.batch|batch|COMPLETED|0:0|1:30:00|2048K|4096K|1:30:00|2048K|1|4|1:28:00\n"
            ),
            returncode=0,
        )
        result = job_resources("12345")
        assert result["state"] == "COMPLETED"
        assert result["exit_code"] == "0:0"
        assert result["elapsed"] == "1:30:00"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_job_not_found(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=1)
        result = job_resources("99999")
        assert "error" in result


# ============================================================================
# Integration tests: queue_info
# ============================================================================


class TestQueueInfo:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_all_partitions(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "seville*|up|7-00:00:00|10|mixed|32|256000\n"
                "gpu_queue|up|3-00:00:00|4|idle|16|128000\n"
            ),
            returncode=0,
        )
        partitions = queue_info()
        assert len(partitions) == 2
        assert partitions[0]["partition"] == "seville"
        assert partitions[0]["default"] is True
        assert partitions[1]["partition"] == "gpu_queue"
        assert partitions[1]["default"] is False

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_empty_info(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        assert queue_info() == []


# ============================================================================
# Integration tests: node_info
# ============================================================================


class TestNodeInfo:
    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_all_nodes(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|test_queue|512000|1031268|8/48/0/56|mixed|12.50\n"
                "node02|test_queue|1031268|1031268|0/56/0/56|idle|0.01\n"
                "node03|test_queue|128000|1031268|48/8/0/56|allocated|55.00\n"
            ),
            returncode=0,
        )
        result = node_info()
        assert result["total_matching"] == 3
        # Sorted by free_mem_gb descending
        assert result["nodes"][0]["node"] == "node02"
        assert result["nodes"][0]["state"] == "idle"
        assert result["nodes"][0]["free_mem_gb"] == 1007.1
        assert result["nodes"][1]["node"] == "node01"
        assert result["nodes"][2]["node"] == "node03"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_filter_min_mem(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|test_queue|65536|1031268|8/48/0/56|mixed|12.50\n"
                "node02|test_queue|32768|1031268|40/16/0/56|mixed|40.00\n"
            ),
            returncode=0,
        )
        result = node_info(min_mem_free_gb=64)
        # node02 has 32GB free, should be filtered out
        assert result["total_matching"] == 1
        assert result["nodes"][0]["node"] == "node01"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_filter_min_cpus(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|test_queue|512000|1031268|48/8/0/56|mixed|48.00\n"
                "node02|test_queue|512000|1031268|8/48/0/56|mixed|8.00\n"
            ),
            returncode=0,
        )
        result = node_info(min_cpus_free=16)
        # node01 has 8 idle CPUs, should be filtered out
        assert result["total_matching"] == 1
        assert result["nodes"][0]["node"] == "node02"
        assert result["nodes"][0]["cpus_idle"] == 48

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_filter_state(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|test_queue|512000|1031268|8/48/0/56|mixed|12.50\n"
                "node02|test_queue|1031268|1031268|0/56/0/56|idle|0.01\n"
            ),
            returncode=0,
        )
        result = node_info(state="idle")
        assert result["total_matching"] == 1
        assert result["nodes"][0]["node"] == "node02"

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = node_info()
        assert result["nodes"] == []

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_no_nodes_match(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="node01|test_queue|32768|1031268|48/8/0/56|allocated|48.00\n",
            returncode=0,
        )
        # Require 512GB free — node01 only has 32GB
        result = node_info(min_mem_free_gb=512)
        assert result["nodes"] == []

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_deduplicates_nodes(self, mock_run):
        # Same node listed in two partitions
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|part_a|512000|1031268|8/48/0/56|mixed|12.50\n"
                "node01|part_b|512000|1031268|8/48/0/56|mixed|12.50\n"
            ),
            returncode=0,
        )
        result = node_info()
        assert result["total_matching"] == 1

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_cpu_parsing(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="node01|test_queue|512000|1031268|16/32/8/56|mixed|16.00\n",
            returncode=0,
        )
        result = node_info()
        assert result["nodes"][0]["cpus_allocated"] == 16
        assert result["nodes"][0]["cpus_idle"] == 32
        assert result["nodes"][0]["cpus_total"] == 56


# ============================================================================
# MCP server tool tests (JSON round-trip)
# ============================================================================


class TestMCPToolRoundTrip:
    """Test the MCP tool functions return valid JSON."""

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_submit_job_json(self, mock_run, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Submitted batch job 55555\n", returncode=0
        )
        from slurm_mcp.server import slurm_submit_job
        result = json.loads(slurm_submit_job(script=mock_script, job_name="json_test"))
        assert "job_id" in result or "error" in result

    def test_slurm_submit_job_dry_run_json(self, mock_script):
        from slurm_mcp.server import slurm_submit_job
        result = json.loads(slurm_submit_job(script=mock_script, job_name="dry", dry_run=True))
        assert result["dry_run"] is True

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_submit_job_test_only_json(self, mock_run, mock_script):
        mock_run.return_value = MagicMock(
            stdout="Job 12345 to start at 2026-03-01T10:00:00 using 8 processors on node01\n",
            stderr="",
            returncode=0,
        )
        from slurm_mcp.server import slurm_submit_job
        result = json.loads(slurm_submit_job(script=mock_script, job_name="testonly_mcp", test_only=True))
        assert result["test_only"] is True
        assert result["feasible"] is True

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_job_status_json(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="12345|test|RUNNING|seville|0:10:00|4:00:00|node01|1|2\n",
            returncode=0,
        )
        from slurm_mcp.server import slurm_job_status
        result = json.loads(slurm_job_status("12345"))
        assert isinstance(result, list)

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_list_jobs_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        from slurm_mcp.server import slurm_list_jobs
        result = json.loads(slurm_list_jobs())
        assert "jobs" in result

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_queue_info_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        from slurm_mcp.server import slurm_queue_info
        result = json.loads(slurm_queue_info())
        assert "partitions" in result

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_node_info_json(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="node01|test_queue|512000|1031268|8/48/0/56|mixed|12.50\n",
            returncode=0,
        )
        from slurm_mcp.server import slurm_node_info
        result = json.loads(slurm_node_info())
        assert "nodes" in result
        assert result["total_matching"] == 1

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_node_info_empty_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        from slurm_mcp.server import slurm_node_info
        result = json.loads(slurm_node_info())
        assert "nodes" in result
        assert result["nodes"] == []

    @patch("slurm_mcp.slurm_cli._run_cmd")
    def test_slurm_node_info_with_filters_json(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "node01|test_queue|65536|1031268|8/48/0/56|mixed|12.50\n"
                "node02|test_queue|32768|1031268|40/16/0/56|mixed|40.00\n"
            ),
            returncode=0,
        )
        from slurm_mcp.server import slurm_node_info
        result = json.loads(slurm_node_info(partition="test_queue", min_mem_free_gb=64))
        assert result["total_matching"] == 1
        assert result["nodes"][0]["node"] == "node01"


# ============================================================================
# Partition limits: loading tests
# ============================================================================


SAMPLE_PARTITION_LIMITS = {
    "generated_at": "2026-02-28T19:31:00",
    "generated_by": "slurm-mcp setup.sh",
    "partitions": {
        "componc_cpu": {
            "time_limit_hours": 168,
            "max_cpus_per_node": 64,
            "max_mem_mb": 1031268,
            "max_mem_gb": 1007,
            "total_nodes": 62,
            "gpu_types": {"a100": {"max_per_node": 4}},
            "max_gpus_per_node": 4,
            "has_gpu_nodes": True,
            "has_cpu_only_nodes": True,
        },
        "cpushort": {
            "time_limit_hours": 2,
            "max_cpus_per_node": 56,
            "max_mem_mb": 515634,
            "max_mem_gb": 503,
            "total_nodes": 10,
            "gpu_types": {},
            "max_gpus_per_node": 0,
            "has_gpu_nodes": False,
            "has_cpu_only_nodes": True,
        },
        "gpu_big": {
            "time_limit_hours": 72,
            "max_cpus_per_node": 128,
            "max_mem_mb": 2063376,
            "max_mem_gb": 2015,
            "total_nodes": 4,
            "gpu_types": {"h200": {"max_per_node": 8}},
            "max_gpus_per_node": 8,
            "has_gpu_nodes": True,
            "has_cpu_only_nodes": False,
        },
    },
}


class TestPartitionLimitsLoading:
    def test_load_valid_json(self, tmp_path):
        """Load valid partition limits JSON → partition_limits populated."""
        limits_file = tmp_path / "limits.json"
        limits_file.write_text(json.dumps(SAMPLE_PARTITION_LIMITS))

        cfg = SlurmConfig(partition_limits=SlurmConfig._load_partition_limits(str(limits_file)))
        assert len(cfg.partition_limits) == 3
        assert "componc_cpu" in cfg.partition_limits
        assert cfg.partition_limits["componc_cpu"]["max_cpus_per_node"] == 64

    def test_load_missing_file(self, tmp_path):
        """Missing file → empty dict, no crash."""
        cfg = SlurmConfig(partition_limits=SlurmConfig._load_partition_limits(str(tmp_path / "nonexistent.json")))
        assert cfg.partition_limits == {}

    def test_load_malformed_json(self, tmp_path):
        """Malformed JSON → empty dict, no crash."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{this is not json!!!")

        cfg = SlurmConfig(partition_limits=SlurmConfig._load_partition_limits(str(bad_file)))
        assert cfg.partition_limits == {}

    def test_load_from_env_var(self, tmp_path):
        """SLURM_PARTITION_LIMITS env var → partition_limits loaded in __post_init__."""
        limits_file = tmp_path / "limits.json"
        limits_file.write_text(json.dumps(SAMPLE_PARTITION_LIMITS))

        with patch.dict(os.environ, {"SLURM_PARTITION_LIMITS": str(limits_file)}):
            cfg = SlurmConfig()
        assert len(cfg.partition_limits) == 3

    def test_no_env_var(self):
        """No SLURM_PARTITION_LIMITS env var → empty dict."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SLURM_PARTITION_LIMITS", None)
            cfg = SlurmConfig()
        assert cfg.partition_limits == {}

    def test_get_partition_limits_known(self, tmp_path):
        """get_partition_limits('known') → returns dict."""
        limits_file = tmp_path / "limits.json"
        limits_file.write_text(json.dumps(SAMPLE_PARTITION_LIMITS))

        cfg = SlurmConfig(partition_limits=SlurmConfig._load_partition_limits(str(limits_file)))
        result = cfg.get_partition_limits("componc_cpu")
        assert result is not None
        assert result["max_cpus_per_node"] == 64

    def test_get_partition_limits_unknown(self, tmp_path):
        """get_partition_limits('unknown') → returns None."""
        limits_file = tmp_path / "limits.json"
        limits_file.write_text(json.dumps(SAMPLE_PARTITION_LIMITS))

        cfg = SlurmConfig(partition_limits=SlurmConfig._load_partition_limits(str(limits_file)))
        assert cfg.get_partition_limits("nonexistent_partition") is None


# ============================================================================
# Partition limits: per-partition validation tests
# ============================================================================


@pytest.fixture
def config_with_partition_limits(tmp_path):
    """SlurmConfig with partition limits loaded."""
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(SAMPLE_PARTITION_LIMITS))
    return SlurmConfig(
        default_partition="componc_cpu",
        default_nodes=1,
        default_ntasks_per_node=1,
        default_cpus_per_task=8,
        default_mem="64G",
        default_time="4:00:00",
        log_dir=str(tmp_path / "logs"),
        audit_log=str(tmp_path / "logs" / "audit.jsonl"),
        max_cpus=32,
        max_mem_gb=128,
        max_time_hours=72,
        max_gpus=2,
        partition_limits=SlurmConfig._load_partition_limits(str(limits_file)),
    )


class TestPerPartitionValidation:
    def test_cpu_exceeds_partition_limit(self, config_with_partition_limits):
        """CPU valid globally but exceeds partition limit → violation mentions partition name."""
        # cpushort has max 56 CPUs; global is 32 — request 60
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=60,
            mem="8G",
            time="1:00:00",
            partition="cpushort",
            gpu="",
        )
        assert len(violations) >= 1
        assert any("cpushort" in v and "60" in v for v in violations)

    def test_memory_exceeds_partition_limit(self, config_with_partition_limits):
        """Memory exceeds partition limit → violation."""
        # cpushort max_mem_gb=503; request 600G
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=4,
            mem="600G",
            time="1:00:00",
            partition="cpushort",
            gpu="",
        )
        assert any("Memory" in v and "cpushort" in v for v in violations)

    def test_time_exceeds_partition_limit(self, config_with_partition_limits):
        """Time exceeds partition limit → violation."""
        # cpushort time_limit_hours=2; request 4h
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=4,
            mem="8G",
            time="4:00:00",
            partition="cpushort",
            gpu="",
        )
        assert any("Time" in v and "cpushort" in v for v in violations)

    def test_gpu_on_cpu_only_partition(self, config_with_partition_limits):
        """GPU requested on CPU-only partition → 'no GPU nodes' violation."""
        # cpushort has_gpu_nodes=False
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=4,
            mem="8G",
            time="1:00:00",
            partition="cpushort",
            gpu="1",
        )
        assert any("no GPU nodes" in v for v in violations)

    def test_unknown_partition_falls_back_to_global(self, config_with_partition_limits):
        """Unknown partition → falls back to global limits (no false violations)."""
        # Global max_cpus=32, request 30 → should pass
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=30,
            mem="64G",
            time="24:00:00",
            partition="nonexistent_part",
            gpu="",
        )
        assert violations == []

    def test_within_partition_limits_passes(self, config_with_partition_limits):
        """Request within partition limits → empty violations list."""
        # componc_cpu: 64 CPUs, 1007G mem, 168h time
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=32,
            mem="500G",
            time="48:00:00",
            partition="componc_cpu",
            gpu="",
        )
        assert violations == []

    def test_large_gpu_within_partition_limit(self, config_with_partition_limits):
        """Large GPU request within GPU partition limit → passes."""
        # gpu_big: max_gpus_per_node=8
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=32,
            mem="500G",
            time="24:00:00",
            partition="gpu_big",
            gpu="8",
        )
        assert violations == []

    def test_gpu_count_exceeds_partition_limit(self, config_with_partition_limits):
        """GPU count exceeds partition GPU limit → violation."""
        # componc_cpu: max_gpus_per_node=4; request 6
        violations = _validate_resources(
            config_with_partition_limits,
            ntasks_per_node=8,
            mem="64G",
            time="4:00:00",
            partition="componc_cpu",
            gpu="6",
        )
        assert any("GPUs" in v and "componc_cpu" in v for v in violations)
