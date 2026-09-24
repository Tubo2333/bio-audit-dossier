"""expected_types 评测配置（窗口 M / M1.1：预期决策点强制检查）。

设计裁决（M1.1，2026-08-16 经项目负责人在线确认）：
- 预期决策点清单放**评测配置**（``data/expected_types.yaml``，per 范式×平台），
  不放引擎硬编码（清单变更走评审，与任务集同门禁风格）；
- 缺失预期决策 → 补入 ``provenance=expected`` 参与评分（该做没做）；
- 豁免（B7/G5 保守原则）：仅显式 ``optional: true`` 且 ``when_not_applicable``
  谓词满足（谓词事实由评测者/评测配置声明）→ 合理省略，不补入不评分；
- 引擎层不猜测研究范围：谓词事实缺失 → 不豁免（保守默认"该做没做"）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from bioaudit.ontology.loader import get_ontology
from bioaudit.paths import EXPECTED_TYPES_PATH

#: sequencing 值 → 配置平台键（facts 事实驱动，不凭文件名）
SEQUENCING_TO_PLATFORM = {
    "10X_scRNA_seq": "scrna_10x",
    "smartseq2": "scrna_smartseq2",
    "bulk_RNA_seq": "deg",
}


class ExpectedTypesError(ValueError):
    """expected_types 配置缺失/非法。"""


def load_expected_types(path: Optional[str | Path] = None) -> dict:
    """读取 expected_types 配置（结构校验：version + defaults 映射）。"""
    p = Path(path) if path else EXPECTED_TYPES_PATH
    if not p.exists():
        raise ExpectedTypesError(f"expected_types 配置不存在: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExpectedTypesError(f"expected_types 配置不是合法 YAML: {exc}") from exc
    if not isinstance(data, dict) or "defaults" not in data:
        raise ExpectedTypesError(f"expected_types 配置缺 defaults 键: {p}")
    defaults = data["defaults"]
    if not isinstance(defaults, dict):
        raise ExpectedTypesError(f"expected_types defaults 必须为映射: {p}")
    for key, types in defaults.items():
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ExpectedTypesError(f"defaults[{key}] 必须为字符串列表: {p}")
    return data


def platform_for(paradigm: str, facts: Optional[dict] = None) -> str:
    """范式×平台键：scrna 按 facts['sequencing'] 映射；deg/pan 直接返回范式。"""
    if paradigm in ("deg", "pan"):
        return paradigm
    if paradigm == "scrna":
        seq = (facts or {}).get("sequencing")
        if seq in SEQUENCING_TO_PLATFORM:
            return SEQUENCING_TO_PLATFORM[seq]
        # 无平台事实 → 默认 10X 清单（保守：10X 清单含 doublet_detection，
        # 缺失会补入；评测者应声明 sequencing 事实——G-2 平台查证纪律）
        return "scrna_10x"
    return paradigm


#: when_not_applicable 谓词注册表（facts 事实求值；事实缺失 → 不满足 → 不豁免）
#: 谓词签名：predicate(facts: dict, decision_type: str) -> bool（True = 适用性不成立 = 合理省略）
PREDICATES: dict[str, callable] = {}


def _register(name: str):
    def deco(fn):
        PREDICATES[name] = fn
        return fn
    return deco


@_register("single_sample_or_no_batch")
def _single_sample_or_no_batch(facts, decision_type):
    # 事实缺失 → 不豁免（保守：n_samples 未声明时不做单样本假设）
    n = facts.get("n_samples")
    return (n is not None and n <= 1) or facts.get("has_batch") is False


@_register("no_cbioportal_projection_claim")
def _no_cbioportal_projection_claim(facts, decision_type):
    return not facts.get("cbioportal_projection_claimed", False)


@_register("claim_not_made")
def _claim_not_made(facts, decision_type):
    return decision_type not in (facts.get("claims") or [])


@_register("no_independent_marker_evidence")
def _no_independent_marker_evidence(facts, decision_type):
    return not facts.get("independent_marker_evidence", False)


@_register("no_survival_analysis")
def _no_survival_analysis(facts, decision_type):
    return not facts.get("has_survival_analysis", False)


@_register("no_enrichment_analysis")
def _no_enrichment_analysis(facts, decision_type):
    return not facts.get("has_enrichment_analysis", False)


@_register("no_drug_sensitivity_analysis")
def _no_drug_sensitivity_analysis(facts, decision_type):
    return not facts.get("has_drug_sensitivity_analysis", False)


@_register("no_immune_analysis")
def _no_immune_analysis(facts, decision_type):
    return not facts.get("has_immune_analysis", False)


@_register("no_trajectory_inference")
def _no_trajectory_inference(facts, decision_type):
    return not facts.get("has_trajectory_inference", False)


@_register("study_not_trajectory_focused")
def _study_not_trajectory_focused(facts, decision_type):
    return facts.get("trajectory_focused") is False


@_register("no_independent_prognostic_claim")
def _no_independent_prognostic_claim(facts, decision_type):
    return not facts.get("independent_prognostic_claimed", False)


def is_exempt(decision_type: str, facts: Optional[dict] = None) -> bool:
    """B7/G5 合理省略判定：optional:true 且 when_not_applicable 谓词满足 → 豁免。

    事实缺失 → 谓词不满足 → **不豁免**（保守默认"该做没做"，引擎不猜研究范围）。
    """
    ont = get_ontology()
    t = ont.get_type(decision_type)
    if t is None:
        return False
    if not t.get("optional"):
        return False
    predicate = t.get("when_not_applicable")
    if not predicate:
        return False
    fn = PREDICATES.get(predicate)
    if fn is None:
        return False  # 未注册谓词 → 保守不豁免
    return bool(fn(facts or {}, decision_type))


def expected_types_for(
    paradigm: str,
    facts: Optional[dict] = None,
    path: Optional[str | Path] = None,
) -> list[str]:
    """按范式×平台返回**生效**预期清单（应用 B7 豁免）。

    清单来源 = 配置 defaults[platform_for(paradigm, facts)]；
    可选类型且谓词满足 → 剔除（合理省略）；其余全部保留（缺失即补入）。
    """
    cfg = load_expected_types(path)
    platform = platform_for(paradigm, facts)
    types = list(cfg["defaults"].get(platform, []))
    return [t for t in types if not is_exempt(t, facts)]


def synthesize_expected_decision(
    decision_type: str,
    *,
    context: Optional[dict] = None,
    declared_choice: Optional[str] = None,
    rationale: str = "",
    step_id: Optional[str] = None,
) -> dict:
    """构造补入决策（provenance=expected；choice 优先取 Agent 已声明值，不伪造）。

    返回满足 Decision schema 的 dict；调用方负责 verdict 创建与落盘。
    """
    return {
        "step_id": step_id or f"expected_{decision_type}",
        "decision_type": decision_type,
        "choice": declared_choice or "not_performed",
        "rationale": rationale or "预期决策缺失补入（expected_types；该做没做，B7 未豁免）",
        "context": dict(context or {}),
    }


__all__ = [
    "SEQUENCING_TO_PLATFORM",
    "ExpectedTypesError",
    "load_expected_types",
    "platform_for",
    "PREDICATES",
    "is_exempt",
    "expected_types_for",
    "synthesize_expected_decision",
]
