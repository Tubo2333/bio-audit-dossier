"""ErrorPropagationTracer — 自 fullflow-demo 迁移（含 B8 修正），B2 本体化接线。

B8: Dependency graph loaded from YAML, not hardcoded.
B2（本体化）：默认依赖图从**本体 depends_on** 读取（ontology-design-v1 §五：
"error_tracer 从本体读 depends_on，替代缺失的 dependency_graph.yaml"）。
- 旧 data/mappings/dependency_graph.yaml 仅有 3 条 DEG 条目（deg_method /
  multiple_testing_correction / significance_threshold），本体化后由 34 个
  决策类型的 `depends_on` 组合出完整图（DEG 3 条边逐条保留，scRNA/pan 为新增）。
- 构造参数 dep_graph_path 保留兼容：显式传入路径仍按旧文件加载。
"""

import yaml
from pathlib import Path

from bioaudit.models.score import DecisionScore, ErrorChain
from bioaudit.ontology.loader import get_ontology


class ErrorPropagationTracer:
    """Traces how errors propagate through a decision pipeline.

    B8 FIX: Dependency graph loaded from YAML, not hardcoded.
    B2 FIX: Default graph comes from the ontology `depends_on` fields.
    """

    def __init__(self, dep_graph_path: Path | str | None = None):
        self.dep_graph = self._load_graph(
            Path(dep_graph_path) if dep_graph_path else None
        )

    def _load_graph(self, path: Path | None) -> dict:
        if path is not None:
            # 兼容：显式传入旧依赖图文件
            try:
                with open(path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except FileNotFoundError:
                return {}
        # B2: 本体 depends_on 组合（下游类型 → 上游依赖列表，error_tracer 兼容格式）
        return get_ontology().dep_graph()

    def trace(self, step_scores: list[DecisionScore]) -> list[ErrorChain]:
        """Find low-score steps and trace their downstream impact."""
        error_chains = []

        low_score_steps = {
            s.step_id: s for s in step_scores if 0 <= s.level <= 1
        }

        for step_id, score in low_score_steps.items():
            affected = []
            for other in step_scores:
                if other.step_id == step_id:
                    continue
                deps = self.dep_graph.get(other.decision_type, [])
                if score.decision_type in deps:
                    affected.append(other.step_id)

            if affected:
                error_chains.append(ErrorChain(
                    source_step=step_id,
                    source_error=f"{score.agent_choice} — {score.explanation}",
                    affected_steps=affected,
                    propagation_path=(
                        f"步骤 {step_id} 的 {score.decision_type} 选择错误 "
                        f"→ 影响步骤 {', '.join(affected)} 的有效性"
                    ),
                    severity="critical" if score.level == 0 else "major",
                ))

        return error_chains
