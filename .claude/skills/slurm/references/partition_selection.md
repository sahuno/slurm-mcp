# Partition Selection — Matching Algorithm & Anti-Patterns

## Inputs

Before selecting, gather:

1. **Task requirements** (from Step 1 classification):
   - `cpus` — integer
   - `mem_gb` — float
   - `time_hours` — float
   - `needs_gpu` — bool
   - `gpu_count` — int (if GPU needed)

2. **Cluster state** (from MCP):
   - Read `slurm://partition-limits` for hard constraints
   - Read `slurm://partitions` for real-time availability (optional, used
     for tie-breaking)

## Algorithm

```
1. Filter by feasibility:
     partition.max_time_hours >= task.time_hours
     AND partition.max_mem_gb  >= task.mem_gb
     AND partition.max_cpus    >= task.cpus
     AND (task.needs_gpu implies partition.has_gpu_nodes)

2. If zero partitions pass: STOP.
     Report which constraint knocked out the last candidate.
     Ask user to either reduce the request or request access to a
     bigger partition.

3. For each surviving partition, compute tightness-of-fit score:
     tf_mem  = task.mem_gb       / partition.max_mem_gb
     tf_time = task.time_hours   / partition.max_time_hours
     tf_cpu  = task.cpus         / partition.max_cpus
     tightness = max(tf_mem, tf_time, tf_cpu)

4. Prefer partitions where 0.25 <= tightness <= 0.75.
     Below 0.25 = wasteful (tiny job on a huge partition).
     Above 0.75 = no headroom for spikes.
     Inside the band = "tightest fit wins".

5. Tiebreak by partition size (more total_nodes usually = shorter queue).

6. Final output: recommended partition + one-line reasoning.
     Example: "cpushort — 2h limit fits 45-min job with 2× margin,
              503G max absorbs 48G request easily."
```

## Worked Example

Task: STAR alignment, one sample, 30 min expected, 50 GB, 16 CPUs, no GPU.

Suppose `slurm://partition-limits` returns:

| Partition | max_time_h | max_mem_gb | max_cpus | has_gpu |
|-----------|------------|------------|----------|---------|
| cpushort | 2 | 503 | 64 | false |
| componc_cpu | 168 | 1007 | 128 | false |
| gpu_a100 | 72 | 256 | 48 | true |
| gpu_h100 | 48 | 503 | 64 | true |

Apply filter (feasibility):
- cpushort: 2h ≥ 1h (2× margin), 503G ≥ 50G, 64 ≥ 16, no GPU needed → PASS
- componc_cpu: 168h ≥ 1h, 1007G ≥ 50G, 128 ≥ 16 → PASS
- gpu_a100, gpu_h100: PASS (GPU not needed but allowed)

Tightness:
- cpushort: max(50/503, 1/2, 16/64) = max(0.099, 0.5, 0.25) = **0.5** ✓
- componc_cpu: max(50/1007, 1/168, 16/128) = max(0.050, 0.006, 0.125) = **0.125** ✗ too loose
- gpu_* : similar to componc_cpu, wasteful

**Pick: cpushort.** Reasoning: fits within time limit with 2× margin,
tightest resource fit (0.5 on time), doesn't waste GPU nodes.

## Anti-Patterns (flag these explicitly)

| Anti-pattern | Why | Instead |
|--------------|-----|---------|
| 30-min job on 7-day partition | Wastes scheduling priority; blocks longer jobs | Use shortest partition that fits |
| GPU partition for CPU-only work | GPU nodes are scarce; CPU quota is separate | Use a CPU partition |
| 1 TB on a 503G-max partition | Silent `sbatch` rejection or `PartitionConfig` pending state | Pick a partition whose max ≥ request |
| 8h on a 2h-max partition | Same: rejection | Pick a longer partition |
| Default partition without checking | User's default may not fit the task | Always read `slurm://partition-limits` |
| Requesting GPUs when tool is CPU-only | Wastes GPU quota | Check `references/resource_profiles.md` GPU list |
| 10× memory "just in case" | Queue wait time grows with request size | 2× safety margin max (see time safety in profiles) |

## Per-Cluster Defaults (MSKCC Iris)

For Samuel's home cluster (MSKCC), the typical mapping is:

| Task shape | Preferred partition |
|------------|---------------------|
| Q1/Q3 fast (< 2h, < 500G) | `cpushort` |
| Q2 long CPU (< 168h, < 500G) | `componc_cpu` |
| Q4 huge memory (> 500G) | Whichever partition has the highest memory nodes per `slurm://partition-limits` |
| GPU (dorado, AlphaFold, MD) | The GPU partition with the longest time limit that fits |

**These are hints, not rules.** Always re-check `slurm://partition-limits`
because cluster config drifts. The skill must not hardcode partition names.

## Falling Back When No Perfect Fit Exists

If step 4 produces no partition in the `0.25–0.75` band:

- All candidates are too loose (task too small for any partition): pick the
  tightest (smallest tightness value that's still a feasible partition), note
  the wastefulness to the user, proceed.
- All candidates are too tight (task near partition max): pick the one with
  the most headroom (lowest tightness), explicitly warn the user that the
  job is near the partition limit and may get killed if memory/time creeps.
- No candidate is feasible at all: explained in algorithm step 2.

## Edge Cases

- **User specifies a partition explicitly**: respect it, but still validate
  (Step 4 of the main workflow). If the partition doesn't fit, tell them
  which constraint is violated and what a better choice would be — don't
  silently submit to a partition that will reject the job.
- **GPU type matters**: some partitions have multiple GPU types (A100 vs
  H100). For dorado, H100 ≥ A100 ≥ V100 ≥ older. For AlphaFold, A100 is
  sufficient. Check `slurm://partition-limits` for `gpu_types` if present.
- **Array jobs**: the `--time` and `--mem` apply *per array element*, not
  to the whole array. Compute per-element resources first, then match to
  partition.