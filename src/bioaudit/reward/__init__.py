"""reward 包（阶段 4 训练信号层，窗口 E）。

外围输出层纪律：本包**只消费** run_audit 的评分结果（level/step_scores），
零改动评分路径（matcher/evaluator/aggregator/registry）；golden 0 差异为
硬验收（E4.11）。核心文档：docs/reward-mapping.md（映射定稿"宪法"）+
docs/reward-protocol.md（配方/校准协议）。
"""

from bioaudit.reward.mapping import (
    CEILING_REWARD,
    HARD_PENALTY_GAMMA,
    PRM_WEIGHT_DEFAULT,
    REWARD_BY_LEVEL,
    REWARD_SCHEMA_VERSION,
)

__all__ = [
    "REWARD_SCHEMA_VERSION",
    "REWARD_BY_LEVEL",
    "CEILING_REWARD",
    "HARD_PENALTY_GAMMA",
    "PRM_WEIGHT_DEFAULT",
]
