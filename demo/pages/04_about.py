"""关于页。

设计依据：深色审计台设计规范 §3.5（必改项）+ §4（旧痛点 × 新手段
对照表）+ 验收 §12.1。

内容：
  项目一句话 + 三价值层（lint/benchmark/reward）白话 + 工程数字（读
  engineering_summary + current_snapshot 三元组）+ 路线图（已做八个阶段 /
  排期项）+ MCP 说明（文字 + 代码示例，不作演示页）+ takeaway 三句话
  + 旧痛点 × 新手段对照表。

外围层纪律：只调 bioaudit.api + capture 公共类 + demo/data，零评分路径改动。
"""
from __future__ import annotations

import data_index
import narrative
import result_view
import streamlit as st

from bioaudit.report import current_snapshot

_esc = result_view.esc

#: 路线图 · 已做阶段（八个阶段，按开发顺序列出）
DONE_WINDOWS: tuple[tuple[str, str], ...] = (
    ("真实评测", "declared 注入 + 规则平台键放宽 + 重评出分"),
    ("文档站", "GitHub Pages + site-design §6.2 数字口径纪律"),
    ("黄金对照 · Smart-seq2", "三份黄金报告（A/B/C 口径分列）"),
    ("规则质量", "重评 + 规则审查"),
    ("评分正确性", "immune scRNA 规则落地（L-1 清零）+ scrna_r0 锚定"),
    ("黄金对照 · 10X", "真实短评测：双平台决策集差异实证 + 真实 LLM 短任务如实呈现"),
    ("采集完整性", "expected_types 闭环（静默跳过被补入 → blocked 走采集链路）"),
    ("演示台重建", "骨架 → 审计工坊 → 采集演示 → 评测与关于 → 打磨发布"),
)

#: 路线图 · 排期项（如实声明）
ROADMAP_OPEN: tuple[str, ...] = (
    "L3/L4 结论级审计（未实现，规则覆盖缺口留待规则库按本体 backlog 生长）",
    "多 Agent 对比评测（当前真实运行 n=1，样本不足不做统计推断）",
    "真实 h5ad 在线分析（大文件不进 demo，本地投屏演示为主）",
    "reward RLHF 校准（experimental_uncalibrated → 生产信号前必经）",
    "PRM 过程奖励模型（接口已预留，占位权重 1.0 诚实标注 C≡A）",
    "Docker / 线上部署（需装 Noto CJK 字体防中文豆腐块，可选评估项）",
)

#: takeaway 三句话（给 ③ 类路人观众，设计 §2 观众画像）
TAKEAWAY: tuple[str, str, str] = (
    "方法学错误会被逐条标出来——不是给个总分，而是每一步决策的等级与依据。",
    "每条结论都可追溯：规则、文献证据、三元组快照，同一份分析重跑结果一致。",
    "系统自己也被持续评测：60 任务 benchmark + 双平台黄金对照 + 真实运行档案，"
    "分数不是自说自话。",
)

#: 旧痛点 × 新手段对照表（设计 §4 视觉设计节）
PAIN_VS_FIX: tuple[tuple[str, str], ...] = (
    ("白底平铺、信息无层级", "深色分层审计台：大数字 + verdict 色点 + 快照徽章"),
    ("默认控件、操作不直观", "定制组件：范式级联 + 轨迹多选对比 + Split Button"),
    ("一条条轨迹跑、看不出对比", "最多 3 条并排对比，决策行按本体对齐（错位即信息）"),
    ("分数无来源、不可信", "每页结果带快照徽章（engine/ruleset/ontology + 来源）"),
    ("数字口径混乱", "site-design §6.2 口径纪律：平台 × 版本 × 出处分列，禁止混写"),
)


def _value_layers() -> None:
    """三价值层（lint / benchmark / reward）白话——顺序叙事，非等宽卡片。"""
    st.markdown('<div class="ba-section-title">三个价值层</div>',
                unsafe_allow_html=True)
    layers = (
        (
            "01",
            "lint 层 · 审得准",
            "像代码 linter 逐行检查一样，把「分析怎么做的」拆成一条条方法学决策"
            "（QC 怎么过滤、双联体有没有检测、归一化选了什么、多重检验校没校正…），"
            "逐条对照规则库给出等级（L3 正确 → L0 危险）与文献依据。"
            "不是给个总分了事——每一步都有说法，可展开、可追溯。",
        ),
        (
            "02",
            "benchmark 层 · 信得过",
            "60 条带人工标注的任务集 + 双平台黄金对照 + 真实运行档案组成回归底座："
            "每次引擎/规则改动先重放，golden 0 差异 + pytest 全绿才允许合入——"
            "修好一个不弄坏十个，数字有出处、口径分列。",
        ),
        (
            "03",
            "reward 层 · 可训练",
            "把等级映射成奖励信号（L4 1.00 → L0 0.00，L-1 mask），给未来的分析 "
            "Agent 当强化学习信号。当前诚实标注 experimental_uncalibrated——"
            "未做 RLHF 校准，不用于生产决策。",
        ),
    )
    for num, title, desc in layers:
        st.markdown(
            '<div class="ba-layer-row">'
            f'<div class="ba-layer-num">{_esc(num)}</div>'
            "<div>"
            f'<div class="ba-layer-title">{_esc(title)}</div>'
            f'<div class="ba-layer-desc">{_esc(desc)}</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )


def _engineering_numbers() -> None:
    """工程数字（读 engineering_summary 提炼摘要 + current_snapshot 三元组）。"""
    eng = data_index.engineering_summary()
    snap = current_snapshot()
    ci_value = "+".join(str(v) for v in eng.get("ci_matrix_versions", [])) or "双矩阵"
    st.markdown('<div class="ba-section-title">工程数字</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-stat-grid">'
        f'<div class="ba-stat-card"><div class="ba-stat-value">{eng["n_tests"]}</div>'
        '<div class="ba-stat-label">pytest 测试</div>'
        '<div class="ba-stat-sub">pytest --collect-only 实测（与实跑同源）</div></div>'
        f'<div class="ba-stat-card"><div class="ba-stat-value">{_esc(ci_value)}</div>'
        '<div class="ba-stat-label">CI 双矩阵</div>'
        f'<div class="ba-stat-sub">{_esc(eng["ci_matrix"])}</div></div>'
        f'<div class="ba-stat-card"><div class="ba-stat-value">'
        f'{eng["golden_diff"]} 差异</div>'
        '<div class="ba-stat-label">golden 重放</div>'
        f'<div class="ba-stat-sub">{_esc(eng["golden"])}</div></div>'
        '<div class="ba-stat-card"><div class="ba-stat-value ba-stat-value-sm">'
        f'{_esc(snap.engine_version)} · {_esc(snap.ruleset_version)} · '
        f'{_esc(snap.ontology_version)}</div>'
        '<div class="ba-stat-label">三元组快照</div>'
        '<div class="ba-stat-sub">engine · ruleset · ontology（每页结果带）</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ba-callout ba-callout-note">{_esc(eng["note"])}</div>',
        unsafe_allow_html=True,
    )


def _roadmap() -> None:
    """路线图：已做阶段 / 排期项（如实）。"""
    st.markdown('<div class="ba-section-title">路线图</div>',
                unsafe_allow_html=True)
    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown(
            '<div class="ba-card">'
            '<div class="ba-result-title">已做 · 八个阶段</div>'
            '<ul class="ba-road-list">'
            + "".join(
                f"<li><b>{_esc(w)}</b> · {_esc(d)}</li>" for w, d in DONE_WINDOWS
            )
            + "</ul></div>",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            '<div class="ba-card">'
            '<div class="ba-result-title">排期项（如实声明）</div>'
            '<ul class="ba-road-list">'
            + "".join(f"<li>{_esc(item)}</li>" for item in ROADMAP_OPEN)
            + "</ul></div>",
            unsafe_allow_html=True,
        )


def _mcp() -> None:
    """MCP 说明：文字 + 代码示例（不作演示页，设计 §3.5）。"""
    st.markdown('<div class="ba-section-title">MCP 接入（给 AI Agent 的工具链）</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-card">'
        '<div class="ba-layer-desc">Bio-Audit 以 <b>MCP（Model Context Protocol）'
        "</b>服务器暴露审计能力（bio-audit-mcp v1.0.0，JSON-RPC）：AI Agent 可"
        "直接调用 <span class=\"ba-mono\">audit_decision / audit_trajectory / "
        "report</span> 三个工具，把方法学审计嵌入自己的工具链——审计结论与 demo "
        "同源同口径（契约：docs/mcp-contract.md）。</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.code(
        """{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":"2024-11-05","capabilities":{},
           "clientInfo":{"name":"my-agent","version":"1.0"}}}

{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"audit_trajectory",
           "arguments":{"act":"scrna",
                        "trajectory":{"version":2,"decisions":[
                          {"decision_type":"qc_filtering",
                           "choice":"MAD5_adaptive_threshold",
                           "evidence_citations":[]}
                        ]}}}}""",
        language="json",
    )
    st.caption(
        "完整契约与端到端示例见 docs/mcp-contract.md；本演示不提供 MCP 交互页。"
    )


def _takeaway() -> None:
    st.markdown('<div class="ba-section-title">三句话带走</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-card ba-takeaway">'
        + "".join(
            '<div class="ba-takeaway-line">'
            f'<span class="ba-takeaway-num">{i + 1}</span>'
            f"<span>{_esc(t)}</span></div>"
            for i, t in enumerate(TAKEAWAY)
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _pain_vs_fix() -> None:
    st.markdown('<div class="ba-section-title">旧痛点 × 新手段</div>',
                unsafe_allow_html=True)
    rows = "".join(
        "<tr>"
        f"<td>{_esc(old)}</td>"
        f"<td>{_esc(new)}</td>"
        "</tr>"
        for old, new in PAIN_VS_FIX
    )
    st.markdown(
        '<div class="ba-cmp-wrap"><table class="ba-cmp ba-cmp-narrow">'
        "<thead><tr><th>旧 demo 痛点</th><th>新 demo 手段</th></tr></thead>"
        "<tbody>" + rows + "</tbody></table></div>",
        unsafe_allow_html=True,
    )
    st.caption(
    "对照表出处：深色审计台设计规范 §4（写入设计说明）。"
    )


def render() -> None:
    st.markdown('<h1 class="ba-page-title">关于</h1>', unsafe_allow_html=True)
    st.markdown(narrative.lead_html("about"), unsafe_allow_html=True)
    st.markdown(
        '<div class="ba-page-sub">Bio-Audit 把单细胞分析方法学审计自动化：'
        "分析管线被拆成一条条决策，逐条对照可执行规则给出等级与依据——"
        "分数可复现、结论可追溯、证据可展开。</div>",
        unsafe_allow_html=True,
    )
    _value_layers()
    _engineering_numbers()
    _roadmap()
    _mcp()
    _takeaway()
    _pain_vs_fix()
    st.markdown(
        "文档站：[https://tubo2333.github.io/bio-audit/]"
        "(https://tubo2333.github.io/bio-audit/)　·　仓库："
        "[https://github.com/Tubo2333/bio-audit]"
        "(https://github.com/Tubo2333/bio-audit)　·　快速开始："
        "`docs/quickstart.md`",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render()
