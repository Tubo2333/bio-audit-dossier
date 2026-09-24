"""引擎包（自 fullflow-demo/src/engine 迁移，D5 修复后基线）。

- evaluator: 匹配规则评分（最严取分 A3 / D5 修复后无无条件提升）
- matcher: 决策解析 + 规则匹配（B8 mappings 锚定）
- aggregator: 三维度最低分主导聚合（C1/C2）
- conflict_detector: 规则间冲突检测（A7）
- error_tracer: 错误传播追踪（B8 依赖图锚定）
"""

from bioaudit.engine.aggregator import ScoreAggregator
from bioaudit.engine.conflict_detector import ConflictDetector
from bioaudit.engine.error_tracer import ErrorPropagationTracer
from bioaudit.engine.evaluator import LEVEL_LABELS, LEVEL_TO_SCORE, RuleEvaluator
from bioaudit.engine.matcher import RuleMatcher

__all__ = [
    "ScoreAggregator",
    "ConflictDetector",
    "ErrorPropagationTracer",
    "RuleEvaluator",
    "RuleMatcher",
    "LEVEL_LABELS",
    "LEVEL_TO_SCORE",
]
