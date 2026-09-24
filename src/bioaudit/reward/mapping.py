"""reward Level→reward 映射（窗口 E / 阶段 4 E1.2：映射定稿 = reward 层"宪法"）。

决策记录全文见 ``docs/reward-mapping.md``（数值 + 论证 + min/mean 选择 +
85.0 饱和处理 + -1 mask），本模块只承载数值与机制，论证归属文档。

定稿要点（refactor-plan-v1.1 F1 + 窗口 E 验收纪律）：
- 映射定义在 **level（序数语义）** 上，而非 numeric_score 的再缩放：
  level 是引擎的规范判定（LEVEL_LABELS 语义），numeric_score 仅是同一
  判定的数值化展示；reward 复用引擎数值会与评分路径耦合，违反"外围层"。
- **等距 vs 非线性**：采用**非线性（严重性凸）**映射——L0→L1→L2→L3→L4
  的相邻间隔 = 0.30 / 0.30 / 0.25 / 0.15（L4 在 MVP 中未启用，见下）。
  论证：level 语义间距不等（"将导致错误结论" 与 "有微小瑕疵" 的科学
  后果差距远大于 "正确" 与 "示范" 的差距），等距（0.25 等步长）会高估
  顶部区分度、低估底部严重性；凸映射使训练信号对 L0/L1 更敏感（RLHF
  语境下"惩罚错误方法"的边际收益高于"奖励锦上添花"）。
- **-1（无法评估）→ mask（None）**：不参与任何聚合（F1 定死）。-1 不
  携带好坏信息，给固定值会注入虚假信号（给 0.5 相当于"中性的好"，
  给 0 相当于惩罚无规则覆盖的方法学空白）。
- **85.0 天花板饱和**：L3/L4 合并（D3：MVP 只判 Level 3 方法，L4 留给
  v0.2 LLM 论证评估），故全对轨迹 reward = 0.85（引擎 trajectory_score
  = 85.0 同源）。**明确不做 evidence 质量微调**：证据引用数量/置信度
  目前不是经过校准的质量信号，用作微调会把未校准噪声注入训练信号；
  扩展点 = :data:`EVIDENCE_ADJUSTMENT_HOOK`（L4 LLM 评估落地时启用，
  且必须附带校准论证，见 docs/reward-mapping.md §4）。
- **聚合选 mean（credit assignment）**：见 docs/reward-mapping.md §5；
  L0 硬惩罚在配方层（recipes.py，配方 B）实现，与 mean 组合。
"""

from __future__ import annotations

from typing import Optional

#: reward schema 版本（meta.reward_schema；变更走评审，与 report schema 同策略）
REWARD_SCHEMA_VERSION = "reward.v1"

#: 全部合法 level（-1 为无法评估，单独列出）
LEVELS: tuple[int, ...] = (0, 1, 2, 3, 4)
# M2.4（窗口 M，2026-08-16）：未验证（-2，关键上下文缺失）与 -1 同掩码——
# 两者都不携带好坏信息，给固定值会注入虚假信号（与 F1 -1 mask 同原则）
MASKED_LEVELS: frozenset[int] = frozenset({-1, -2})
MASKED_LEVEL = -1

#: **Level→reward 映射表（定稿，E1.2）**：非线性（严重性凸），见模块 docstring。
#: L4=1.00 保留占位（MVP 合并 3+4，D3）；L3=0.85 与引擎 LEVEL_TO_SCORE 同值——
#: 这是唯一一处与引擎数值对齐（天花板语义一致），其余值独立论证。
REWARD_BY_LEVEL: dict[int, float] = {
    4: 1.00,   # 示范级（v0.2 LLM 论证评估启用后可达；MVP 中 L3/L4 合并）
    3: 0.85,   # 正确级 —— 天花板（85.0 饱和）
    2: 0.60,   # 可接受（微小瑕疵）
    1: 0.30,   # 有风险
    0: 0.00,   # 危险（将导致错误结论）
}

#: 饱和天花板（= L3 值；L3/L4 合并 → 全对轨迹 reward 上限 0.85，与引擎 85.0 同源）
CEILING_REWARD = REWARD_BY_LEVEL[3]

#: E2.5 硬惩罚系数：任一（未 mask 的）L0 → 轨迹级 reward × 该系数。
#: 数值与依据见 docs/reward-mapping.md §6（γ=0.30：含 L0 轨迹 reward 显著
#: 低于全对 0.85 且低于典型 L2-only 轨迹 0.60，任意轨迹长度 n≥2 成立）。
HARD_PENALTY_GAMMA = 0.30

#: PRM 预留接口（配方 C）默认权重（占位；PRM 未实现，见 docs/reward-mapping.md §7）
PRM_WEIGHT_DEFAULT = 1.0

#: mask 原因常量（meta.mask_reasons 计数键 / StepReward.mask_reason）
MASK_REASON_LEVEL_MINUS_ONE = "level_minus_one"     # level = -1（无法评估，F1）
MASK_REASON_LEVEL_UNVERIFIED = "level_unverified"   # level = -2（未验证，关键上下文缺失，M2.4）
MASK_REASON_REVOKED = "revoked"                     # B4：revoked 步骤 reward 置 mask
MASK_REASON_PROVISIONAL = "provisional_not_final"   # B4：非 final 不消费
MASK_REASON_NO_VERDICT = "no_verdict_record"        # B4：有 verdict 会话但无记录

#: 决策来源标注（E1.3 时序化：M1 声明为准，M3 补漏按阶段末尾聚合）
SOURCE_DECLARED = "declared"      # M1 声明（或 legacy/benchmark：无 M1/M3 区分）
SOURCE_BACKFILLED = "backfilled"  # M3 补漏（阶段末尾聚合）

#: L4 LLM 评估落地前的证据质量微调钩子（**明确不启用**，理由见模块 docstring）。
#: 接入协议：必须返回 |delta| 有界的小量并附校准论证，否则保持 None。
EVIDENCE_ADJUSTMENT_HOOK = None


def level_reward(level: int) -> Optional[float]:
    """level → reward；-1/-2（无法评估/未验证）→ **None（mask，不参与聚合）**。

    Parameters
    ----------
    level : int
        -1（mask）/ -2（mask，未验证，M2.4）或 0..4（映射表）。

    Returns
    -------
    float | None
        None 表示该步骤被 mask（调用方必须跳过，不得参与任何聚合）。
    """
    if level in MASKED_LEVELS:
        return None
    return REWARD_BY_LEVEL.get(level)


def is_masked_level(level: int) -> bool:
    """level 是否应被 mask（-1 无法评估 / -2 未验证）。"""
    return level in MASKED_LEVELS


__all__ = [
    "REWARD_SCHEMA_VERSION",
    "LEVELS",
    "MASKED_LEVEL",
    "MASKED_LEVELS",
    "REWARD_BY_LEVEL",
    "CEILING_REWARD",
    "HARD_PENALTY_GAMMA",
    "PRM_WEIGHT_DEFAULT",
    "MASK_REASON_LEVEL_MINUS_ONE",
    "MASK_REASON_LEVEL_UNVERIFIED",
    "MASK_REASON_REVOKED",
    "MASK_REASON_PROVISIONAL",
    "MASK_REASON_NO_VERDICT",
    "SOURCE_DECLARED",
    "SOURCE_BACKFILLED",
    "EVIDENCE_ADJUSTMENT_HOOK",
    "level_reward",
    "is_masked_level",
]
