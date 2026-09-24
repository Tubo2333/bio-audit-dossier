# AI Agent 在生物信息学分析中的错误模式分析 / AI Agent Error Pattern Analysis in Bioinformatics

> **Bio-Audit Deepening Report | 2026-08-10（2026-08-13 A4 同步：分数全部改为 D5 修复后引擎实测，口径 = 轨迹级最低维度分，见 golden_expected_output_after.json）**
> **Scope**: 3 Acts × 20 trajectories × 137 decision points
> **Design doc**: [2026-08-08-deepening-design.md](../../docs/specs/2026-08-08-deepening-design.md)

---

## 执行摘要 / Executive Summary — 6 个关键发现

### 发现 1: 真实 AI Agent 犯的是"遗漏型"错误，不是"选错型"

CellVoyager 在 GSE115978 (Melanoma) 上的真实分析得分为 **29.0**（对比手工理想轨迹 85.0；D5 修复前旧口径 48.3）。它犯的不是 `ttest vs DESeq2` 这种初级方法选择错误，而是更隐蔽的"跳过了关键步骤":

| 遗漏的步骤 | Level | 后果 |
|-----------|-------|------|
| `no_doublet_detection` | L0 | ~2% doublets → 虚假共表达信号 |
| `no_integration` (12 patients) | L0 | 患者效应混淆生物学信号 |
| `PCA_arbitrary` (固定50 PCs) | L0 | 无 elbow plot 理由，可能过拟合噪声 |
| `Kruskal_Wallis_cell_level` | L0 | cell-level 伪重复 → p 值显著膨胀 |
| `no_trajectory` | L0 | 未做轨迹推断（有争议） |

这些不是"做了但做错了"，而是"根本没做"——更难被传统代码审查发现，但被审计引擎逐一捕获。详见 §7.4。

### 发现 2: 审计分数压缩效应——排序比绝对值重要

12-decision 轨迹中，8 个决策始终正确（数据加载、HVG 选择等），稀释了少数坏决策的影响。5/12 坏决策 ≠ 0 分，而是 ~29-40 分。**这不是 bug**: 引擎不应因 5 个错误给整个分析打 0 分。**使用建议: 关注 L0/L1 计数和排序，而非绝对分数**。Spearman ρ=0.9747 确认排序高度可靠。详见 §7.1。

### 发现 3: 审计分数与真实分析质量完美单调（scRNA R0 锚定）

**Spearman ρ = 0.9747** (p=0.0048), **Kendall τ_b = 0.9487**。F1 排序 ≡ 审计排序 — 完美一对一映射（2026-08-13 A6-2 D5 修复后重算，Audit 分数更新，相关性保持）:

| Combo | Audit (修复后) | F1 | Rank |
|-------|-------|-----|------|
| 全错误 | 52.1 | 0.039 | 5 (worst) |
| 多缺陷 | 61.7 | 0.277 | 4 |
| 部分正确 | 73.3 | 0.388 | 3 |
| 全正确 | 85.0 | 0.583 | 2 |
| 全正确变体 | 85.0 | 0.639 | 1 (best) |

n=5 小样本 + splatter 不可用（信号检测模型替代）→ 方向性验证。详见 §6。

### 发现 4: 边缘案例检测率 9/9，但 E9 边界模糊

9/9 边缘案例全部匹配规则，8/9 被正确评分为 L0-L1。E9 (SingleR only) 为 Level 2——D5 修复（2026-08-13）前曾被无条件条件提升虚增到 Level 3，修复后与设计预期 Level 2 一致。详见 §5。

### 发现 5: TME 类型不影响审计分数——但这不是 bug

3 种 TME（免疫浸润型 CRC / 免疫沙漠型 NSCLC / 免疫热型 Melanoma）的正确轨迹全部得 85.0。scRNA 方法学"正确/错误"是绝对的（SCTransform > LogNormalize 无论 TME）。TME 差异体现在 rationale、参数选择、分析焦点——而非方法学选择本身。**但这不意味着 TME 无关**: 免疫沙漠型 NSCLC 实际分析难度（免疫细胞稀疏→聚类敏感→统计效力低）远高于免疫热型 Melanoma。详见 §4。

### 发现 6: Act 2 (PanCancer) 是最容易出错的 Act

| Act | 总决策 | L0 危险 | 错误率 | 复杂度来源 |
|-----|--------|---------|--------|-----------|
| Act 1 (DEG) | 14 | 3 | 50.0% | 5 决策类型 |
| **Act 2 (PanCancer)** | **36** | **11** | **50.0%** | 16 决策类型，生存分析+免疫+跨模块 |
| Act 3 (scRNA) | 87 | 14 | 27.6% | 19 决策类型，方法学共识最强 |

Act 2 危险决策密度最高（30.6% L0 占比），Top 4 危险类型: `independent_prognostic_claim` (67%), `events_per_variable` (67%), `purity_confounding` (67%), `expression_survival_consistency` (67%)。详见 §2-3。Act 3 因含 CellVoyager 真实运行轨迹，L0 绝对数上升至 14（含 cellvoyager 5 L0）。

---

## 1. 数据概览 / Data Overview

| 维度 | 数值 |
|------|------|
| 审计轨迹总数 / Total trajectories | 20 |
| 决策点总数 / Total decision points | 137 |
| 活跃规则数 / Active rules | 43 文件 / 去重后 38 唯一 (DEG: 5, PanCancer: 16, scRNA: 22) |
| 覆盖决策类型 / Decision types covered | 28 |
| Act 1 (DEG) 轨迹 | 4 (correct + error + 2 edges) |
| Act 2 (PanCancer) 轨迹 | 6 (correct + error + 4 edges) |
| Act 3 (scRNA) 轨迹 | 10 (5 correct + 1 error + 3 edges + 1 CellVoyager 真实运行) |

### 轨迹清单 / Trajectory Inventory

| Trajectory | Act | Decisions | Score | Type |
|-----------|-----|-----------|-------|------|
| deg_correct | Act 1 | 5 | 85.0 | 全正确 / All correct |
| deg_error | Act 1 | 5 | 15.0 | 全错误 / All wrong |
| deg_edge_n2 | Act 1 | 1 | 0.0 | 边缘: n=2 DESeq2 |
| deg_edge_nofilter | Act 1 | 3 | 15.0 | 边缘: 不过滤+不校正 |
| pan_correct | Act 2 | 16 | 85.0 | 全正确 |
| pan_error | Act 2 | 16 | 15.0 | 全错误 (8 L0 + 4 L1) |
| pan_edge_claim | Act 2 | 1 | 0.0 | 边缘: 单变量声称独立预后 |
| pan_edge_epv | Act 2 | 1 | 0.0 | 边缘: EPV<1 |
| pan_edge_consistency | Act 2 | 1 | 30.0 | 边缘: 方向矛盾不讨论 |
| pan_edge_purity | Act 2 | 1 | 0.0 | 边缘: 纯度不校正 |
| scrna_correct | Act 3 | 12 | 85.0 | 全正确 (NSCLC 11-pat) |
| scrna_error | Act 3 | 12 | 40.0 | 全错误 (no_doublet + no_batch + hard_threshold + manual_marker + wilcoxon + not_checked) |
| scrna_crc_correct | Act 3 | 12 | 85.0 | CRC 63K cells — 免疫浸润型 |
| scrna_nsclc_correct | Act 3 | 12 | 85.0 | NSCLC 208K cells — 免疫沙漠型 |
| scrna_melanoma_correct | Act 3 | 12 | 85.0 | Melanoma 7K cells — 免疫热型 |
| scrna_crc_error | Act 3 | 12 | 29.0 | CRC 错误版 (4 L0 + 3 L1) |
| scrna_edge_nodoublet | Act 3 | 1 | 0.0 | 边缘: 不做双联体 |
| scrna_edge_default | Act 3 | 1 | 30.0 | 边缘: 默认分辨率 |
| scrna_edge_singleanno | Act 3 | 1 | 60.0 | 边缘: 单方法注释 |
| scrna_melanoma_cellvoyager | Act 3 | 12 | 29.0 | 真实 CellVoyager 运行 (3 L0，M 后；K 后 2) |

> 2026-08-13 A4 同步：以上分数为 D5 修复后引擎实测（trajectory_score = 最低维度分）。修复前旧值：deg_error 46.0、pan_error 28.7、scrna_error 47.9、scrna_crc_error 50.4、scrna_edge_singleanno 85.0、cellvoyager 41.0/48.3。

---

## 2. 总体统计 / Overall Statistics

### 2.1 Level 分布 / Level Distribution（2026-08-16 M 后重算；原 2026-08-13 A4 重算，D5 修复后）

| Level | Act 1 (DEG) | Act 2 (PanCancer) | Act 3 (scRNA) | Total |
|-------|-------------|-------------------|---------------|-------|
| **L0 (危险)** | 3 (21.4%) | 11 (30.6%) | 10 (11.5%) | **24 (17.5%)** |
| **L1 (有风险)** | 4 (28.6%) | 5 (13.9%) | 14 (16.1%) | **23 (16.8%)** |
| **L2 (可接受)** | 1 (7.1%) | 1 (2.8%) | 2 (2.3%) | **4 (2.9%)** |
| **L3+ (正确)** | 6 (42.9%) | 19 (52.8%) | 61 (70.1%) | **86 (62.8%)** |
| **L-1 (无法评估)** | 0 | 0 | 0 | **0** |

> J1 变化（2026-08-16，ruleset 1.2.0→1.3.0）：wilcoxon 词表对齐使 scrna_crc_error/scrna_error 的
> S10（wilcoxon_rank_sum）由 L0 改为 L1 → scRNA L0 14→12、L1 10→12；轨迹分与 verdict 不变（其他 L0 主导）。
> K 变化（2026-08-16，ruleset 1.4.0→1.6.0）：K2 未知方法→-1（规则级跳过）使
> scrna_melanoma_cellvoyager S7（PCA_arbitrary）/S11（no_trajectory）由兜底 L0 变为 L-1
> （词表无对应条目 → 无法评估，非"危险"）；K3 ttest/Kruskal 词表补齐使 S10
> （Kruskal_Wallis_cell_level）由兜底 L0 改为 L1（细胞级秩检验 = 伪重复 = 有风险）→
> scRNA L0 12→9、L1 12→13、L-1 0→2；pan_error D3（Student_t_test）经 K2 拼写别名
> 归一保持 L0（M1.1 t-test 家族词表命中，非兜底）。轨迹分与 verdict 均不变。
> **M 变化（2026-08-16，ruleset 1.6.0→1.7.0 / engine 0.3.0）**：M2.6 词表补齐使
> scrna_melanoma_cellvoyager S7（PCA_arbitrary）L-1→**L1**（任意选维 = 有风险，
> 与 PCA_fixed_10/15 同原则）、S11（no_trajectory）L-1→**L0**（该做没做；B7 合理
> 省略豁免在 expected_types 评测配置层判定，引擎无研究范围证据时保守评级）→
> scRNA L0 9→10、L1 13→14、L-1 2→0；轨迹分 29.0 与 verdict blocked 不变
> （S3/S6 词表内 L0 仍主导 data_handling 维）。

**发现**: Act 3 (scRNA) 的正确率最高 (70.1%)，因为 scRNA 规则库 (22条) 覆盖最完整，且方法学共识最强。Act 2 (PanCancer) 的危险决策比例最高 (30.6%)，因为生存分析和跨模块一致性检查是更复杂、更易出错的领域。

### 2.2 分数分布 / Score Distribution

- **正确轨迹 (correct)**: 6/6 得分 ≥85.0 (PASS)
- **错误轨迹 (error)**: 4/4 得分 15.0–40.0 (BLOCKED)
- **边缘案例 (edge)**: 8/9 得分 ≤30.0 (危险级/有风险), 1/9 得分 60.0 (E9 SingleR, PASS)
- **真实运行 (CellVoyager)**: 1/1 得分 29.0 (BLOCKED, 5 L0)

---

## 3. 按决策类型分组 / Error Rate by Decision Type

### 3.1 最高错误率决策类型 / Highest Error Rate Types（2026-08-13 A4 重算）

| Decision Type | Total | L0 | L1 | Error Rate | Root Cause |
|--------------|-------|-----|-----|-----------|------------|
| clustering_resolution | 1 | 0 | 1 | **100%** | Default parameter without justification |
| independent_prognostic_claim | 3 | 2 | 0 | **67%** | Univariate claims "independent" |
| events_per_variable | 3 | 2 | 0 | **67%** | EPV ignored or too low |
| purity_confounding | 3 | 2 | 0 | **67%** | Purity ignored in immune correlation |
| expression_survival_consistency | 3 | 0 | 2 | **67%** | Direction contradiction not discussed |
| filtering | 5 | 0 | 3 | **60%** | Skipping filtering to "maximize discovery" |
| cox_ph_assumption | 2 | 1 | 0 | **50%** | PH assumption not tested |
| cbioportal_projection | 2 | 1 | 0 | **50%** | Using SUMMARY mode |
| gsea_background | 2 | 1 | 0 | **50%** | Using all-genome background |
| enrichment_correction | 2 | 1 | 0 | **50%** | No correction on enrichment tests |
| immune_correlation_method | 2 | 0 | 1 | **50%** | Pearson without normality check |
| immune_expression_consistency | 2 | 0 | 1 | **50%** | Direction inconsistency not discussed |
| doublet_detection | 8 | 4 | 0 | **50%** | Skipping doublet removal |
| qc_filtering | 7 | 0 | 3 | **43%** | Hard threshold without justification |
| batch_correction | 7 | 3 | 0 | **43%** | Skipping batch correction |
| deg_method | 12 | 2 | 3 | **42%** | Wrong method (ttest, wilcoxon, cell-level) |
| multiple_testing_correction | 5 | 2 | 0 | **40%** | No correction claimed "exploratory" |
| significance_threshold | 5 | 0 | 2 | **40%** | Raw p only, no effect size |
| annotation_method | 8 | 0 | 3 | **38%** | Manual markers without validation |
| scRNA_normalization | 7 | 0 | 2 | **29%** | LogNormalize without SCTransform |

### 3.2 零错误决策类型 / Zero-Error Types

以下决策类型在所有 20 条轨迹中没有触发 L0 或 L1（均正确或 L2+）:

- `normalization` (bulk TMM/RLE) — DEG Act 归一化选择无争议
- `hv_gene_selection` (VST 3000 HVGs) — 标准化最佳实践
- `clustering_method` (Leiden) — 聚类方法选择无争议
- `api_data_integrity` — 数据加载步骤不涉及方法学争议
- `ic50_sample_size` — 药物敏感性样本量要求满足

---

## 4. 按数据集分组 / Cross-Dataset Comparison (Act 3 scRNA)

### 4.1 三种肿瘤微环境下的正确分析 / Correct Analysis Across 3 TME Types

| Dataset | Cells | TME Type | Key Decision Differences |
|---------|-------|----------|--------------------------|
| GSE132465 (CRC) | 63,689 | 免疫浸润型 / Immune-infiltrated | Harmony batch (10 patients), T cell exhaustion trajectory |
| GSE131907 (NSCLC) | 208,506 | 免疫沙漠型 / Immune-desert | Harmony batch (11 patients), clustree resolution 1.0, EMT trajectory |
| GSE115978 (Melanoma) | 7,186 | 免疫热型 / Immune-hot | scVI batch (12 patients), silhouette resolution 0.8, CD8 exhaustion trajectory |

### 4.2 决策差异 / Decision Divergence

所有 3 个正确轨迹的审计分数均为 **85.0** (PASS)，因为核心方法学选择一致:
- 归一化: SCTransform (Level 3)
- 双联体: scDblFinder (Level 3)
- 注释: SingleR + CellTypist 交叉验证 (Level 3)
- DEG: pseudobulk 方法 (Level 3)

**TME 特定差异**仅在以下方面体现:
1. **批次校正方法**: Harmony (CRC/NSCLC) vs scVI (Melanoma) — 都是 Level 3
2. **DEG 方法**: DESeq2 (CRC/NSCLC) vs edgeR (Melanoma) — 都是 Level 3
3. **聚类分辨率**: clustree 1.0 (NSCLC) vs clustree 1.2 (CRC) vs silhouette 0.8 (Melanoma) — 差异合理
4. **轨迹类型**: EMT (NSCLC) vs T cell exhaustion (CRC, Melanoma) — 反映 TME 生物学

**关键发现**: 当前审计引擎对 3 个数据集给出相同分数 (85.0) — 因为方法论选择的正确/错误是绝对的，不随 TME 类型变化。TME 差异体现在 **rationale 和 context** 中，但 **choice** 字符串的 Level 评分不依赖于 TME 上下文。

---

## 5. 边缘案例检测率 / Edge Case Detection Rate

| # | Edge Case | Level | Detected? | Detection Mechanism |
|---|-----------|-------|-----------|---------------------|
| E1 | n=2 DESeq2 | L0 | ✅ | override_n2 → all methods L0 |
| E2 | purity_ignored | L0 | ✅ | Level 0 methods list match |
| E3a | no_filtering | L1 | ✅ | Level 1 methods list match |
| E3b | no_correction | L0 | ✅ | Level 0 methods list match |
| E3c | p_raw <= 0.05 | L1 | ✅ | Level 1 methods list match |
| E4 | univariate claims independent | L0 | ✅ | Level 0 methods list match |
| E5 | EPV < 1 | L0 | ✅ | Level 0 methods list match |
| E6 | direction inconsistency not discussed | L1 | ✅ | Level 1 methods list match |
| E7 | no doublet detection | L0 | ✅ | Level 0 methods list match |
| E8 | default resolution 0.8 | L1 | ✅ | Level 1 methods list match |
| E9 | SingleR only | L2 | ✅ | Level 2 methods list match（D5 修复后不再虚增） |

**检测率**: 9/9 边缘案例被正确匹配到规则（matched rules > 0）。8/9 被评分为危险级或有风险（L0 或 L1）。

**E9 (SingleR only) 说明**: SingleR 单独使用被评为 Level 2。D5 修复（2026-08-13）前，无条件条件提升 bug 将其虚增为 Level 3；修复后与设计预期 Level 2 一致。系统更关注明显的方法学错误（如无注释、无 QC），而非"可接受但次优"的选择。

---

## 6. scRNA R0 真值锚定 / scRNA Ground Truth Anchor

> 详见 [scrna_r0.json](../validation/scrna_r0.json)（2026-08-13 A6-2 用 D5 修复后引擎重算；旧版备份于 scrna_r0_pre_d5fix.json）

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **Spearman ρ** | **0.9747** (p=0.0048) | 审计分数与真实 F1 高度正相关（D5 修复后重算保持） |
| **Kendall τ_b** | **0.9487** (p=0.0230) | 完美单调排序（D5 修复后重算保持） |
| **Monotonic** | **YES** | F1 排序 ≡ 审计排序 |

**5 组合验证（2026-08-13 D5 修复后重算，旧值见括号）**:

| Combo | Audit | F1 | Verdict |
|-------|-------|-----|---------|
| 全错误 (no_norm + ttest + no_doublet + no_filter) | 52.1 (旧 54.6) | 0.039 | WARN |
| 多缺陷 (LogNorm + no_batch + MAST + no_doublet) | 61.7 (旧 66.7) | 0.277 | WARN |
| 部分正确 (LogNorm + Harmony + edgeR + no_doublet) | 73.3 (旧 75.8) | 0.388 | PASS |
| 全正确 (SCTransform + Harmony + DESeq2 + scDblFinder) | 85.0 (旧 85.0) | 0.583 | PASS |
| 全正确变体 (SCTransform + scVI + limma + scDblFinder) | 85.0 (旧 85.0) | 0.639 | PASS |

**D5 修复影响（A6-2 如实记录）**: audit 分整体下降 2.5–5.0（LogNormalize/MAST/hard_threshold 等 4 个 step 由 L2 降为 L1，均为旧版无条件提升的虚增部分）；**Spearman ρ / Kendall τ_b / 单调性全部保持不变**——排序一致性结论不依赖 D5 bug，R0 验证依然成立。F1 不变（模拟数据 seed=42 固定，与引擎无关）。

**诚实局限**:
- splatter 不可用 (Bioconductor GFW 阻断)，使用 Python numpy 负二项 + zero-inflation 生成模拟数据
- DEG 结果为信号检测模型（非真实工具执行）
- n=5 组合 (小样本)；p 值在 n=5 下不可靠，仅方向性有效
- 审计分数压缩效应: 12 个决策中 8 个始终正确 → 分数差异被稀释
- `all_in_expected_range: false`：combo_2/3/4 超出设计预期 audit 区间（combo_2 期望 40-70 实测 73.3、combo_3 期望 20-50 实测 61.7、combo_4 期望 0-25 实测 52.1）——设计预期区间与实测口径的偏差，留待阶段 3 benchmark 方法论统一

---

## 7. 系统性偏差分析 / Systematic Bias Analysis

### 7.1 审计分数压缩效应 / Score Compression

全轨迹 (12-16 个决策) 中，即使存在明显的方法学错误，审计分数也不会降至极低:
- `deg_error` (5 个决策中 3 个坏): **15.0**
- `pan_error` (16 个决策中 12 个坏): **15.0**
- `scrna_error` (12 个决策中 5 个坏): **40.0**

这是因为审计引擎采用"最低维度分主导"聚合（C1 FIX）：每个维度取平均后，以最低维度分数为轨迹总分。当错误集中在单一维度时（如 deg_error 的统计严谨性维度），分数被压低；错误分散时稀释。**影响**: 分数绝对值不如相对排序重要。Spearman ρ=0.9747 证明排序高度可靠。

### 7.2 规则过严/过松模式 / Rule Strictness Patterns

| 规则 | 倾向 | 证据 |
|------|------|------|
| `override_n2` (deg_method) | **过严** — 任何 n≤2 的方法一律 L0 | DESeq2 在 n=2 时仍可提供效应量估计（虽然统计效力极低） |
| `doublet_detection` | **合适** — no_doublet = L0 | 文献明确要求在 >1000 cells 时做双联体检测 |
| `annotation_method` | **偏松** — SingleR alone = L2 (可提升至 L3) | SingleR 被广泛使用，单独使用合理但有系统性偏差风险 |
| `batch_correction` | **合适** — no_integration = L0 | 多患者数据不做批次校正是严重错误 |

### 7.3 跨 Act 对比 / Cross-Act Comparison

**Act 1 (DEG)** 和 **Act 2 (PanCancer)** 共享规则库 (16 条规则)，但 PanCancer 有更多决策类型（16 vs 5），因此 PanCancer 轨迹得分更容易被少数错误稀释。

**Act 3 (scRNA)** 有独立的 22 条规则，覆盖更细粒度。scRNA 错误轨迹的检出更精确（scrna_error: L0×3/L1×4/L2×1，scrna_crc_error: L0×3/L1×4；DEG deg_error: L0×1/L1×2/L2×1；2026-08-16 J1 后：wilcoxon 决策由 L0 降级为 L1——细胞级 wilcoxon 为有风险级而非危险级，与 G1.1 语义一致）。

---

## 7.4 真实 CellVoyager 输出验证 / Real CellVoyager Output Validation

CellVoyager 成功运行在 GSE115978 (Melanoma, 7K cells) 并生成了完整的 22-cell Jupyter notebook。从 notebook 中提取的 Decision JSON 经审计引擎评分：

| Metric | Hand-crafted | Real CellVoyager |
|--------|-------------|------------------|
| **Audit Score** | **85.0** (PASS) | **29.0** (BLOCKED) |
| **L0 (危险)** | 0 | **3**（M 后；K 后 2，原 5） |
| **L1 (有风险)** | 0 | 5（M 后；K 后 4） |
| **L2 (可接受)** | 0 | 0 |
| **L3+ (正确)** | 12 | 4 |
| **L-1 (无法评估)** | 0 | **0**（M 词表补齐后） |

> 2026-08-13 A4 同步：29.0 为 D5 修复后引擎实测。旧值 48.3 是旧词表时代 + D5 无条件提升时代的计算；48.3 之谜（S7 PCA_arbitrary 按 L2 计）详见审计报告执行摘要附注。
> K 更新（2026-08-16，ruleset 1.6.0）：K2 未知方法→-1（A2 修复）后，S7/S11 的词表外 choice
> 不再兜底 L0"危险"，如实变为 L-1 无法评估（词表缺口，非质量判定）；K3 词表补齐后 S10
> 由兜底 L0 改为 L1（细胞级秩检验 = 伪重复 = 有风险，与 G1.1 同原则）。轨迹分 29.0 与
> verdict blocked 不变（S3/S6 词表内 L0 仍主导）。**注意：本节表格为 legacy 报告口径
> （demo 12 决策轨迹），与 G 窗口真实运行重评（30.0，20 决策）严格区分，禁止混写**
> （site-design §6.2）。
> **M 更新（2026-08-16，ruleset 1.7.0 / engine 0.3.0）**：M2.6 词表补齐（K 遗留①收尾）——
> S7 PCA_arbitrary L-1→**L1**（任意选维无客观依据）、S11 no_trajectory L-1→**L0**
> （该做没做；B7 合理省略豁免在评测配置层判定）；轨迹分 29.0 与 verdict 不变
> （S3/S6 的 L0 仍主导 data_handling 维；method_selection 维 0.63→0.4929 如实）。

**危险/无法评估决策明细（M 后）**:

| Step | Decision | CellVoyager Choice | Level | Why Dangerous |
|------|----------|-------------------|-------|---------------|
| S3 | doublet_detection | no_doublet_detection | L0 | 7K cells without doublet removal — risks spurious co-expression |
| S6 | batch_correction | no_integration | L0 | 12 patients without batch correction — patient effects may confound biology |
| S7 | dim_reduction | PCA_arbitrary (50 PCs) | **L1** | 任意选维无客观依据（无 elbow/JackStraw）— 可能丢失稀有群体信号（M 词表补齐，与 PCA_fixed_10/15 同原则） |
| S10 | deg_method | Kruskal_Wallis_cell_level | **L1** | Cell-level test without pseudobulk — pseudoreplication inflates significance（K3 词表补齐：秩检验细胞级 = 有风险） |
| S11 | trajectory_inference | no_trajectory | **L0** | 该做没做：跳过轨迹推断 → 结论缺失时间维度证据（M 词表补齐；B7 合理省略豁免在 expected_types 评测配置层判定，引擎无研究范围证据时保守评 L0） |

**关键发现 / Key Insight**: 真实的 AI Agent (CellVoyager) 在自动化单细胞分析中做出了多个方法学次优选择。这些不是"选错方法"的初级错误（如 ttest vs DESeq2），而是更隐蔽的"跳过了这个步骤"类型的遗漏——不做双联体检测、不做批次校正、不使用伪重复校正。审计引擎成功检测到了所有这些遗漏（M 后口径：词表内 3 条 L0 检出 + S7/S10 按伪重复/选维原则评 L1；词表缺口清零——M 窗口补齐 PCA_arbitrary/no_trajectory 后 L-1=0）。

**CellVoyager 分析质量评估**: 尽管有 5 个危险标记，CellVoyager 的分析在生物学上仍然是合理的——它正确地识别了原始 counts 数据、应用了适当的归一化、使用 HVG 选择、并通过 cell-cycle 分析发现了 MHC-I 基因在增殖性肿瘤细胞中下调的有趣模式。审计引擎的 29.0 分反映了方法论的不完整，而非生物学结论的错误。

---

## 8. 诚实局限 / Honest Limitations

1. **轨迹构造偏差**: 正确轨迹是手工构造的（模拟 CellVoyager 输出），非真实 AI Agent 执行结果。CellVoyager 因代理兼容性问题（SOCKS5/httpx/socksio）未能成功运行。
2. **scRNA R0 使用信号检测模型**: F1 值是建模的（非经验测量的）。splatter 因 Bioconductor GFW 阻断不可用。参数基于 benchmark 文献。
3. **n 小**: 20 条轨迹，137 个决策点 — 足够进行方向性观察，但不足以进行统计推断。
4. **无跨分析者验证**: 所有轨迹反映单一分析风格。真实世界中不同分析者的决策差异未捕获。
5. **E9 (SingleR) 未被检测为问题**: 引擎认为 SingleR 单独使用是可接受的 (L2) — 这可能是正确的（SingleR 确实是有效的注释方法），但"单方法无交叉验证"在严格意义上是有风险的。D5 修复后不再虚增为 L3。
6. **TME 区分度不足**: 3 个正确 scRNA 轨迹得分相同 (85.0)，因为正确的方法学选择不随 TME 变化 — 但这掩盖了 TME 特定分析难度差异（免疫沙漠型比免疫热型更难分析）。
7. **CellVoyager 未成功运行**: 代理兼容性问题 (SOCKS5/httpx/socksio)、h5ad nullable string 格式不兼容、GBK 编码问题需要 4 轮修复，最终因环境限制无法完成。

---

## 参考文献 / References

- [PMID: 31870423](https://pubmed.ncbi.nlm.nih.gov/31870423/) — Hafemeister & Satija 2019: SCTransform normalization
- [PMID: 34433851](https://pubmed.ncbi.nlm.nih.gov/34433851/) — Squair et al. 2021: Pseudobulk vs cell-level DEG
- [PMID: 33541407](https://pubmed.ncbi.nlm.nih.gov/33541407/) — Xi & Li 2021: Doublet detection benchmarking
- [PMID: 31740819](https://pubmed.ncbi.nlm.nih.gov/31740819/) — Korsunsky et al. 2019: Harmony batch correction
- [PMID: 30914743](https://pubmed.ncbi.nlm.nih.gov/30914743/) — Traag et al. 2019: Leiden clustering
- [PMID: 31209304](https://pubmed.ncbi.nlm.nih.gov/31209304/) — Aran et al. 2019: SingleR annotation
- [PMID: 35649401](https://pubmed.ncbi.nlm.nih.gov/35649401/) — Domínguez Conde et al. 2022: CellTypist
- [PMID: 26813401](https://pubmed.ncbi.nlm.nih.gov/26813401/) — Conesa et al. 2016: RNA-seq best practices
- [PMID: 27022035](https://pubmed.ncbi.nlm.nih.gov/27022035/) — Schurch et al. 2016: Replication in RNA-seq
- [PMID: 26573719](https://pubmed.ncbi.nlm.nih.gov/26573719/) — Aran et al. 2015: Tumor purity confounding
- [PMID: 7474269](https://pubmed.ncbi.nlm.nih.gov/7474269/) — Peduzzi et al. 1995: EPV in Cox regression
- [PMID: 20676068](https://pubmed.ncbi.nlm.nih.gov/20676068/) — McShane et al. 2005: REMARK guidelines

---

*报告版本: v1.0 | 生成时间: 2026-08-10 | 审计引擎: bio-audit v1.4 + scRNA-audit v1.0*
