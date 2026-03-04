# SLURM MCP Competitive Landscape

**Date**: 2026-03-04
**Purpose**: Survey of existing SLURM MCP servers and adjacent HPC scheduler
projects, with differentiation analysis for slurm-mcp.

---

## 1. Direct SLURM MCP Servers

### SLURM-MCP-Server (Zanetach)
- **GitHub**: https://github.com/Zanetach/SLURM-MCP-Server
- **Stars**: 2
- **Language**: Python
- **Status**: Maintained (last activity June 2025)
- **Description**: FastMCP-based SLURM task management via HTTP/SSE transport
- **Tools (7)**: `get_slurm_jobs`, `query_slurm_job`, `submit_slurm_job`,
  `cancel_slurm_job`, `get_slurm_job_history`, `get_slurm_nodes_info`,
  `get_slurm_partitions_info`
- **Notable**: HTTP POST with JSON payloads (requires running a separate server
  process, not native Claude Code stdio)

### mcp-slurm (alchemicduncan)
- **GitHub**: https://github.com/alchemicduncan/mcp-slurm
- **Stars**: 0
- **Language**: Not specified
- **Status**: Early-stage (1 commit, Oct 2025)
- **Description**: "MCP server for managing a Slurm cluster and managing jobs"
- **Notable**: Minimal documentation, early alpha

### mcp-server (jalzoubi)
- **GitHub**: https://github.com/jalzoubi/mcp-server
- **Stars**: 0
- **Language**: Python
- **Status**: April 2025
- **Description**: Mock SLURM job submission + HDF5 data handling
- **Notable**: Simulator — UUID-based job IDs, not connected to real SLURM.
  Useful for dev/testing without cluster access.

---

## 2. Jupyter + SLURM MCP Servers

### jlab-mcp (kdkyum)
- **GitHub**: https://github.com/kdkyum/jlab-mcp
- **Stars**: 0
- **Language**: Python (98.5%), Shell (1.5%)
- **Status**: Active (47 commits)
- **Description**: Execute Python on GPU nodes via JupyterLab on SLURM clusters
- **Tools (10)**: `execute_code`, `start_notebook`, `check_resources`, `ping`,
  etc.
- **Notable**: Interactive GPU compute, not batch job submission. One-command
  install with auto-detection. MIT license.

---

## 3. Multi-Server Gateways / Aggregate Platforms

### vMCP — Virtual MCP (JaimeCernuda / Stacklok)
- **GitHub**: https://github.com/JaimeCernuda/vmcp
- **Stars**: 1
- **Language**: Python
- **Status**: Maintained (13 commits)
- **Description**: Unified gateway for MCP servers, inspired by DXT Desktop
  Extensions
- **Notable**: Includes slurm-mcp as 1 of 14 pre-packaged extensions for
  scientific computing (alongside HDF5, ADIOS2, pandas, plot, lmod, etc.)

### agent-toolkit / CLIO Kit (IoWarp)
- **GitHub**: https://github.com/iowarp/agent-toolkit
- **URL**: https://mcpmarket.com/server/iowarp
- **Stars**: Not visible
- **Language**: Python 3.10+
- **Status**: Active (738 commits)
- **Description**: "Bringing AI practically to science" — 16 MCP servers for
  scientific computing
- **Notable**: One-command deploy (`uvx agent-toolkit slurm`), covers data I/O,
  HPC, visualization, research workflows. Broadest ecosystem.

### hpcgpt-cli (NCSA / Center for AI Innovation)
- **GitHub**: https://github.com/Center-for-AI-Innovation/hpcgpt-cli
- **Stars**: 3
- **Language**: JavaScript
- **Status**: Maintained (54 commits)
- **Description**: Agent CLI integrating MCP servers for SLURM-based HPC
- **Notable**: Multi-provider — SLURM + Jira + Confluence + institutional docs.
  Built for NCSA Delta/Delta AI supercomputer. Provides `accounts`, `sinfo`,
  `squeue`, `scontrol`.

---

## 4. SLURM Optimization via MCP

### SlurmSlim (JianYang-Lab)
- **GitHub**: https://github.com/JianYang-Lab/SlurmSlim
- **Stars**: 2
- **Language**: Python
- **Status**: Unmaintained (marked as such, March 2025)
- **Description**: "Optimize Slurm Job Scheduling with MCP-Based Memory
  Estimation"
- **Notable**: Uses LLM + MCP for intelligent memory prediction by analyzing
  script characteristics and input file sizes. Unique approach but no longer
  maintained.

---

## 5. Adjacent (Non-SLURM HPC)

No MCP servers found for PBS, SGE, or LSF schedulers.

Kubernetes MCP servers exist (containers/kubernetes-mcp-server,
alexei-led/k8s-mcp-server, Flux159/mcp-server-kubernetes) but serve container
orchestration, not HPC batch scheduling.

No SLURM MCP servers appear on major registries (mcpservers.org, smithery.ai,
mcp.so).

---

## 6. Feature Comparison Matrix

| Feature | slurm-mcp (ours) | Zanetach | jlab-mcp | IoWarp | hpcgpt-cli | SlurmSlim |
|---------|-------------------|----------|----------|--------|------------|-----------|
| Per-partition validation | JSON profile | Queries only | Configurable | Unknown | Queries only | Memory only |
| Resource safety limits | Global + per-partition | None | GPU-focused | Unknown | None | Memory est. |
| Audit log | JSONL | None | None | Unknown | None | None |
| Job arrays / batch | Chained deps | None | None | Unknown | None | None |
| Setup automation | Interactive setup.sh | None | One-command | `uvx` | Config file | None |
| `sbatch --test-only` | 3-tier validation | None | None | Unknown | None | None |
| Transport | stdio (Claude Code) | HTTP/SSE | stdio | stdio | stdio | N/A |
| Test suite | 74 tests (mocked) | None visible | Unknown | Unknown | Unknown | None |
| Node resource view | `slurm_node_info` | Yes | Yes | Unknown | `sinfo` | No |

---

## 7. slurm-mcp Differentiators

### Clear edges (no competitor has these)

1. **Partition-aware resource validation**: Generates a per-partition limits
   profile during setup (`~/.slurm-mcp/partition_limits_latest.json`) and
   validates every submission against actual max CPUs, memory, time, and GPUs
   for the target partition. Others either hardcode limits or skip validation.

2. **Three-tier validation flow**: `dry_run` (show command) → `test_only`
   (ask SLURM scheduler) → real submit. No other project exposes
   `sbatch --test-only` as a first-class feature.

3. **Audit trail**: JSONL audit log (`~/slurm_logs/audit.jsonl`) records every
   submit and cancel with timestamp, user, job ID, and full command. No
   competitor has this.

4. **Batch submission with dependency chaining**: `slurm_submit_batch` submits
   multiple scripts with optional `afterok` dependency chains. No competitor
   handles multi-script pipeline submission.

5. **Interactive setup with live validation**: `setup.sh` detects accounts
   (3-strategy fallback: sacctmgr → env var → previous config), validates
   account+partition via `sbatch --test-only`, generates partition limits,
   compares user defaults against real limits, offers auto-adjustment. No
   competitor does end-to-end setup validation.

6. **Comprehensive test suite**: 74 fully mocked tests covering parsing,
   validation, submission, status, logs, cancellation, resources, queue info,
   node info, batch submission, partition limits loading, and per-partition
   validation. Most competitors have zero visible tests.

### Competitive parity

- Core SLURM operations (submit, status, cancel, logs, queue info)
- Node-level resource visibility
- Environment variable configuration
- Python + FastMCP stack

### Gaps relative to competitors

- **jlab-mcp**: Interactive Jupyter execution on GPU nodes (we do batch, not
  interactive compute)
- **IoWarp/agent-toolkit**: Broader scientific computing ecosystem (HDF5,
  ADIOS2, visualization — 16 servers). We are SLURM-focused only.
- **SlurmSlim**: LLM-powered memory estimation from script analysis (smarter
  resource prediction based on what the script does, not just partition caps)
- **hpcgpt-cli**: Multi-provider integration (Jira, Confluence, institutional
  docs alongside SLURM — broader developer experience)

---

## 8. Market Observations

- The SLURM MCP space is early and fragmented. Most projects have 0-3 stars.
- No project dominates. The ecosystem is wide open.
- No PBS/SGE/LSF MCP servers exist — SLURM is the only HPC scheduler with
  MCP coverage.
- Major MCP registries (mcpservers.org, smithery.ai) do not prominently index
  any SLURM server — listing there would provide immediate visibility.
- The gateway projects (vMCP, IoWarp) are building ecosystems — being included
  in those distributions would expand reach without additional work.
