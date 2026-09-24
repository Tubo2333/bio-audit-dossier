"""ScoreAggregator — 自 fullflow-demo 迁移（含 C1/C2 修正），B2 本体化接线。

保留修正：
- C1: Lowest-score-dominant aggregation. Fatal errors cannot be diluted.
- C2: MVP uses 3 dimensions (not 6). 3 empty dimensions removed.
- B8: scRNA 聚合器已含 annotation_validation / trajectory_validation 两类

B2 变更（本体化）：
- 删除 TYPE_TO_DIMENSION 硬编码字典（约 34 条），dimension 改为从本体读取
  （bioaudit.ontology.loader.Ontology.dimension；决策类型定义文件
  decision_types/*.yaml 的 `dimension` 键，ontology-design-v1 §五）。
  维度取值与旧硬编码逐一相同（tests/test_ontology.py 有等价性守卫）。
"""

from bioaudit.models.score import DecisionScore, AggregatedScore
from bioaudit.models.profile import ScenarioProfile
from bioaudit.ontology.loader import get_ontology


class ScoreAggregator:
    """Aggregates individual decision scores into trajectory-level report.

    C1 FIX: Uses "lowest-dimension-dominant" strategy.
    A single Level 0 in any dimension should dominate the final score.
    Weighted average is kept as a supplementary metric.
    """

    # C2 FIX: 3 dimensions with rebalanced weights
    DEFAULT_DIMENSION_WEIGHTS = {
        "data_handling": 0.33,
        "method_selection": 0.34,
        "statistical_rigor": 0.33,
    }

    def __init__(self, ontology=None):
        # B2: 聚合维度从本体读（删除 TYPE_TO_DIMENSION 硬编码）
        self.ontology = ontology if ontology is not None else get_ontology()

    def aggregate(
        self, step_scores: list[DecisionScore],
        profile: ScenarioProfile | None = None
    ) -> AggregatedScore:
        # 1. Group scores by dimension (C2: 3 dims only)
        dim_scores: dict[str, list[float]] = {}
        for score in step_scores:
            # M2.4（窗口 M）：未验证（-2）与无法评估（-1）同掩码——不进维度分
            if score.level in (-1, -2):
                continue
            # B2: 本体读 dimension；未知类型 → None → 跳过（与旧硬编码行为一致）
            dim = self.ontology.dimension(score.decision_type)
            if dim is None:
                continue  # C2: skip unmapped types
            dim_scores.setdefault(dim, []).append(score.numeric_score)

        # 2. Per-dimension average
        dim_averages = {}
        for dim, scores in dim_scores.items():
            dim_averages[dim] = sum(scores) / len(scores)

        # 3. C1 FIX: Lowest-dimension-dominant score
        if dim_averages:
            lowest_dim_score = min(dim_averages.values())
        else:
            lowest_dim_score = 0.0  # No recognized dimensions → worst (all unmapped)

        # 4. Weighted average (supplementary)
        weights = (
            profile.dimension_weights if profile
            else self.DEFAULT_DIMENSION_WEIGHTS
        )
        weighted_sum = 0.0
        total_weight = 0.0
        for dim, avg in dim_averages.items():
            w = weights.get(dim, 0.0)
            weighted_sum += avg * w
            total_weight += w
        weighted_avg = (
            (weighted_sum / total_weight * 100) if total_weight > 0 else 100.0
        )

        # C1: Trajectory score = lowest-dimension score (primary)
        trajectory_score = round(lowest_dim_score * 100, 1)

        # 5. Critical issues
        critical = [
            s for s in step_scores if 0 <= s.level <= 1
        ]

        # 6. Verdict
        verdict = "pass"
        if any(s.level == 0 for s in step_scores):
            verdict = "blocked"
        elif any(s.level == 1 for s in step_scores):
            verdict = "needs_correction"
        elif trajectory_score < 60:
            verdict = "needs_correction"

        return AggregatedScore(
            step_scores=step_scores,
            dimension_scores=dim_averages,
            trajectory_score=trajectory_score,
            critical_issues=[
                f"[Level {s.level}] Step {s.step_id}: {s.agent_choice} — {s.explanation}"
                for s in critical
            ],
            verdict=verdict,
        )
