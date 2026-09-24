"""演示叙事层（narrative）——四页的导语、页间过渡与名词释义。

设计意图（2026-09-20）：讲稿（demo/docs/demo-script.md）里有完整的讲解逻辑，
但页面本身此前没有把这条逻辑表达给观众——观众只看到名词与数字，不知道
"这一页在处理什么、看完往哪走"。本模块把叙事显式化：

- ``LEAD``：页面导语——位于页标题下方，一句话交代本页的问题与结论；
- ``NEXT``：页间过渡——位于页尾，指明下一步看什么（四页串成一条路径）；
- ``TERMS``：名词释义——专业名词首次出现处的一行解释（不打断阅读）。

文风约定：专业、克制、准确。使用领域术语但句子保持可读；不使用口语化
转述，也不使用机器腔（"本页面用于…"）。数字以页面快照徽章为准。
"""
from __future__ import annotations

# ── 页面导语（页标题下方）────────────────────────────────────
LEAD: dict[str, str] = {
    "workshop": (
        "同一数据集上的两条方法学路径：对照组遵循文献共识，实验组跳过关键决策点。"
        "审计判定 <span class='ba-lead-key'>85.0（通过）</span> 与 "
        "<span class='ba-lead-key'>40.0（阻断）</span>。"
        "两者输出报告的完成度相当——分差来自每一步方法学决策的科学性，而非结果本身。"
    ),
    "capture": (
        "Agent 未声明跳过双联体检测。审计从执行产物中还原该决策点、纳入判定，"
        "并给出<span class='ba-lead-key'>致命级（L0）</span>。"
        "本页呈现采集链路——声明的、执行的、预期的三类事实如何对齐。"
    ),
    "benchmark": (
        "审计系统自身处于被评测状态：60 条真值标注任务集、双标注一致性 "
        "<span class='ba-lead-key'>κ=0.8336</span>、真实运行成本核算，"
        "以及审计分数与真实分析质量的排序一致性验证 "
        "<span class='ba-lead-key'>ρ=0.9747</span>。"
        "任何对外引用的数字均可回溯至具体报告。"
    ),
    "about": (
        "三层能力：<span class='ba-lead-key'>运行时审计（lint）</span>、"
        "<span class='ba-lead-key'>可重复评测（benchmark）</span>、"
        "<span class='ba-lead-key'>训练信号（reward）</span>；"
        "附工程指标与 MCP 接入契约。"
    ),
}

# ── 页间过渡（页尾）──────────────────────────────────────────
NEXT: dict[str, str] = {
    "workshop": (
        "<span class='ba-next-key'>下一步 · ② 采集演示 ｜ 决策点还原</span>："
        "工坊给出的是结论；采集演示拆开结论的来源——该页可自选案例（5 条黄金对照），"
        "每条演示不同的采集机制：声明与执行全一致 / 虚报撤销 / 预期决策点补入 / "
        "方法学风险。"
    ),
    "capture": (
        "<span class='ba-next-key'>下一步 · ③ 评测与奖励 ｜ 系统可信度</span>："
        "审计本身如何被验证。"
    ),
    "benchmark": (
        "<span class='ba-next-key'>下一步 · ④ 关于 ｜ 能力与接入</span>："
        "三层能力与 MCP 契约。"
    ),
    "about": (
        "<span class='ba-next-key'>演示结束</span>：可返回 "
        "① 审计工坊 重放默认路径（含 2 次点击的默认态恢复）。"
    ),
}

# ── 案例交代卡（采集页专用 · 数据驱动）─────────────────────
# 采集页案例**可自选**（case_registry），因此卡片不再为单一案例手写——
# 由注册表元数据（label / story / declared / 口径）+ verdict 中文映射生成：
# 换案例自动换文案，杜绝"文案写死、数据已换"的漂移。

_WORKSHOP_RELATION = (
    "工坊页给出<b>结论</b>（现象层）；本页拆开<b>结论的来源</b>（机制层）。"
    "两页各自自治：本页案例可自选——每条案例演示的采集机制不同。"
)


def capture_case_card_html(case_id: str) -> str:
    """采集案例交代卡（按案例动态生成）。未知案例返回空串。"""
    try:
        import case_registry
        import zh_labels

        case = case_registry.get_case(case_id)
    except Exception:  # pragma: no cover - 注册表缺失时降级为空卡片
        return ""

    extra = case.extra or {}
    facts = []
    if extra.get("dataset"):
        facts.append(str(extra["dataset"]))
    if extra.get("window"):
        facts.append(str(extra["window"]))
    src_bits = " · ".join(facts)

    caliber = (
        f"{case.expected_score} · {zh_labels.verdict_zh(case.expected_verdict)}"
    )
    if case.source_report_score is not None:
        caliber += f"（源报告口径 {case.source_report_score}）"

    # 行结构固定（每案例同 5 行）：长口径说明独立成「口径注」行（小字），
    # 不内联进「核验口径」——否则长短不一会造成卡片折行数不一致。
    rows = [
        ("案例", f"{case.label}" + (f" · {src_bits}" if src_bits else "")),
        ("机制看点", case.story or "—"),
        # 核验口径 = 短文本（分数 · verdict [+ 源报告口径]）→ 用 nowrap 类，
        # 避免长一点的案例（如带"源报告口径"注）在这一行折行、破坏卡片行高一致
        ("核验口径", f'<span class="ba-case-val-inline">{caliber}</span>'),
    ]
    if case.caliber_note:
        rows.append(
            ("口径注", f'<span class="ba-case-note">{case.caliber_note}</span>')
        )
    rows.append(("与工坊页的关系", _WORKSHOP_RELATION))

    body = "".join(
        f'<div class="ba-case-row">'
        f'<span class="ba-case-key">{key}</span>'
        f'<span class="ba-case-val">{val}</span></div>'
        for key, val in rows
    )
    return f'<div class="ba-case-card">{body}</div>'

# ── 名词释义（首次出现处的一行小字）──────────────────────────
TERMS: dict[str, str] = {
    "levels": (
        "<b>判定级别</b>：L3 正确级（符合文献共识）· L2 可接受（常规选择）· "
        "L1 风险级（方法选择值得商榷，结论受影响）· L0 致命级（结论不可信）· "
        "L−1 无法评估（上下文缺失，不做判定）"
    ),
    "verdict": (
        "<b>审计结论</b>：通过 = 各决策均达可判定等级（决策等级的聚合，非科学结论）· "
        "需修正 = 存在风险级决策 · 阻断 = 存在致命级决策，分析结果不可信"
    ),
    "expected": (
        "<b>预期决策点（expected_types）</b>：该平台与范式下标准流程应包含的决策；"
        "缺失即判定「该做未做」，不因未声明而略过"
    ),
    "reliability": (
        "<b>κ</b> = 双标注一致性系数（Cohen's kappa）· "
        "<b>ρ</b> = 审计分数与真实分析质量的排序一致性（Spearman）· "
        "<b>L 分布</b> = 各判定级别的决策计数"
    ),
}


def lead_html(page_id: str) -> str:
    """页面导语 HTML（页标题下方）。未知页返回空串。"""
    text = LEAD.get(page_id)
    return f'<div class="ba-page-lead">{text}</div>' if text else ""


def next_step_html(page_id: str) -> str:
    """页间过渡 HTML（页尾）。未知页返回空串。"""
    text = NEXT.get(page_id)
    return f'<div class="ba-next-step">{text}</div>' if text else ""


def term_html(term_key: str) -> str:
    """名词释义 HTML（小字一行）。未知键返回空串。"""
    text = TERMS.get(term_key)
    return f'<div class="ba-term-note">{text}</div>' if text else ""
