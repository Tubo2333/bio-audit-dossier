"""生成 benchmark 任务集批 2（30 条 → 60 条；窗口 F / F1；execution-plan §六.九 F1）。

管线（E6 防泄漏，与批 1 相同）：
  语料（20 条真实 Agent 轨迹 + CellVoyager 轨迹）→ 变体规格（本文件 SPECS，
  由生成器 LLM 依 generator_prompt.md 产出 + 人工审核）→ 确定性变换
  （bioaudit.benchmark.generator.apply_spec）→ 任务草稿（data/tasks/）。

批 2 设计目标（F1.1 / F3.8）：
  - 30 条：scrna 10 / pan 10 / deg 10（与批 1 合计 60 条，三范式均衡）；
  - 难度分布补齐批 1 缺口：批 1 scrna 无 easy → 批 2 scrna 补 4 条 easy
    （≤10 决策且 ≤1 错误）；三范式 × 三梯度批 2 后全部非空；
  - 语料扩展（F1.2）：CellVoyager hook 真实运行仍未实测（窗口 C 遗留），
    继续使用现有语料库；bmd_scrna_020 以 **真实 CellVoyager 轨迹**
    （scrna_melanoma_cellvoyager）为底，纳入 PCA_arbitrary /
    Kruskal_Wallis_cell_level / LogNormalize / no_trajectory 等真实错误模式。

- 错误注入素材只来自语料（error_pattern_sources 记录），不做规则反推；
- 任务草稿不含 gold（标注管线独立产出，E3）；
- 生成器模型与评测 Agent（确定性引擎 0.1.3）不同（E6）。
- _intent 仅为生成规划用的内部预期标签（供覆盖/难度规划，**不写入任务文件**；
  正式 gold 以双标注合并结果为准）。

用法：python scripts/generate_benchmark_tasks_batch2.py [--tasks-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 复用批 1 脚本的决策工厂与 scrna 附加决策词汇（全部来自语料/capture 签名）
from generate_benchmark_tasks import (  # noqa: E402
    SCRNA_ADD_ANNO_DEG_FULL,
    SCRNA_ADD_ANNO_VALID,
    SCRNA_ADD_CLUST_RES0_8,
    SCRNA_ADD_CLUST_SIL,
    SCRNA_ADD_MTC_BH,
    SCRNA_ADD_MTC_NONE,
    SCRNA_ADD_PCA_10,
    SCRNA_ADD_PCA_30,
    SCRNA_ADD_QC_MITO_25,
    SCRNA_ADD_QC_MITO_ADAPTIVE,
    SCRNA_ADD_TRAJ_ANNO_VALID,
    SCRNA_ADD_TRAJ_VALID,
    D,
)

from bioaudit.benchmark.generator import load_corpus, write_draft  # noqa: E402

# ── pan 边界/错误素材（来自语料 pan_edge_*/pan_error + 批 1 已审核规格）───
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
PAN_ERR_PH = {"step": "D6", "choice": "no_ph_test",
              "rationale": "Cox regression is standard survival analysis, used "
                           "directly by convention."}
PAN_ERR_UNIVARIATE = {"step": "D7", "choice": "univariate_Cox_claiming_independent",
                      "rationale": "Univariate Cox HR=1.38, p=0.008 — CSTB is an "
                                   "independent prognostic factor."}
PAN_ERR_PEARSON = {"step": "D10", "choice": "Pearson_no_normality_check",
                   "rationale": "Pearson correlation is the standard linear "
                                "correlation method."}
PAN_ERR_DIRECTION = {"step": "D15", "choice": "direction_inconsistent_not_discussed",
                     "rationale": "Two results reported separately."}
PAN_ERR_TTEST = {"step": "D3", "choice": "Student_t_test",
                 "rationale": "t-test is the classic differential test, simple and direct."}
PAN_ERR_NOCORR = {"step": "D4", "choice": "no_correction",
                  "rationale": "We only care about the top genes, correction is "
                               "too conservative."}

# ── 30 条批 2 变体规格 ────────────────────────────────────────────────────────
SPECS: list[dict] = []

# ============ scrna（10：bmd_scrna_013..022；补 easy 缺口）============

SPECS += [
    # 013: 短管线（9）全正确（easy）
    {
        "trajectory_id": "bmd_scrna_013", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S6", "S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE149614"},
                              "S3": {"n_cells": 112233},
                              "S10": {"n_patients": 12}},
        "error_pattern_sources": [],
        "_intent": {"S1": "correct", "S2": "correct", "S3": "correct",
                    "S4": "correct", "S5": "correct", "S7": "correct",
                    "S8": "correct", "S9": "correct", "S10": "correct"},
    },
    # 014: 短管线（9）+ 1 错（wilcoxon 伪重复）（easy）
    {
        "trajectory_id": "bmd_scrna_014", "act": "scrna",
        "base": "scrna_crc_correct",
        "remove_steps": ["S6", "S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S10", "choice": "wilcoxon_rank_sum",
             "rationale": "Wilcoxon rank-sum is the standard scRNA DEG method, "
                          "Seurat default."},
        ],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S10": "error"},
    },
    # 015: 短管线（9）全正确（scVI/pseudobulk_edgeR 变体）（easy）
    {
        "trajectory_id": "bmd_scrna_015", "act": "scrna",
        "base": "scrna_melanoma_correct",
        "remove_steps": ["S6", "S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE115978"},
                              "S3": {"n_cells": 7761}},
        "error_pattern_sources": [],
        "_intent": {"S1": "correct", "S2": "correct", "S3": "correct",
                    "S4": "correct", "S5": "correct", "S7": "correct",
                    "S8": "correct", "S9": "correct", "S10": "correct"},
    },
    # 016: 全管线（16）全正确（medium）
    {
        "trajectory_id": "bmd_scrna_016", "act": "scrna",
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
    # 017: 全管线删 S11（11）+ 2 错（hard_threshold、no_doublet）+ PCA_fixed_10（medium）
    {
        "trajectory_id": "bmd_scrna_017", "act": "scrna",
        "base": "scrna_crc_correct",
        "remove_steps": ["S11"],
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S2", "choice": "hard_threshold",
             "rationale": "Fixed thresholds: mito<25%, genes 200-6000. Simple and widely used."},
            {"step": "S3", "choice": "no_doublet_detection",
             "rationale": "Doublet rate is low (<5%), skipping to save compute time."},
            {"step": "S7", "choice": "PCA_fixed_10",
             "rationale": "Top 10 PCs, Seurat default."},
        ],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S2": "error", "S3": "error", "S7": "edge"},
    },
    # 018: 短管线（9）+ 4 adds（MTC no_correction 错 / 默认分辨率 edge）（medium）
    {
        "trajectory_id": "bmd_scrna_018", "act": "scrna",
        "base": "scrna_nsclc_correct",
        "remove_steps": ["S6", "S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE131907"},
                              "S3": {"n_cells": 151203}},
        "choice_replacements": [
            {"step": "S9", "choice": "manual_marker",
             "rationale": "Manual marker-based annotation "
                          "(CD3D/CD8A/CD4/CD19/CD14/EPCAM) — more intuitive and controllable."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_25, SCRNA_ADD_PCA_10,
                          SCRNA_ADD_MTC_NONE, SCRNA_ADD_CLUST_RES0_8],
        "error_pattern_sources": ["scrna_error", "deg_error"],
        "_intent": {"S9": "edge", "A1": "edge", "A2": "edge", "A3": "error",
                    "A4": "edge"},
    },
    # 019: 全管线（20）+ 3 错（wilcoxon、not_checked、no_correction）（hard）
    {
        "trajectory_id": "bmd_scrna_019", "act": "scrna",
        "base": "scrna_correct",
        "context_overrides": {"S1": {"data_source": "GSE131907"},
                              "S3": {"n_cells": 198234}},
        "choice_replacements": [
            {"step": "S10", "choice": "wilcoxon_rank_sum",
             "rationale": "Wilcoxon rank-sum is the standard scRNA DEG method, Seurat default."},
            {"step": "S12", "choice": "not_checked",
             "rationale": "Each module analyzed independently, results reported separately."},
        ],
        "add_decisions": [SCRNA_ADD_QC_MITO_25, SCRNA_ADD_PCA_10,
                          SCRNA_ADD_MTC_NONE, SCRNA_ADD_ANNO_VALID,
                          SCRNA_ADD_TRAJ_VALID, SCRNA_ADD_ANNO_DEG_FULL,
                          SCRNA_ADD_TRAJ_ANNO_VALID, SCRNA_ADD_CLUST_SIL],
        "error_pattern_sources": ["scrna_error"],
        "_intent": {"S10": "error", "S12": "error", "A3": "error",
                    "A1": "edge", "A2": "edge"},
    },
    # 020: CellVoyager 真实轨迹（melanoma）原样（hard；真实错误模式素材）
    {
        "trajectory_id": "bmd_scrna_020", "act": "scrna",
        "base": "scrna_melanoma_cellvoyager",
        "context_overrides": {"S1": {"data_source": "GSE115978"},
                              "S3": {"n_cells": 7186}},
        "error_pattern_sources": ["scrna_melanoma_cellvoyager"],
        "_intent": {"S2": "error", "S3": "error", "S4": "edge", "S6": "error",
                    "S7": "error", "S8": "edge", "S9": "edge", "S10": "error",
                    "S11": "edge"},
    },
    # 021: 删 S6/S11（10）+ 1 错（no_doublet）+ PCA_fixed_10 + MTC BH（medium）
    {
        "trajectory_id": "bmd_scrna_021", "act": "scrna",
        "base": "scrna_crc_correct",
        "remove_steps": ["S6", "S11"],
        "context_overrides": {"S1": {"data_source": "GSE132465"},
                              "S3": {"n_cells": 63689}},
        "choice_replacements": [
            {"step": "S3", "choice": "no_doublet_detection",
             "rationale": "Doublet rate is low (<5%), skipping to save compute time."},
            {"step": "S7", "choice": "PCA_fixed_10",
             "rationale": "Top 10 PCs, Seurat default."},
        ],
        "add_decisions": [SCRNA_ADD_MTC_BH],
        "error_pattern_sources": ["scrna_error", "scrna_edge_nodoublet"],
        "_intent": {"S3": "error", "S7": "edge", "A1": "correct"},
    },
    # 022: 短管线（8）全正确（easy）
    {
        "trajectory_id": "bmd_scrna_022", "act": "scrna",
        "base": "scrna_correct",
        "remove_steps": ["S5", "S6", "S11", "S12"],
        "context_overrides": {"S1": {"data_source": "GSE149614"},
                              "S3": {"n_cells": 85210},
                              "S10": {"n_patients": 12}},
        "error_pattern_sources": [],
        "_intent": {"S1": "correct", "S2": "correct", "S3": "correct",
                    "S4": "correct", "S7": "correct", "S8": "correct",
                    "S9": "correct", "S10": "correct"},
    },
]

# ============ pan（10：bmd_pan_011..020）============

SPECS += [
    # 011: 短管线（5）全正确（easy）
    {
        "trajectory_id": "bmd_pan_011", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13",
                         "D14", "D15", "D16"],
        "context_overrides": {"D1": {"n_replicates": 4}},
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 012: 短管线（8）+ EPV 5-10（edge）（easy）
    {
        "trajectory_id": "bmd_pan_012", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D9", "D10", "D11", "D12", "D13", "D14", "D15", "D16"],
        "context_overrides": {"D1": {"n_replicates": 4}},
        "choice_replacements": [PAN_EDGE_EPV],
        "error_pattern_sources": ["pan_edge_epv"],
        "_intent": {"D8": "edge"},
    },
    # 013: 短管线（5）+ TPM（edge）（easy）
    {
        "trajectory_id": "bmd_pan_013", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13",
                         "D14", "D15", "D16"],
        "context_overrides": {"D1": {"n_replicates": 3}},
        "choice_replacements": [PAN_EDGE_TPM],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"D2": "edge"},
    },
    # 014: 全管线 + 2 错（no_ph_test、univariate 宣称独立）（medium）
    {
        "trajectory_id": "bmd_pan_014", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 4}},
        "choice_replacements": [PAN_ERR_PH, PAN_ERR_UNIVARIATE],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D6": "error", "D7": "error"},
    },
    # 015: 全管线 + 2 错（Pearson、方向矛盾未讨论）（medium）
    {
        "trajectory_id": "bmd_pan_015", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 5}},
        "choice_replacements": [PAN_ERR_PEARSON, PAN_ERR_DIRECTION],
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D10": "error", "D15": "edge"},
    },
    # 016: 全管线（17）+ 2 错（no_ph_test、方向矛盾加决策）（hard：n_decisions≥17）
    {
        "trajectory_id": "bmd_pan_016", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 4}},
        "choice_replacements": [PAN_ERR_PH],
        "add_decisions": [D(
            "expression_survival_consistency", "direction_inconsistent_not_discussed",
            "High expression of the gene but protective HR — two results reported separately.",
            {"integration_type": "cross_module"})],
        "error_pattern_sources": ["pan_error", "pan_edge_consistency"],
        "_intent": {"D6": "error", "A1": "edge"},
    },
    # 017: pan_error 语料原样（16 决策多错）（hard）
    {
        "trajectory_id": "bmd_pan_017", "act": "pan",
        "base": "pan_error",
        "context_overrides": {"D1": {"n_replicates": 3}},
        "error_pattern_sources": ["pan_error"],
        "_intent": {"D1": "error", "D2": "edge", "D3": "error", "D6": "error",
                    "D7": "error", "D8": "error", "D9": "error", "D10": "error",
                    "D11": "edge", "D12": "error", "D13": "error",
                    "D15": "edge", "D16": "edge"},
    },
    # 018: 短管线（8）+ 2 错（Student_t、no_correction）（medium）
    {
        "trajectory_id": "bmd_pan_018", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13",
                         "D14", "D15", "D16"],
        "context_overrides": {"D1": {"n_replicates": 3}},
        "choice_replacements": [PAN_ERR_TTEST, PAN_ERR_NOCORR],
        "error_pattern_sources": ["pan_error", "deg_error"],
        "_intent": {"D3": "error", "D4": "error"},
    },
    # 019: 短管线（8）+ 纯度声明为局限（edge）（easy）
    {
        "trajectory_id": "bmd_pan_019", "act": "pan",
        "base": "pan_correct",
        "remove_steps": ["D6", "D7", "D8", "D9", "D10", "D11", "D12", "D13",
                         "D14", "D15", "D16"],
        "context_overrides": {"D1": {"n_replicates": 5}},
        "choice_replacements": [PAN_EDGE_PURITY],
        "error_pattern_sources": ["pan_edge_purity"],
        "_intent": {"D11": "edge"},
    },
    # 020: 全管线边界集中（TPM/padj 0.1/EPV 5-10/纯度局限）（medium）
    {
        "trajectory_id": "bmd_pan_020", "act": "pan",
        "base": "pan_correct",
        "context_overrides": {"D1": {"n_replicates": 3}},
        "choice_replacements": [PAN_EDGE_TPM, PAN_EDGE_SIG,
                                PAN_EDGE_PURITY, PAN_EDGE_EPV],
        "error_pattern_sources": ["deg_error", "pan_error"],
        "_intent": {"D2": "edge", "D5": "edge", "D8": "edge", "D11": "edge"},
    },
]

# ============ deg（10：bmd_deg_009..018）============

DEG_ERR_NOFILT = {"step": "s1", "choice": "no_filtering",
                  "rationale": "Skipping filtering to retain all genes for "
                               "maximum discovery power."}
DEG_ERR_TPM = {"step": "s2", "choice": "TPM",
               "rationale": "TPM is the standard normalization for RNA-seq data."}
DEG_ERR_TTEST = {"step": "s3", "choice": "Student_t_test",
                 "rationale": "t-test is the classic differential test, simple and direct."}
DEG_ERR_PRAW = {"step": "s5", "choice": "p_raw <= 0.05",
                "rationale": "Using raw p<0.05 as the cutoff for DEG selection."}
DEG_EDGE_BY = {"step": "s4", "choice": "BY",
               "rationale": "Benjamini-Yekutieli — more conservative FDR control."}
DEG_EDGE_SIG = {"step": "s5", "choice": "padj <= 0.1, |logFC| >= 0.5",
                "rationale": "Relaxed threshold to capture more candidates for validation."}

SPECS += [
    # 009: 全正确（easy）
    {
        "trajectory_id": "bmd_deg_009", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 010: 2 边界（TPM、BY）（easy）
    {
        "trajectory_id": "bmd_deg_010", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "choice_replacements": [DEG_ERR_TPM, DEG_EDGE_BY],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s2": "edge", "s4": "edge"},
    },
    # 011: 1 错（no_filtering）（easy）
    {
        "trajectory_id": "bmd_deg_011", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "choice_replacements": [DEG_ERR_NOFILT],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error"},
    },
    # 012: 2 错（no_filtering、p_raw）（medium）
    {
        "trajectory_id": "bmd_deg_012", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3},
                              "s5": {"n_replicates": 3}},
        "choice_replacements": [DEG_ERR_NOFILT, DEG_ERR_PRAW],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error", "s5": "error"},
    },
    # 013: 3 错（no_filtering、Student_t、p_raw）（hard）
    {
        "trajectory_id": "bmd_deg_013", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3},
                              "s5": {"n_replicates": 3}},
        "choice_replacements": [DEG_ERR_NOFILT, DEG_ERR_TTEST, DEG_ERR_PRAW],
        "error_pattern_sources": ["deg_error", "pan_error"],
        "_intent": {"s1": "error", "s3": "error", "s5": "error"},
    },
    # 014: 2 错 + 1 边界（no_filtering、TPM、p_raw）（medium）
    {
        "trajectory_id": "bmd_deg_014", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3},
                              "s5": {"n_replicates": 3}},
        "choice_replacements": [DEG_ERR_NOFILT, DEG_ERR_TPM, DEG_ERR_PRAW],
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error", "s2": "edge", "s5": "error"},
    },
    # 015: 全正确（limma-voom 变体）（easy）
    {
        "trajectory_id": "bmd_deg_015", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "choice_replacements": [
            {"step": "s3", "choice": "limma_voom",
             "rationale": "limma-voom handles count-based RNA-seq well — standard choice."},
        ],
        "error_pattern_sources": [],
        "_intent": {},
    },
    # 016: 2 错（no_filtering、Student_t）（medium）
    {
        "trajectory_id": "bmd_deg_016", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3},
                              "s5": {"n_replicates": 3}},
        "choice_replacements": [DEG_ERR_NOFILT, DEG_ERR_TTEST],
        "error_pattern_sources": ["deg_error", "pan_error"],
        "_intent": {"s1": "error", "s3": "error"},
    },
    # 017: 2 边界（BY、padj 0.1）（easy）
    {
        "trajectory_id": "bmd_deg_017", "act": "deg",
        "base": "deg_correct",
        "context_overrides": {"s1": {"n_replicates": 6}, "s3": {"n_replicates": 6},
                              "s5": {"n_replicates": 6}},
        "choice_replacements": [DEG_EDGE_BY, DEG_EDGE_SIG],
        "error_pattern_sources": [],
        "_intent": {"s4": "edge", "s5": "edge"},
    },
    # 018: deg_error 语料原样（4 错）（hard）
    {
        "trajectory_id": "bmd_deg_018", "act": "deg",
        "base": "deg_error",
        "context_overrides": {"s1": {"n_replicates": 3}, "s3": {"n_replicates": 3},
                              "s5": {"n_replicates": 3}},
        "error_pattern_sources": ["deg_error"],
        "_intent": {"s1": "error", "s2": "edge", "s4": "error", "s5": "error"},
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
        out = write_draft(
            spec, corpus, tasks_dir=args.tasks_dir,
            reviewed_by="window-F review",
            reviewed_at="2026-08-16",
        )
        n_drafts += 1
        draft = json.loads(out.read_text(encoding="utf-8"))
        rows.append((draft["trajectory_id"], draft["act"], len(draft["decisions"])))

    print(f"✅ 生成 {n_drafts} 条批 2 任务草稿（prompt_hash={prompt_hash()}）")
    print(f"{'task':24s} act    n_decisions")
    for tid, act, n in rows:
        print(f"{tid:24s} {act:5s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
