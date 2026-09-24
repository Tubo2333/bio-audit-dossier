"""评测与奖励页（四 tab 降载）。

设计依据：深色审计台设计规范 §3.4（四 tab）+ §7（素材清单）+ 验收 §12.1。

四 tab（**全部数字读 demo/data 提炼摘要，零硬编码；口径分列**）：
  Tab 1 benchmark 摘要：60 任务——recall 0.820 / precision 0.7455 / F1 0.7810 /
      IRR κ=0.8336（出处见摘要 JSON 内注明）/ gap +0.046（基准产物值）+
      复核后复跑 0.0449 注明（delta_after_m 键）；层间比较（strata + comparisons）；
  Tab 2 平台对照：Smart-seq2 vs 10X 黄金对照决策集差异（10X 多双联体/UMI/批次）；
      63.7 仅限 10X-B expected 口径、66.7 仅限 Smart-seq2-C（site-design §6.2）；
  Tab 3 reward 校准：level→reward 映射表（-1 mask 不参与分子分母）+ spike-in
      掉分演示（scrna 0.85→0.2354 / deg 0.85→0.2125 / pan 0.85→0.2400，
      出处 reward 校准报告 §三.9 + reward-protocol §七.2 + reward-mapping.md）；
  Tab 4 真实评测档案：CellVoyager 两次运行（30.0 复核后重评 L1×19/L3×1 /
      30.0 L1×4/L2×1，读 eval_summary 条目级 provenance）+ 成本
      （¥2.55 长评测 / ¥0.43 短评测）+ 黄金对照定位声明（确定性脚本非 LLM）+
      R0 锚定（ρ=0.9747，r0_summary）。

外围层纪律：只调 bioaudit.api + capture 公共类 + demo/data，零评分路径改动。
"""
from __future__ import annotations

import data_index
import narrative
import result_view
import streamlit as st
import zh_labels  # 演示层中文标签（案例中文名 / verdict 中文 / 平台大写规范）

_esc = result_view.esc

#: 平台对照 · 决策集构成（出处 评测报告 §4.3/§5 平台互补对照表）
SHARED_DECISION_TYPES: tuple[str, ...] = (
    "qc_filtering",
    "scRNA_normalization",
    "hv_gene_selection",
    "dim_reduction",
    "batch_correction",
    "clustering_method",
    "annotation_method",
    "deg_method",
    "multiple_testing_correction",
    "significance_threshold",
)
TENX_ONLY_DECISION_TYPE = "doublet_detection"


def _prov_chips(prov: dict, extra: list[tuple[str, str]] | None = None) -> str:
    """provenance 徽章行（来源 / generated_at / exported_at + 可选附加 chip）。"""
    chips = [
        f'<span class="ba-snapshot-chip ba-snapshot-src">source '
        f"{_esc(prov.get('source', '?'))}</span>",
        f'<span class="ba-snapshot-chip">generated '
        f"{_esc(prov.get('generated_at') or '—')}</span>",
        f'<span class="ba-snapshot-chip">exported '
        f"{_esc(prov.get('exported_at') or '—')}</span>",
    ]
    for label, value in (extra or []):
        chips.append(
            f'<span class="ba-snapshot-chip">{_esc(label)} {_esc(value)}</span>')
    return '<div class="ba-snapshot">' + "".join(chips) + "</div>"


# ── Tab 1 · benchmark 摘要 ──────────────────────────────────────────────

def _tab_benchmark() -> None:
    bm = data_index.benchmark_summary()
    det = bm["detection"]
    irr = bm["irr"]
    gap = bm["gap"]

    st.markdown(
        f'<div class="ba-section-title">{bm["n_tasks_run"]} 任务 benchmark · '
        "检出与一致性</div>",
        unsafe_allow_html=True,
    )
    cards = [
        ("recall", f"{det['recall']:.3f}",
         f"gold error 检出率（{bm['n_decisions']} 决策中 error "
         f"{bm['n_gold_error']}）"),
        ("precision", f"{det['precision']:.4f}", "检出结果中确为错误的占比"),
        ("F1", f"{det['f1']:.4f}", "recall 与 precision 调和均值"),
        ("IRR κ", f"{irr['kappa']:.4f}",
         f"{bm['n_decisions']} 决策 · 一致率 {irr['agreement'] * 100:.2f}%"),
    ]
    st.markdown(
        '<div class="ba-stat-grid">'
        + "".join(
            '<div class="ba-stat-card">'
            f'<div class="ba-stat-value">{_esc(v)}</div>'
            f'<div class="ba-stat-label">{_esc(label)}</div>'
            f'<div class="ba-stat-sub">{_esc(sub)}</div>'
            "</div>"
            for label, v, sub in cards
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    # gap（基线产物值 + 复核后复跑注明，delta_after_m 键；容差区间读数据）
    tone = "ba-callout-ok" if gap.get("in_tolerance") else "ba-callout-err"
    lo, hi = (float(x) for x in gap.get("tolerance_interval", [-0.1, 0.1]))
    st.markdown(
        f'<div class="ba-callout {tone}">'
        f"<b>gold 分差 gap = +{gap['delta']:.3f}</b>（读自 "
        "benchmark_run_baseline.json · 基线产物）；复核后复跑 "
        f"<b>+{gap['delta_after_m']:.4f}</b>（delta_after_m 键）——预注册口径 "
        f"[{lo:+.2f}, {hi:+.2f}] 区间内无告警。</div>",
        unsafe_allow_html=True,
    )

    # 决策构成（correct / edge / error 堆叠条）
    n_total = bm["n_decisions"]
    n_correct = bm["n_gold_correct"]
    n_error = bm["n_gold_error"]
    n_edge = n_total - n_correct - n_error

    def _seg(n: int, color: str, label: str) -> str:
        pct = n / n_total * 100
        return (f'<span class="ba-stack-seg" style="width:{pct:.1f}%;'
                f'background:{color}" title="{label} {n}"></span>')

    st.markdown(
        '<div class="ba-card">'
        '<div class="ba-stack">'
        + _seg(n_correct, "#10b981", "correct") + _seg(n_edge, "#f59e0b", "edge")
        + _seg(n_error, "#ef4444", "error")
        + "</div>"
        '<div class="ba-stack-legend">'
        f'<span class="ba-stat-chip" style="border-color:rgba(16,185,129,.4)">'
        f"correct {n_correct}</span>"
        f'<span class="ba-stat-chip" style="border-color:rgba(245,158,11,.4)">'
        f"edge {n_edge}</span>"
        f'<span class="ba-stat-chip" style="border-color:rgba(239,68,68,.4)">'
        f"error {n_error}</span>"
        f'<span class="ba-stat-chip">合计 {n_total} 决策 · taskset '
        f'{_esc(bm["taskset_version"])}</span>'
        "</div></div>",
        unsafe_allow_html=True,
    )

    # 层间比较（strata：act / difficulty）
    st.markdown('<div class="ba-section-title">层间比较（strata）</div>',
                unsafe_allow_html=True)
    for group_key, group_label, strata in (
        ("act", "范式", bm["strata"]["act"]),
        ("difficulty", "难度", bm["strata"]["difficulty"]),
    ):
        rows = []
        for name, s in strata.items():
            point = float(s["point"])
            lo, hi = (float(x) for x in s["ci"])
            rows.append(
                '<div class="ba-dim-row">'
                f'<span class="ba-dim-label">{_esc(group_label)} {_esc(name)}</span>'
                '<span class="ba-dim-track">'
                f'<span class="ba-dim-fill" style="width:{point * 100:.1f}%;'
                'background:#22d3ee"></span>'
                "</span>"
                f'<span class="ba-dim-val">{point:.4f}</span>'
                f'<span class="ba-dim-ci">[{lo:.4f}, {hi:.4f}] · '
                f'n={s["n"]}</span>'
                "</div>"
            )
        st.markdown(
            f'<div class="ba-card ba-card-tight">{"".join(rows)}</div>',
            unsafe_allow_html=True,
        )

    # 层间比较检验（comparisons：bootstrap p + Holm 校正）
    comp_rows = []
    for c in bm["comparisons"]:
        comp_rows.append(
            "<tr>"
            f'<td class="ba-cmp-type">{_esc(c["grouping"])}</td>'
            f"<td>{_esc(c['group_a'])} vs {_esc(c['group_b'])}</td>"
            f'<td class="ba-mono">{float(c["bootstrap_p"]):.4f}</td>'
            f'<td class="ba-mono">{float(c["holm_adjusted_p"]):.4f}</td>'
            "</tr>"
        )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp ba-cmp-narrow">'
        "<thead><tr><th>分组</th><th>比较对</th><th>bootstrap p</th>"
        "<th>Holm 校正 p</th></tr></thead><tbody>"
        + "".join(comp_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "两样本置换 bootstrap 检验（B=2000）+ Holm-Bonferroni 校正"
        "（protocol 说明随摘要 JSON 自带）。"
    )
    adjusted = [float(c["holm_adjusted_p"]) for c in bm["comparisons"]]
    if adjusted and all(p >= 0.05 for p in adjusted):
        st.markdown(
            '<div class="ba-callout ba-callout-note">层间比较：全部 Holm 校正后 '
            "p ≥ 0.05，范式 / 难度层间差异未达显著性（如实呈现，不做强行解读）。"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        _prov_chips(bm["provenance"]),
        unsafe_allow_html=True,
    )
    st.caption(f'IRR 注：{irr["note"]}　|　gap 注：{gap["note"]}')


# ── Tab 2 · 平台对照 ───────────────────────────────────────────────────

def _tab_platform() -> None:
    gs = data_index.golden_summary()
    entries = gs["entries"]

    # 口径纪律 callout：文本直接读 golden_summary 条目 note（单一事实源，页面零字面量）
    notes_by_id = {e["id"]: str(e.get("note", "")) for e in entries}
    caliber_note = (
        notes_by_id.get("windowL_10X_B_expected", "")
        + "；"
        + notes_by_id.get("windowI_C", "")
    )
    st.markdown(
        '<div class="ba-callout ba-callout-warn"><b>口径纪律（site-design §6.2）'
        f"</b>：{_esc(caliber_note)}</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="ba-section-title">五份黄金对照（平台 × 版本分列）</div>',
                unsafe_allow_html=True)
    rows = []
    for e in entries:
        dims = e.get("dimension_scores", {})
        dim_html = "".join(
            f'<div>{_esc(result_view.DIM_LABELS.get(k, k))} '
            f'<span class="ba-mono">{float(v):.2f}</span></div>'
            for k, v in dims.items()
        )
        issues = e.get("critical_issues") or []
        issue_html = (
            f'<span class="ba-cmp-issues" title="{_esc("；".join(issues)[:300])}">'
            f"{len(issues)} 条</span>"
            if issues else '<span class="ba-cmp-missing">无</span>'
        )
        rows.append(
            "<tr>"
            f'<td class="ba-cmp-type" title="{_esc(e["id"])}">'
            f'{_esc(zh_labels.name_zh(e["id"]))}</td>'
            f"<td>{_esc(zh_labels.platform_zh(e['platform']))}</td>"
            # 合规约束：单元格内亦以 verdict 先行、分数后置（DOM 序）。
            '<td><div>'
            f'{result_view.verdict_dot_html(e["eval_verdict"])}'
            f'{_esc(zh_labels.verdict_zh(e["eval_verdict"]))}</div>'
            '<div class="ba-cmp-score ba-mono">'
            f'{float(e["trajectory_score"]):.1f}</div></td>'
            f'<td class="ba-mono">{e["n_decisions"]}</td>'
            f"<td>{dim_html}</td>"
            f"<td>{issue_html}</td>"
            "</tr>"
        )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp">'
        "<thead><tr><th>案例</th><th>平台</th><th>分数 · 结论</th><th>决策数</th>"
        "<th>维度分</th><th>致命问题</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "分数/决策数/维度分/问题数全部读 golden_summary 提炼摘要（含各条目口径 "
        "note）；两平台黄金 A 同分（见上表）却决策集不同——同一分数跨平台 = "
        "不同决策集，见下方差异。"
    )

    # 决策集差异可视化（10X 多双联体 / UMI / 批次维度）
    st.markdown(
        '<div class="ba-section-title">决策集差异（10X 多双联体 / UMI / 批次维度）</div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown(
            '<div class="ba-card">'
            '<div class="ba-result-title">Smart-seq2 平台</div>'
            '<div class="ba-guide">GSE115978 黑色素瘤 · 7,186 cells × 22,454 '
            "genes · 32 患者</div>"
            '<ul class="ba-decl-list">'
            "<li>黄金 A/B/C 决策集 10 类型（分数·verdict 见上表）</li>"
            "<li><b>无 doublet_detection</b>：D1.1 规则 required_context 10X 专属，"
            "Smart-seq2 板式 FACS 分选无液滴双联体前提</li>"
            "<li>scRNA_normalization 走 <b>raw_counts</b> 分支（全长转录本）</li>"
            "<li>batch_correction 以 32 患者为批次</li>"
            "</ul></div>",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            '<div class="ba-card">'
            '<div class="ba-result-title">10X 平台</div>'
            '<div class="ba-guide">GSE132465 CRC · 63,689 cells × 25,655 genes · '
            "33 文库 / 23 患者</div>"
            '<ul class="ba-decl-list">'
            "<li>黄金 A 决策集 11 类型（expected 补入后 blocked，分数·verdict 见上表）</li>"
            "<li><b>doublet_detection 存在且可评</b>：scDblFinder 真实执行 → L3"
            "（D1.1 首次真实验证；跳过 → L0）</li>"
            "<li>scRNA_normalization 走 <b>umi_counts</b> 分支（整数值 UMI）</li>"
            "<li>batch_correction 以 <b>33 个 10X 文库</b>为批次（B1.2 ≥2 条件）</li>"
            "</ul></div>",
            unsafe_allow_html=True,
        )

    # 决策集构成表（共享 10 类型 + 10X 独有 doublet_detection）
    type_rows = []
    for tid in SHARED_DECISION_TYPES:
        type_rows.append(
            "<tr>"
            f'<td class="ba-cmp-type">{_esc(tid)}</td>'
            '<td class="ba-mono">✓</td><td class="ba-mono">✓</td>'
            "</tr>"
        )
    type_rows.append(
        '<tr class="ba-cmp-highlight">'
        f'<td class="ba-cmp-type">{_esc(TENX_ONLY_DECISION_TYPE)}</td>'
        '<td class="ba-cmp-missing">— 决策不存在</td>'
        '<td class="ba-mono">✓（scDblFinder → L3）</td>'
        "</tr>"
    )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp ba-cmp-narrow">'
        "<thead><tr><th>决策类型</th><th>Smart-seq2</th><th>10X</th></tr></thead>"
        "<tbody>"
        + "".join(type_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "两平台共享 10 个决策类型全评分；10X 独有 doublet_detection（D1.1）——"
        "各平台适用决策集差异被如实呈现，不硬套（出处：L1 报告 §4.3/§5 平台互补"
        "对照表；决策数 = golden_summary entries）。"
    )
    st.markdown(
        _prov_chips(gs["provenance"]),
        unsafe_allow_html=True,
    )


# ── Tab 3 · reward 校准 ────────────────────────────────────────────────

_LEVEL_SEMANTICS: dict[int, str] = {
    4: "示范级（v0.2 LLM 增强后启用）",
    3: "正确级",
    2: "可接受（微小瑕疵）",
    1: "有风险（方法选择值得商榷）",
    0: "危险（将导致错误结论）",
}


def _tab_reward() -> None:
    rw = data_index.reward_summary()
    mapping = rw["mapping"]

    st.markdown(
        '<div class="ba-section-title">level → reward 映射（reward-mapping.md §2 '
        "宪法）</div>",
        unsafe_allow_html=True,
    )
    map_rows = []
    for level in (4, 3, 2, 1, 0):
        map_rows.append(
            "<tr>"
            f"<td>{result_view.level_badge_html(level)}</td>"
            f"<td>{_esc(_LEVEL_SEMANTICS[level])}</td>"
            f'<td class="ba-mono">{float(mapping[str(level)]):.2f}</td>'
            "</tr>"
        )
    map_rows.append(
        "<tr>"
        '<td><span class="ba-level ba-level-lm">L-1</span></td>'
        "<td>无法评估（规则不适用）</td>"
        '<td class="ba-mono" style="color:#9ca3af">mask（None）</td>'
        "</tr>"
    )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp ba-cmp-narrow">'
        "<thead><tr><th>level</th><th>语义</th><th>reward</th></tr></thead>"
        "<tbody>"
        + "".join(map_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ba-callout ba-callout-note"><b>L-1 mask</b>：'
        f'{_esc(rw["mask"]["semantic"])}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ba-callout"><b>聚合</b>：{_esc(rw["aggregation"])}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ba-callout ba-callout-warn"><b>状态：{_esc(rw["status"])}'
        f"</b>——{_esc(rw['status_note'])}</div>",
        unsafe_allow_html=True,
    )

    # spike-in 掉分演示（读校准摘要，强锚点 ≥ 0.30）
    st.markdown(
        '<div class="ba-section-title">spike-in 掉分演示（强锚点 ≥ 0.30）</div>',
        unsafe_allow_html=True,
    )
    spike_rows = []
    for s in rw["spike_in"]:
        pct = s["drop"] * 100
        spike_rows.append(
            '<div class="ba-spike-row">'
            '<div class="ba-spike-head">'
            f'<span class="ba-cmp-type">{_esc(zh_labels.PARADIGM_ZH.get(str(s["paradigm"]), s["paradigm"]))}</span>'
            f'<span class="ba-spike-trail">{_esc(zh_labels.name_zh(s["trajectory"]))} + 注入 '
            f'<span class="ba-mono">{_esc(s["injection"])}</span></span>'
            f'<span class="ba-spike-val ba-mono">{s["before"]:.2f} → '
            f'{s["after"]:.4f}</span>'
            "</div>"
            '<div class="ba-spike-bar">'
            '<span class="ba-spike-track">'
            f'<span class="ba-spike-fill" style="width:{pct:.1f}%"></span>'
            "</span>"
            f'<span class="ba-spike-drop ba-mono">drop {s["drop"]:.4f} ≥ '
            f'{s["threshold"]:.2f}</span>'
            "</div></div>"
        )
    st.markdown('<div class="ba-card">' + "".join(spike_rows) + "</div>",
                unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-guide">注入 = 引擎实测判 L0 的决策（自校验）；判据 '
        "drop ≥ 0.30 预注册（reward 校准报告 §三.9 实测 + reward-protocol §七.2 实测表，"
        "锚点与任务集无关、五闸常驻）。</div>",
        unsafe_allow_html=True,
    )
    st.markdown(_prov_chips(rw["provenance"]), unsafe_allow_html=True)


# ── Tab 4 · 真实评测档案 ───────────────────────────────────────────────

def _tab_archive() -> None:
    ev = data_index.eval_summary()
    runs = ev["runs"]

    st.markdown('<div class="ba-section-title">CellVoyager 真实评测 × 2</div>',
                unsafe_allow_html=True)
    for r in runs:
        counts = r.get("level_counts", {})
        # L 分布：0 档省略（与 result_view.level_counts_label 同约定），降序
        dist = " · ".join(
            f"L{k}×{v}" for k, v in sorted(
                counts.items(), key=lambda kv: int(kv[0]), reverse=True)
            if int(v) > 0
        ) or "—"
        cost = r.get("cost", {})
        prov = r.get("provenance", {})
        st.markdown(
            '<div class="ba-card ba-run-card">'
            '<div class="ba-run-head">'
            '<div class="ba-run-meta">'
            f'<div class="ba-result-title">{_esc(r["label"])}</div>'
            f'<div>{result_view.verdict_dot_html(r["eval_verdict"])}'
            f'{_esc(zh_labels.verdict_zh(r["eval_verdict"]))} · {r["n_decisions"]} 决策 · '
            f'{_esc(dist)}</div>'
            '<div class="ba-run-cost">成本 '
            f'<span class="ba-cost-chip ba-mono">¥{float(cost["amount"]):.2f}</span>'
            f'<span class="ba-inst">{_esc(cost.get("caliber", ""))}'
            f"（{_esc(cost.get('source', ''))}）</span></div>"
            "</div></div>"
            f'<div class="ba-callout ba-callout-note">{_esc(r["note"])}</div>'
            + _prov_chips(prov)
            # 合规约束：聚合分数不得置于结论位（DOM 渲染序首个结论性元素），
            # 故由本卡首位移至卡末（与 02_capture.py 同构全移，非半移）。
            + '<div class="ba-score-sm ba-mono" style="color:#f59e0b">'
            + f'{float(r["trajectory_score"]):.1f}</div>'
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="ba-callout ba-callout-ok"><b>黄金对照定位声明</b>：黄金 '
        "Agent = <b>确定性脚本（非 LLM）</b>——按公开最佳实践编写（Luecken & "
        "Theis 2019 等），在真实数据上真实执行；脚本不引用规则库（隔离审查），"
        "分数由系统独立评分（防「设计高分」）。<b>真实评测</b> = 真实 LLM Agent "
        "（deepseek-chat）真实运行，n=1，LLM 随机性不可复现，不做统计推断"
        "（出处：L1 报告 §2/§8；agent-eval-report-g2 §7）。</div>",
        unsafe_allow_html=True,
    )

    # R0 锚定（scrna_r0.json K/M 后版本）
    r0 = data_index.r0_summary()
    st.markdown(
        '<div class="ba-section-title">R0 锚定（scrna_r0.json · K/M 后版本）</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="ba-card">'
        '<div class="ba-run-head">'
        f'<div class="ba-score-sm ba-mono" style="color:#10b981">'
        f'{_esc(r0["key_metric"])}</div>'
        '<div class="ba-run-meta">'
        f'<span class="ba-flow-chip ba-flow-chip-final">{_esc(r0.get("status", ""))}'
        "</span>"
        '<span class="ba-inst">审计分数与真实分析质量排序一致性（方向性验证）'
        "</span></div></div>"
        f'<div class="ba-ev-cite">{_esc(r0["detail"])}</div>'
        f'<div class="ba-callout ba-callout-note"><b>诚实局限</b>：'
        f'{_esc(r0["limit"])}</div>'
        + _prov_chips(r0["provenance"])
        + "</div>",
        unsafe_allow_html=True,
    )


def render() -> None:
    st.markdown('<h1 class="ba-page-title">评测与奖励</h1>', unsafe_allow_html=True)
    st.markdown(narrative.lead_html("benchmark"), unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-page-sub">60 任务 benchmark、双平台黄金对照、'
        "reward 校准与真实评测档案——四类证据分列四个页签。</div>",
        unsafe_allow_html=True,
    )
    st.markdown(narrative.term_html("reliability"), unsafe_allow_html=True)
    tab_bm, tab_plat, tab_rw, tab_arch = st.tabs(
        ["benchmark 摘要", "平台对照", "reward 校准", "真实评测档案"]
    )
    with tab_bm:
        _tab_benchmark()
    with tab_plat:
        _tab_platform()
    with tab_rw:
        _tab_reward()
    with tab_arch:
        _tab_archive()


if __name__ == "__main__":
    render()
