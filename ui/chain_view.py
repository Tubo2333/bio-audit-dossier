"""链条总览（chain view）：把五个决策点摆成 1→5 的流水线主线。

为什么有它
----------
五个决策点数据上永远是按流水线顺序存的（filtering → normalization →
differential_method → multiple_testing_correction → significance_threshold，
见 `DP-AUDIT-ENTRY-DECISION.md` §五），但检查页与交付文档都是「五个独立卡片」。
人读一份分析是**按流程读**的：第一步做了什么、审了什么、结果是什么，第二步……
缺这条主线，判断就显得「突然掏出来一个，又掏出来一个」。

本模块是**两处渲染器共用的单一事实源**（检查页与交付文档都调用它），
保证「链条」在两个形态里长一模一样。

**它合成总评吗？不。** 每个步骤只呈现**既有的逐点判定**（auditable / conflicted /
scientifically_limited / not_auditable / integrity_failed），末尾状态行只是
**给既有判定数数**，并显式声明「这不是总评」。守 C-02「不可标量化」与
「不出总分」的边界。表下另有一小段是 **DP-COHERENCE 的派生视图**
（`cross_step_coherence`）：4 对相邻衔接 + 3 条全局自洽，三态标签 + 一句话理由，
来自报告自带数据，**不改动任何逐点判定、不构成总评、不触发停止条件**。

**它新增判定词吗？不。** 判定词全部取自报告自带的五档（`_JUDGMENT_ZH` 只是翻译）；
连贯三态（coherent / needs_review / incoherent 及中文对照）同样取自已锁定的
报告 `tokens`，本模块不新增第三个来源。
"""

from __future__ import annotations

from typing import Any

# 五步的流水线顺序 = 报告数据本身的顺序；这里只给中文名与一句「这一步审什么」
_STEPS: list[tuple[str, str]] = [
    ("filtering", "低表达过滤"),
    ("normalization", "归一化"),
    ("differential_method", "差异分析方法"),
    ("multiple_testing_correction", "多重检验校正"),
    ("significance_threshold", "显著性/效应量阈值"),
]

_JUDGMENT_ZH = {
    "auditable": "可审计",
    "scientifically_limited": "科学上支持不足",
    "conflicted": "冲突未解",
    "not_auditable": "无法评估",
    "integrity_failed": "完整性/权威失败",
    "pending_attempt": "待验证的提交",
}

_GAP_FLAGS = {
    "missing_critical_context": "缺关键上下文",
    "absent_execution_observation": "无执行观测",
    "declared_observed_conflict": "声明与观测冲突",
}


def _esc(value: Any) -> str:
    from html import escape
    return escape(str(value if value is not None else ""), quote=False)


def _step_rows(report: dict[str, Any]) -> list[str]:
    points = {p.get("name"): p for p in report.get("per_point", {}).get("points", [])}
    rows: list[str] = []
    for idx, (key, zh) in enumerate(_STEPS, start=1):
        point = points.get(key)
        if point is None:
            rows.append(f'<tr class="chv-step"><td>{idx}</td><td>{_esc(zh)}</td>'
                        f'<td>（报告中无此步骤）</td><td>—</td></tr>')
            continue
        judgment = point.get("judgment") or "?"
        jz = _JUDGMENT_ZH.get(judgment, judgment)
        method = point.get("declared_method") or "（未声明）"
        evidence = point.get("evidence") or []
        if evidence:
            ev_summary = f"{len(evidence)} 条观测证据"
        else:
            ev_summary = '<span class="chv-none">无观测证据</span>'
        gaps = point.get("evidence_gaps") or []
        gap_txt = ""
        for g in gaps:
            kind = g.get("kind") if isinstance(g, dict) else None
            flag = _GAP_FLAGS.get(kind or "")
            if flag:
                gap_txt += f' <span class="chv-gap">{_esc(flag)}</span>'
        rows.append(
            f'<tr class="chv-step"><td>{idx}</td><td>{_esc(zh)}</td>'
            f'<td><code>{_esc(method)}</code></td>'
            f'<td><span class="chv-judge">{_esc(jz)}</span>{_esc(" ")}{gap_txt}</td>'
            f'<td>{ev_summary}</td></tr>')
    return rows


def _state_line(report: dict[str, Any]) -> str:
    """逐点状态数数（不是总评）。只列既有判定词的计数。"""
    points = report.get("per_point", {}).get("points", [])
    counts: dict[str, int] = {}
    for p in points:
        j = p.get("judgment") or "?"
        counts[j] = counts.get(j, 0) + 1
    if not counts:
        return ""
    parts = []
    for j in ("auditable", "scientifically_limited", "conflicted",
              "not_auditable", "integrity_failed", "pending_attempt"):
        if counts.get(j):
            parts.append(f"{counts[j]} 步「{_JUDGMENT_ZH.get(j, j)}」")
    # 注意：这是给 HTML 片段的文本，不能用 markdown 记号（片段不经页面转换器）
    return ("链条状态（逐点汇总，不是总评）：" + "、".join(parts) + "。")


def _coherence_block(report: dict[str, Any]) -> str:
    """跨步骤科学连贯小段：4 对相邻 + 3 条全局，每行三态标签 + 一句话理由 + 动作。

    判词与理由全部来自报告自带的 ``cross_step_coherence`` 派生视图（token 与
    中文对照以报告里的 ``tokens`` 为准）；本模块不新增任何判定词。报告没带
    该视图时（旧报告/夹具）如实渲染一行「未派生」，不静默省略。
    """
    coh = report.get("cross_step_coherence")
    if not isinstance(coh, dict) or not isinstance(coh.get("items"), list) \
            or not coh["items"]:
        return '<p class="chv-coh-missing">本报告未派生跨步骤科学连贯视图。</p>'
    tokens = coh.get("tokens") or {}
    action_zh = {"request_evidence": "建议补证据", "request_review": "建议复核"}
    rows = []
    for item in coh["items"]:
        state = item.get("state") or "?"
        zh = tokens.get(state, state)
        act = action_zh.get(item.get("action"))
        act_html = f' <span class="chv-coh-act">{_esc(act)}</span>' if act else ''
        rows.append(
            f'<li class="chv-coh-row"><span class="chv-coh-tag chv-coh-{_esc(state)}">'
            f'{_esc(zh)}</span><span class="chv-coh-label">{_esc(item.get("label") or "")}</span>'
            f'<span class="chv-coh-reason">{_esc(item.get("reason") or "")}</span>{act_html}</li>')
    token_note = "、".join(f"{_esc(k)}＝{_esc(v)}" for k, v in tokens.items())
    note = _esc(coh.get("note") or "")
    return (
        '<div class="chv-coh">'
        f'<p class="chv-coh-head">跨步骤科学连贯（{token_note}）</p>'
        f'<ul class="chv-coh-list">{"".join(rows)}</ul>'
        + (f'<p class="chv-coh-note">{note}</p>' if note else "")
        + '</div>'
    )


def chain_overview(report: dict[str, Any]) -> str:
    rows = "\n".join(_step_rows(report))
    state = _state_line(report)
    coherence = _coherence_block(report)
    return (
        '<div class="chv">'
        f'<table class="chv-table">'
        '<thead><tr><th>#</th><th>这一步审什么</th><th>声明的做法</th>'
        '<th>判定</th><th>证据链</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        + (f'<p class="chv-state">{_esc(state)}</p>' if state else "")
        + coherence
        + '<p class="chv-note">每一行彼此独立；「链条状态」只是逐点汇总，'
        '不构成总评，不产生任何总分。</p>'
        '</div>'
    )
