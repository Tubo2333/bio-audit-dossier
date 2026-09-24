"""Page 3: 审计报告（薄壳 v2）— Bilingual Audit Report。

相对 fullflow-demo/src/ui/pages/03_report.py：
- 导出报告修正为合法 JSON（审计 D1：原 str(dict) 非法 JSON）
- R0 卡保持 A6 状态（scrna_r0.json 实测数字 + 诚实局限）
- 标题与 verdict 口径去「科学验证」类表述（P-04 v0.2 §4.3 / P-02 §2.2-7）
"""
import json

import streamlit as st

st.set_page_config(page_title="Bio-Audit — 审计报告 / Audit Report", page_icon="📊", layout="wide")

if "audit_result" not in st.session_state or st.session_state.audit_result is None:
    st.warning("请先运行审计 / Please run audit first.")
    st.stop()

result = st.session_state.audit_result
demo_mode = st.session_state.get("demo_mode", "correct")

# ── Header ──
# P-04 v0.2 §4.3 / P-02 §2.2-7：报告标题去「科学验证」类表述——
# 本报告呈现的是**审计判定**（决策等级的聚合与分级），不是对科学正确性的验证结论。
st.title("审计报告 / Audit Report")
st.caption(
    f"Bio-Audit v2 (B1) | "
    f"模式/Mode: {'正确分析 Correct' if demo_mode == 'correct' else '典型错误分析 Error'}"
)
st.markdown("---")

# ── Overall Score ──
score = result["trajectory_score"]
verdict = result["eval_verdict"]

# P-04 v0.2 §4.3：verdict 文案去「通过科学验证／科学决策符合共识」类科学结论口径——
# 审计评分为决策等级的聚合，不替科学正确性背书。
VERDICT_INFO = {
    "pass": ("✅ PASS — 各决策均达可判定等级 / All decisions at judgeable levels",
             "#00B07C", "该分析的各决策均达可判定等级（决策等级的聚合），非科学正确性结论。"),
    "blocked": ("⛔ BLOCKED — 存在致命科学错误 / Fatal scientific errors detected",
                "#8B0000", "该分析存在 Level 0(危险级)决策错误, 分析结果不可信。必须修正后重新分析。"),
    "needs_correction": ("⚠ NEEDS CORRECTION — 需要修正 / Corrections required",
                         "#E8A000", "该分析存在需要修正的科学问题, 建议修正后再使用分析结果。"),
}

verdict_label, verdict_color, verdict_explain = VERDICT_INFO.get(
    verdict, (verdict, "#808080", "")
)

col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    # P-04 v0.2 §4.3：结论位＝DOM 渲染序首个结论性元素。
    # 故先渲染 verdict（结论），再渲染分数；分数单位去「/100 总分」类表述，
    # 并就地声明其为决策等级的聚合、不替代四轴分列。
    st.markdown(
        f"<h3 style='text-align:center;color:{verdict_color}'>{verdict_label}</h3>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<h1 style='text-align:center;color:{verdict_color}'>{score:.0f}"
        f"<span style='font-size:20px'> 决策等级聚合</span></h1>",
        unsafe_allow_html=True,
    )
    st.caption(
        "本数值为**决策等级的聚合**（审计评分，口径：audit 0–100 聚合值），"
        "不是科学总分、质量排名、置信度，也不是百分比合格率；"
        "**不替代 P-02 §4.7 四轴分列**（inference_type / explanation_depth / scope / "
        "validation）——该四轴本期未展示。"
    )
    st.caption(verdict_explain)

st.markdown("---")

# ── 术语速查 / Quick Glossary ──
with st.expander("📖 术语速查 / Quick Glossary (点击展开)", expanded=False):
    glossary_cols = st.columns(3)
    terms = [
        ("DESeq2", "基于负二项分布的差异表达分析方法, 要求 raw counts 输入。Negative-binomial-based DEG method, requires raw counts."),
        ("edgeR", "基于负二项分布的差异表达分析方法, 使用 TMM 归一化。Negative-binomial-based DEG method with TMM normalization."),
        ("limma-voom", "基于经验贝叶斯的 DEG 方法, 对小样本更稳健。Empirical-Bayes-based DEG method, more robust for small samples."),
        ("TPM", "Transcripts Per Million — 基因内比较的归一化方式, 不适用于跨样本 DEG 分析。Normalization for within-sample comparison, not suitable for cross-sample DEG."),
        ("TMM", "Trimmed Mean of M-values — 校正 RNA 组成偏差的归一化方法。Normalization method that corrects for RNA composition bias."),
        ("BH 校正 / BH Correction", "Benjamini-Hochberg FDR 校正 — 控制假阳性比例的标准方法。Standard method to control false discovery rate."),
        ("FDR", "False Discovery Rate — 假阳性占所有阳性结果的比例。Proportion of false positives among all positive results."),
        ("logFC", "log2 Fold Change — 表达量的对数倍数变化, |logFC|=1 表示 2 倍差异。Log2 expression ratio; |logFC|=1 means 2-fold change."),
        ("p-adj", "校正后的 p 值 (adjusted p-value), 经过多重检验校正。p-value after multiple testing correction."),
    ]
    for i, (term, desc) in enumerate(terms):
        with glossary_cols[i % 3]:
            st.markdown(f"**{term}**")
            st.caption(desc)

st.markdown("---")

# ── Dimension Breakdown ──
st.subheader("维度分解 / Dimension Breakdown")
st.caption("三个决策质量维度, 最高危等级主导聚合（决策等级聚合，非科学评分）/ Three decision-quality dimensions, highest-risk-level-dominant aggregation")

dim_scores = result.get("dimension_scores", {})
DIM_INFO = {
    "data_handling": {
        "cn": "数据处理", "en": "Data Handling",
        "detail_cn": "过滤策略 + 归一化方法\nFiltering + Normalization",
        "explain_cn": "数据预处理的质量直接影响所有下游分析。过滤掉噪声基因、选择与下游方法兼容的归一化方式, 是分析可靠性的基础。",
    },
    "method_selection": {
        "cn": "方法选择", "en": "Method Selection",
        "detail_cn": "差异分析方法\nDEG Method",
        "explain_cn": "不同的统计方法有不同的数据假设和适用条件。选择与数据特征匹配的方法, 才能得到可靠的统计推断。",
    },
    "statistical_rigor": {
        "cn": "统计严谨性", "en": "Statistical Rigor",
        "detail_cn": "多重检验校正 + 显著性阈值\nMultiple Testing + Threshold",
        "explain_cn": "高通量数据中多重检验问题不可忽视。不做校正即报告结果, 等同于从噪声中挑选'显著'发现。",
    },
}

if dim_scores:
    cols = st.columns(len(dim_scores))
    for col, (dim, val) in zip(cols, dim_scores.items()):
        info = DIM_INFO.get(dim, {"cn": dim, "en": dim, "detail_cn": "", "explain_cn": ""})
        pct = val * 100
        if pct >= 70:
            color = "green"
        elif pct >= 40:
            color = "orange"
        else:
            color = "red"
        with col:
            st.metric(
                f"{info['cn']} / {info['en']}",
                f"{pct:.0f}%",
                delta=None,
            )
            st.progress(val)
            st.caption(info["detail_cn"])

    # Dimension explanations（D2 修复：不再用循环变量泄漏的 pct）
    with st.expander("📖 各维度说明 / Dimension Details", expanded=False):
        for dim, val in dim_scores.items():
            info = DIM_INFO.get(dim, {"cn": dim, "en": dim, "detail_cn": "", "explain_cn": ""})
            st.markdown(f"**{info['cn']} / {info['en']}** — {val * 100:.0f}%")
            st.write(info["explain_cn"])

st.markdown("---")

# ── Critical Issues ──
critical = result.get("critical_issues", [])
if critical:
    st.subheader("严重问题 / Critical Issues")
    st.caption("以下决策的评分低于可接受标准, 需要修正")
    for issue in critical:
        st.error(issue)
else:
    st.success("✅ 无致命或风险级决策 / No critical or risky decisions — 全部决策达可判定等级（审计判定，非科学结论）。")

# ── Error Propagation ──
error_chains = result.get("error_chains", [])
if error_chains:
    st.subheader("错误传播分析 / Error Propagation Analysis")
    st.caption("展示上游错误如何影响下游分析步骤 / Shows how upstream errors cascade to downstream steps")
    for chain in error_chains:
        with st.container(border=True):
            sev_label = "严重 Critical" if chain.get("severity") == "critical" else "重要 Major"
            sev_color = "#8B0000" if chain.get("severity") == "critical" else "#E8A000"
            st.markdown(f"**源头步骤 / Source:** `{chain['source_step']}` — {chain['source_error']}")
            st.markdown(f"**影响步骤 / Affected:** `{'`, `'.join(chain['affected_steps'])}`")
            st.markdown(f"**传播路径 / Path:** {chain['propagation_path']}")
            st.progress(
                1.0 if chain.get("severity") == "critical" else 0.6,
                text=f"严重度 / Severity: {sev_label}",
            )
else:
    st.info("✅ 未检测到错误传播链 / No error propagation chains detected")

# ── Audit Metadata ──
st.markdown("---")
st.subheader("审计元数据 / Audit Metadata")
meta_cols = st.columns(3)
with meta_cols[0]:
    st.metric("审计决策数 / Decisions Audited", result["report"]["n_decisions"])
with meta_cols[1]:
    st.metric("匹配规则数 / Rules Matched", result["report"]["n_rules_matched"])
with meta_cols[2]:
    conflicts = len(result["report"].get("conflicts_needing_review", []))
    st.metric("需人工审核 / Needs Human Review", conflicts)

# ── Methodology Notes ──
st.markdown("---")
with st.expander("⚠ 评分方法论说明 / Methodology Notes", expanded=False):
    st.markdown("""
    **当前版本 v2 (B1) — 实验性评分, 未经过效标校准**

    本评分为**决策等级的聚合**（审计评分，audit 0–100 聚合值），基于 5 位领域专家交叉审查后修正的规则。
    聚合采用"最高危等级主导"策略——一个致命错误(Level 0)会显著压低**聚合值**, 因为该错误意味着分析结果不可信。
    该聚合值不是科学总分、质量排名或置信度，也不替代四轴分列。

    **评分不等于终极真理:** 科学判断在特定领域内可能存在专家分歧。
    本系统提供的是一个基于文献共识的参考框架, 而非替代人类专家的最终判断。

    **计划改进 v0.3+ / 阶段 2-4:**
    - 效标验证: 与已知 ground truth 的数据集比对校准
    - 人类专家信度: Krippendorff's α ≥ 0.80
    - 连续化 reward signal: 支持 RLHF 训练（阶段 4）

    **核心参考文献 (PubMed 链接, 点击直达原文):**
    - [Conesa et al. (2016) Genome Biology — RNA-seq 最佳实践综述](https://pubmed.ncbi.nlm.nih.gov/26813401/) (PMID: 26813401)
    - [Schurch et al. (2016) RNA — n=48 酵母基准研究](https://pubmed.ncbi.nlm.nih.gov/27022035/) (PMID: 27022035)
    - [Love et al. (2014) Genome Biology — DESeq2 方法论文](https://pubmed.ncbi.nlm.nih.gov/25516281/) (PMID: 25516281)
    - [Dillies et al. (2013) Briefings in Bioinformatics — 归一化方法比较](https://pubmed.ncbi.nlm.nih.gov/22988256/) (PMID: 22988256)
    - [Robinson & Oshlack (2010) Genome Biology — TMM normalization](https://pubmed.ncbi.nlm.nih.gov/20196867/) (PMID: 20196867)
    - [Bourgon et al. (2010) PNAS — Independent filtering](https://pubmed.ncbi.nlm.nih.gov/20460310/) (PMID: 20460310)
    - [Benjamini & Hochberg (1995) JRSS-B — Controlling the FDR](https://www.jstor.org/stable/2346101) (统计期刊, JSTOR)
    """)

# ── Footer ──
st.markdown("---")
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("← 返回审计过程 / Back to Audit", use_container_width=True):
        st.switch_page("pages/02_audit.py")
with c2:
    if st.button("重新选择演示 / New Demo", use_container_width=True):
        st.session_state.audit_result = None
        st.switch_page("pages/01_upload.py")
with c3:
    # D1 修复：导出合法 JSON（原实现 str(dict) 是 Python repr，非法 JSON）
    export_data = json.dumps({
        "trajectory_score": result["trajectory_score"],
        "verdict": result["eval_verdict"],
        "dimension_scores": result.get("dimension_scores", {}),
        "critical_issues": result.get("critical_issues", []),
        "error_chains": result.get("error_chains", []),
        "step_scores": result["step_scores"],
        "report": result["report"],
    }, ensure_ascii=False, indent=1)
    st.download_button(
        label="导出报告 JSON / Export Report",
        data=export_data,
        file_name=f"bio-audit-report-{demo_mode}.json",
        mime="application/json",
        use_container_width=True,
    )

st.divider()
st.header("📊 独立审计证据 / Independent Audit Evidence")
st.caption("4 轮验证 R0-R3 | 4 位独立专家 x 2 轮交叉审查 | 设计迭代 v1.0-v2.0-v2.1")
VD = [
    ("R0 scRNA 真值锚定", "Spearman ρ 0.9747", "PASS", "#00B07C", "numpy neg-binom + zero-inflation 模拟 (1000 genes × 2000 cells, 100 true DEGs, 30% dropout), 5 方法组合信号检测模型: audit 52.1–85.0, F1 0.039–0.639, Kendall τ_b=0.9487, 单调 YES（仅 scRNA R0 工件落地: data/validation/scrna_r0.json, 2026-08-13 D5 修复后重算）", "n=5 组合小样本; F1 为信号检测模型(非真实工具执行, splatter 因 GFW 不可用); bulk R0 (SimSeq/F1 0.824) 无落地工件, 不再展示"),
    ("R1 酵母基准", "85 vs 0", "PASS", "#00B07C", "Schurch 2016 n=48, 4方法x5n, 区分力明确", "tau_b因二值评分趋零"),
    ("R2 跨基因", "零差异", "PASS", "#00B07C", "CSTB/TP53/CD274评分完全一致", "仅同一分析者风格"),
    ("R3A 合规", "1/2", "PARTIAL", "#E8A000", "n=2 override正确, Level-1未实现", "未知方法被误判为危险"),
    ("R3B 案例", "100%", "PASS", "#00B07C", "权威文献(BH/Schurch/Conesa)4/4检测", "生信DEG撤稿几乎不存在"),
]
vcols = st.columns(5)
for i, (lb, mt, sts, cl, dt, lm) in enumerate(VD):
    with vcols[i]:
        st.markdown(f"**{lb}**")
        st.markdown(f"<span style=\"font-size:18px;color:{cl}\">{sts}</span>", unsafe_allow_html=True)
        st.caption(f"**{mt}**")
        with st.expander("详情+局限"):
            st.caption(dt)
            st.warning(f"\u26a0 {lm}")

st.divider()
st.subheader("\u26a0 诚实局限 / Honest Limitations")
for lim in ["Level -1 未实现, 未知方法被误判为危险级", "tau_b 因审计二值评分失效", "人类校准未做 (R4 Pilot 保留设计)", "效标验证仅覆盖 DEG 分析类型", "R0 仅 scRNA 工件落地: n=5 组合小样本, F1 为信号检测模型(非真实工具执行), Spearman 排序方向性有效但 p 值不可靠"]:
    st.warning(lim)
