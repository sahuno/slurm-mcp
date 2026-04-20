# Resource Profiles — Quadrant Classification & Per-Tool Lookup

Compressed lookup for the skill. For the full per-tool table with notes and
edge cases, read `docs/job_resource_profiles.md` in the slurm-mcp repo —
that is the source of truth.

## The Quadrant Plane

```
  Memory
  (GB)
   ^
   |  Q3: High-Mem / Low-Time       Q4: High-Mem / High-Time
   |  (bursty, RAM-hungry)          (the big ones)
   |
   |  Q1: Low-Mem / Low-Time        Q2: Low-Mem / High-Time
   |  (quick utilities)             (serial, I/O-bound, iterative)
   +-----------------------------------------------------------> Time (h)
```

| Quadrant | CPUs | Memory | Time | GPU? |
|----------|------|--------|------|------|
| Q1 | 1-4 | 1-8 G | minutes-2h | no |
| Q2 | 1-4 | 2-16 G | 12h-7d | no |
| Q3 | 8-64 | 32-512 G | minutes-4h | rarely |
| Q4 | 8-128 | 64 G-2 TB | 4h-7d | often |

---

## Per-Tool Lookup (IF task mentions → classification + resource range)

### Q3 — High-Mem / Low-Time

```
STAR | BWA-MEM2 | minimap2 | HISAT2 | bowtie2
  → Q3 : 8-16 CPU, 40-50G (STAR) or 16-32G (others), 20-60 min
  → Per-sample parallelism; job array for multi-sample

STAR genomeGenerate | Salmon index | BLAST makeblastdb
  → Q3 : 4-16 CPU, 16-64G, 10-60 min
  → One-time cost per reference

Kraken2 classify
  → Q3 : 8-16 CPU, 64-256G, 10-60 min
  → Poster child of Q3 — huge RAM for k-mer DB, then fast
  → DB size dictates memory floor: mini=8G, standard=50G, pluspf=70G, full=256G+

samtools sort | samtools merge | MarkDuplicates | BaseRecalibrator | BAM→CRAM
  → Q3 : 4-8 CPU, 16-64G, 15-90 min
  → samtools sort: 2-4G per thread, scales linearly

GATK HaplotypeCaller (per-interval)
  → Q3 : 4-8 CPU, 16-32G, 30 min-2h
  → Scatter-gather via --interval-list, then combine

DeepVariant (GPU)
  → Q3/Q4 boundary : 8-16 CPU, 32-64G + 1 GPU, 1-3h
  → CPU-only pushes into Q4

featureCounts | Salmon quant | HTSeq-count
  → Q3 : 4-8 CPU, 8-32G, 5-30 min
  → featureCounts fastest; HTSeq slowest same shape

DESeq2 | edgeR | limma | fgsea
  → Q3 : 4-8 CPU, 8-32G, 5-30 min
  → Single R job, parallelized across genes internally

MACS2 | MACS3 | Genrich
  → Q3 : 2-4 CPU, 8-16G, 10-30 min
  → Fast, cpushort-friendly

STARsolo | CellRanger count (single sample)
  → Q3/Q4 boundary : 16 CPU, 40-128G, 1-6h
  → One job per sample; integration step is separate

Seurat QC | Scanpy preprocessing | Scrublet | DoubletFinder
  → Q3 : 4-8 CPU, 32-128G, 10-45 min
  → Memory scales with cell count

MetaPhlAn | DIAMOND
  → Q3 : 4-16 CPU, 16-64G, 15 min-2h
```

### Q4 — High-Mem / High-Time

```
hifiasm | Flye | Canu | Verkko | SPAdes (large)
  → Q4 : 32-64 CPU, 128-500G, 12h-3d
  → Verkko (T2T) is the most demanding: 256-500G
  → No GPU — CPU-bound assembly graphs

Minigraph-cactus | PGGB | vg giraffe (large graph)
  → Q4 : 16-64 CPU, 64-512G, 12h-3d
  → Pangenome construction

dorado (basecalling, SUP model)
  → Q4 : 4-8 CPU, 16-32G, 1-4 GPU, 4-24h
  → ~1h per 10GB pod5; GPU required for realistic runtime
  → Modified bases (5mCG_5hmCG, 6mA) adds ~50% runtime
  → Match model to chemistry (4kHz vs 5kHz)

GATK GenotypeGVCFs (cohort 100+) | GATK VQSR | GLnexus
  → Q4 : 8-16 CPU, 64-256G, 4-48h
  → Memory and time scale with sample count

CellRanger aggr | Seurat integration (100K+ cells) | scVI
  → Q4 : 8-16 CPU, 64-256G, 2-12h
  → scVI needs 1 GPU; Seurat CPU-only

metaSPAdes | MEGAHIT (complex metagenomes)
  → Q4 : 16-32 CPU, 256G-1T, 24-72h
  → metaSPAdes can exceed 1 TB RAM — largest memory consumer in bioinfo
  → Not all partitions have >1 TB nodes

AlphaFold2/3 | ESM-2 inference | Enformer | scGPT fine-tuning
  → Q4 : 8-16 CPU, 32-256G, 1-4 GPU, 2-24h
  → AlphaFold: large proteins/multimers are worst case

GROMACS | AMBER | NAMD (production MD)
  → Q4 : 8-32 CPU, 32-128G, 0-4 GPU, days-weeks
  → Longest-time GPU partition

SAIGE | REGENIE | PLINK2 biobank | Beagle imputation
  → Q4 : 8-16 CPU, 64-256G, 4-48h
  → Scales with cohort size

HiC-Pro | Juicer | cooler balance (fine resolution)
  → Q4 : 8-16 CPU, 32-256G, 8-24h
  → Memory scales with resolution^2
```

### Q2 — Low-Mem / High-Time

```
BLAST vs nr/nt | RepeatMasker | LiftOver (large) | VEP | ANNOVAR
  → Q2 : 1-4 CPU, 4-16G, 8-48h
  → I/O-bound or DB-lookup-bound, serial

Bismark (single-threaded) | modkit pileup (high-cov ONT) | DMR calling
  → Q2 : 1-2 CPU, 4-12G, 2-48h
  → Long-time CPU partition

BEAST | MrBayes | RAxML bootstraps | PAML codeml
  → Q2 : 1-2 CPU, 2-8G, days-weeks
  → Fundamentally serial MCMC/likelihood

SHAPEIT | WhatsHap
  → Q2 : 1-2 CPU, 4-16G, 4-24h

SRA download | prefetch | fasterq-dump | wget | curl
  → Q2 : 1 CPU, 2-4G, hours-days
  → Network-bound

Permutation tests (serial) | MCMC (Stan, PyMC)
  → Q2 : 1-2 CPU, 2-8G, 4-24h

Snakemake/Nextflow coordinator (when submitted as SLURM job)
  → Q2 : 1 CPU, 2-4G, pipeline-duration
  → Must stay alive — use longest partition
```

### Q1 — Low-Mem / Low-Time (quick utilities)

```
Generic file conversion | small awk/sed scripts | samtools view (small BAM)
  → Q1 : 1-2 CPU, 1-4G, <30 min
  → Often better as a login-node one-liner than an SLURM job
```

---

## Decision Heuristics

### Is this really an SLURM job?

Before generating anything, sanity-check:

- **< 5 min runtime AND < 2 GB memory**: should probably run on the login
  node as a one-liner, not via sbatch.
- **Single short command on a large input** (e.g., `samtools index` on
  a 50 GB BAM): one job is correct.
- **N independent invocations of the same command**: job array
  (`--array=1-N`), not N individual submissions.
- **DAG with > 3 rules / per-sample parallelism + cohort steps**: Snakemake
  or Nextflow, not ad-hoc sbatch.

### GPU required?

| Tool | GPU? |
|------|------|
| dorado basecalling | Required (CPU is 10-50× slower) |
| AlphaFold / ESM / Enformer | Required |
| GROMACS / AMBER / NAMD | Optional but 10× speedup typical |
| scVI | Recommended for large atlases |
| DeepVariant | Optional (speedup varies) |
| Everything else in this doc | No |

Default to **no GPU** unless the tool is on this list.

### Memory floor for index/DB-loading tools

Some tools have hard memory floors regardless of input size:

| Tool | Floor |
|------|-------|
| STAR (human/mouse index) | 32G |
| Kraken2 (standard DB) | 50G |
| Kraken2 (plusPF DB) | 70G |
| Kraken2 (full/GTDB DB) | 256G+ |
| Minimap2 (human index) | 8G |
| BLAST nr/nt | 32G+ |

**Cannot go below the floor** — if the partition max is lower, the partition
is wrong, not the memory request.

### Time safety margin

- **Quick jobs (< 30 min)**: request 2× expected.
- **Medium jobs (30 min – 4h)**: request 1.5× expected.
- **Long jobs (> 4h)**: request 1.25× expected.

Over-safety-margin increases queue wait. Under-margin risks mid-job kill.
If the user has no prior run to estimate from, use the upper bound of the
per-tool range above.
