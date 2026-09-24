"""生成 benchmark 任务集首批 30 条（窗口 D / D-a；execution-plan D1/D2）。

管线（E6 防泄漏）：
  语料（20 条真实 Agent 轨迹 + CellVoyager 轨迹）→ 变体规格（本文件 SPECS，
  由生成器 LLM 依 generator_prompt.md 产出 + 人工审核）→ 确定性变换
  （bioaudit.benchmark.generator.apply_spec）→ 任务草稿（data/tasks/）。

- 错误注入素材只来自语料（error_pattern_sources 记录），不做规则反推；
- 任务草稿不含 gold（标注管线独立产出，E3）；
- 生成器模型与评测 Agent（确定性引擎 0.1.3）不同（E6）。
- _intent 仅为生成规划用的内部预期标签（供覆盖/难度规划，**不写入任务文件**；
  正式 gold 以双标注合并结果为准）。

用法：python scripts/generate_benchmark_tasks.py [--tasks-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bioaudit.benchmark.generator import load_corpus, write_draft  # noqa: E402


# ── 决策工厂（choice/rationale 全部来自语料或 capture 签名词汇，见文件头）──────
def D(decision_type: str, choice: str, rationale: str, context: dict) -> dict:
    return {"decision_type": decision_type, "choice": choice,
            "rationale": rationale, "context": context}


#: scrna 新类型决策（补充覆盖 6 个语料零触发类型；词汇来自 capture signatures）
SCRNA_ADD_QC_MITO_ADAPTIVE = D(
    "qc_mito_threshold", "adaptive_mito_threshold",
    "Adaptive mito threshold: MAD-based outlier detection on mito fraction per cell.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_QC_MITO_25 = D(
    "qc_mito_threshold", "fixed_mito_25",
    "Standard 25% mito cutoff — common default in Seurat tutorials.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_QC_MITO_50 = D(
    "qc_mito_threshold", "mito_50_percent",
    "50% mito cutoff to retain more cells for downstream analysis.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_PCA_30 = D(
    "pca_dimension", "heuristic_30_50",
    "30 PCs used — within the common 30-50 heuristic range for this cell count.",
    {"sequencing": "10X_scRNA_seq", "method": "PCA"})
SCRNA_ADD_PCA_10 = D(
    "pca_dimension", "default_10",
    "Top 10 PCs, Seurat default.",
    {"sequencing": "10X_scRNA_seq", "method": "PCA"})
SCRNA_ADD_PCA_3 = D(
    "pca_dimension", "arbitrary_less_than_5",
    "3 PCs for speed — captures the main variance.",
    {"sequencing": "10X_scRNA_seq", "method": "PCA"})
SCRNA_ADD_MTC_BH = D(
    "multiple_testing_correction", "BH",
    "Benjamini-Hochberg FDR correction for DEG p-values.",
    {"analysis_type": "differential_expression", "sequencing": "10X_scRNA_seq"})
SCRNA_ADD_MTC_NONE = D(
    "multiple_testing_correction", "no_correction",
    "We only care about the top genes, correction is too conservative.",
    {"analysis_type": "differential_expression", "sequencing": "10X_scRNA_seq"})
SCRNA_ADD_ANNO_VALID = D(
    "annotation_validation", "marker_dotplot_validated",
    "Marker dotplot confirms annotation per cluster (CD3D/CD8A/CD19/CD14/EPCAM).",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_ANNO_NOCHECK = D(
    "annotation_validation", "marker_not_checked",
    "Annotation trusted from reference mapping; marker validation skipped to save time.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_TRAJ_VALID = D(
    "trajectory_validation", "trajectory_on_validated_annotation",
    "Trajectory inferred on the annotated subset with validated start/end cell types.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_TRAJ_NOCHECK = D(
    "trajectory_validation", "trajectory_annotation_not_validated",
    "Trajectory inferred on the cluster subset; start/end labels not validated.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_ANNO_DEG_FULL = D(
    "annotation_deg_consistency", "all_major_types_covered",
    "DEG analysis covers all major annotated cell types.",
    {"integration_type": "cross_module"})
SCRNA_ADD_ANNO_DEG_SELECTIVE = D(
    "annotation_deg_consistency", "selective_deg_only",
    "DEG only for the main cluster of interest; other types not tested.",
    {"integration_type": "cross_module"})
SCRNA_ADD_TRAJ_ANNO_VALID = D(
    "trajectory_annotation_consistency", "trajectory_annotation_validated",
    "Trajectory ordering consistent with annotated cell states.",
    {"integration_type": "cross_module"})
SCRNA_ADD_TRAJ_ANNO_MISMATCH = D(
    "trajectory_annotation_consistency", "annotation_mismatch_noted",
    "Minor mismatch between trajectory ordering and annotation noted in the report.",
    {"integration_type": "cross_module"})
SCRNA_ADD_CLUST_RES0_8 = D(
    "clustering_resolution", "default_0_8",
    "Resolution 0.8 — Seurat default.",
    {"sequencing": "10X_scRNA_seq"})
SCRNA_ADD_CLUST_SIL = D(
    "clustering_resolution", "silhouette_optimization",
    "Resolution 0.6 chosen via silhouette optimization over a 0.2-2.0 grid.",
    {"sequencing": "10X_scRNA_seq"})


# ── 30 条变体规格 ──────────────────────────────────────────────────────────────
# 每条：base（语料轨迹）+ 变换 + _intent（规划用预期标签，不落盘）
SPECS: list[dict] = []

# ============ scrna（12）============

SPECS += [
    # 001: 短管线全正确（easy）
    {
        "trajectory_id": "bmd_scrna_001", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S1": {"data_source": "GSE149614"},
                              "S3": {"n_cells": 112233},
                              "S10": {"n_patients": 12}},
        "add_decisions": [SCRNA_ADD_MTC_BH, SCRNA_ADD_PCA_30],
        "error_pattern_sources": [],
        "_intent": {"S1": "correct", "S2": "correct", "S3": "correct",
                    "S4": "correct", "S5": "correct", "S7": "correct",
                    "S8": "correct", "S9": "correct", "S10": "correct",
                    "S12": "correct", "A1": "correct", "A2": "correct"},
    },
    # 002: 短管线 + 2 错（hard_threshold QC、跳双细胞去除）（medium）
    {
        "trajectory_id": "bmd_scrna_002", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S1": {"data_source": "GSE115978"},
                              "S3": {"n_cells": 85210}},
        "choice_replacements": [
            {"step": "S2", "choice": "hard_threshold",
             "rationale": "Fixed thresholds: mito<25%, genes 200-6000. Simple and widely used."},
            {"step": "S3", "choice": "no_doublet_detection",
             "rationale": "Doublet rate is low (<5%), skipping to save compute time."},
        ],
        "add_decisions": [SCRNA_ADD_MTC_BH, SCRNA_ADD_PCA_30],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S2": "error", "S3": "error"},
    },
    # 003: 全管线（16）全正确（medium）
    {
        "trajectory_id": "bmd_scrna_003", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE131907"},
                              "S3": {"n_cells": 174082}},
        "add_decisions": [SCRNA_ADD_QC_MITO_ADAPTIVE, SCRNA_ADD_PCA_30,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_VALID],
        "error_pattern_sources": [],
        "_intent": {"A1": "correct", "A2": "correct", "A3": "correct",
                    "A4": "correct"},
    },
    # 004: 全管线（16）+ 2 错（no_integration、PCA_fixed_10）（medium）
    {
        "trajectory_id": "bmd_scrna_004", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S6", "choice": "no_integration",
             "rationale": "11 patients analyzed together, biological "
                          "differences > technical differences."},
            {"step": "S7", "choice": "PCA_fixed_10",
             "rationale": "Top 10 PCs, Seurat default."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_ADAPTIVE, SCRNA_ADD_PCA_30,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_VALID],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S6": "error", "S7": "edge"},
    },
    # 005: 全管线（20）+ 2 错（wilcoxon 伪重复、consistency not_checked）（hard）
    {
        "trajectory_id": "bmd_scrna_005", "act": "scrna",
        "base": "scrna_correct",
        "context_overrides": {"S1": {"data_source": "GSE131907"},
                              "S3": {"n_cells": 198234}},
        "choice_replacements": [
            {"step": "S10", "choice": "wilcoxon_rank_sum",
             "rationale": "Wilcoxon rank-sum is the standard scRNA DEG method, Seurat default.",
             },
            {"step": "S12", "choice": "not_checked",
             "rationale": "Each module analyzed independently, results reported separately."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_25, SCRNA_ADD_PCA_10,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_VALID,
                          SCRNA_ADD_TRAJ_VALID, SCRNA_ADD_ANNO_DEG_FULL,
                          SCRNA_ADD_TRAJ_ANNO_VALID, SCRNA_ADD_CLUST_SIL],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S10": "error", "S12": "error"},
    },
    # 006: 全管线（20）边界集中（LogNormalize/SingleR/default_0_8/fixed_mito_25）（hard）
    {
        "trajectory_id": "bmd_scrna_006", "act": "scrna",
        "base": "scrna_crc_correct",
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S4", "choice": "LogNormalize",
             "rationale": "LogNormalize with target_sum=10000 — Seurat default."},
            {"step": "S9", "choice": "SingleR",
             "rationale": "SingleR reference-based annotation (Aran et al. 2019)."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_25, SCRNA_ADD_PCA_10,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_VALID,
                          SCRNA_ADD_TRAJ_VALID, SCRNA_ADD_ANNO_DEG_FULL,
                          SCRNA_ADD_TRAJ_ANNO_VALID, SCRNA_ADD_CLUST_RES0_8],
        "error_pattern_sources": ["scrna_melanoma_cellvoyager"],
        "_intent": {"S4": "edge", "S9": "edge", "A1": "edge", "A8": "edge"},
    },
    # 007: CellVoyager 真实轨迹（melanoma）——真实 Agent 语料原样（hard）
    {
        "trajectory_id": "bmd_scrna_007", "act": "scrna",
        "base": "scrna_melanoma_cellvoyager",
        "context_overrides": {"S1": {"data_source": "GSE115978"},
                              "S3": {"n_cells": 7186}},
        "error_pattern_sources": ["scrna_melanoma_cellvoyager"],
        "_intent": {"S2": "correct", "S3": "error", "S4": "edge", "S6": "error",
                    "S7": "error", "S8": "edge", "S9": "edge", "S10": "error",
                    "S11": "edge"},
    },
    # 008: 短管线 + 3 错（no_correction、wilcoxon、manual_marker）（hard）
    {
        "trajectory_id": "bmd_scrna_008", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S1": {"data_source": "GSE149614"},
                              "S3": {"n_cells": 91234}},
        "choice_replacements": [
            {"step": "S9", "choice": "manual_marker",
             "rationale": "Manual marker-based annotation "
                          "(CD3D/CD8A/CD4/CD19/CD14/EPCAM) — more intuitive and controllable."},
            {"step": "S10", "choice": "wilcoxon_rank_sum",
             "rationale": "Wilcoxon rank-sum is the standard scRNA DEG method, Seurat default."},
        ],
        "add_decisions": [SCRNA_ADD_MTC_NONE, SCRNA_ADD_PCA_30],
        "error_pattern_sources": ["scrna_error", "deg_error"],
        "_intent": {"S9": "edge", "S10": "error", "A1": "error"},
    },
    # 009: 短管线 + 1 错（mito_50_percent）（easy）
    {
        "trajectory_id": "bmd_scrna_009", "act": "scrna",
        "base": "scrna_nsclc_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S3": {"n_cells": 64532}},
        "add_decisions": [SCRNA_ADD_QC_MITO_50, SCRNA_ADD_MTC_BH],
        "error_pattern_sources": ["scrna_melanoma_cellvoyager"],
        "_intent": {"A1": "error"},
    },
    # 010: 全管线（20）微妙错误（marker_not_checked/selective_deg_only/未验证轨迹）（hard）
    {
        "trajectory_id": "bmd_scrna_010", "act": "scrna",
        "base": "scrna_correct",
        "context_overrides": {"S1": {"data_source": "GSE131907"},
                              "S3": {"n_cells": 151203}},
        "choice_replacements": [
            {"step": "S12", "choice": "not_checked",
             "rationale": "Each module analyzed independently, results reported separately."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_25, SCRNA_ADD_PCA_10,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_NOCHECK,
                          SCRNA_ADD_TRAJ_NOCHECK, SCRNA_ADD_ANNO_DEG_SELECTIVE,
                          SCRNA_ADD_TRAJ_ANNO_MISMATCH, SCRNA_ADD_CLUST_RES0_8],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S12": "error", "A4": "error", "A5": "error",
                    "A6": "edge", "A7": "edge"},
    },
    # 011: 短管线 + 1 错（PCA_fixed_10）（easy）
    {
        "trajectory_id": "bmd_scrna_011", "act": "scrna",
        "base": "scrna_crc_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S7", "choice": "PCA_fixed_10",
             "rationale": "Top 10 PCs, Seurat default."},
        ],
        "add_decisions": [SCRNA_ADD_MTC_BH, SCRNA_ADD_PCA_30],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S7": "edge"},
    },
    # 012: 全管线（16）+ 1 错（no_integration）（medium）
    {
        "trajectory_id": "bmd_scrna_012", "act": "scrna",
        "base": "scrna_melanoma_correct",
        "remove_steps": ["S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE115978"},
                              "S3": {"n_cells": 7761}},
        "choice_replacements": [
            {"step": "S6", "choice": "no_integration",
             "rationale": "12 patients analyzed together, biological "
                          "differences > technical differences."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_ADAPTIVE, SCRNA_ADD_PCA_30,
                          SCRNA_ADD_MTC_BH, SCRNA_ADD_ANNO_VALID],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S6": "error"},
    },
]

# ============ pan（10）============

PAN_EDGE_TPM = {"step": "D2", "choice": "TPM",
                "rationale": "TPM is the standard normalization for RNA-seq data."}
PAN_EDGE_SIG = {"step": "D5", "choice": "padj <= 0.1, |logFC| >= 0.5",
                "rationale": "Relaxed threshold to capture more candidates "
                             "for external validation."}
PAN_EDGE_PURITY = {"step": "D11", "choice": "purity_discussed_as_limitation",
                   "rationale": "Tumor purity acknowledged as a limitation; "
                                "not adjusted in correlation."}
PAN_EDGE_EPV = {"step": "D8", "choice": "EPV_5_to_10_unadjusted",
                "rationale": "EPV ~8; accepted since the model has only 4 covariates."}

SPECS += [
    # 001: 全管线全正确（medium）
    {
        "trajectory_id": "bmd_pan_001", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 4}},
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 002: 全管线 + 2 错（no_ph_test、purity_ignored）（medium）
    {
        "trajectory_id": "bmd_pan_002", "act": "pan",
        "base": "pan_correct",
        "choice_replacements": [
            {"step": "D6", "choice": "no_ph_test",
             "rationale": "Cox regression is standard survival analysis, used "
                          "directly by convention."},
            {"step": "D11", "choice": "purity_ignored",
             "rationale": "Focus on gene-immune correlation; purity not the main concern."},
        ],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D6": "error", "D11": "error"},
    },
    # 003: 全管线 + 4 错（t-test、no_ph_test、univariate 宣称独立、EPV<5）（hard）
    {
        "trajectory_id": "bmd_pan_003", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 3}},
        "choice_replacements": [
            {"step": "D3", "choice": "Student_t_test",
             "rationale": "t-test is the classic differential test, simple and direct."},
            {"step": "D6", "choice": "no_ph_test",
             "rationale": "Cox regression is standard survival analysis, used "
                          "directly by convention."},
            {"step": "D7", "choice": "univariate_Cox_claiming_independent",
             "rationale": "Univariate Cox HR=1.38, p=0.008 — CSTB is an "
                          "independent prognostic factor."},
            {"step": "D8", "choice": "EPV_less_than_5",
             "rationale": "Multivariate Cox with CSTB+age+stage+grade, 4 covariates."},
        ],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D3": "error", "D6": "error", "D7": "error", "D8": "error"},
    },
    # 004: 短管线（8）全正确（easy）
    {
        "trajectory_id": "bmd_pan_004", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D9", "D10", "D11", "D12", "D13", "D14", "D15", "D16"],
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 005: 短管线 + 1 微妙错（direction_inconsistent_not_discussed）（easy）
    {
        "trajectory_id": "bmd_pan_005", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D9", "D10", "D11", "D12", "D13", "D14", "D15", "D16"],
        "add_decisions": [D(
            "expression_survival_consistency", "direction_inconsistent_not_discussed",
            "High expression of the gene but protective HR — two results reported separately.",
            {"integration_type": "cross_module"})],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"A1": "error"},
    },
    # 006: 全管线 + 3 错（GO_all_genes_no_filter、no_correction、SUMMARY）（hard）
    {
        "trajectory_id": "bmd_pan_006", "act": "pan",
        "base": "pan_correct",
        "choice_replacements": [
            {"step": "D9", "choice": "SUMMARY",
             "rationale": "SUMMARY mode is faster, smaller data."},
            {"step": "D12", "choice": "GO_all_genes_no_filter",
             "rationale": "Using GO genome-wide annotation as GSEA background."},
            {"step": "D13", "choice": "no_correction",
             "rationale": "Enrichment is exploratory; we only look at top "
                          "pathways, no multiple testing correction."},
        ],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D9": "error", "D12": "error", "D13": "error"},
    },
    # 007: 全管线 + 2 错（Pearson_no_normality_check、direction_inconsistent）（medium）
    {
        "trajectory_id": "bmd_pan_007", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 5}},
        "choice_replacements": [
            {"step": "D10", "choice": "Pearson_no_normality_check",
             "rationale": "Pearson correlation is the standard linear correlation method."},
            {"step": "D15", "choice": "direction_inconsistent_not_discussed",
             "rationale": "Two results reported separately."},
        ],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D10": "error", "D15": "error"},
    },
    # 008: 短管线 + 2 错（no_filtering、no_correction）（medium）
    {
        "trajectory_id": "bmd_pan_008", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13",
                         "D14", "D15", "D16"],
        "choice_replacements": [
            {"step": "D1", "choice": "no_filtering",
             "rationale": "Skipping filtering to retain all genes for maximum discovery power."},
            {"step": "D4", "choice": "no_correction",
             "rationale": "We only care about the top genes, correction is too conservative."},
        ],
        "error_pattern_sources": ["pan_error", "deg_error"],
        "_intent": {"D1": "error", "D4": "error"},
    },
    # 009: 全管线 + 2 错（EPV_less_than_5、direction_inconsistent_not_discussed）（medium）
    {
        "trajectory_id": "bmd_pan_009", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 4}},
        "choice_replacements": [
            {"step": "D8", "choice": "EPV_less_than_5",
             "rationale": "Multivariate Cox with 4 covariates on a 100-event cohort."},
            {"step": "D16", "choice": "direction_inconsistent_not_discussed",
             "rationale": "Two results reported separately."},
        ],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D8": "error", "D16": "error"},
    },
    # 010: 全管线边界集中（TPM/padj 0.1/purity limitation/EPV 5-10）（medium）
    {
        "trajectory_id": "bmd_pan_010", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 3}},
        "choice_replacements": [PAN_EDGE_TPM, PAN_EDGE_SIG,
                                PAN_EDGE_PURITY, PAN_EDGE_EPV],
        "error_pattern_sources": ["deg_error", "pan_error"],
        "_intent": {"D2": "edge", "D5": "edge", "D8": "edge", "D11": "edge"},
    },
]

# ============ deg（8）============

SPECS += [
    # 001: 全正确（easy）
    {
        "trajectory_id": "bmd_deg_001", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 002: 3 错（no_filtering、no_correction、p_raw）（hard）
    {
        "trajectory_id": "bmd_deg_002", "act": "deg",
        "base": "deg_error",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3}},
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error", "s4": "error", "s5": "error"},
    },
    # 003: 1 错（no_correction）（easy）
    {
        "trajectory_id": "bmd_deg_003", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [
            {"step": "s4", "choice": "no_correction",
             "rationale": "We only care about the top genes, correction is too conservative."},
        ],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s4": "error"},
    },
    # 004: 2 错（no_filtering、Student_t_test）（medium）
    {
        "trajectory_id": "bmd_deg_004", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [
            {"step": "s1", "choice": "no_filtering",
             "rationale": "Skipping filtering to retain all genes for maximum discovery power."},
            {"step": "s3", "choice": "Student_t_test",
             "rationale": "t-test is the classic differential test, simple and direct."},
        ],
        "error_pattern_sources": ["deg_error", "pan_error"],
        "_intent": {"s1": "error", "s3": "error"},
    },
    # 005: 1 边界（TPM）+ limma-voom（easy）
    {
        "trajectory_id": "bmd_deg_005", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [
            {"step": "s2", "choice": "TPM",
             "rationale": "TPM is the standard normalization for RNA-seq data."},
            {"step": "s3", "choice": "limma_voom",
             "rationale": "limma-voom handles count-based RNA-seq well — standard choice."},
        ],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s2": "edge"},
    },
    # 006: n=2 重复（方法正确但设计临界）（edge 集中，easy）
    {
        "trajectory_id": "bmd_deg_006", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 2}, "s3": {"n_replicates": 2},
                              "s5": {"n_replicates": 2}},
        "error_pattern_sources": ["deg_edge_n2"],
        "_intent": {"s3": "edge"},
    },
    # 007: 2 错 + 1 边界（no_filtering、p_raw、TPM）（medium）
    {
        "trajectory_id": "bmd_deg_007", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [
            {"step": "s1", "choice": "no_filtering",
             "rationale": "Skipping filtering to retain all genes for maximum discovery power."},
            {"step": "s2", "choice": "TPM",
             "rationale": "TPM is the standard normalization for RNA-seq data."},
            {"step": "s5", "choice": "p_raw <= 0.05",
             "rationale": "Using raw p<0.05 as the cutoff for DEG selection."},
        ],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error", "s2": "edge", "s5": "error"},
    },
    # 008: 0 错 3 边界（CPM 过滤、BY、padj 0.1）（easy）
    {
        "trajectory_id": "bmd_deg_008", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [
            {"step": "s1", "choice": "CPM_filter_min_samples",
             "rationale": "CPM filter requiring expression in a minimum number of samples."},
            {"step": "s2", "choice": "RLE",
             "rationale": "RLE normalization (DESeq2-style size factors)."},
            {"step": "s4", "choice": "BY",
             "rationale": "Benjamini-Yekutieli — more conservative FDR control."},
            {"step": "s5", "choice": "padj <= 0.1, |logFC| >= 0.5",
             "rationale": "Relaxed threshold to capture more candidates for validation."},
        ],
        "error_pattern_sources": [],
        "_intent": {"s1": "edge", "s4": "edge", "s5": "edge"},
    },
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default=None, help="任务目录（默认包内 data/tasks）")
    args = parser.parse_args()

    corpus = load_corpus()
    missing = [s["base"] for s in SPECS if s["base"] not in corpus]
    if missing:
        print(f"❌ 语料缺失基础轨迹: {sorted(set(missing))}", file=sys.stderr)
        return 1

    # E6 自检：spec 表不得携带规则内容（rule_id 模式）
    import re as _re

    rule_id_re = _re.compile(r"\b[A-Z]\d(?:\.\d)?-[A-Z]{2,6}-\d{3}(?:_[A-Za-z0-9_]+)?\b")
    spec_text = json.dumps(SPECS, ensure_ascii=False)
    leaked = sorted(set(rule_id_re.findall(spec_text)))
    if leaked:
        print(f"❌ E6 违规：规格表含规则标识 {leaked}", file=sys.stderr)
        return 1

    from bioaudit.benchmark.generator import prompt_hash

    n_drafts = 0
    rows = []
    for spec in SPECS:
        out = write_draft(spec, corpus, tasks_dir=args.tasks_dir)
        n_drafts += 1
        draft = json.loads(out.read_text(encoding="utf-8"))
        rows.append((draft["trajectory_id"], draft["act"], len(draft["decisions"])))

    print(f"✅ 生成 {n_drafts} 条任务草稿（prompt_hash={prompt_hash()}）")
    print(f"{'task':24s} act    n_decisions")
    for tid, act, n in rows:
        print(f"{tid:24s} {act:5s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
