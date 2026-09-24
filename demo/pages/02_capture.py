"""采集演示页（机制层）。

设计依据：深色审计台设计规范 §3.3（采集页 = 机制层）+ §6（63.7 复现
技术说明）+ 验收清单。

页面结构（机制层四大块）：
1. **声明 vs 事实对齐表（四类判定）**：10X-B 真实交叉验证结果的逐决策
   对齐——一致/虚报/漏报/未验证四色 + expected 补入徽章；M1 声明 vs
   M3 事实（operative 工具签名 + 实例数）双列对照；
2. **verdict 状态位流转时间线**：verdicts jsonl 三态真实数据
   （11 provisional + 13 final + 1 revoked）逐 verdict 流转
   （provisional → final/revoked；expected 补入直接 final）；
3. **expected_types 机制交互（63.7 复现）**：勾选预期决策点清单
   （读 expected_types.yaml）→ 实时重算 → 「静默跳过被补入 → L0 →
   63.7 · blocked」完整复现；与工坊页现象层数字一致（共享
   capture_chain 单一事实源）；断言基准 = demo/data 副本；
4. **declared 注入（高级折叠区）**：expandable + 工具提示——普通观众
   不细看，想懂的人能展开（按设计建议）。

分工纪律：工坊页 = 现象（点按钮看结果）；本页 = 机制（勾选清单实时重算、
看中间产物：对齐表四色 + 补入过程 + verdict 流转）。两处数字必须一致
（验收会独立重算）。

输入全部为 demo/data 提炼副本（自包含性硬约束），零读仓库外产物。
"""
from __future__ import annotations

import html
import json

import capture_chain
import case_registry
import components
import data_index
import narrative
import result_view
import streamlit as st
import zh_labels

from bioaudit.ontology.loader import get_ontology
from bioaudit.report import current_snapshot

#: 勾选交互键前缀（cap_exp_{decision_type}；session_state 持久化，刷新不丢）
CHECK_PREFIX = "cap_exp"

#: 四类判定 → (中文名, 色, 语义说明)（四色语义严格绑定，设计 §3.3）
STATUS_META: dict[str, tuple[str, str, str]] = {
    "consistent": ("一致", "#10b981", "M1 声明的 choice 被 M3 已验证实例证实"),
    "false_positive": (
        "虚报", "#ef4444",
        "M1 声明但 M3 无对应执行证据（含 choice 不符 / 仅未定证据）",
    ),
    "false_negative": (
        "漏报", "#f59e0b",
        "M3 执行但 M1 未声明 → 自动补入审计（verdict final，来源 M3解析）",
    ),
    "unverified": ("未验证", "#9ca3af", "预期决策点双方都无证据（绝不伪造）"),
}

STATUS_ORDER = ("consistent", "false_positive", "false_negative", "unverified")


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


# ── verdict 状态位流转（真实数据：verdicts jsonl 25 行三态）─────────────

def _verdict_raw_records(case_id: str | None = None) -> list[dict]:
    """该案例 verdicts jsonl 原始记录（status 分布 = 冻结锚点）。
    路径单一事实源 = ``capture_chain.case_paths``（按案例注册表取）。"""
    verdicts_path, _, _ = capture_chain.case_paths(case_id)
    return [
        json.loads(line)
        for line in verdicts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _verdict_flows(case_id: str | None = None) -> list[dict]:
    """逐 verdict 重建流转：同 verdict_id 记录按时间追加 → 末条为终态。"""
    flows: dict[str, list[dict]] = {}
    for rec in _verdict_raw_records(case_id):
        flows.setdefault(rec["verdict_id"], []).append(rec)
    out: list[dict] = []
    for vid, recs in flows.items():
        last = recs[-1]
        history = [h["status"] for h in last["history"]]
        out.append({
            "verdict_id": vid,
            "decision_type": last["decision_type"],
            "choice": last["choice"],
            "provenance_source": last["provenance_source"],
            "status": last["status"],
            "history": history,
            "reason": last["history"][-1]["reason"] if last["history"] else "",
            "created_at": recs[0]["created_at"],
            "updated_at": last["updated_at"],
        })
    return out


def _verdict_count(records: list[dict]) -> dict[str, int]:
    """25 行原始记录按 status 计数（11 provisional + 13 final + 1 revoked——
    与冻结锚点一致；逐 verdict 的终态流转见下方逐行展示）。"""
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    return counts


# ── 实时重算（st.cache_data 按勾选清单缓存——控件交互不重算一切）────────

@st.cache_data(show_spinner=False)
def _chain_cached(case_id: str, expected: tuple[str, ...]) -> dict:
    """按（案例, 勾选清单）实时重算链路（与工坊页现象层共用 capture_chain）。"""
    return capture_chain.run_chain(list(expected), case_id=case_id)


def _expected_checkbox_list(case_id: str | None = None) -> tuple[str, ...]:
    """当前勾选的预期决策点（顺序 = 清单顺序；session_state 持久化）。"""
    return tuple(
        tid for tid in capture_chain.default_expected(case_id)
        if st.session_state.get(f"{CHECK_PREFIX}_{tid}", True)
    )


# ── 案例切换过渡动画（与工坊页共用 components.process_animation）─────────
# 纪律：任何「选择 → 结果」都要有过渡——切案例时按真实步骤重算并逐步呈现，
# 观众能看到这一轮「增加了什么 / 减少了什么」，而不是内容瞬间全变。
CAPTURE_ANIM_STEP_SECONDS = 0.9
CAPTURE_RECHECK_STEP_SECONDS = 0.6
CAPTURE_ANIM_CASE_KEY = "cap_anim_sig"


def _recheck_animation_steps(case_id: str, chain: dict) -> list[tuple[str, str]]:
    """勾选预期决策点后的重算过程（3 步 · 短过渡）。

    ``expected`` 清单变化会改变交叉验证结果与结论——同样属于
    「选择 → 结果」，因此也要有过渡（显示这一轮补入与结论的变化）。
    """
    stats = chain["stats"]
    added = [a["decision_type"] for a in chain["added"]]
    state = chain["state"]
    return [
        (
            "重算预期决策点清单",
            f"当前勾选 {stats.get('expected_added', 0) + stats.get('consistent', 0)} 类"
            f"（含声明与补入）",
        ),
        (
            "交叉验证与补入更新",
            f"一致 {stats.get('consistent', 0)} / 虚报 {stats.get('false_positive', 0)}"
            f" / 补入 {stats.get('expected_added', 0)} 类"
            + (f"（{'、'.join(added)}）" if added else "（无缺失）"),
        ),
        (
            "更新结论",
            f"final {chain['final_n']} 决策 → {float(state['trajectory_score']):.1f}"
            f" · {zh_labels.verdict_zh(state['eval_verdict'])}",
        ),
    ]


def _capture_animation_steps(case_id: str, chain: dict) -> list[tuple[str, str]]:
    """采集页案例切换的五步过程（每步填该案例的真实数字）。"""
    case = case_registry.get_case(case_id)
    stats = chain["stats"]
    added = [a["decision_type"] for a in chain["added"]]
    state = chain["state"]
    return [
        (
            "载入案例声明（M1）",
            f"{case.label}｜声明 {chain['n_m1']} 条（verdict_id 去重取末条）",
        ),
        (
            "解析执行产物（M3）",
            f"{case.executed_source_kind} 源 → {chain['m3_n_candidates']} 候选"
            f"（未定 {chain['m3_n_uncertain']}）",
        ),
        (
            "声明 × 执行交叉验证",
            f"一致 {stats.get('consistent', 0)} / 虚报 {stats.get('false_positive', 0)}"
            f" / 漏报 {stats.get('false_negative', 0)}"
            f" / 未验证 {stats.get('unverified', 0)}",
        ),
        (
            "预期决策点比对",
            f"补入 {stats.get('expected_added', 0)} 类"
            + (f"（{'、'.join(added)}）" if added else "（无缺失）"),
        ),
        (
            "判级并生成结论",
            f"final {chain['final_n']} 决策 → {float(state['trajectory_score']):.1f}"
            f" · {zh_labels.verdict_zh(state['eval_verdict'])}",
        ),
    ]


# ── §1 声明 vs 事实对齐表（四类判定）──────────────────────────────────

def _render_alignment_table(chain: dict) -> None:
    st.markdown('<div class="ba-section-title">① 声明 vs 事实对齐表（四类判定）</div>',
                unsafe_allow_html=True)
    st.caption(
        "10X-B 真实交叉验证逐决策对齐（demo/data 副本实时重算）："
        "「一致 / 虚报 / 漏报 / 未验证」四色 + expected 补入徽章；"
        "M3 事实 = 该类型最终 operative 实例的工具签名。"
    )
    stats = chain["stats"]
    chips = "".join(
        f'<span class="ba-stat-chip" style="color:{STATUS_META[s][1]};'
        f'border-color:{STATUS_META[s][1]}66">'
        f"{STATUS_META[s][0]} {stats.get(s, 0)}</span>"
        for s in STATUS_ORDER
    )
    chips += (
        f'<span class="ba-stat-chip ba-stat-chip-exp">补入 expected '
        f"{stats.get('expected_added', 0)}</span>"
    )
    st.markdown(
        f'<div class="ba-stat-row">{chips}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-align-legend">'
        '<span class="ba-align-legend-item"><i style="background:#10b981"></i>'
        "一致</span>"
        '<span class="ba-align-legend-item"><i style="background:#ef4444"></i>'
        "虚报（声明未执行）</span>"
        '<span class="ba-align-legend-item"><i style="background:#f59e0b"></i>'
        "漏报（执行未声明 → 自动补入）</span>"
        '<span class="ba-align-legend-item"><i style="background:#9ca3af"></i>'
        "未验证（双方都无，不伪造）</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    ontology = get_ontology()
    # 行序 = 本体阶段顺序（与工坊页对比表同一对齐纪律）
    order = result_view.ontology_decision_order(ontology)
    align_map = {a["decision_type"]: a for a in chain["alignments"]}
    rows = [align_map[t] for t in order if t in align_map]

    body = []
    for a in rows:
        _label, color, _desc = STATUS_META.get(
            a["status"], (a["status"], "#9ca3af", ""))
        m1_choice = a["m1_choice"] or "—"
        if a["m3_tool"]:
            inst_note = f"×{a['n_instances']}" if a["n_instances"] > 1 else ""
            m3_cell = (
                f'<span class="ba-mono">{_esc(a["m3_tool"])}</span>'
                f'<span class="ba-inst">{inst_note} 实例</span>'
            )
        else:
            m3_cell = '<span class="ba-align-none">无执行证据</span>'
        badge = (
            f'<span class="ba-align-badge" style="color:{color};'
            f'border-color:{color}66;background:{color}14">{_esc(_label)}</span>'
        )
        if a["expected_added"]:
            badge += (
                '<span class="ba-align-badge ba-align-badge-exp" '
                'title="预期决策缺失 → 补入 provenance=expected（该做没做）">'
                "补入 expected</span>"
            )
        row_class = "ba-align-row-fp" if a["status"] == "false_positive" else ""
        meta = ontology.get_type(a["decision_type"]) or {}
        cn = meta.get("display", {}).get("cn", a["decision_type"])
        body.append(
            f'<tr class="{row_class}">'
            f'<td><div class="ba-align-type">{_esc(cn)}</div>'
            f'<div class="ba-align-tid ba-mono">{_esc(a["decision_type"])}</div></td>'
            f"<td>{badge}</td>"
            f'<td class="ba-mono">{_esc(m1_choice)}</td>'
            f"<td>{m3_cell}</td>"
            f'<td class="ba-align-detail" title="{_esc(a["detail"])}">'
            f"{_esc(a['detail'][:120])}</td>"
            "</tr>"
        )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-align">'
        "<thead><tr><th>决策点</th><th>判定</th><th>M1 声明</th>"
        "<th>M3 事实</th><th>机制说明</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "本案例无漏报 / 未验证（分布为真实运行结果，不凑数）；"
        "「被后续迭代取代」= 同类型多次调用（实例 1 → N），最终实例与声明一致。"
    )


# ── §2 verdict 状态位流转时间线 ──────────────────────────────────────

def _render_verdict_flow(flows: list[dict], case_id: str | None = None) -> None:
    st.markdown('<div class="ba-section-title">② verdict 状态位流转</div>',
                unsafe_allow_html=True)
    raw_records = _verdict_raw_records(case_id)
    session = raw_records[0]["session_id"] if raw_records else "?"
    counts = _verdict_count(raw_records)
    st.caption(
        f"verdicts jsonl {len(raw_records)} 行原始记录（{session} 会话）："
        "M1 主动上报即 provisional → 交叉验证一致转 final / 判虚报撤销为 "
        "revoked；expected 补入直接 final。下方计数 = 记录分布（与冻结锚点"
        "11/13/1 一致）；逐行展示每条 verdict 的流转（终态）。"
    )
    st.markdown(
        '<div class="ba-stat-row">'
        f'<span class="ba-flow-stat">provisional <b>{counts.get("provisional", 0)}</b></span>'
        '<span class="ba-flow-arrow">→</span>'
        f'<span class="ba-flow-stat ba-flow-final">final <b>{counts.get("final", 0)}</b></span>'
        '<span class="ba-flow-arrow">/</span>'
        f'<span class="ba-flow-stat ba-flow-revoked">revoked '
        f'<b>{counts.get("revoked", 0)}</b></span>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="ba-flow-legend">'
        '<span class="ba-flow-chip ba-flow-chip-prov">provisional</span>'
        '<span class="ba-flow-arrow">→</span>'
        '<span class="ba-flow-chip ba-flow-chip-final">final · 一致</span>'
        '<span class="ba-flow-arrow">/</span>'
        '<span class="ba-flow-chip ba-flow-chip-revoked">revoked · 虚报</span>'
        '<span class="ba-flow-arrow">/</span>'
        '<span class="ba-flow-chip ba-flow-chip-exp">final · expected 补入</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    ontology = get_ontology()
    rows = []
    for f in flows:
        meta = ontology.get_type(f["decision_type"]) or {}
        cn = meta.get("display", {}).get("cn", f["decision_type"])
        is_expected = f["provenance_source"] == "expected"
        is_revoked = f["status"] == "revoked"
        if is_expected:
            chips = (
                '<span class="ba-flow-chip ba-flow-chip-exp">final · expected 补入</span>'
            )
        elif is_revoked:
            chips = (
                '<span class="ba-flow-chip ba-flow-chip-prov">provisional</span>'
                '<span class="ba-flow-arrow">→</span>'
                '<span class="ba-flow-chip ba-flow-chip-revoked">revoked · 虚报</span>'
            )
        else:
            chips = (
                '<span class="ba-flow-chip ba-flow-chip-prov">provisional</span>'
                '<span class="ba-flow-arrow">→</span>'
                '<span class="ba-flow-chip ba-flow-chip-final">final · 一致</span>'
            )
        row_class = "ba-flow-row-revoked" if is_revoked else (
            "ba-flow-row-exp" if is_expected else "")
        rows.append(
            f'<div class="ba-flow-row {row_class}">'
            f'<div class="ba-flow-dec"><span class="ba-flow-cn">{_esc(cn)}</span>'
            f'<span class="ba-flow-tid ba-mono">{_esc(f["decision_type"])}</span>'
            f'<span class="ba-flow-choice ba-mono">{_esc(f["choice"])}</span></div>'
            f'<div class="ba-flow-chips">{chips}</div>'
            f'<div class="ba-flow-reason" title="{_esc(f["reason"])}">'
            f"{_esc(f['reason'][:64])}</div>"
            "</div>"
        )
    st.markdown(
        '<div class="ba-card ba-flow">' + "".join(rows) + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "skip_doublet 声明 = 完整「虚报」流转：provisional（M1 主动上报）→ "
        "revoked（M3 无 doublet_detection 任何执行证据）；随后该预期决策点"
        "按 expected 补入（新 verdict，final，重跑两次留 2 条记录——"
        "23:04 初跑 + 23:05 基准生成时刻，逐行忠实呈现）。历史记录另含 1 条 "
        "expected api_data_integrity（早期清单），现行配置已不含该类型"
        "（M3 无确定性签名）。"
    )


# ── §3 expected_types 机制交互（63.7 复现）────────────────────────────

def _render_expected_interaction(case_id: str | None = None) -> None:
    st.markdown(
        '<div class="ba-section-title">③ expected_types 机制交互（63.7 复现）</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="ba-callout ba-callout-warn">预期决策点清单放评测配置'
        "（expected_types.yaml，per 范式×平台，非引擎硬编码）。**缺失的预期决策"
        "会被补入 provenance=expected 参与评分**——「该做没做」不再依赖决策被"
        "声明：10X 平台下 doublet_detection 为标准管线预期决策点，静默跳过也会"
        "被补入 → D1.1 L0 → blocked。</div>",
        unsafe_allow_html=True,
    )

    expected = capture_chain.default_expected(case_id)
    # 勾选键预置（首次渲染前初始化默认全选；之后以 session_state 为准——
    # 不带 value 参数创建 widget，避免「默认值与 Session State 值并存」警告）
    for tid in expected:
        st.session_state.setdefault(f"{CHECK_PREFIX}_{tid}", True)
    checked = _expected_checkbox_list(case_id)
    ontology = get_ontology()

    # 工具行（按钮必须先于勾选框实例化——点击后在当轮 rerun 写 session key
    # 才合法；按钮放网格前，语义 = 「先复位，再看清单」）
    r1, r2 = st.columns([3, 1])
    with r1:
        st.caption(
            f"已勾选 {len(checked)}/{len(expected)} ——勾选只影响「缺失的预期"
            "决策点」：缺失类型由链路实测判定（见下方对齐表与补入步骤）；"
            "已声明且与执行一致的类型不受勾选影响。"
        )
    with r2:
        if st.button("恢复默认（全选）", key="cap_reset", use_container_width=True):
            for tid in expected:
                st.session_state[f"{CHECK_PREFIX}_{tid}"] = True
            st.rerun()

    # 勾选清单（2 列；真实 st.checkbox，session_state 持久化）
    c_left, c_right = st.columns(2)
    for idx, tid in enumerate(expected):
        meta = ontology.get_type(tid) or {}
        cn = meta.get("display", {}).get("cn", tid)
        with (c_left if idx % 2 == 0 else c_right):
            st.checkbox(
                f"{cn} · {tid}",
                key=f"{CHECK_PREFIX}_{tid}",
                help="预期决策点（缺失即补入参与评分）；取消勾选 = 从预期清单"
                "移除该类型（跳过不再可见）",
            )

    chain = _chain_cached(case_id, checked)
    bench = chain["benchmark"]
    state = chain["state"]
    stats = chain["stats"]
    added = chain["added"]
    ok, mismatches = capture_chain.chain_matches_benchmark(chain, bench)
    doublet_checked = "doublet_detection" in checked

    # 链路步骤条（中间产物）
    st.markdown(
        '<div class="ba-chain">'
        f'<div class="ba-chain-step"><b>① M1 声明重建</b>：verdicts jsonl → '
        f'{chain["n_m1"]} 条（verdict_id 去重取末条，含 skip_doublet）</div>'
        f'<div class="ba-chain-step"><b>② M3 解析</b>：executed.py 副本 → '
        f'{chain["m3_n_candidates"]} 候选'
        f'（未定 {chain["m3_n_uncertain"]}），双联体零执行证据</div>'
        f'<div class="ba-chain-step"><b>③ 交叉验证</b>：一致 '
        f'{stats["consistent"]} / 虚报 {stats["false_positive"]}'
        f'（skip_doublet 撤销）/ 漏报 {stats["false_negative"]} / '
        f'未验证 {stats["unverified"]} / 补入 {stats["expected_added"]}</div>'
        + (
            '<div class="ba-chain-step ba-chain-step-highlight"><b>④ 预期决策点'
            f'缺失，已补入</b>：{_esc(added[0]["decision_type"])} / '
            f'{_esc(added[0]["choice"])}（provenance = expected，该做没做，'
            "B7 未豁免）→ 参与评分</div>"
            if added else
            '<div class="ba-chain-step"><b>④ 预期决策点补入</b>：本次无补入'
            "（勾选清单不覆盖缺失类型）——静默跳过不可见</div>"
        )
        + f'<div class="ba-chain-step"><b>⑤ 补入后评分</b>：final '
        f'{chain["final_n"]} 决策 → <b>{state["trajectory_score"]:.1f} · '
        f"{_esc(state['eval_verdict'])}</b></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # 补入过程卡（中间产物：choice 取 Agent 声明，context = M1 事实）
    if added:
        ctx = added[0].get("context") or {}
        ctx_chips = "".join(
            f'<span class="ba-ctx-chip ba-mono">{_esc(k)}={_esc(v)}</span>'
            for k, v in sorted(ctx.items())
        )
        st.markdown(
            '<div class="ba-card ba-added-card">'
            '<div class="ba-added-title">补入决策（provenance = expected · '
            "verdict final）</div>"
            f'<div class="ba-added-row"><span class="ba-added-type ba-mono">'
            f'{_esc(added[0]["decision_type"])}</span>'
            f'<span class="ba-flow-choice ba-mono">{_esc(added[0]["choice"])}</span>'
            '<span class="ba-inst">choice 取 Agent 已撤销声明（不伪造）；'
            "无声明 → not_performed</span></div>"
            f'<div class="ba-added-ctx">{ctx_chips}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="ba-guide">无补入：勾选清单不覆盖缺失类型 → 跳过'
            "不可见（这正是机制演示——预期清单外，跳过不可见）。</div>",
            unsafe_allow_html=True,
        )

    # 断言徽章（分数卡已于 2026-09-21 按 §4.3 移至本组末位）
    if chain.get("had_error"):
        st.error(f"链路审计异常：{state.get('error')}")
    if ok:
        st.markdown(
            '<div class="ba-callout ba-callout-ok">与断言基准一致 ✓ '
            f'（windowL_10X_B_expected.json：{bench["trajectory_score"]:.1f} · '
            f"{_esc(bench['eval_verdict'])} · {bench['n_decisions']} 决策；"
            "provenance.source = windowL_10X_B_expected.json）</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="ba-callout ba-callout-err">与断言基准不一致 ✗ '
            "（预期清单被修改）："
            + "；".join(_esc(m) for m in mismatches)
            + "——取消勾选即复现「跳过不可见」场景；恢复全选回到 63.7 · blocked。</div>",
            unsafe_allow_html=True,
        )
    for issue in state.get("critical_issues") or []:
        st.markdown(
            f'<div class="ba-issue-line">⚠ {_esc(str(issue))}</div>',
            unsafe_allow_html=True,
        )
    if not doublet_checked:
        a_entry = next(
            (e for e in data_index.golden_summary()["entries"]
             if e["id"] == "windowL_10X_A"), None
        )
        a_score = f"{a_entry['trajectory_score']:.1f}" if a_entry else "?"
        st.markdown(
            f'<div class="ba-callout ba-callout-note">取消勾选 doublet_detection '
            f"→ {state['trajectory_score']:.1f} · {_esc(state['eval_verdict'])}——"
            f"静默跳过在预期清单外不可见；恰与黄金对照 A（10X，双联体真实执行）"
            f"同分 {a_score}。勾选回来 → 补入 → 63.7 · blocked。</div>",
            unsafe_allow_html=True,
        )
    st.caption(
        "与工坊页「expected_types 现象演示」共用同一链路（capture_chain 单一"
        "事实源）——现象层点按钮看结果，本页勾选清单看机制；两处数字必须一致。"
    )
    # 合规约束：聚合分数不得置于结论位（表格/组内均以渲染序首个结论性元素为准），
    # 故移至本组其余元素之后（参数未改）。
    report = state.get("report") or {}
    snapshot = report.get("snapshot") or current_snapshot().as_dict()
    result_view.render_score_header(
        state, snapshot, chain["generated_at"],
        source="63.7 复现 · 实时重算（demo/data 副本）",
    )


# ── §4 declared 注入（高级折叠区 + 工具提示）──────────────────────────

def _render_declared_section() -> None:
    with st.expander("高级：declared 注入（评测者 / 数据事实声明）", expanded=False):
        st.markdown(
            "M3 解析的上下文有三**级可信源**：**调用参数 > 数据元数据 > "
            "declared**——declared = 评测者/数据事实声明（运行宪法/评测配置"
            "注入的键值，如数据集平台），**与 Agent 自证（M1）严格区分**"
            "（纪律：Agent 上报的键永远不进 declared）。"
        )
        st.code(
            'declared = {"sequencing": "10X_scRNA_seq"}',
            language="python",
        )
        st.markdown(
            "本链路中 declared 的两处作用（工具提示悬停查看）："
        )
        st.markdown(
            '<ul class="ba-decl-list">'
            "<li><b>平台解析</b>：<span class='ba-mono'>sequencing</span> → "
            "平台键 <span class='ba-mono'>scrna_10x</span> → "
            "expected_types.yaml 的 11 决策清单（无平台事实时保守默认 10X 清单，"
            "平台查证纪律）</li>"
            "<li><b>补入上下文</b>：补入决策的 context 优先取 M1 声明事实"
            "（Agent 自己声明的 n_cells / n_patients 等，不伪造）；无声明时才"
            "用 declared</li>"
            "</ul>",
            unsafe_allow_html=True,
        )
        st.caption(
            "普通观众无需细看本节；想深入机制的人展开即得。"
        )


# ── 主渲染 ───────────────────────────────────────────────────────────

def render() -> None:
    st.markdown('<h1 class="ba-page-title">采集演示</h1>', unsafe_allow_html=True)
    st.markdown(narrative.lead_html("capture"), unsafe_allow_html=True)

    # 案例自选（本页自治：工坊页 = 结果层，本页 = 机制层；不做跨页联动）
    case_id = components.case_selectbox()
    st.markdown(narrative.capture_case_card_html(case_id), unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-page-sub">所有中间产物（对齐表 / verdict 流转 / 补入过程）'
        "由 demo/data 副本实时重算，零硬编码。</div>",
        unsafe_allow_html=True,
    )
    st.markdown(narrative.term_html("expected"), unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-callout ba-callout-note">与审计工坊分工：<b>工坊页 = '
        "现象</b>（点按钮看结果）；<b>本页 = 机制</b>（勾选清单实时重算、看"
        "中间产物）——两处数字一致（验收会独立重算核对）。</div>",
        unsafe_allow_html=True,
    )

    chain = _chain_cached(case_id, _expected_checkbox_list(case_id))
    # 过渡纪律：任何「选择 → 结果」都要有过程——
    #   案例切换（不同链路）→ 5 步 × 0.9s；勾选变化（同一链路重算）→ 3 步 × 0.6s。
    # 依据 (case_id, 勾选清单) 签名判定变化；首次进入按案例切换处理。
    sig = (case_id, tuple(_expected_checkbox_list(case_id)))
    prev_sig = st.session_state.get(CAPTURE_ANIM_CASE_KEY)
    if prev_sig != sig:
        st.session_state[CAPTURE_ANIM_CASE_KEY] = sig
        case_changed = prev_sig is None or prev_sig[0] != case_id
        if case_changed:
            steps = _capture_animation_steps(case_id, chain)
            components.process_animation(
                f"采集链路重算（{len(steps)} 步）",
                steps,
                step_seconds=CAPTURE_ANIM_STEP_SECONDS,
            )
        else:
            steps = _recheck_animation_steps(case_id, chain)
            components.process_animation(
                f"预期清单变更重算（{len(steps)} 步）",
                steps,
                step_seconds=CAPTURE_RECHECK_STEP_SECONDS,
            )
    _render_alignment_table(chain)
    _render_verdict_flow(_verdict_flows(case_id), case_id)
    _render_expected_interaction(case_id)
    _render_declared_section()


if __name__ == "__main__":
    render()
