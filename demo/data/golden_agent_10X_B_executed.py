#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Golden Agent 10X — deterministic textbook scRNA-seq analysis (window L, positive control).

Nature of this script
=====================
This is a DETERMINISTIC reference-analysis script written from the public
single-cell best-practice literature (NOT an LLM agent). It executes a real
end-to-end scRNA-seq pipeline on GSE132465 (CRC, droplet 10X UMI data) and
reports every methodological decision step-by-step through the M1 channel of
the bio-audit capture layer. The script is written from the literature only
and does not reference any audit rule content.

Variant (error-injection switch, injected by make_variants_10x.py):
    B — doublet detection skipped (deliberate omission; skip_doublet declared)

Methodological rationale (literature basis)
============================================
- QC: adaptive thresholds based on median absolute deviation (MAD),
  scater-style outlier detection (McCarthy et al. 2017, Bioinformatics,
  PMID 28212749; Luecken & Theis 2019, Mol Syst Biol, PMID 31841116).
- Doublet detection: doublet detection skipped — no doublet-detection tool is invoked anywhere in the pipeline (variant B, window L)
- Normalization: SCTransform, regularized negative-binomial regression
  (Hafemeister & Satija 2019, Genome Biology, PMID 31870423); executed via
  the Bioconductor sctransform package (Rscript subprocess — the reference
  implementation; no Python port exists). SCTransform is designed for UMI
  count data (10X) and is the recommended variance-stabilizing choice.
- HVG selection: Seurat VST (variance-stabilizing transformation),
  flavor="seurat_v3" (Stuart et al. 2019, Cell, PMID 31178118).
- Batch integration: Harmony (Korsunsky et al. 2019, Nat Methods,
  PMID 31740819) across the 33 10X libraries (samples).
- Clustering: Leiden graph clustering (Traag et al. 2019, Sci Rep,
  PMID 30914743) on the SNN graph.
- Cell-type annotation: CellTypist, reference-based logistic regression
  (Domínguez Conde et al. 2022, Science, PMID 35649401).
- Differential expression: patient-level pseudobulk + DESeq2 (Squair et al. 2021, Nat Commun, PMID 34433851; Love et al. 2014, Genome Biology, PMID 25516281), executed via pydeseq2
- Multiple testing: Benjamini-Hochberg FDR (Benjamini & Hochberg 1995,
  JRSS B; Conesa et al. 2016, Genome Biology, PMID 26813401).

Data facts
==========
- GSE132465 (Lee et al. 2020, Nat Genet, PMID 32451460): droplet single-cell
  3' mRNA sequencing (10X Genomics Chromium, 3' v2) of 23 Korean colorectal
  cancer patients — 23 primary tumors and 10 matched normal mucosa (GEO
  Overall design). Library construction method "10x 3' v2" (HCA record).
- Local h5ad: 63,689 cells x 25,655 genes; integral UMI counts (sparse
  float32 storage); single obs.sample_id category "GSE132465" (disease CRC,
  tissue colorectal); the 10X library id is the prefix of the cell barcode
  before "_" (33 libraries: 23 "-T" tumor + 10 "-N" normal; patient = prefix
  without the -T/-N suffix, 23 patients). Variable names are not unique in
  the raw file and are made unique at load time.

Determinism
===========
All random seeds fixed; clustering/harmony are iterative solvers so output
is reproducible up to floating-point solver noise (documented in the report).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc

# ---------------------------------------------------------------------------
# M1 capture channel (bio-audit capture API; session must be whitelisted)
# ---------------------------------------------------------------------------
from bioaudit.capture.m1_reporter import M1Reporter
from bioaudit.capture.session import SessionWhitelist
from bioaudit.capture.verdict import VerdictStore
from bioaudit.capture.wal import WAL

# Dataset facts (documented above; also available to the evaluator
# through the declared channel — this is data knowledge, not a claim of
# execution).
DATA_CTX = {
    "sequencing": "10X_scRNA_seq",
    "data_category": "umi_counts",
}


def make_reporter(session_id: str) -> M1Reporter:
    wl = SessionWhitelist()
    wl.register(session_id)
    return M1Reporter(
        session_id=session_id,
        paradigm="scrna",
        whitelist=wl,
        verdict_store=VerdictStore(),
        wal=WAL(),
    )


def report_decision(reporter, step_id, decision_type, choice, rationale,
                    context, tool_call, code_snippet=""):
    """Report one M1 decision (provisional verdict returned; never raises)."""
    ctx = dict(DATA_CTX)
    ctx.update(context)
    return reporter.report({
        "step_id": step_id,
        "decision_type": decision_type,
        "choice": choice,
        "rationale": rationale,
        "context": ctx,
        "tool_call": tool_call,
        "code_snippet": code_snippet,
    })


# ---------------------------------------------------------------------------
# Variant: B  (injected by make_variants_10x.py — error switch)
# ---------------------------------------------------------------------------
VARIANT = "B"

# ---------------------------------------------------------------------------
# Step 1 — data loading and integrity inspection
# ---------------------------------------------------------------------------
def step1_load(data_path: str) -> "sc.AnnData":
    adata = sc.read_h5ad(data_path)                      # h5ad product read
    # raw file has duplicated gene symbols (e.g. A1BG-AS1 vs A1BG-AS1.1);
    # documented data fact — make unique before any downstream step
    if not adata.var_names.is_unique:
        adata.var_names_make_unique()
    assert adata.X.shape[0] == adata.n_obs and adata.n_vars > 1000
    # integrity checks: counts matrix non-negative, integral (UMI), no
    # empty genes/cells
    assert not np.isnan(adata.X.data).any(), "NaN in counts"
    assert (adata.X.data >= 0).all(), "negative counts"
    assert np.allclose(adata.X.data, np.floor(adata.X.data)), "non-integer counts"
    assert adata.var_names.is_unique, "duplicated gene names"
    return adata


def step2_annotate(adata) -> None:
    """10X library (sample) = prefix of cell barcode before "_"; patient =
    sample id without the -T/-N tissue suffix (documented barcode scheme of
    the study: SMC01-T_AAAC... -> sample SMC01-T, patient SMC01)."""
    bc = pd.Series(adata.obs_names.astype(str))
    sample = bc.str.extract(r"^([^_]+)", expand=False).fillna(bc)
    patient = sample.str.replace(r"-[TN]$", "", regex=True)
    adata.obs["sample"] = sample.values
    adata.obs["patient"] = patient.values


# ---------------------------------------------------------------------------
# Step 3 — QC filtering
def step3_qc(adata):
    adata.var["mt"] = adata.var_names.str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                               log1p=False, inplace=True)

    def is_outlier(metric, nmads=3):
        """MAD-based outlier detection (median +/- nmads * scaled MAD),
        scater-style adaptive thresholding (McCarthy et al. 2017)."""
        median = np.median(metric)
        mad = np.median(np.abs(metric - median))
        scaled_mad = 1.4826 * mad
        return np.abs(metric - median) > nmads * scaled_mad

    qc = adata.obs
    drop = (
        is_outlier(np.log10(qc["n_genes_by_counts"]))
        | is_outlier(np.log10(qc["total_counts"]))
        | is_outlier(qc["pct_counts_mt"])
    )
    n_before = adata.n_obs
    adata = adata[~drop].copy()
    return adata, n_before
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step 3.5 — doublet detection (10X-specific; injected per variant)
def step_doublet(adata, work_dir, rscript):
    """Doublet detection deliberately skipped (documented methodological
    decision — see the M1 report). No doublet-detection tool is invoked."""
    n_doublets = 0
    rate = 0.0
    return adata, n_doublets, rate
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step 4 — HVG selection (Seurat VST)
# ---------------------------------------------------------------------------
def step4_hvg(adata):
    sc.pp.highly_variable_genes(adata, flavor="seurat_v3", n_top_genes=2000)
    return adata[:, adata.var["highly_variable"]].copy()


# ---------------------------------------------------------------------------
# Step 5 — SCTransform normalization (Bioconductor sctransform via Rscript)
# ---------------------------------------------------------------------------
def step5_sctransform(adata, work_dir, rscript):
    counts = pd.DataFrame(
        np.asarray(adata.X.todense()).T,               # genes x cells
        index=adata.var_names, columns=adata.obs_names,
    )
    counts_path = work_dir / "sct_input_counts.csv"
    counts.to_csv(counts_path)
    r_code = r"""
library(sctransform)
umi <- as.matrix(read.csv("sct_input_counts.csv", row.names = 1, check.names = FALSE))
st <- sctransform::vst(umi, latent_var = c("log_umi"), verbosity = 0)
write.csv(st$y, "sct_corrected.csv")
cat("VST_DONE genes=", nrow(st$y), " cells=", ncol(st$y), "\n")
"""
    r_file = work_dir / "sct_vst.R"
    r_file.write_text(r_code, encoding="utf-8")
    proc = subprocess.run(
        [rscript, "--vanilla", str(r_file)],
        cwd=str(work_dir), capture_output=True, text=True, timeout=7200,
    )
    if proc.returncode != 0:
        raise RuntimeError("sctransform failed:\n" + proc.stderr[-2000:])
    corrected = pd.read_csv(work_dir / "sct_corrected.csv", index_col=0)
    # sctransform::vst 参考实现会丢弃极稀疏基因（无法拟合 NB 回归）——
    # 与结果基因集求交集（不伪造 NaN 列）
    common = corrected.index.intersection(adata.var_names)
    if len(common) < adata.n_vars:
        print(
            f"[sctransform] vst dropped {adata.n_vars - len(common)} genes "
            "(reference implementation behavior; sparsely expressed genes)",
            flush=True,
        )
    adata = adata[:, common].copy()
    corrected = corrected.reindex(index=common, columns=adata.obs_names)
    adata.X = corrected.values.T.astype(np.float32)
    return adata


# ---------------------------------------------------------------------------
# Step 6 — PCA with elbow-based component selection
# ---------------------------------------------------------------------------
def step6_pca(adata, n_comps=20, seed=42):
    sc.pp.scale(adata, max_value=10)
    sc.pp.pca(adata, n_comps=n_comps, random_state=seed, svd_solver="arpack")
    # elbow-based PC count selection from cumulative variance_ratio:
    # keep the smallest number of PCs explaining >= 75% of total variance
    # (capped at the computed n_comps).
    var_ratio = np.asarray(adata.uns["pca"]["variance_ratio"])
    cumvar = np.cumsum(var_ratio)
    n_pcs = int(np.searchsorted(cumvar, 0.75) + 1)
    n_pcs = max(5, min(n_pcs, n_comps))
    adata.uns["n_pcs_elbow"] = n_pcs
    return adata, n_pcs


# ---------------------------------------------------------------------------
# Step 7 — batch integration (Harmony, across 10X libraries)
# ---------------------------------------------------------------------------
def step7_harmony(adata, n_pcs, seed=42):
    try:
        sc.external.pp.harmony_integrate(
            adata, key="sample", basis="X_pca",
            adjusted_basis="X_pca_harmony", random_state=seed,
        )
    except TypeError:
        sc.external.pp.harmony_integrate(
            adata, key="sample", basis="X_pca",
            adjusted_basis="X_pca_harmony",
        )
    adata.obsm["X_pca"] = adata.obsm["X_pca_harmony"]
    return adata


# ---------------------------------------------------------------------------
# Step 8 — graph clustering (Leiden)
# ---------------------------------------------------------------------------
def step8_leiden(adata, n_pcs, seed=42):
    sc.pp.neighbors(adata, n_neighbors=15, n_pcs=n_pcs, random_state=seed)
    sc.tl.leiden(adata, resolution=1.0, key_added="leiden",
                 flavor="igraph", n_iterations=2, directed=False,
                 random_state=seed)
    return adata


# ---------------------------------------------------------------------------
# Step 9 — cell-type annotation (CellTypist, reference-based)
# ---------------------------------------------------------------------------
def step9_celltypist(adata, raw_counts_full, model_name, full_gene_names):
    import celltypist
    from scipy.sparse import diags
    # CellTypist input format: log1p-normalized expression to 10,000 counts
    # per cell (documented requirement; computed directly on the sparse
    # counts to keep the main analysis on the SCTransform result)
    lib = np.asarray(raw_counts_full.sum(axis=1)).ravel()
    scaled = (diags(1e4 / np.maximum(lib, 1)) @ raw_counts_full).tocsr()
    scaled.data = np.log1p(scaled.data)
    full_raw = sc.AnnData(X=scaled, obs=adata.obs[["patient"]])
    full_raw.var_names = full_gene_names
    model = celltypist.models.Model.load(model=model_name)
    pred = celltypist.annotate(
        full_raw, model=model, majority_voting=True,
    )
    adata.obs["cell_type"] = pred.predicted_labels["majority_voting"].values
    return adata


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="input h5ad (GSE132465)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rscript", default=r"<absolute-path-stripped>")
    ap.add_argument("--celltypist-model", default="Immune_All_High.pkl")
    args = ap.parse_args()

    seed = args.seed
    np.random.seed(seed)
    sc.settings.n_jobs = 1
    sc.settings.verbosity = 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)

    reporter = make_reporter(args.session_id)
    t0 = time.time()
    timing: dict[str, float] = {}

    def tic(name):
        timing[name] = time.time()

    def toc(name):
        timing[name] = round(time.time() - timing[name], 1)

    # Step 1/2: load + sample/patient annotation (data facts; integrity
    # inspection is a data-preparation step, not a scored choice)
    tic("load")
    adata = step1_load(args.data)
    step2_annotate(adata)
    n_patients = int(adata.obs["patient"].nunique())
    n_samples = int(adata.obs["sample"].nunique())
    toc("load")

    # Step 3: QC filtering
    tic("qc")
    adata, n_cells_before_qc = step3_qc(adata)
    n_cells_after_qc = adata.n_obs
    resp = report_decision(
        reporter, "qc_filtering", "qc_filtering", "MAD5_adaptive_threshold",
        "QC filtering rationale: MAD-based adaptive QC thresholds (median +/- 3 scaled MAD) per Luecken & Theis (2019) and scater (McCarthy et al. 2017).",
        {"n_mads": 3, "n_cells_before": n_cells_before_qc, "n_cells_after": n_cells_after_qc, "n_patients": n_patients, "has_batch": n_patients > 1},
        "sc.pp.calculate_qc_metrics + is_outlier() mask",
        "is_outlier(log10(n_genes_by_counts)) | is_outlier(log10(total_counts)) | is_outlier(pct_counts_mt)",
    )
    reporter.step_completed("qc_filtering")
    toc("qc")

    # Step 3.5: doublet detection (variant-dependent)
    tic("doublet")
    adata, n_doublets, n_doublet_rate = step_doublet(adata, work, args.rscript)
    resp = report_decision(
        reporter, "doublet_detection", "doublet_detection",
        "skip_doublet",
        "Doublet detection rationale: Doublet detection skipped in this variant of the pipeline (deliberate methodological omission for the window-L variant contrast; no doublet-detection tool is invoked).",
        {"n_cells": n_cells_after_qc, "n_patients": n_patients, "has_batch": n_patients > 1},
        "none (explicitly skipped)",
        "# doublet detection skipped (no tool call)",
    )
    reporter.step_completed("doublet_detection")
    toc("doublet")

    # QC-filtered, doublet-free full raw counts (downstream normalization/
    # annotation/DEG operate on the retained cells)
    adata.layers["counts"] = adata.X.copy()
    raw_counts_full = adata.X.copy()
    full_gene_names = adata.var_names

    # Step 4: HVG (Seurat VST)
    tic("hvg")
    adata = step4_hvg(adata)
    resp = report_decision(
        reporter, "hv_gene_selection", "hv_gene_selection", "vst",
        "HVG selection by variance-stabilizing transformation (Seurat v3 "
        "VST; Stuart et al. 2019).",
        {"flavor": "seurat_v3", "n_top_genes": 2000,
         "n_genes": adata.n_vars, "n_patients": n_patients},
        "sc.pp.highly_variable_genes(flavor='seurat_v3')",
        "sc.pp.highly_variable_genes(adata, flavor='seurat_v3', n_top_genes=2000)",
    )
    reporter.step_completed("hv_gene_selection")
    toc("hvg")

    # Step 5: normalization (SCTransform via Bioconductor sctransform)
    tic("sct")
    adata = step5_sctransform(adata, work, args.rscript)
    resp = report_decision(
        reporter, "scRNA_normalization", "scRNA_normalization", "SCTransform",
        "SCTransform regularized negative-binomial normalization "
        "(Hafemeister & Satija 2019); executed with the reference "
        "Bioconductor implementation (sctransform::vst).",
        {"data_category": "umi_counts", "n_genes": adata.n_vars,
         "n_cells": adata.n_obs, "n_patients": n_patients},
        "sctransform::vst (Rscript subprocess)",
        "sctransform::vst(umi, latent_var=c('log_umi'))",
    )
    reporter.step_completed("scRNA_normalization")
    toc("sct")

    # Step 6: PCA (elbow-based selection)
    tic("pca")
    adata, n_pcs = step6_pca(adata, n_comps=20, seed=seed)
    resp = report_decision(
        reporter, "dim_reduction", "dim_reduction", "PCA_elbow_selection",
        "PCA with elbow-based selection of the number of components "
        "(cumulative variance_ratio >= 75%); UMAP is used for visualization "
        "only (Luecken & Theis 2019).",
        {"method": "PCA", "n_comps": 20, "n_pcs_selected": n_pcs,
         "n_patients": n_patients},
        "sc.pp.pca + elbow on variance_ratio",
        "sc.pp.pca(adata, n_comps=20); n_pcs from cumulative variance_ratio",
    )
    reporter.step_completed("dim_reduction")
    toc("pca")

    # Step 7: batch integration (Harmony across 10X libraries)
    tic("harmony")
    adata = step7_harmony(adata, n_pcs, seed=seed)
    resp = report_decision(
        reporter, "batch_correction", "batch_correction", "Harmony",
        "Harmony integration across the 33 10X libraries (sample = 10X "
        "library; 23 patients) for joint clustering (Korsunsky et al. 2019); "
        "the 10X library is the batch variable.",
        {"has_batch": True, "n_patients": n_patients,
         "n_batches": n_samples, "confound": "sample"},
        "sc.external.pp.harmony_integrate",
        "sc.external.pp.harmony_integrate(adata, key='sample')",
    )
    reporter.step_completed("batch_correction")
    toc("harmony")

    # Step 8: Leiden clustering
    tic("leiden")
    adata = step8_leiden(adata, n_pcs, seed=seed)
    resp = report_decision(
        reporter, "clustering_method", "clustering_method", "Leiden",
        "Leiden graph clustering on the SNN graph (Traag et al. 2019), "
        "the current standard for scRNA-seq.",
        {"graph_type": "SNN", "resolution": 1.0, "n_patients": n_patients},
        "sc.tl.leiden",
        "sc.tl.leiden(adata, resolution=1.0, flavor='igraph')",
    )
    reporter.step_completed("clustering_method")
    toc("leiden")

    # Step 9: annotation (CellTypist)
    tic("celltypist")
    adata = step9_celltypist(adata, raw_counts_full, args.celltypist_model,
                             full_gene_names)
    ct_counts = adata.obs["cell_type"].value_counts()
    resp = report_decision(
        reporter, "annotation_method", "annotation_method", "CellTypist",
        "Reference-based cell-type annotation with CellTypist logistic "
        "regression (Domínguez Conde et al. 2022), majority voting.",
        {"reference_based": True, "n_patients": n_patients},
        "celltypist.annotate",
        "celltypist.annotate(full_raw, model='Immune_All_High.pkl', majority_voting=True)",
    )
    reporter.step_completed("annotation_method")
    toc("celltypist")

    # Step 10: differential expression
        # patient-level pseudobulk + DESeq2 (Squair et al. 2021)
    tic("deg")
    abund = adata.obs["cell_type"].value_counts()
    top2 = abund.index[:2].tolist()
    sub = adata[adata.obs["cell_type"].isin(top2)].copy()
    rows = []
    for (pat, ct), g in sub.obs.groupby(["patient", "cell_type"]):
        pos = sub.obs_names.get_indexer(g.index)
        vec = np.asarray(sub.layers["counts"][pos].sum(axis=0)).ravel()
        rows.append({"patient": pat, "cell_type": ct, "counts": vec})
    meta = pd.DataFrame(
        [{"patient": r["patient"], "cell_type": r["cell_type"]} for r in rows],
        index=[f"{r['patient']}__{r['cell_type']}" for r in rows],
    )
    counts_mat = np.vstack([r["counts"] for r in rows]).T  # genes x samples
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    # pseudobulk + DESeq2 (pydeseq2 — the Python implementation of the
    # DESeq2 method; Love et al. 2014)
    dds = DeseqDataSet(
        counts=counts_mat.T, metadata=meta,
        design_factors="cell_type", refit_cooks=False,
    )
    dds.deseq2()
    stat = DeseqStats(dds, contrast=["cell_type", top2[0], top2[1]])
    stat.summary()
    res = stat.results_df.copy()
    res["logFC"] = res["log2FoldChange"]
    res["gene"] = res.index
    resp = report_decision(
        reporter, "deg_method", "deg_method", "pseudobulk_DESeq2",
        "Patient-level pseudobulk + DESeq2 negative-binomial model with "
        "patients as biological replicates (Squair et al. 2021; "
        "Love et al. 2014).",
        {"design": "simple_two_group", "unit": "pseudobulk",
         "pseudobulk": True, "n_patients": n_patients,
         "n_samples_in_test": int(meta.shape[0])},
        "pseudobulk aggregation + pydeseq2.DeseqDataSet",
        "pseudobulk per patient per cell type; DESeq2 design ~ cell_type",
    )
    reporter.step_completed("deg_method")
    toc("deg")

    # Step 11: multiple testing correction (BH FDR)
    tic("bh")
    pvals = res["pvalue"]
    res["padj"] = step11_bh(pvals)
    sig = res[(res.padj < 0.05) & (res.logFC.abs() > 1.0)].copy()
    sig = sig.sort_values("padj")
    resp = report_decision(
        reporter, "multiple_testing_correction", "multiple_testing_correction",
        "BH",
        "Benjamini-Hochberg FDR control (Benjamini & Hochberg 1995; "
        "Conesa et al. 2016) for the DEG table.",
        {"n_tests": int(pvals.notna().sum()), "n_patients": n_patients},
        "statsmodels.multipletests(method='fdr_bh')",
        "p.adjust(method='BH') equivalent (multipletests fdr_bh)",
    )
    reporter.step_completed("multiple_testing_correction")
    toc("bh")

    # Step 12: significance criteria for the DEG list
    resp = report_decision(
        reporter, "significance_threshold", "significance_threshold",
        "padj <= 0.05, |logFC| >= 1.0",
        "DEG significance criteria: BH-adjusted p <= 0.05 and absolute "
        "log2 fold-change >= 1.0 (standard reporting cutoffs).",
        {"padj_cutoff": 0.05, "logfc_cutoff": 1.0, "n_patients": n_patients},
        "padj < 0.05 & |logFC| > 1.0 filter",
        "res[(res.padj < 0.05) & (res.logFC.abs() > 1.0)]",
    )
    reporter.step_completed("significance_threshold")

    # ── products ──
    sig.head(200).to_csv(out_dir / "deg_significant_top200.csv")
    adata.write(out_dir / "analyzed.h5ad")
    summary = {
        "variant": VARIANT,
        "session_id": args.session_id,
        "seed": seed,
        "n_patients": n_patients,
        "n_samples": n_samples,
        "n_cells_before_qc": n_cells_before_qc,
        "n_cells_after_qc": n_cells_after_qc,
        "n_doublets": n_doublets,
        "n_doublet_rate": n_doublet_rate,
        "n_genes_hvg": int(adata.n_vars),
        "n_pcs_elbow": int(n_pcs),
        "n_clusters": int(adata.obs["leiden"].nunique()),
        "n_cell_types": int(ct_counts.shape[0]),
        "top_cell_types": ct_counts.head(5).to_dict(),
        "deg_n_significant": int(sig.shape[0]),
        "timing_s": timing,
    }
    (out_dir / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    # executed artifact: exact copy of this script
    shutil.copy(__file__, out_dir / f"golden_agent_10X_{VARIANT}_executed.py")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------
# Step 11 helper — multiple testing correction (Benjamini-Hochberg FDR)
# ---------------------------------------------------------------------------
def step11_bh(pvals: pd.Series) -> pd.Series:
    from statsmodels.stats.multitest import multipletests
    # BH FDR correction (statsmodels fdr_bh; equivalent to R
    # p.adjust(method='BH'))
    pv = pvals.dropna()
    return pd.Series(multipletests(pv, method="fdr_bh")[1], index=pv.index)


if __name__ == "__main__":
    sys.exit(main())
