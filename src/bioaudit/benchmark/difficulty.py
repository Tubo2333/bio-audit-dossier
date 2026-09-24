"""难度梯度独立量化（refactor-plan-v1.1 E4；execution-plan D2.5）。

铁律：**难度标签不得由审计分数定义**——本模块只消费任务特征
（决策数 / gold 隐藏错误数 / 微妙错误数 / 一致性族类型数），
**不 import 任何引擎评分模块**（防循环：audit 分数与难度无推导关系）。

预注册 rubric（difficulty.v1，2026-08-16 冻结）：
  1. hard（3）：n_decisions >= 17 或 n_errors >= 3 或 n_subtle_errors >= 2
  2. easy（1）：n_decisions <= 10 且 n_errors <= 1
  3. 其余 → medium（2）

分级顺序固定（先 hard 后 easy），保证确定性。
"""

from __future__ import annotations

from bioaudit.benchmark.models import (
    SUBTLE_ERROR_TYPES,
    Difficulty,
    DifficultyFeatures,
    GoldAnnotation,
)

RUBRIC_VERSION = "difficulty.v1"


def features_from_gold_with_types(
    gold: GoldAnnotation, decision_types: dict[str, str]
) -> DifficultyFeatures:
    """由 gold + 决策类型映射计算特征（类型映射：step_id → decision_type）。"""
    labels = gold.labels
    n_errors = 0
    n_edge = 0
    n_subtle_errors = 0
    n_consistency_family = 0
    for lab in labels:
        dtype = decision_types.get(lab.step_id, "")
        if lab.label == "error":
            n_errors += 1
            if dtype in SUBTLE_ERROR_TYPES:
                n_subtle_errors += 1
        elif lab.label == "edge":
            n_edge += 1
        if dtype in SUBTLE_ERROR_TYPES:
            # 一致性族类型出现次数（无论对错——任务复杂度信号）
            from bioaudit.benchmark.models import CONSISTENCY_FAMILY

            if dtype in CONSISTENCY_FAMILY:
                n_consistency_family += 1
    return DifficultyFeatures(
        n_decisions=len(labels),
        n_errors=n_errors,
        n_edge=n_edge,
        n_subtle_errors=n_subtle_errors,
        n_consistency_family=n_consistency_family,
    )


def difficulty_from_features(features: DifficultyFeatures) -> int:
    """预注册 rubric（difficulty.v1）：特征 → 难度标签（1/2/3）。"""
    if (
        features.n_decisions >= 17
        or features.n_errors >= 3
        or features.n_subtle_errors >= 2
    ):
        return 3
    if features.n_decisions <= 10 and features.n_errors <= 1:
        return 1
    return 2


def assign_difficulty(gold: GoldAnnotation, decision_types: dict[str, str]) -> Difficulty:
    """任务难度（确定性）：gold 特征 → 标签；难度与审计分数零接触。"""
    features = features_from_gold_with_types(gold, decision_types)
    label = difficulty_from_features(features)
    return Difficulty(
        label=label,
        rubric_version=RUBRIC_VERSION,
        features=features,
    )


__all__ = [
    "RUBRIC_VERSION",
    "features_from_gold_with_types",
    "difficulty_from_features",
    "assign_difficulty",
]
