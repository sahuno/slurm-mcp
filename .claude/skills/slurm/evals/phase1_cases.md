# Phase 1 Eval Cases

Ground-truth scenarios for the Resource Advisor + Script Generator. Each
case specifies the user prompt, the expected classification, the expected
partition shape (not a specific partition name — partitions vary by
cluster), and the key script features that must appear.

A skill output passes a case if **all** "Must have" items are present and
**no** "Must NOT have" items appear. Partition-name mismatches are OK as
long as the shape (time limit, memory ceiling, GPU availability) matches.

---

## Case 1 — STAR alignment, multi-sample

**Prompt**: "Run STAR alignment on 20 RNA-seq samples, human hg38."

**Expected classification**: Q3 (High-mem, low-time)
**Expected per-sample resources**: 8-16 CPU, 40-50G mem, 20-60 min time, no GPU
**Expected partition shape**: short-time (< 4h) OR medium-time; ≥ 50G memory ceiling; no GPU

**Must have:**
- `--array=1-20` (job array, not 20 submissions)
- `--cpus-per-task` in range 8-16
- `--mem=40G` to `--mem=50G`
- `--time=` ≤ `02:00:00` (with per-sample margin)
- Sample list indirection via `$SLURM_ARRAY_TASK_ID`
- Completion marker `=== DONE: ... ===`
- Pre-submission `dry_run=True` or `test_only=True` call

**Must NOT have:**
- `--gres=gpu:*` (STAR is CPU-only)
- 20 individual `slurm_submit_job` calls
- `--time=7-00:00:00` or similar week-long request
- Memory request above 100G (STAR human index peaks around 32G, 50G is ample)

---

## Case 2 — Dorado basecalling

**Prompt**: "Basecall this pod5 directory with dorado, SUP model, with 5mCG_5hmCG and 6mA."

**Expected classification**: Q4 (High-mem, high-time, GPU-required)
**Expected resources**: 4-8 CPU, 16-32G mem, 1-4 GPU, 4-24h
**Expected partition shape**: GPU partition with ≥ 12h time limit

**Must have:**
- `--gres=gpu:N` where N ≥ 1
- `--time=` ≥ `04:00:00`
- Model string includes `5mCG_5hmCG` and `6mA`
- `nvidia-smi` diagnostic in the script
- Confirmation prompt or explicit note about matching dorado model to
  sequencing chemistry (4kHz vs 5kHz)
- `APPTAINER_CACHEDIR` env-var note if running via Apptainer (per CLAUDE.md §3A)

**Must NOT have:**
- CPU-only partition
- `--time=00:30:00` or similar short-time request
- Missing GPU allocation

---

## Case 3 — Week-long BEAST MCMC

**Prompt**: "Submit a BEAST phylogenetics run, should take about 5 days."

**Expected classification**: Q2 (Low-mem, high-time)
**Expected resources**: 1-2 CPU, 4-8G mem, 5-7 days
**Expected partition shape**: longest-time CPU partition available on the cluster

**Must have:**
- `--time=5-` prefix or similar multi-day format
- `--cpus-per-task=1` or `2`
- `--mem=` between 4G and 16G
- Partition selected from the longest-time tier (not `cpushort`)

**Must NOT have:**
- `--gres=gpu:*`
- High CPU request (> 4)
- High memory request (> 32G)
- Short partition (would reject the time request)

---

## Case 4 — Metagenome assembly

**Prompt**: "Run metaSPAdes on this soil metagenome."

**Expected classification**: Q4 (extreme memory, long time)
**Expected resources**: 16-32 CPU, 256G-1T mem, 24-72h

**Must have:**
- `--mem=` at least 256G, preferably higher
- `--cpus-per-task` in range 16-32
- `--time=` ≥ `1-00:00:00` (24h)
- Explicit partition choice that can serve high memory (skill should call
  out if the cluster's biggest partition can't reach 1 TB — metaSPAdes
  may exceed available nodes)

**Must NOT have:**
- `--gres=gpu:*` (metaSPAdes is CPU-only)
- `--mem=64G` (would silently OOM on complex communities)
- Short partition selection

---

## Case 5 — Kraken2 classification, multi-sample array

**Prompt**: "Classify 50 metagenome samples against the Kraken2 standard DB."

**Expected classification**: Q3 (high-mem, low-time — poster child)
**Expected per-sample resources**: 8-16 CPU, ≥ 50G mem (floor dictated by
standard DB size), 10-60 min

**Must have:**
- `--array=1-50`
- `--mem=` ≥ 50G (standard DB floor)
- `--cpus-per-task` 8-16
- `--time=` ≤ `02:00:00` per element
- `%K` throttling considered (e.g., `--array=1-50%10`) — cap concurrency
  to avoid filesystem thrash from 50 simultaneous DB reads

**Must NOT have:**
- `--mem=16G` (below the DB floor — would fail immediately)
- Long-time partition (wasteful for Q3)

---

## Case 6 — Quick utility on a huge file

**Prompt**: "Run samtools index on this 200 GB BAM."

**Expected classification**: Q3 lower edge (fast, moderate memory)
**Expected resources**: 2-4 CPU, 4-8G mem, 30-60 min

**Must have:**
- Short-time partition
- `--cpus-per-task` small (1-4)
- `--mem=` modest (4-16G)

**Must NOT have:**
- Job array (single file → single job)
- `--gres=gpu:*`

**Soft check:** The skill might suggest this is small enough to run on the
login node. Either answer (submit a short job OR suggest login-node
execution with caveat) is acceptable.

---

## Case 7 — Ad-hoc single command, unclear tool

**Prompt**: "Submit this for me: `python my_analysis.py --input data.tsv --out results/`"

**Expected behavior**: Skill should **ask**, not guess. The tool and input
shape are unknown. Expected questions:
- What does `my_analysis.py` do? (memory / time shape)
- How big is `data.tsv`?
- Is this CPU-only, GPU-needed?
- Where is `results/` — same filesystem?

**Must have:**
- At least one clarifying question before generating a script
- OR, if the skill chooses to generate with conservative defaults:
  a `#SBATCH --time=` ≤ 2h and `--mem=` ≤ 16G plus a note that the user
  should refine if the real job is bigger

**Must NOT have:**
- Hardcoded "STAR-ish" or "AlphaFold-ish" resource guesses based on filename
- Silent submission without any disclosure of the assumptions made

---

## Case 8 — Failure diagnosis (NOT in Phase 1 scope)

**Prompt**: "My job 12345 got OOM killed, what should I do?"

**Expected behavior**: The skill should **defer** to `slurm_diagnose_job`
directly — this is not pre-submission work. Phase 1 does not handle this.

**Must have:**
- Call to `slurm_diagnose_job(12345)` as the first action
- OR an explicit hand-off: "calling diagnose tool for you"

**Must NOT have:**
- The skill attempting to re-derive the OOM explanation from first principles
- Reading log files before calling `slurm_diagnose_job`
- A full new Step 1-4 pass — the skill is for *new* submissions, not
  re-submissions (Phase 2 will handle bump-and-resubmit)

---

## Scoring Criteria

Per case, mark one of:
- **PASS** — all "Must have" present, no "Must NOT have"
- **PASS WITH NOTES** — one minor deviation (e.g., partition name chosen
  differs but shape matches)
- **FAIL** — any "Must NOT have" present, or any "Must have" missing

Target: 7/8 cases PASS before declaring Phase 1 stable. Case 7 (ad-hoc) and
Case 8 (failure diagnosis handoff) are the hardest judgment calls — these
tell us most about whether the skill over-reaches or correctly defers.