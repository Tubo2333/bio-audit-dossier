"""跨步骤科学连贯判定（DP-COHERENCE）——独立判定模块。

为什么有这个模块
----------------
五个决策点各自的判定回答的是「这一步本身站不站得住」；**没有一处回答**
「第 1 步和第 2 步之间接得上吗、两个声明会不会互相矛盾」。这一条是对
**链条**的判定，不是对任何单点的判定，所以它自成一个模块：
- **不进规则库**（沿用既有裁定「规则不参与判定」：判据就在本文件里，读它不读 ruleset）；
- **不改五点判定**（本模块只读声明，不写账、不改 findings、不触发停止条件）；
- **纯函数**（无 I/O、无时钟、无副作用；同输入两次调用逐字相同）。

判什么（人类逐条拍板，见 ``DP-COHERENCE-CHECKLIST.md``）
-------------------------------------------------------
相邻 4 对：① 过滤→归一化 ② 归一化→差异方法 ③ 差异方法→多重检验 ④ 多重检验→阈值
全局 3 条：1 过滤口径↔阈值口径 2 样本量↔方法敏感性 3 样本规则↔方法假设

三态（**token 定完即锁，不得改名**）
------------------------------------
``coherent`` = 衔接一致 · ``needs_review`` = 需复核 · ``incoherent`` = 衔接矛盾

尺度是**保守**的：拿不准一律 ``needs_review``；只有声明层面**确凿矛盾**的搭配
（例如校正后 p 的阈值配「不做校正」、长度归一化的表达量喂给计数模型、
每组只有 1 个样本）才 ``incoherent``。

它**不产生**总分、不排名、不合成一句总评；三态之间不可加权、不可比较大小。
不连贯只生成既有的 ``request_evidence`` / ``request_review`` 类动作。

输入（全部来自**既有声明**，不新增输入域）
------------------------------------------
``register``：五点结果（含各自的 ``declaration``）——取 ``method`` / ``threshold`` /
``sample_rule``。
``analysis_context``：取 ``comparison`` 与 ``n_samples``（分组样本量，OPEN-1 B1 落点）。

缺失一律 fail-closed：该维度如实标「未知」→ ``needs_review``，不猜、不代填。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# 三态：token 与中文对照（契约；不得改名——见 DP-COHERENCE-INSTRUCTION 第 12 条）
# ---------------------------------------------------------------------------

COHERENCE_TOKENS: tuple[str, ...] = ("coherent", "needs_review", "incoherent")

COHERENCE_ZH: dict[str, str] = {
    "coherent": "衔接一致",
    "needs_review": "需复核",
    "incoherent": "衔接矛盾",
}

#: 三态 → 动作：只用**既有**动作 token，不新增动作域；一致则无动作。
ACTION_FOR_STATE: dict[str, str | None] = {
    "coherent": None,
    "needs_review": "request_review",
    "incoherent": "request_evidence",
}

_POINT_ORDER: tuple[str, ...] = (
    "filtering",
    "normalization",
    "differential_method",
    "multiple_testing_correction",
    "significance_threshold",
)

# ---------------------------------------------------------------------------
# 方法族表（只做「名称 → 口径族」归一，不给分、不排名、不引用任何规则 ID）
# ---------------------------------------------------------------------------

FILTERING_FAMILIES: dict[str, str] = {
    "low_count_filter": "count_filter",
    "filterbyexpr": "count_filter",
    "cpm_filter_min_samples": "count_filter",
    "counts_per_million_min": "count_filter",
    "mean_expression_filter": "count_filter",
    "median_expression_filter": "count_filter",
    "no_filtering": "no_filter",
    "none": "no_filter",
}

NORMALIZATION_FAMILIES: dict[str, str] = {
    "tmm": "count_preserving",
    "rle": "count_preserving",
    "deseq2_median_of_ratios": "count_preserving",
    "median_of_ratios": "count_preserving",
    "upper_quartile": "count_preserving",
    "cpm_no_log": "count_preserving",
    "cpm": "count_preserving",
    "tpm": "length_normalized",
    "fpkm": "length_normalized",
    "rpkm": "length_normalized",
    "raw_counts_no_normalization": "no_normalization",
    "none": "no_normalization",
}

DIFFERENTIAL_FAMILIES: dict[str, str] = {
    "deseq2": "count_model",
    "edger": "count_model",
    "limma_voom": "count_model",
    "limma_trend": "count_model",
    "wilcoxon_rank_sum": "rank_test",
    "wilcoxon": "rank_test",
    "ttest_equal_variance": "parametric_test",
    "ttest": "parametric_test",
}

CORRECTION_FAMILIES: dict[str, str] = {
    "bh": "bh_family",
    "fdr": "bh_family",
    "benjamini_hochberg": "bh_family",
    "by": "other_correction",
    "benjamini_yekutieli": "other_correction",
    "bonferroni": "other_correction",
    "q_value_storey": "other_correction",
    "storey": "other_correction",
    "no_correction": "no_correction",
    "none": "no_correction",
}

THRESHOLD_FAMILIES: dict[str, str] = {
    "padj_and_logfc": "adjusted_p",
    "padj_0_05": "adjusted_p",
    "padj_0.05": "adjusted_p",
    "padj_0_01": "adjusted_p",
    "padj_0.01": "adjusted_p",
    "raw_p_value": "raw_p",
    "logfc_only": "effect_only",
    "lfc_1": "effect_only",
    "no_threshold": "no_threshold",
    "not_applicable": "no_threshold",
}

#: 计数模型内部默认就施加 BH；秩检验/等方差 t 检验没有内建校正。
_MODELS_WITH_BUILT_IN_BH: frozenset[str] = frozenset({"count_model"})

_COMPARISON_RE = re.compile(r"^(?P<left>.+?)_vs_(?P<right>.+)$")

#: 声明里代表「没声明/不适用」的取值（归一后）。
_UNSET = frozenset({"", "none", "not_applicable", "na", "null"})


def _norm(value: Any) -> str:
    """声明值的归一：去空白、小写、连字符与空格转下划线。"""
    if value is None:
        return ""
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _family(table: Mapping[str, str], declared: Any) -> str | None:
    key = _norm(declared)
    if not key:
        return None
    return table.get(key)


def _declared(register: Mapping[str, Any], point: str, field: str) -> Any:
    """取某点声明里的一个字段；声明缺失（fail-closed 的点）时返回 None。"""
    result = register.get(point) if isinstance(register, Mapping) else None
    declaration = getattr(result, "declaration", None)
    if declaration is None:
        return None
    return getattr(declaration, field, None)


def _group_sizes(value: Any) -> dict[str, int] | None:
    """分组样本量：{组名: 正整数}；形状不对 → None（未知，不猜）。"""
    if not isinstance(value, Mapping) or not value:
        return None
    out: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            size = raw
        elif isinstance(raw, float) and raw.is_integer():
            size = int(raw)
        else:
            return None
        if size <= 0:
            return None
        out[key.strip()] = size
    return out


def _align_groups(comparison: Any, sizes: Mapping[str, int]) -> tuple[bool, str]:
    """比较组两侧的组名是否都能在样本量声明里对上。

    返回 ``(aligned, reason)``；``comparison`` 不是 ``X_vs_Y`` 形式时 aliged=False。
    """
    text = _norm(comparison)
    if not text:
        return False, "比较组未声明，无法把组名与样本量对齐"
    match = _COMPARISON_RE.match(text)
    if match is None:
        return False, "比较组不是「甲_vs_乙」形式，无法自动把组名与样本量对齐"
    keys = {_norm(k) for k in sizes}
    if match.group("left") in keys and match.group("right") in keys:
        return True, ""
    return False, "声明的组名与比较组对不上（两侧组名要与样本量声明一致）"


def _item(item_id: str, kind: str, label: str, state: str, reason: str,
          inputs: Mapping[str, Any]) -> dict[str, Any]:
    if state not in COHERENCE_TOKENS:
        raise ValueError(f"unknown coherence token: {state!r}")
    return {
        "id": item_id,
        "kind": kind,
        "label": label,
        "state": state,
        "reason": reason,
        "action": ACTION_FOR_STATE[state],
        "inputs": {k: v for k, v in inputs.items()},
    }


# ---------------------------------------------------------------------------
# 相邻 4 对
# ---------------------------------------------------------------------------

def _pair_1_filtering_normalization(filtering: Any, normalization: Any) -> tuple[str, str]:
    """① 过滤 → 归一化：两步的「留在矩阵里的量」是不是同一套口径。"""
    f_fam = _family(FILTERING_FAMILIES, filtering)
    n_fam = _family(NORMALIZATION_FAMILIES, normalization)
    if f_fam is None or n_fam is None:
        return "needs_review", "过滤点或归一化点的方法不在本线已知族内：不猜，标记复核。"
    if f_fam == "count_filter" and n_fam == "count_preserving":
        return "coherent", "按计数过滤、接保计数的归一化，两步口径同族。"
    if f_fam == "count_filter" and n_fam == "no_normalization":
        return "coherent", "过滤后保留原始计数，未做归一化与过滤口径不冲突。"
    if f_fam == "no_filter":
        return "coherent", "未做过滤，没有可与归一化冲突的过滤口径。"
    if f_fam == "count_filter" and n_fam == "length_normalized":
        return ("needs_review",
                "过滤按计数、归一化按长度，两步的表达量口径不同族，"
                "需确认后续统计用的是哪一套。")
    return "needs_review", "这两步的组合不在本线已知的相称搭配内：不猜，标记复核。"


def _pair_2_normalization_differential(normalization_method: Any,
                                      differential_method: Any) -> tuple[str, str]:
    """② 归一化 → 差异方法：喂给模型的量是不是模型假定的那种。"""
    n_fam = _family(NORMALIZATION_FAMILIES, normalization_method)
    d_fam = _family(DIFFERENTIAL_FAMILIES, differential_method)
    if n_fam is None or d_fam is None:
        return "needs_review", "归一化点或差异方法点的方法不在本线已知族内：不猜，标记复核。"
    if n_fam == "length_normalized" and d_fam == "count_model":
        return ("incoherent",
                "把长度归一化（TPM/FPKM/RPKM）的表达量喂给计数模型："
                "模型假定的是原始计数，口径矛盾。")
    if n_fam in ("count_preserving", "no_normalization") and d_fam == "count_model":
        return "coherent", "归一化产出的是计数口径的量，与计数模型的假定一致。"
    if n_fam == "length_normalized" and d_fam == "rank_test":
        return "coherent", "秩检验对单调变换不敏感，两步不冲突。"
    if n_fam == "length_normalized" and d_fam == "parametric_test":
        return "needs_review", "长度归一化数据配等方差 t 检验，分布与方差假设是否成立需确认。"
    if n_fam in ("count_preserving", "no_normalization") and d_fam == "parametric_test":
        return "needs_review", "未做对数变换的计数配等方差 t 检验，方差/正态假设可能不相称。"
    return "needs_review", "这两步的组合不在本线已知的相称搭配内：不猜，标记复核。"


def _pair_3_differential_correction(differential_method: Any,
                                    correction_method: Any) -> tuple[str, str]:
    """③ 差异方法 → 多重检验：校正会不会重复施加或被遗漏。"""
    d_fam = _family(DIFFERENTIAL_FAMILIES, differential_method)
    c_fam = _family(CORRECTION_FAMILIES, correction_method)
    if d_fam is None or c_fam is None:
        return "needs_review", "方法或校正声明不在本线已知族内：不猜，标记复核。"
    if c_fam == "no_correction":
        return ("coherent",
                "声明不做外部校正：与模型内建不冲突；「一次校正都没有」属该点自身判定的事，"
                "本线不重复判。")
    if d_fam in _MODELS_WITH_BUILT_IN_BH and c_fam == "bh_family":
        return "coherent", "声明与模型内建程序一致。"
    if d_fam in _MODELS_WITH_BUILT_IN_BH and c_fam == "other_correction":
        return ("needs_review",
                "模型内建 BH，又声明外部做另一种校正：可能重复施加，"
                "需确认校正只做了一次。")
    if d_fam in ("rank_test", "parametric_test"):
        return "coherent", "检验方法不自带校正，校正必须由外部步骤提供，声明齐全。"
    return "needs_review", "这两步的组合不在本线已知的相称搭配内：不猜，标记复核。"


def _pair_4_correction_threshold(correction_method: Any,
                                 threshold_method: Any) -> tuple[str, str]:
    """④ 多重检验 → 阈值：阈值取的是校正后的量还是原始量。"""
    c_fam = _family(CORRECTION_FAMILIES, correction_method)
    t_fam = _family(THRESHOLD_FAMILIES, threshold_method)
    if c_fam is None or t_fam is None:
        return "needs_review", "阈值口径或校正声明不在本线已知族内：不猜，标记复核。"
    if t_fam == "adjusted_p" and c_fam != "no_correction":
        return "coherent", "阈值取校正后 p，校正声明存在，口径配合。"
    if t_fam == "adjusted_p" and c_fam == "no_correction":
        return ("incoherent",
                "阈值口径写的是校正后 p，但校正声明是不做校正："
                "校正后的 p 不存在，口径矛盾。")
    if t_fam == "raw_p" and c_fam == "no_correction":
        return "coherent", "不做校正、按原始 p 取阈值，口径自洽。"
    if t_fam == "raw_p" and c_fam != "no_correction":
        return ("needs_review",
                "做了校正却按原始 p 取阈值：校正可能没被用到阈值上，需确认是否有意。")
    if t_fam == "effect_only":
        return ("needs_review",
                "只按效应量取阈值：校正声明与阈值的配合无法从声明确认。")
    if t_fam == "no_threshold":
        return "needs_review", "未声明阈值口径，无法与校正配合判断。"
    return "needs_review", "这两步的组合不在本线已知的相称搭配内：不猜，标记复核。"


# ---------------------------------------------------------------------------
# 全局 3 条
# ---------------------------------------------------------------------------

def _global_1_filter_scope_vs_threshold(filtering_method: Any, filtering_threshold: Any,
                                        threshold_method: Any) -> tuple[str, str]:
    """全局 1：过滤口径 ↔ 阈值口径。

    声明的局限（写入理由，不假装更强）：报告里没有「过滤后保留了多少」的量化，
    本条只能查口径同族性与「过滤阈值有没有声明」。
    """
    f_fam = _family(FILTERING_FAMILIES, filtering_method)
    t_fam = _family(THRESHOLD_FAMILIES, threshold_method)
    if f_fam is None or t_fam is None:
        return "needs_review", "过滤口径或阈值口径不在本线已知族内：不猜，标记复核。"
    if t_fam == "no_threshold":
        return "needs_review", "未声明阈值口径，过滤与阈值的配合无从判断。"
    if f_fam == "no_filter":
        return "coherent", "未做过滤，无从与阈值口径冲突。"
    if f_fam == "count_filter":
        th = _norm(filtering_threshold)
        if th in _UNSET:
            return ("needs_review",
                    "过滤方法声明了、过滤阈值没声明：无法确认保留量与后续阈值口径是否自洽。")
        if t_fam in ("adjusted_p", "effect_only"):
            return "coherent", "按计数过滤后按显著性/效应量取阈值，两步各自声明了口径。"
        if t_fam == "raw_p":
            return "needs_review", "按计数过滤、阈值只看原始 p：两步口径可能不同族。"
    return "needs_review", "过滤与阈值的组合不在本线已知的相称搭配内：不猜，标记复核。"


def _global_2_group_sizes_vs_method(sizes: dict[str, int] | None,
                                    comparison: Any,
                                    differential_method: Any) -> tuple[str, str]:
    """全局 2：分组样本量 ↔ 方法敏感性（依赖 n_samples）。"""
    if sizes is None:
        return ("needs_review",
                "分组样本量未声明或形状不对：本维度如实标「未知」，不猜、不代填。")
    aligned, why_not = _align_groups(comparison, sizes)
    if not aligned:
        return "needs_review", why_not + "；样本量维度无法确认。"
    d_fam = _family(DIFFERENTIAL_FAMILIES, differential_method)
    if d_fam is None:
        return "needs_review", "差异方法不在本线已知族内：不猜，标记复核。"
    min_n = min(sizes.values())
    if min_n == 1:
        return ("incoherent",
                "每组只有 1 个样本：组内方差或组内秩无从谈起，与组间比较的前提矛盾。")
    if d_fam == "count_model":
        if min_n == 2:
            return ("needs_review",
                    "每组只有 2 个样本，计数模型的离散度估计不稳，与方法假设可能不相称。")
        return "coherent", f"声明的最小分组样本量为 {min_n}，与计数模型的离散度估计相称。"
    if d_fam == "rank_test":
        return "coherent", f"声明的最小分组样本量为 {min_n}，秩检验在每组 ≥2 个样本下可用。"
    return ("needs_review",
            "等方差 t 检验对分布与方差假设敏感，该样本量下假设是否成立需确认。")


def _global_3_sample_rule_vs_method(sample_rule: Any, sizes: dict[str, int] | None,
                                    differential_method: Any) -> tuple[str, str]:
    """全局 3：样本规则 ↔ 方法假设（以及它与分组样本量声明的一致性）。"""
    rule = _norm(sample_rule)
    d_fam = _family(DIFFERENTIAL_FAMILIES, differential_method)
    if rule in _UNSET:
        return "needs_review", "样本规则未声明，无法与方法假设对照。"
    if rule == "no_replication":
        if d_fam is not None:
            return ("incoherent",
                    "声明无重复，却选了需要组内重复的组间比较方法。")
        if sizes is not None and max(sizes.values()) >= 2:
            return ("incoherent",
                    "声明无重复，但样本量声明显示有 ≥2 个样本：两处声明互相矛盾。")
        return "needs_review", "声明无重复，但差异方法不在本线已知族内或样本量未声明：标记复核。"
    if rule in ("at_least_three_samples", "at_least_two_samples"):
        required = 3 if rule == "at_least_three_samples" else 2
        if sizes is None:
            return "needs_review", "样本量未知，样本规则与样本量的内部一致性无法核。"
        min_n = min(sizes.values())
        if min_n < required:
            return ("incoherent",
                    f"声明每组至少 {required} 个，样本量声明里却有组少于 {required}：互相矛盾。")
        if d_fam == "count_model":
            return "coherent", "声明有重复、计数模型要求重复，两者相称。"
        return "needs_review", "声明与样本量不矛盾，但方法假设的相称性无法从现有声明确认。"
    if rule == "paired_by_batch":
        if d_fam == "parametric_test":
            return ("needs_review",
                    "声明配对设计，检验方法却是非配对形式，需确认配对结构是否被建模。")
        if d_fam == "count_model":
            return "needs_review", "配对结构是否进入模型无法从声明确认。"
        return "needs_review", "声明配对设计，但配对结构与方法形式的配合无法从声明确认。"
    return "needs_review", "样本规则的取值不在本线已知族内：不猜，标记复核。"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def derive_cross_step_coherence(register: Mapping[str, Any],
                                analysis_context: Mapping[str, Any] | None = None
                                ) -> dict[str, Any]:
    """从五点声明 + 分析上下文派生出跨步骤科学连贯视图（纯函数）。

    返回 ``{"items": [7 条], "tokens": {...}, "note": ...}``：
    - 4 条 ``kind="pair"``（相邻对）＋ 3 条 ``kind="global"``；
    - 每条 = ``{id, kind, label, state, reason, action, inputs}``；
    - **不产生**总分、排名或任何加权合成；``state`` 只能是锁定的三个 token。

    缺失一律 fail-closed：相关维度如实标「未知」→ ``needs_review``。
    """
    if not isinstance(register, Mapping):
        raise TypeError("register must be a mapping of the five point results")
    context: Mapping[str, Any] = analysis_context if isinstance(analysis_context, Mapping) else {}

    f_method = _declared(register, "filtering", "method")
    f_threshold = _declared(register, "filtering", "threshold")
    n_method = _declared(register, "normalization", "method")
    d_method = _declared(register, "differential_method", "method")
    d_rule = _declared(register, "differential_method", "sample_rule")
    c_method = _declared(register, "multiple_testing_correction", "method")
    t_method = _declared(register, "significance_threshold", "method")

    comparison = context.get("comparison")
    sizes = _group_sizes(context.get("n_samples"))

    specs: list[tuple[str, str, str, tuple[str, str], dict[str, Any]]] = [
        ("pair_1_filtering_to_normalization", "pair", "① 过滤 → 归一化",
         _pair_1_filtering_normalization(f_method, n_method),
         {"filtering_method": f_method, "normalization_method": n_method}),
        ("pair_2_normalization_to_differential", "pair", "② 归一化 → 差异分析方法",
         _pair_2_normalization_differential(n_method, d_method),
         {"normalization_method": n_method, "differential_method": d_method}),
        ("pair_3_differential_to_correction", "pair", "③ 差异分析方法 → 多重检验校正",
         _pair_3_differential_correction(d_method, c_method),
         {"differential_method": d_method, "correction_method": c_method}),
        ("pair_4_correction_to_threshold", "pair", "④ 多重检验校正 → 显著性/效应量阈值",
         _pair_4_correction_threshold(c_method, t_method),
         {"correction_method": c_method, "threshold_method": t_method}),
        ("global_1_filter_scope_vs_threshold_scope", "global",
         "全局 1 · 过滤口径 ↔ 阈值口径",
         _global_1_filter_scope_vs_threshold(f_method, f_threshold, t_method),
         {"filtering_method": f_method, "filtering_threshold": f_threshold,
          "threshold_method": t_method}),
        ("global_2_group_sizes_vs_method_sensitivity", "global",
         "全局 2 · 样本量 ↔ 方法敏感性",
         _global_2_group_sizes_vs_method(sizes, comparison, d_method),
         {"comparison": comparison, "n_samples": dict(sizes) if sizes else None,
          "differential_method": d_method}),
        ("global_3_sample_rule_vs_method_assumptions", "global",
         "全局 3 · 样本规则 ↔ 方法假设",
         _global_3_sample_rule_vs_method(d_rule, sizes, d_method),
         {"differential_sample_rule": d_rule,
          "n_samples": dict(sizes) if sizes else None,
          "differential_method": d_method}),
    ]

    items = [_item(item_id, kind, label, state, reason, inputs)
             for item_id, kind, label, (state, reason), inputs in specs]

    return {
        "items": items,
        "tokens": dict(COHERENCE_ZH),
        "note": (
            "这七条判的是链条上相邻两步之间、以及全局三处声明是否自洽；"
            "它不改动任何逐点判定，不构成总评，不产生分数或排名，"
            "也不触发任何停止条件——不连贯只会生成补证据或复核类动作。"
        ),
    }
