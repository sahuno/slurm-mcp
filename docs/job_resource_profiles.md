# Computational Biology Job Resource Profiles

A classification of common computational biology SLURM jobs mapped onto a
**Time × Memory** Cartesian plane. Use this as a reference when choosing
partitions, setting resource defaults, and designing partition-aware validation
rules for slurm-mcp.

```
  Memory
  (GB)
   ^
   |
   |  Q3: High-Mem / Low-Time       Q4: High-Mem / High-Time
   |  (bursty, RAM-hungry)          (the big ones)
   |
   |  ·······························································
   |
   |  Q1: Low-Mem / Low-Time        Q2: Low-Mem / High-Time
   |  (quick utilities)             (serial, I/O-bound, iterative)
   |
   +--------------------------------------------------------------> Time (h)
```

---

## Q2: Low Memory / Low Cores, High Time

**Profile**: 1-4 CPUs, 2-16 GB RAM, 12 h – 7 days

These jobs are serial or lightly parallel, typically I/O-bound or iterative.
They are the most likely to hit wall-time limits on short partitions.

### Genomics / Sequence Analysis

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| BLAST vs nr/nt | 1-2 CPU, 8G | 12-48h | Single-threaded query against massive DB |
| RepeatMasker (large genome) | 1-4 CPU, 4-8G | 12-48h | Scans against repeat libraries, mostly serial |
| LiftOver at scale | 1 CPU, 2-4G | 2-12h | Millions of intervals, one-at-a-time |
| Variant annotation (VEP, ANNOVAR) | 1-2 CPU, 4-8G | 8-24h | Per-variant DB lookups, I/O-bound |
| Phasing (SHAPEIT, WhatsHap) | 1-2 CPU, 4-16G | 4-24h | Sequential per chromosome |

### Methylation / Epigenomics

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| Bismark (single-threaded) | 1 CPU, 8-12G | 24-48h | Bisulfite alignment is inherently slow |
| modkit pileup (high-cov ONT) | 1-2 CPU, 4-8G | 2-8h | Iterates every read at every CpG, I/O-bound |
| DMR calling (metilene, dmrseq) | 1-2 CPU, 4-8G | 2-12h | Pairwise iterative, not parallelizable |

### Phylogenetics / Evolution

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| BEAST / MrBayes (MCMC) | 1-2 CPU, 4-8G | Days-weeks | Chain sampling, fundamentally serial |
| RAxML bootstrapping (per rep) | 1 CPU, 2-4G | Hours each | Thousands of small replicates |
| dN/dS (PAML / codeml) | 1 CPU, 2G | Hours total | Per-gene likelihood, thousands of genes |

### Structural Biology

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| MD equilibration (GROMACS, CPU) | 2-4 CPU, 4-8G | Days | ns-scale simulations |
| Rosetta folding/docking | 1-2 CPU, 4G | Hours/model | Single-trajectory decoy generation |

### Downloads / Data Transfer

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| SRA download (prefetch + fasterq-dump) | 1 CPU, 4G | Hours | Network-bound |
| S3/GCS bulk transfers | 1 CPU, 2G | Hours | Bandwidth-limited |
| Database mirroring (NCBI, UniProt) | 1 CPU, 2G | Days | wget/curl limited by remote server |

### Iterative / Statistical

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| Permutation testing (serial) | 1 CPU, 2-8G | 4-24h | 10K-1M permutations |
| MCMC samplers (Stan, PyMC) | 1-2 CPU, 4-8G | Hours-days | Posterior sampling chains |
| Hyperparameter sweep (per trial) | 1-2 CPU, 4-8G | Hours each | Each trial small but slow |
| Cross-validation loops (per fold) | 1-2 CPU, 4-8G | Hours each | k-fold, each fold independent |

### Orchestration / Long-running Monitors

| Job | Typical Resources | Time | Notes |
|-----|-------------------|------|-------|
| Snakemake/Nextflow coordinator | 1 CPU, 2-4G | Hours-days | Must stay alive for full pipeline |
| Watchdog / polling scripts | 1 CPU, 1G | Hours-days | Trigger downstream on file creation |

### Partition Implications

These jobs **waste resources on GPU/high-mem partitions** and **will be rejected
by short-time partitions** (e.g., `cpushort` at 2h). Best suited for long-time,
CPU-only partitions like `componc_cpu` (168h).

---

## Q1: Low Memory / Low Cores, Low Time

**Profile**: 1-4 CPUs, 1-8 GB RAM, minutes – 2 h

*TODO*

---

## Q3: High Memory / High Cores, Low Time

**Profile**: 8-64 CPUs, 32-512 GB RAM, minutes – 4 h

These are "bursty" jobs — they grab a lot of resources but release them quickly.
The dominant pattern is **Load-Process-Done**: load a large index or dataset into
RAM, do parallel work across many cores, finish fast.

```
Memory
  ^
  |     ┌────────────┐
  |     │ index/data  │
  |     │ in RAM      │  <- flat plateau while processing
  |    /│             │\
  |   / │             │ \
  |  /  │             │  \  <- rapid teardown
  | /   │             │   \
  +─────┴─────────────┴─────> Time
   load    process      done
```

### Alignment / Mapping

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| STAR alignment | 8-16 | 40-50G | 20-40 min | 32G genome index loaded into RAM, then fast parallel alignment |
| BWA-MEM2 | 8-16 | 16-32G | 30 min-2h | Multi-threaded, index in memory |
| minimap2 (ONT/HiFi) | 8-16 | 16-32G | 15-60 min | Lighter index but high throughput |
| HISAT2 | 8 | 8-16G | 20-40 min | Smaller index than STAR |
| Bowtie2 | 8 | 8-16G | 30-60 min | FM-index in memory |

All share the same shape: big upfront memory allocation for the index, then
CPU-bound parallel work.

### Index / Database Building

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| STAR genomeGenerate | 8-16 | 32-64G | 30-60 min | One-time cost, produces the index used by STAR align |
| Salmon index | 4-8 | 16-32G | 10-30 min | Quasi-mapping index from transcriptome |
| BLAST makeblastdb (nr) | 4 | 32-64G | 30-60 min | Formats the database for fast lookup |
| Kraken2 build | 8-16 | 64-256G | 1-3h | k-mer database, memory scales with DB breadth |

### BAM Processing / Post-alignment

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| samtools sort | 4-8 | 16-32G | 15-45 min | 2-4G per thread, scales linearly |
| samtools merge (multi-BAM) | 4-8 | 16-32G | 20-60 min | Merging many BAMs, I/O + memory |
| Picard MarkDuplicates | 4 | 32-64G | 30-90 min | Holds read pairs in JVM heap; worst case at high-cov WGS |
| GATK BaseRecalibrator | 4-8 | 16-32G | 30-60 min | Multi-threaded, moderate memory |
| BAM to CRAM conversion | 4-8 | 8-16G | 20-40 min | Compression is CPU-bound |

### Variant Calling (parallelized by region)

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| GATK HaplotypeCaller (per-interval) | 4-8 | 16-32G | 30 min-2h | Scattered across intervals, each finishes fast |
| DeepVariant (GPU) | 8-16 | 32-64G + 1 GPU | 1-3h | Model loading is the memory hit |
| bcftools mpileup+call | 4-8 | 8-16G | 30-60 min | Lightweight but parallel |

### Quantification / Counting

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| featureCounts | 4-8 | 8-32G | 5-30 min | Loads all BAMs, counts in parallel — fastest RNA-seq step |
| Salmon quant | 8 | 16-32G | 5-15 min | Quasi-mapping, very fast once index loaded |
| HTSeq-count | 1-2 | 8-16G | 30-60 min | Slower than featureCounts but same shape |

### Metagenomics / Classification

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| Kraken2 classify | 8-16 | 64-256G | 10-60 min | **Poster child of Q3** — loads entire k-mer DB into RAM, then blazing fast |
| MetaPhlAn | 4-8 | 16G | 15-30 min | Marker gene mapping |
| DIAMOND (fast BLAST) | 8-16 | 32-64G | 30 min-2h | Index in memory, parallel alignment |

### scRNA-seq Preprocessing

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| STARsolo | 8-16 | 40-50G | 1-2h | Same STAR index + barcode handling |
| Seurat object creation + QC | 4-8 | 32-128G | 10-30 min | Loading large sparse matrices |
| Scanpy preprocessing | 4-8 | 32-64G | 10-30 min | Same pattern, AnnData in memory |
| Scrublet / DoubletFinder | 4-8 | 32-64G | 15-45 min | Simulates doublets — memory for synthetic data |

### DGE / Statistical Analysis

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| DESeq2 | 4-8 | 8-32G | 5-30 min | Fits GLMs, parallelized across genes |
| edgeR / limma-voom | 4 | 8-16G | 2-10 min | Lighter than DESeq2 |
| GSEA (fgsea) | 4-8 | 8-16G | 5-15 min | Permutation-based but parallelized |

### Peak Calling (ChIP-seq / ATAC-seq)

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| MACS2/MACS3 | 2-4 | 8-16G | 10-30 min | Fast, moderate memory |
| Genrich (ATAC-seq) | 2-4 | 8-16G | 10-20 min | Similar profile |

### Partition Implications

- Best on **high-memory, short-time partitions** — `cpushort` (2h, 503G) is
  actually ideal for many of these if the memory fits.
- These jobs cycle through partitions quickly and free up resources for others,
  so schedulers like them.
- **Avoid long-time partitions** when possible — holding a 500G node for 30 min
  on a 7-day partition blocks other users unnecessarily.
- Kraken2 with the full database (256G+) needs the largest-memory nodes —
  partition-aware validation prevents silent failures on 500G-max nodes.
- DeepVariant and some variant callers straddle Q3/Q4 boundary depending on
  coverage and whether GPU is used.

---

## Q4: High Memory / High Cores, High Time

**Profile**: 8-128 CPUs, 64 GB – 2 TB RAM, 4 h – 7 days, often GPU

These are the "big iron" jobs. They need everything and hold it for a long time.
Unlike Q3 (which finishes fast because it parallelizes well), Q4 jobs can't
escape their runtime — either the algorithm is inherently iterative, the dataset
is too large to process quickly even with many cores, or GPU compute is the
bottleneck.

### Genome Assembly (large eukaryotic genomes)

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| Hifiasm (mammalian) | 32-64 | 200-500G | 12-48h | HiFi assembly; memory scales with genome size + coverage |
| Flye (ONT, mammalian) | 32-64 | 128-256G | 24-72h | ONT assembly, iterative polishing rounds |
| Canu (ONT/HiFi) | 32-64 | 128-256G | Days | Known for extreme resource appetite |
| Verkko (T2T-grade) | 32-64 | 256-500G | Days | HiFi + ONT for telomere-to-telomere; the most demanding assembler |
| SPAdes (large hybrid) | 16-32 | 128-512G | 12-48h | de Bruijn graph scales with genome complexity |

Assembly is probably the single most resource-intensive category in genomics.
The graph data structures alone can consume hundreds of GB.

### Pangenome Construction

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| Minigraph-cactus | 32-64 | 128-512G | Days | Pangenome graph from multiple assemblies |
| PGGB | 16-32 | 64-256G | 12-48h | All-vs-all alignment, then graph construction |
| vg giraffe (large graph) | 8-16 | 64-128G | Hours/sample | Graph-based alignment; graph loading is the memory hit |

### ONT Basecalling

| Job | CPUs | Memory | GPUs | Time | Notes |
|-----|------|--------|------|------|-------|
| Dorado (SUP model) | 4-8 | 16-32G | 1-4 A100/H200 | 4-24h | ~1h per 10GB pod5; SUP model is slowest/most accurate |
| Dorado (5mCG_5hmCG + 6mA) | 4-8 | 16-32G | 1-4 GPU | 6-36h | Modified base calling adds ~50% runtime |
| Dorado (CPU fallback) | 16-32 | 32G | 0 | Days | 10-50x slower without GPU; last resort |

Basecalling is the only step in the ONT pipeline that truly needs GPUs.
Everything downstream is CPU-only.

### WGS Joint Genotyping (cohort-scale)

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| GATK GenotypeGVCFs (100+ samples) | 8-16 | 64-256G | 12-48h | Memory and time scale with sample count |
| GATK VQSR | 4-8 | 32-64G | 4-12h | Training Gaussian mixture on large callsets |
| GLnexus (cohort joint calling) | 8-16 | 64-128G | 4-12h | Faster than GATK but still scales with cohort |
| DeepVariant (WGS, CPU-only) | 16-32 | 64G | 12-24h | Without GPU, runtime explodes into Q4 territory |

### scRNA-seq (large-scale / multi-sample integration)

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| CellRanger count (large library) | 16 | 64-128G | 2-6h | Fixed pipeline; memory scales with read count |
| CellRanger aggr (multi-sample) | 16 | 128-256G | 4-12h | Aggregating millions of cells |
| Seurat integration (100K+ cells) | 8-16 | 64-256G | 2-8h | Harmony/CCA/RPCA; memory scales with cell count |
| Scanpy + scVI (large atlas) | 8-16 | 64-128G + 1 GPU | 4-12h | VAE training on large cell counts |
| RNA velocity (scVelo, large) | 8-16 | 64-128G | 2-8h | Moments computation over spliced/unspliced |

### Spatial Transcriptomics

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| Cell2location | 4-8 | 32-64G + 1 GPU | 4-12h | MCMC-based deconvolution of spatial spots |
| MERFISH/seqFISH processing | 8-16 | 64-128G | 4-12h | Millions of transcripts + spatial registration |
| 10x Xenium analysis | 8-16 | 64-128G | 4-8h | Large image + subcellular transcript data |

### Hi-C / 3D Genome

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| HiC-Pro (full pipeline) | 8-16 | 32-64G | 8-24h | Alignment + binning + ICE normalization |
| Juicer (high-resolution) | 8-16 | 64-128G | 12-24h | Contact matrix at 1kb resolution needs enormous memory |
| cooler balance (fine resolution) | 4-8 | 64-256G | 4-12h | ICE normalization; memory scales with resolution^2 |
| TAD calling (high-res) | 4-8 | 32-64G | 4-8h | Insulation score / directionality across fine bins |

### Deep Learning for Genomics

| Job | CPUs | Memory | GPUs | Time | Notes |
|-----|------|--------|------|------|-------|
| AlphaFold2/3 | 8-16 | 64-256G | 1-4 | 2-24h | Per-protein; large proteins / multimers are worst case |
| ESM-2 inference (proteome-scale) | 8 | 64-128G | 1-2 | 4-24h | Embedding millions of proteins |
| Enformer / Basenji | 4-8 | 32-64G | 1-2 | 4-12h | Sequence-to-expression prediction |
| scGPT / Geneformer fine-tuning | 4-8 | 32-64G | 1-4 | 4-24h | Foundation model fine-tuning on single-cell data |
| Custom CNN/transformer training | 8-16 | 32-128G | 1-4 | Hours-days | Variant effect prediction, regulatory models |

### Metagenomics Assembly

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| metaSPAdes (complex communities) | 16-32 | 256G-1T | 24-72h | Memory explodes with community diversity |
| MEGAHIT (complex metagenome) | 16-32 | 128-512G | 12-48h | Lighter than SPAdes but still massive |
| Binning (MetaBAT2 + refinement) | 8-16 | 32-64G | 4-12h | Post-assembly, iterative |

metaSPAdes on a complex soil/gut metagenome can exceed 1 TB RAM. This is the
most extreme memory consumer in all of bioinformatics.

### Population Genetics / Biobank-scale GWAS

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| SAIGE / REGENIE | 8-16 | 64-128G | 4-24h | Mixed-model GWAS on 100K+ individuals |
| PLINK2 (biobank PCA/GWAS) | 8-16 | 64-256G | 4-12h | Millions of variants x hundreds of thousands of samples |
| Beagle (whole-genome imputation) | 4-8 | 64-128G | 12-48h | Large reference panel + many target samples |
| ADMIXTURE (large cohort) | 4-8 | 32-64G | 12-48h | Population structure; iterative EM |

### Molecular Dynamics (production runs)

| Job | CPUs | Memory | GPUs | Time | Notes |
|-----|------|--------|------|------|-------|
| GROMACS (microsecond MD) | 8-16 | 32-64G | 1-4 | Days-weeks | Production simulations of large biomolecular systems |
| AMBER | 8-16 | 32-64G | 1-4 | Days-weeks | Similar profile |
| NAMD (large systems) | 16-32 | 64-128G | 0-4 | Days | Scales with atom count |

### Multi-omics Integration

| Job | CPUs | Memory | Time | Notes |
|-----|------|--------|------|-------|
| MOFA+ (large multi-omics) | 4-8 | 32-64G | 4-12h | Factor analysis across modalities |
| ArchR (scATAC-seq, full pipeline) | 8-16 | 64-256G | 4-12h | Fragment counting + peak calling + integration |
| Seurat WNN (multi-modal) | 8-16 | 64-128G | 2-8h | Weighted nearest neighbor across RNA + ATAC/protein |

### Partition Implications

- These jobs **require the largest partitions**: long time limits AND high
  memory per node (e.g., `componc_cpu` at 168h / 1007G).
- metaSPAdes and large assemblies may need nodes with >1 TB RAM — not all
  partitions have them.
- GPU jobs (basecalling, AlphaFold, MD) need both GPU access AND long time
  limits — the intersection of GPU partitions with >12h limits may be small.
- **Job arrays don't help** — these can't be chunked into small independent
  pieces.
- Over-requesting is common (users ask for 1T "just in case") — partition
  limits give a concrete ceiling to negotiate against.
- **Failure cost is highest here.** An OOM kill at hour 23 of a 24h assembly,
  or a silent sbatch rejection for a week-long MD run, wastes the most user
  time. This is exactly where partition-aware validation pays off the most.

---

## How to Use This Reference

1. **Choosing a partition**: Match your job's quadrant to a partition with
   appropriate time and memory limits. slurm-mcp's partition-aware validation
   will warn you if your request exceeds the partition's capabilities.

2. **Setting defaults**: If most of your work falls in Q2, set conservative
   defaults (4 CPUs, 8G, 24h) and scale up only for Q3/Q4 jobs.

3. **Array jobs**: Many Q1 and Q2 jobs are ideal candidates for SLURM job
   arrays — submit thousands of small jobs with `--array=1-1000`.

4. **GPU awareness**: Only Q4 jobs (and some Q3) need GPUs. Requesting GPUs for
   Q1/Q2 jobs wastes quota and may increase queue wait time.
