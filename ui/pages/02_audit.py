"""Page 2: 逐步审计（薄壳 v2）— 审计计算全部走 bioaudit.api.run_audit。

相对 fullflow-demo/src/ui/pages/02_audit.py：
- 移除内联 7 步管道（审计 B3/B4：三处管道漂移）与演示 sleep（审计 D7）
- 只调 api（v1 蓝图：UI 薄壳）；匹配明细走 api.match_details
- 移除 unsafe_allow_html script 注入（审计 D12）
"""
import json
import re

import streamlit as st

from bioaudit.api import match_details, run_audit
from bioaudit.errors import BioAuditError

st.set_page_config(page_title="Bio-Audit — 审计过程", page_icon="🔍", layout="wide")

# ── Guard ──
if "trajectory" not in st.session_state or st.session_state.trajectory is None:
    st.warning("请先返回首页选择演示模式 / Please return to homepage.")
    st.stop()

traj = st.session_state.trajectory
demo_mode = st.session_state.get("demo_mode", "correct")
is_correct = st.session_state.get("is_correct_traj", demo_mode == "correct")
act = st.session_state.get("act", None)

# ── 常量 ──
EMOJI = {3: "🟢", 2: "🟡", 1: "🟠", 0: "🔴", -1: "⚪"}
LV_NAME = {3: "正确级", 2: "可接受", 1: "有风险", 0: "危险级", -1: "无法评估"}
LV_COLOR = {3: "#00B07C", 2: "#E8A000", 1: "#E84040", 0: "#8B0000", -1: "#808080"}

# ═══════════════════════════════════════════════════════════
# ① 标题横幅
# ═══════════════════════════════════════════════════════════
st.title(f"🔬 Bio-Audit: {st.session_state.get('act_title', 'Audit')} / Decision Audit")
st.caption(f"任务: {st.session_state.get('act_task', (traj.get('task', '') if isinstance(traj, dict) else 'Analysis'))}")

mode_badge = (
    f"<span style='background-color:#00B07C;color:white;padding:4px 14px;"
    f"border-radius:12px;font-weight:bold'>✅ 正确分析模式</span>"
    if is_correct else
    f"<span style='background-color:#E84040;color:white;padding:4px 14px;"
    f"border-radius:12px;font-weight:bold'>⚠ 错误分析模式</span>"
)
st.markdown(mode_badge, unsafe_allow_html=True)
st.divider()

# ═══════════════════════════════════════════════════════════
# 审计执行 — 单次调用 api.run_audit（无 sleep，无内联管道）
# ═══════════════════════════════════════════════════════════
if "audit_result" not in st.session_state or st.session_state.audit_result is None:
    with st.spinner("⏳ 运行审计引擎（7 步管道：解析 → 匹配 → 评分 → 冲突 → 聚合 → 传播 → 报告）..."):
        try:
            result = run_audit(traj["decisions"], act=act)
        except BioAuditError as exc:  # B3：契约错误（错误码显式展示，不裸抛）
            st.error(f"审计输入被拒绝 [{exc.code}]: {exc.message}")
            if exc.details:
                st.code(json.dumps(exc.details, ensure_ascii=False, indent=1))
            st.stop()
    if result.get("error"):
        code = result.get("error_code") or "internal-error"
        st.error(f"审计失败 [{code}]: {result['error']}")
        st.stop()
    st.session_state.audit_result = result
    st.rerun()

result = st.session_state.audit_result
step_scores = result["step_scores"]
parsed_steps = result["parsed_steps"]
matched_rules = result["matched_rules"]

# ═══════════════════════════════════════════════════════════
# ② 审计摘要面板 — 先给结论
# ═══════════════════════════════════════════════════════════
score_val = result["trajectory_score"]
verdict_val = result["eval_verdict"]
VERDICT_STYLE = {
    "pass": ("🟢", "#00B07C", "各决策均达可判定等级（决策等级的聚合，非科学结论）"),
    "blocked": ("⛔", "#8B0000", "检测到致命科学错误 — 分析结果不可信"),
    "needs_correction": ("⚠️", "#E8A000", "需要修正后再使用"),
}
v_emoji, v_color, v_msg = VERDICT_STYLE.get(verdict_val, ("⚪", "#808080", "?"))

st.markdown("## 📊 审计摘要 / Audit Summary")

# P-04 v0.2 §4.3：结论位＝DOM 渲染序首个结论性元素。
# 故本组列序为「verdict、分数、维度分」——verdict 先渲染；分数不再占结论位，
# 且其单位/标签去「总分」类表述（§4.3 明示不得使用「/ 总分」类单位）。
c_verdict, c_total, c_dims = st.columns([2, 1, 2])
with c_verdict:
    st.markdown(
        f"<div style='padding:16px;border-radius:12px;background-color:{v_color}1A;height:100%'>"
        f"<div style='font-size:28px;font-weight:bold;color:{v_color}'>{v_emoji} {verdict_val.upper()}</div>"
        f"<div style='font-size:16px;color:#444;margin-top:6px'>{v_msg}</div>"
        f"</div>", unsafe_allow_html=True,
    )
with c_total:
    st.markdown(
        f"<div style='text-align:center;padding:16px;border-radius:12px;"
        f"background-color:{v_color}1A;'>"
        f"<div style='font-size:64px;font-weight:bold;color:{v_color};line-height:1.2'>{score_val:.0f}</div>"
        f"<div style='font-size:18px;color:#666'>决策等级聚合（审计评分，非总分/非质量排名）</div>"
        f"</div>", unsafe_allow_html=True,
    )
with c_dims:
    dims = result.get("dimension_scores", {})
    dim_labels = {"data_handling": "数据处理", "method_selection": "方法选择", "statistical_rigor": "统计严谨性"}
    dim_md = "<div style='padding:12px;border-radius:12px;background-color:#f8f9fa;height:100%'>"
    for dim, val in dims.items():
        pct = val * 100
        bar_color = "#00B07C" if pct >= 70 else ("#E8A000" if pct >= 40 else "#E84040")
        dim_md += (
            f"<div style='margin-bottom:8px'>"
            f"<span style='font-size:14px;color:#333'>{dim_labels.get(dim, dim)}</span>"
            f"<span style='float:right;font-size:16px;font-weight:bold;color:{bar_color}'>{pct:.0f}%</span></div>"
            f"<div style='background-color:#e9ecef;border-radius:4px;height:8px;margin-bottom:10px'>"
            f"<div style='background-color:{bar_color};border-radius:4px;height:8px;width:{pct}%'></div></div>"
        )
    dim_md += "</div>"
    st.markdown(dim_md, unsafe_allow_html=True)

st.markdown("")
# 红绿灯概览（D5 修复：列数 = 决策数，不再硬编码 5 列）
st.markdown("**关键决策状态 / Decision Status:**")
light_cols = st.columns(len(step_scores))
for col, score in zip(light_cols, step_scores):
    cn = score["decision_type"].replace("_", " ").title()
    lv_color = LV_COLOR.get(score["level"], "#808080")
    with col:
        # P-04 §4.3 同类全量：本概览原渲染原始级别号「Level -1」（外露内部编号），
        # 与下方决策条之「Level -1 · 无法评估」表述不一；改用与该级名称一致的写法
        # （-2 等未登记值仍按原样显示编号，以免静默丢失诊断信息）。
        _lv_no = score["level"]
        _lv_label = LV_NAME.get(_lv_no)
        _lv_txt = f"Level {_lv_no}" + (f" · {_lv_label}" if _lv_label else "")
        st.markdown(
            f"<div style='text-align:center;padding:10px 6px;border-radius:10px;"
            f"border:2px solid {lv_color};'>"
            f"<div style='font-size:28px'>{EMOJI.get(score['level'], '⚪')}</div>"
            f"<div style='font-size:13px;font-weight:bold;color:#333'>{cn}</div>"
            f"<div style='font-size:12px;color:{lv_color};font-weight:bold'>{_lv_txt}</div>"
            f"</div>", unsafe_allow_html=True,
        )

st.divider()

# ═══════════════════════════════════════════════════════════
# ③ Agent 完整分析流程 — 时间轴
# ═══════════════════════════════════════════════════════════
st.markdown("## 📋 Agent 完整分析流程 / Full Analysis Pipeline")
st.caption("Agent 从数据获取到生成报告的每一步 — 状态点: 🟢 正常 | ⚠ 存在风险 | 🔍 被审计")

workflow = traj.get("workflow", [])
score_by_step = {s["step_id"]: s["level"] for s in step_scores}

if workflow:
    for wf in workflow:
        action = wf["action"]
        is_audited = "[决策" in action
        is_warn = "⚠" in action

        if is_audited:
            m = re.search(r"\[决策(\d)\]", action)
            step_key = f"s{m.group(1)}" if m else None
            lvl = score_by_step.get(step_key, -1)
            dot = EMOJI.get(lvl, "⚪")
            dot_color = LV_COLOR.get(lvl, "#808080")
        elif is_warn:
            dot = "⚠️"; dot_color = "#E84040"
        else:
            dot = "✅"; dot_color = "#00B07C"

        with st.container(border=True):
            c_dot, c_info = st.columns([1, 8])
            with c_dot:
                st.markdown(f"<div style='text-align:center;font-size:22px'>{dot}</div>", unsafe_allow_html=True)
            with c_info:
                c_phase, c_action, c_out = st.columns([2, 3, 4])
                with c_phase:
                    st.markdown(f"**步骤 {wf['step']} · {wf['phase']}**")
                with c_action:
                    st.markdown(f"<span style='color:#333'>{wf['action']}</span>", unsafe_allow_html=True)
                with c_out:
                    with st.expander(f"查看输入/输出", expanded=False):
                        st.caption(f"**输入:** {wf['input']}")
                        st.caption(f"**输出:** {wf['output']}")
else:
    st.info("该轨迹未含 workflow 字段 — 时间轴为空（旧轨迹数据限制）")

st.divider()

# ═══════════════════════════════════════════════════════════
# ④ 决策审查 — 彩色头部条 + 三段式
# ═══════════════════════════════════════════════════════════
st.markdown("## 🔍 逐决策透明审查 / Decision-by-Decision Audit")
st.caption("每个决策三段式: ① Agent做了什么 → ② 引擎检查了什么 → ③ 审计判定")

for i, score in enumerate(step_scores):
    cn = score["decision_type"].replace("_", " ").title()
    lvl = score["level"]
    lv_color = LV_COLOR.get(lvl, "#808080")
    lv_name = LV_NAME.get(lvl, "?")

    # P-04 §4.3：level=-1（「无法评估」）之 numeric_score 是**占位值**、引擎侧明定
    # 不参与聚合（见 demo/data/reward_summary.json 之 mapping 不含 -1、mask 将该级
    # 置为不计入）——故该级**不显示 `/10` 数值**，以免出现「无法评估却得 5.0/10」。
    _has_score = lvl != -1 and "numeric_score" in score
    _badge = f"Level {lvl} · {lv_name}"
    if _has_score:
        _badge += f" · {score['numeric_score'] * 10:.1f}/10"

    st.markdown(
        f"<div style='background-color:{lv_color};padding:10px 16px;border-radius:8px 8px 0 0;'>"
        f"<span style='color:white;font-size:17px;font-weight:bold'>{EMOJI.get(lvl, '⚪')} "
        f"决策 {i + 1}: {cn}</span>"
        f"<span style='float:right;color:white;background-color:rgba(255,255,255,0.25);"
        f"padding:2px 12px;border-radius:10px;font-weight:bold'>{_badge}</span>"
        f"</div>", unsafe_allow_html=True,
    )

    step_dict = parsed_steps[i] if i < len(parsed_steps) else {}
    ctx = step_dict.get("normalized_context", {})
    rule_details = match_details(score["decision_type"], ctx, act=act)

    with st.container(border=True):
        # ① Agent 做了什么
        st.markdown("**① Agent 做了什么 / What the Agent Did**")
        c_in, c_act = st.columns([1, 2])
        with c_in:
            st.markdown("**输入上下文:**")
            for k, v in ctx.items():
                st.caption(f"`{k}` = `{v}`")
        with c_act:
            st.code(score["agent_choice"], language=None)
            st.caption(f"*理由:* {score['agent_rationale']}")

        st.markdown("---")

        # ② 引擎检查了什么
        st.markdown("**② 审计引擎检查了什么 / What the Engine Checked**")
        if rule_details:
            for rd in rule_details:
                status_icon = "✅" if rd["matched"] else "❌"
                with st.expander(
                    f"{status_icon} 规则 {rd['rule_id']}: {rd['title']} "
                    f"({'生效 → 参与评分' if rd['matched'] else '未生效 → 跳过'})",
                    expanded=rd["matched"] and lvl <= 1,
                ):
                    for check in rd["checks"]:
                        ck_icon = "✅" if check["pass"] else "❌"
                        st.caption(f"{ck_icon} `{check['expr']}`  实际值: `{check['actual']}`")
        else:
            st.warning("无匹配规则 — 该决策类型不在规则库覆盖范围内")

        st.markdown("---")

        # ③ 审计判定
        st.markdown("**③ 审计判定 / Verdict**")
        c_v1, c_v2 = st.columns([1, 2])
        with c_v1:
            st.markdown(
                f"<div style='text-align:center;padding:12px;border-radius:10px;"
                f"background-color:{lv_color}1A;'>"
                f"<div style='font-size:36px;font-weight:bold;color:{lv_color}'>Level {lvl}</div>"
                f"<div style='font-size:14px;color:#666'>{lv_name}</div>"
                f"</div>", unsafe_allow_html=True,
            )
        with c_v2:
            st.markdown(f"**评分原因:** {score['explanation']}")
            if lvl <= 1:
                st.error(f"**🔧 修正建议:** {' | '.join(score['alternatives']) if score['alternatives'] else '请参照科学标准调整'}")
            else:
                st.success("✅ 该决策达可判定等级（决策等级的聚合，非科学结论）")

        # 证据 (折叠)
        with st.expander("📚 科学依据 (点击直达原文)", expanded=(lvl <= 1)):
            if score["evidence_citations"]:
                for cite in score["evidence_citations"]:
                    st.caption(f"- {cite}")
            else:
                st.caption("无证据引用")

    st.markdown("")  # spacing

# ═══════════════════════════════════════════════════════════
# ⑤ 规则引擎明细 (折叠 — 技术观众)
# ═══════════════════════════════════════════════════════════
st.divider()
with st.expander("⚙️ 规则引擎明细 / Rule Engine Internals (技术细节)", expanded=False):
    from bioaudit.storage.rule_registry import RuleRegistry
    from bioaudit.paths import rules_dir_for

    reg = RuleRegistry(rules_dir_for(act))
    n = reg.load_all()
    st.caption(f"活跃规则: {n} 条 | 决策类型覆盖: {reg.type_count} 种")

    for i, score in enumerate(step_scores):
        st.markdown(f"**决策 {i + 1} ({score['step_id']}):** 类型 `{score['decision_type']}` | "
                    f"匹配规则: {', '.join(matched_rules.get(score['step_id'], [])) or '无'}")

    if result.get("error_chains"):
        st.markdown("**错误传播链:**")
        for chain in result["error_chains"]:
            sev = chain.get("severity", "minor")
            st.error(f"🔗 {chain['source_step']} ({sev}) → 影响 {', '.join(chain['affected_steps'])}")

    if result["report"].get("conflicts_needing_review"):
        st.markdown("**需人工审核的规则冲突:**")
        for c in result["report"]["conflicts_needing_review"]:
            st.warning(f"⚠ {c['rule_a']} (Lv{c['score_a']}) vs {c['rule_b']} (Lv{c['score_b']})")

# ── 导航 ──
st.markdown("---")
c1, _, c2 = st.columns([2, 1, 2])
with c1:
    if st.button("← 返回首页", use_container_width=True):
        st.session_state.audit_result = None
        st.switch_page("pages/01_upload.py")
with c2:
    if st.button("查看审计报告 →", type="primary", use_container_width=True):
        st.switch_page("pages/03_report.py")
