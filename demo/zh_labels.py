"""演示层中文标签（zh_labels）——**仅显示层映射**，不改数据、不改评分路径。

背景：案例选择与结果表原先直接显示英文 slug（如 ``scrna_correct · 85.0 pass``），
对中文观众不友好；本模块提供中文名 + verdict 中文 + 平台大写规范的中文映射，
供 pages/*.py 渲染时调用。

专业名词规范（大写）：scRNA / DEG / Pan-Cancer / Smart-seq2 / 10X。
"""
from __future__ import annotations

# ── 范式（大写规范 + 中文注释）────────────────────────────────
PARADIGM_ZH: dict[str, str] = {
    "deg": "DEG（差异表达）",
    "pan": "Pan-Cancer（泛癌）",
    "scrna": "scRNA（单细胞）",
}

# ── verdict 中文（与 result_view.VERDICT_META 的描述同源）──────
VERDICT_ZH: dict[str, str] = {
    "pass": "通过",
    "needs_correction": "需修正",
    "blocked": "阻断",
    "error": "错误",
    "provisional": "暂定",
    "final": "终定",
    "revoked": "已撤销",
}

#: 评分口径的一句话中文解释（选择区图例 / 悬停说明）
VERDICT_LEGEND: str = (
    "评分说明：通过 = 所有决策符合科学方法学；需修正 = 存在风险级（L1）决策；"
    "阻断 = 检测到致命级（L0）错误，分析结果不可信。"
)

# ── 平台中文（大写规范）─────────────────────────────────────
PLATFORM_ZH: dict[str, str] = {
    "smartseq2": "Smart-seq2",
    "10X_scRNA_seq": "10X",
    "10x": "10X",
    "10X": "10X",
}

# ── 经典轨迹中文名（20 条，来自 demo/data/trajectories_index.json）──
CASE_ZH: dict[str, str] = {
    "deg_correct": "DEG · 正确执行（教科书式）",
    "deg_edge_n2": "DEG · 边缘案例：样本量仅 n=2",
    "deg_edge_nofilter": "DEG · 边缘案例：未做基因过滤",
    "deg_error": "DEG · 错误执行",
    "pan_correct": "Pan-Cancer · 正确执行（教科书式）",
    "pan_edge_claim": "Pan-Cancer · 边缘案例：声明未兑现",
    "pan_edge_consistency": "Pan-Cancer · 边缘案例：一致性风险",
    "pan_edge_epv": "Pan-Cancer · 边缘案例：事件数不足（EPV）",
    "pan_edge_purity": "Pan-Cancer · 边缘案例：肿瘤纯度问题",
    "pan_error": "Pan-Cancer · 错误执行",
    "scrna_correct": "scRNA · 正确执行（Smart-seq2 教科书式）",
    "scrna_crc_correct": "scRNA · 结直肠癌（10X）正确执行",
    "scrna_crc_error": "scRNA · 结直肠癌（10X）跳过双联体检测",
    "scrna_edge_default": "scRNA · 边缘案例：默认参数",
    "scrna_edge_nodoublet": "scRNA · 边缘案例：跳过双联体检测",
    "scrna_edge_singleanno": "scRNA · 边缘案例：单一注释方法",
    "scrna_error": "scRNA · 错误执行（跳过关键步骤）",
    "scrna_melanoma_cellvoyager": "scRNA · 黑色素瘤（CellVoyager 真实运行）",
    "scrna_melanoma_correct": "scRNA · 黑色素瘤（教科书式）",
    "scrna_nsclc_correct": "scRNA · 非小细胞肺癌（教科书式）",
}

# ── 黄金对照中文名（5 条，来自 demo/data/golden_summary.json）──
GOLDEN_ZH: dict[str, str] = {
    "windowI_A": "黄金对照 A（Smart-seq2 教科书式）",
    "windowI_B": "黄金对照 B（Smart-seq2 逻辑断裂：细胞级 DEG）",
    "windowI_C": "黄金对照 C（Smart-seq2 微妙错误：QC 硬阈值）",
    "windowL_10X_A": "黄金对照 A（10X 教科书式，含双联体检测）",
    "windowL_10X_B_expected": "黄金对照 B（10X 静默跳过双联体，expected 补入）",
}


# ── 辅助函数 ────────────────────────────────────────────────

def verdict_zh(verdict: object) -> str:
    """verdict 英文 → 中文（未知值原样返回，不猜）。"""
    s = str(verdict)
    return VERDICT_ZH.get(s, s)


def platform_zh(platform: object) -> str:
    """平台名 → 大写规范中文/通用写法。"""
    s = str(platform)
    return PLATFORM_ZH.get(s, s)


def name_zh(case_id: object) -> str:
    """案例 id → 中文名（经典轨迹或黄金对照；未知返回原 id）。"""
    s = str(case_id)
    return CASE_ZH.get(s) or GOLDEN_ZH.get(s) or s


def case_label(
    case_id: object,
    score: object,
    verdict: object,
    n_decisions: object | None = None,
    platform: object | None = None,
) -> str:
    """统一案例显示串：``中文名 · 分数 · verdict 中文（平台）· n 决策``。

    例：``scRNA · 正确执行（Smart-seq2 教科书式） · 85.0 · 通过 · 12 决策``
    """
    label = f"{name_zh(case_id)} · {float(score):.1f} · {verdict_zh(verdict)}"
    if platform is not None:
        label += f"（{platform_zh(platform)}）"
    if n_decisions is not None:
        label += f" · {n_decisions} 决策"
    return label
