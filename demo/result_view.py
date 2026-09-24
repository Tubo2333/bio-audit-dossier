"""审计工坊 · 结果页全元素渲染。

设计依据：深色审计台设计规范 §3.2（结果页元素）+ §4（token）。

元素清单（验收 §8.1-2）：
1. 总分大卡（等宽字体）+ verdict 色点（绿/黄/红圆点，**与 level 徽章区分**）；
2. 快照徽章（engine/ruleset/ontology/generated_at——report.current_snapshot）；
3. 维度进度条（data_handling / method_selection / statistical_rigor）；
4. 决策状态点（level 五档徽章配色 L3 绿 / L2 青 / L1 黄 / L0 红 / L-1 灰 +
   matched 规则数 + 悬停摘要）；
5. 证据卡（PMID 悬停显示编号、**不点击**——断网演示纪律）；
6. 时间轴（decisions + ontology stages 推导：节点着色正常/风险/危险/未验证；
   阶段含决策 = 已审计）；"未验证"节点色**保留**（决策与理由见时间轴说明）；
7. 对比表（决策行按 ontology 顺序对齐、缺失列"无此决策"——错位即信息）。

外围层纪律：本模块只消费 run_audit 返回的 state（+ ontology 只读查询），
不触碰任何评分路径。
"""
from __future__ import annotations

import html
import re
from collections import Counter
from typing import Optional

import streamlit as st

# ── level 五档徽章（设计 §3.2 钉死：L3 绿 / L2 青 / L1 黄 / L0 红 / L-1 灰）──
# L4（优秀）归 L3 绿；L-2（未验证，M2.4）归 L-1 灰——不新增配色，语义区分靠文案
LEVEL_META: dict[int, tuple[str, str, str]] = {
    4: ("L4", "#10b981", "优秀级"),
    3: ("L3", "#10b981", "正确级"),
    2: ("L2", "#22d3ee", "可接受"),
    1: ("L1", "#f59e0b", "有风险"),
    0: ("L0", "#ef4444", "危险级"),
    -1: ("L-1", "#9ca3af", "无法评估"),
    -2: ("L-2", "#9ca3af", "未验证（关键上下文缺失）"),
}

VERDICT_META: dict[str, tuple[str, str, str]] = {
    "pass": ("pass", "#10b981", "各决策均达可判定等级（决策等级的聚合，非科学结论）"),
    "needs_correction": ("needs_correction", "#f59e0b", "需要修正后再使用"),
    "blocked": ("blocked", "#ef4444", "检测到致命科学错误 — 分析结果不可信"),
}

DIM_LABELS = {
    "data_handling": "数据处理",
    "method_selection": "方法选择",
    "statistical_rigor": "统计严谨性",
}

#: PMID 提取：markdown 链接形态 + 裸 "PMID: n" 形态（悬停显示编号，不点击）
_PMID_RE = re.compile(r"\[PMID:\s*(\d+)\]\([^)]*\)|PMID[:：]?\s*(\d{5,9})")


# ── 基础工具 ───────────────────────────────────────────────

def esc(text: object) -> str:
    """HTML 转义（所有插值进 HTML 的字符串必经）。"""
    return html.escape(str(text), quote=True)


def level_badge_html(level: int) -> str:
    """level 五档徽章 HTML（L-1 灰不误读为危险：灰 = 无法评估）。"""
    label, color, _name = LEVEL_META.get(level, (f"L{level}", "#9ca3af", ""))
    if level >= 3:
        css = "ba-level-l3"
    elif level == 2:
        css = "ba-level-l2"
    elif level == 1:
        css = "ba-level-l1"
    elif level == 0:
        css = "ba-level-l0"
    else:
        css = "ba-level-lm"
    return (
        f'<span class="ba-level {css}" title="level {level}">'
        f"{esc(label)}</span>"
    )


def verdict_dot_html(verdict: str) -> str:
    """verdict 色点（10px 圆点；与 level 徽章视觉区分）。"""
    _label, color, desc = VERDICT_META.get(
        verdict, (verdict, "#9ca3af", "未知")
    )
    return (
        f'<span class="ba-verdict-dot" style="background:{color}" '
        f'title="{esc(desc)}"></span>'
    )


def level_counts_label(step_scores: list[dict]) -> str:
    """L 分布紧凑文本（L3×6 · L2×2 …，含 0 档省略）。"""
    counts = Counter(s.get("level") for s in step_scores)
    parts = []
    for level in (4, 3, 2, 1, 0, -1, -2):
        n = counts.get(level, 0)
        if n:
            label = LEVEL_META.get(level, (f"L{level}", "", ""))[0]
            parts.append(f"{label}×{n}")
    return " · ".join(parts) if parts else "—"


def _snapshot_chip(snapshot: dict, generated_at: str, source: str) -> str:
    """快照徽章 HTML（可信 UI：三元组 + 生成时间 + 来源）。"""
    return (
        '<div class="ba-snapshot" title="报告快照三元组（B5 可复现性）">'
        f'<span class="ba-snapshot-chip">engine {esc(snapshot.get("engine_version", "?"))}</span>'
        f'<span class="ba-snapshot-chip">ruleset {esc(snapshot.get("ruleset_version", "?"))}</span>'
        f'<span class="ba-snapshot-chip">ontology '
        f'{esc(snapshot.get("ontology_version", "?"))}</span>'
        f'<span class="ba-snapshot-chip">generated {esc(generated_at)}</span>'
        f'<span class="ba-snapshot-chip ba-snapshot-src">{esc(source)}</span>'
        "</div>"
    )


def _pmid_safe(text: str) -> str:
    """证据文本安全化：转义 + PMID 转悬停徽章（不生成任何 <a>，断网纪律）。"""
    safe = esc(text).replace("**", "")
    def _repl(m: re.Match) -> str:
        n = m.group(1) or m.group(2)
        return (
            f'<span class="ba-pmid" title="PMID {n} —— 演示纪律：悬停查看编号，'
            f'不跳转外网">PMID {n}</span>'
        )
    return _PMID_RE.sub(_repl, safe)


# ── 结果页元素 ─────────────────────────────────────────────

def render_score_header(
    state: dict,
    snapshot: dict,
    generated_at: str,
    source: str = "实时 run_audit",
    n_decisions: Optional[int] = None,
) -> None:
    """总分大卡 + verdict 色点 + 快照徽章 + 维度进度条。

    n_decisions 显式传入时优先（产物读取卡无 step_scores，按摘要显示决策数）。
    """
    score = float(state.get("trajectory_score", 0))
    verdict = str(state.get("eval_verdict", "unknown"))
    dims = state.get("dimension_scores", {})
    _v_label, v_color, v_desc = VERDICT_META.get(
        verdict, (verdict, "#9ca3af", "未知")
    )
    n_dec = n_decisions if n_decisions is not None else len(
        state.get("step_scores", []))

    # 合规约束：结论位＝DOM 渲染序首个结论性元素，聚合分数不得置于该位。
    # 分数卡块内，维度条须先渲染（DOM 序在前），分数卡在后——
    # 故此处列序为「维度条、分数卡」。
    # 注：两列宽度随内容对调，**视觉上两列左右位置互换**
    # （原为左分数卡/右维度条，现为左维度条/右分数卡），非"画面不变"。
    c_dims, c_score = st.columns([1.6, 1], gap="medium")
    with c_score:
        st.markdown(
            '<div class="ba-card ba-score-card">'
            f'<div class="ba-score" style="color:{v_color}">{score:.1f}</div>'
            '<div class="ba-score-side">'
            f'<div class="ba-verdict-line"><span class="ba-verdict-dot" '
            f'style="background:{v_color}"></span>'
            f'<span class="ba-verdict-label">{esc(verdict)}</span></div>'
            f'<div class="ba-verdict-desc">{esc(v_desc)}</div>'
            f'<div class="ba-score-unit">决策等级聚合 · {n_dec} 个决策</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )
    with c_dims:
        bars = []
        for dim in ("data_handling", "method_selection", "statistical_rigor"):
            val = dims.get(dim)
            if val is None:
                # 该维度无决策（如 scRNA 轨迹无 statistical_rigor 类型决策）：
                # 渲染中性「—」而非误导性的 0% 红条（走查发现）
                bars.append(
                    '<div class="ba-dim-row">'
                    f'<span class="ba-dim-label">{esc(DIM_LABELS.get(dim, dim))}</span>'
                    '<span class="ba-dim-track"></span>'
                    '<span class="ba-dim-val ba-dim-na">—</span>'
                    "</div>"
                )
                continue
            pct = float(val) * 100
            color = "#10b981" if pct >= 70 else ("#f59e0b" if pct >= 40 else "#ef4444")
            bars.append(
                '<div class="ba-dim-row">'
                f'<span class="ba-dim-label">{esc(DIM_LABELS.get(dim, dim))}</span>'
                '<span class="ba-dim-track">'
                f'<span class="ba-dim-fill" style="width:{pct:.0f}%;background:{color}"></span>'
                "</span>"
                f'<span class="ba-dim-val">{pct:.0f}%</span>'
                "</div>"
            )
        st.markdown(
            '<div class="ba-card">' + "".join(bars) + "</div>",
            unsafe_allow_html=True,
        )
    st.markdown(_snapshot_chip(snapshot, generated_at, source),
                unsafe_allow_html=True)
    # 合规约束：四个结果轴（inference_type / explanation_depth / scope /
    # validation）未展示时，须在该数值处显式声明其不替代四轴。
    # 本演示台不展示该四轴，故此处声明为必须项，不可省略。
    st.markdown(
        '<div class="ba-score-disclaimer">本分数为<b>决策等级的聚合</b>（审计评分），'
        '不是科学总分、质量排名或置信度；<b>不替代四轴分列</b>'
        '（inference_type / explanation_depth / scope / validation）'
        '——该四轴本期未展示（见页脚身份与降级声明）。</div>',
        unsafe_allow_html=True,
    )


def render_decision_points(state: dict, ontology) -> None:
    """决策状态点：level 徽章 + choice + matched 规则数 + 悬停摘要。"""
    step_scores = state.get("step_scores", [])
    if not step_scores:
        return
    st.markdown('<div class="ba-section-title">决策状态点</div>',
                unsafe_allow_html=True)
    cards = []
    for s in step_scores:
        tid = str(s.get("decision_type", "?"))
        meta = ontology.get_type(tid)
        cn = (meta or {}).get("display", {}).get("cn", tid)
        summary = str(s.get("explanation", "")).replace("\n", " ")
        n_rules = len(s.get("matched_rules") or [])
        cards.append(
            '<div class="ba-dec-card" title="'
            f'{esc(summary)}">'
            '<div class="ba-dec-top">'
            f"{level_badge_html(int(s.get('level', -1)))}"
            f'<span class="ba-dec-rules">规则 {n_rules}</span>'
            "</div>"
            f'<div class="ba-dec-type">{esc(cn)}</div>'
            f'<div class="ba-dec-choice ba-mono">{esc(s.get("agent_choice", ""))}</div>'
            "</div>"
        )
    st.markdown(
        '<div class="ba-dec-grid">' + "".join(cards) + "</div>",
        unsafe_allow_html=True,
    )


def render_evidence_cards(state: dict, ontology) -> None:
    """证据卡：每决策一个折叠卡，文献证据 PMID 悬停显示编号（不点击）。"""
    step_scores = state.get("step_scores", [])
    if not step_scores:
        return
    st.markdown('<div class="ba-section-title">规则命中与文献证据</div>',
                unsafe_allow_html=True)
    st.caption(
        "证据出处 PMID 悬停显示编号（演示纪律：不点击跳转外网，断网可用）。"
    )
    for s in step_scores:
        tid = str(s.get("decision_type", "?"))
        meta = ontology.get_type(tid)
        cn = (meta or {}).get("display", {}).get("cn", tid)
        citations = s.get("evidence_citations") or []
        alternatives = s.get("alternatives") or []
        missing = s.get("missing_keys") or []
        label = f"{cn} · {esc(str(s.get('agent_choice', '')))}"
        with st.expander(label, expanded=int(s.get("level", -1)) <= 1):
            st.markdown(
                f"**评分摘要**：{esc(str(s.get('explanation', '')))}",
                unsafe_allow_html=True,
            )
            if missing:
                st.markdown(
                    f'<div class="ba-missing-keys">未验证上下文键：'
                    f'{esc(", ".join(missing))}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown("**文献证据**")
            if citations:
                for cite in citations:
                    st.markdown(
                        f'<div class="ba-ev-cite">{_pmid_safe(cite)}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown('<div class="ba-guide">无文献证据引用</div>',
                            unsafe_allow_html=True)
            if alternatives:
                st.markdown(
                    "**备选方案**：" + esc("；".join(alternatives)),
                    unsafe_allow_html=True,
                )


def render_timeline(state: dict, ontology) -> None:
    """分析流程时间轴：decisions + ontology stages 推导（v2 无 workflow 字段）。

    推导规则：按 stages.yaml 阶段顺序分组（data-acquisition → … → conclusion），
    组内保持轨迹内决策顺序；节点着色 = level 语义（L≥2 正常 / L1 风险 /
    L0 危险 / L-1/L-2 未验证）；阶段含决策 → "已审计"标记。
    """
    step_scores = state.get("step_scores", [])
    st.markdown('<div class="ba-section-title">分析流程时间轴</div>',
                unsafe_allow_html=True)
    st.caption(
        "由决策点 + 本体阶段推导（v2 轨迹无 workflow 字段）："
        "正常 / 风险 / 危险 / 未验证（灰）四色节点；含决策的阶段标记「已审计」。"
    )
    if not step_scores:
        st.markdown('<div class="ba-guide">无决策可推导时间轴。</div>',
                    unsafe_allow_html=True)
        return

    by_stage: dict[str, list[dict]] = {}
    unknown: list[dict] = []
    for s in step_scores:
        stage = ontology.stage_of(str(s.get("decision_type", "")))
        if stage is None:
            # 未知阶段不伪造：归"未分类"桶
            unknown.append(s)
        else:
            by_stage.setdefault(stage, []).append(s)

    rows = []
    for stage_id, stage_meta in ontology.stages.items():
        scores = by_stage.get(stage_id, [])
        if not scores:
            rows.append(
                '<div class="ba-tl-stage ba-tl-empty">'
                f'<div class="ba-tl-stage-head"><span class="ba-tl-stage-id">'
                f'{esc(stage_id)}</span>'
                f'<span class="ba-tl-stage-cn">{esc(stage_meta.get("cn", ""))}</span>'
                '<span class="ba-tl-none">无决策</span></div></div>'
            )
            continue
        chips = []
        for s in scores:
            level = int(s.get("level", -1))
            if level >= 2:
                tone = "ba-tl-normal"
            elif level == 1:
                tone = "ba-tl-risk"
            elif level == 0:
                tone = "ba-tl-danger"
            else:
                tone = "ba-tl-unverified"
            tid = str(s.get("decision_type", "?"))
            meta = ontology.get_type(tid)
            cn = (meta or {}).get("display", {}).get("cn", tid)
            chips.append(
                f'<span class="ba-tl-chip {tone}" '
                f'title="{esc(str(s.get("explanation", "")))}">'
                '<span class="ba-tl-dot"></span>'
                f'{level_badge_html(level)}<span class="ba-tl-type">{esc(cn)}</span>'
                f'<span class="ba-tl-choice ba-mono">{esc(str(s.get("agent_choice", "")))}</span>'
                "</span>"
            )
        rows.append(
            '<div class="ba-tl-stage">'
            '<div class="ba-tl-stage-head">'
            f'<span class="ba-tl-stage-id">{esc(stage_id)}</span>'
            f'<span class="ba-tl-stage-cn">{esc(stage_meta.get("cn", ""))}</span>'
            '<span class="ba-tl-tag">已审计</span>'
            "</div>"
            f'<div class="ba-tl-chips">{"".join(chips)}</div>'
            "</div>"
        )
    # 未知阶段决策：归"未分类"桶（不伪造真实阶段）
    if unknown:
        chips = []
        for s in unknown:
            level = int(s.get("level", -1))
            if level >= 2:
                tone = "ba-tl-normal"
            elif level == 1:
                tone = "ba-tl-risk"
            elif level == 0:
                tone = "ba-tl-danger"
            else:
                tone = "ba-tl-unverified"
            chips.append(
                f'<span class="ba-tl-chip {tone}" '
                f'title="{esc(str(s.get("explanation", "")))}">'
                '<span class="ba-tl-dot"></span>'
                f'{level_badge_html(level)}'
                f'<span class="ba-tl-type">{esc(str(s.get("decision_type", "?")))}</span>'
                f'<span class="ba-tl-choice ba-mono">{esc(str(s.get("agent_choice", "")))}</span>'
                "</span>"
            )
        rows.append(
            '<div class="ba-tl-stage">'
            '<div class="ba-tl-stage-head">'
            '<span class="ba-tl-stage-id">unclassified</span>'
            '<span class="ba-tl-stage-cn">未分类（本体无此阶段）</span>'
            '<span class="ba-tl-tag">已审计</span>'
            "</div>"
            f'<div class="ba-tl-chips">{"".join(chips)}</div>'
            "</div>"
        )
    legend = (
        '<div class="ba-tl-legend">'
        '<span class="ba-tl-chip ba-tl-normal"><span class="ba-tl-dot"></span>正常</span>'
        '<span class="ba-tl-chip ba-tl-risk"><span class="ba-tl-dot"></span>风险</span>'
        '<span class="ba-tl-chip ba-tl-danger"><span class="ba-tl-dot"></span>危险</span>'
        '<span class="ba-tl-chip ba-tl-unverified"><span class="ba-tl-dot">'
        '</span>未验证 / 无法评估</span>'
        "</div>"
    )
    st.markdown(
        '<div class="ba-card ba-tl">' + legend + "".join(rows) + "</div>",
        unsafe_allow_html=True,
    )


def ontology_decision_order(ontology) -> list[str]:
    """本体顺序：stages.yaml 阶段顺序 → 阶段内决策类型按字母序（对比对齐用）。"""
    order: list[str] = []
    for stage in ontology.stages:
        order.extend(
            sorted(
                tid for tid, t in ontology.types.items() if t["stage"] == stage
            )
        )
    return order


def render_comparison(
    results: list[dict],
    ontology,
    labels: dict[str, str],
    primary_id: str | None = None,
) -> None:
    """轨迹并排对比：行 = 决策类型（ontology 顺序），缺失列"无此决策"。

    同时给出每列的分数 / verdict 色点 / L 分布 / 问题类型摘要条。

    ``primary_id`` = 基准轨迹 id（「③ 案例轨迹」选中项）：给定后列头标
    「基准 / 对比 N」徽章（主从可辨）；为 None 时保持无标记（兼容旧调用）。
    """
    if len(results) < 2:
        return
    order = ontology_decision_order(ontology)
    present = [t for t in order if any(
        t in {s.get("decision_type") for s in r["state"].get("step_scores", [])}
        for r in results
    )]
    # 本体顺序之外的决策类型（如未收录类型）：不静默丢行，按出现顺序追加末尾
    extra: list[str] = []
    for r in results:
        for s in r["state"].get("step_scores", []):
            tid = str(s.get("decision_type", "?"))
            if tid not in order and tid not in extra:
                extra.append(tid)
    types = present + extra
    st.markdown('<div class="ba-section-title">轨迹并排对比</div>',
                unsafe_allow_html=True)
    st.caption(
        "决策行按本体阶段顺序对齐——缺失列显示「无此决策」：错位即信息"
        "（如 DEG 无 doublet_detection）。"
    )
    head_parts = []
    vs_index = 0
    for r in results:
        rid = r["trajectory_id"]
        label = esc(labels.get(rid, rid))
        if primary_id is not None and rid == primary_id:
            tag = '<span class="ba-cmp-tag ba-cmp-tag-primary">基准</span>'
        elif primary_id is not None:
            vs_index += 1
            tag = f'<span class="ba-cmp-tag ba-cmp-tag-vs">对比 {vs_index}</span>'
        else:
            tag = ""
        head_parts.append(f'<th><div class="ba-cmp-head">{tag}<span>{label}</span></div></th>')
    head = '<thead><tr><th>决策类型</th>' + "".join(head_parts) + "</tr></thead>"
    summary = (
        "<tr class='ba-cmp-summary'><td>分数 · verdict · L 分布 · 问题</td>"
        + "".join(
            f"<td>{_cmp_summary_cell(r)}</td>" for r in results
        )
        + "</tr>"
    )
    body_rows = []
    for tid in types:
        cells = [f'<td class="ba-cmp-type">{esc(tid)}</td>']
        for r in results:
            scores = r["state"].get("step_scores", [])
            s = next((x for x in scores if x.get("decision_type") == tid), None)
            if s is None:
                cells.append('<td class="ba-cmp-missing">无此决策</td>')
            else:
                choice = str(s.get("agent_choice", ""))
                cells.append(
                    "<td>"
                    f"{level_badge_html(int(s.get('level', -1)))}"
                    f'<span class="ba-cmp-choice ba-mono" title="{esc(choice)}">'
                    f"{esc(choice)}</span></td>"
                )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    # 单次 markdown 输出完整表格（走查发现：开/闭标签分两次 st.markdown
    # 调用会被 Streamlit 拆进独立 DOM 容器 → 空 table + 孤儿 thead/tbody，
    # 表格边框/表头样式/窄屏堆叠全部失效）
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp">'
        + head
        + "<tbody>"
        # 合规约束（2026-09-21 定案）：表格汇总行属「结论位」，
        # 故汇总行移至明细行之后（不改数值、不改单元格内容）。
        + "".join(body_rows)
        + summary
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _cmp_summary_cell(r: dict) -> str:
    """对比摘要格：分数 + verdict 色点 + L 分布 + 问题数。"""
    state = r["state"]
    score = float(state.get("trajectory_score", 0))
    verdict = str(state.get("eval_verdict", "unknown"))
    counts = level_counts_label(state.get("step_scores", []))
    issues = state.get("critical_issues") or []
    issue_note = f"问题 {len(issues)} 条" if issues else "无 critical issue"
    return (
        f'<div class="ba-cmp-score ba-mono">{score:.1f}</div>'
        f'<div>{verdict_dot_html(verdict)}{esc(verdict)}</div>'
        f'<div class="ba-cmp-l">{esc(counts)}</div>'
        f'<div class="ba-cmp-issues" title="{esc("；".join(issues)[:400])}">'
        f"{esc(issue_note)}</div>"
    )
