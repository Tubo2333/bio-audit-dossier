"""reward 配方与逐步骤 reward（窗口 E / E2：配方 A/B/C + 硬惩罚 + PRM 预留）。

配方定义（docs/reward-mapping.md §6-7 + docs/reward-protocol.md §三）：
- **A（基线，纯规则分）**：``mean``（未 mask 步骤的 reward 均值）——
  密集 credit assignment；-1/revoked 等一律先 mask 再聚合；
- **B（规则分 + 硬惩罚，默认配方）**：A × :data:`HARD_PENALTY_GAMMA`
  当且仅当存在**未 mask 的 L0**（L0 = "将导致错误结论"，引擎 verdict 即
  blocked）；惩罚为**二元**（不随 L0 数量复利：一个 L0 已使结论失效，
  复利会过度惩罚且与 verdict=blocked 的二元语义一致）；
- **C（规则分 + PRM 预留）**：``加权 mean``（先 mask 后加权），默认权重
  :data:`PRM_WEIGHT_DEFAULT`（均匀占位 → 默认下 C ≡ A，诚实声明：PRM
  未实现）；接口 = ``prm_weights={step_id: float}``，非均匀权重经测试
  证明改变结果（接口生效）。PRM 接入点文档：docs/reward-mapping.md §7。

聚合不变量：
- **-1 必须 mask，不参与聚合**（F1 纪律；聚合分母 = 未 mask 步骤数）；
- **只消费 final verdict**（B4 纪律；revoked/provisional/无记录 → mask）；
- 全部步骤被 mask → trajectory_reward = **None**（不可评估，不给 0 这种
  虚假信号），meta 携带原因。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from bioaudit.reward.mapping import (
    HARD_PENALTY_GAMMA,
    MASK_REASON_LEVEL_MINUS_ONE,
    MASK_REASON_LEVEL_UNVERIFIED,
    MASKED_LEVEL,
    PRM_WEIGHT_DEFAULT,
    SOURCE_DECLARED,
    level_reward,
)

#: 合法配方（消融三组，E2.6）
REWARD_RECIPES: tuple[str, ...] = ("A", "B", "C")

#: recipe 默认（配方 B = 规则分 + 硬惩罚；E2.5 定稿）
DEFAULT_RECIPE = "B"


class StepReward(BaseModel):
    """单步 reward（时序化产物，E1.3）：mask 语义 + 时序顺序 + 来源。"""

    step_id: str
    decision_type: str
    order: int                          # 时序位置（0-based；M1 声明时序，M3 补漏在阶段末尾）
    source: str = SOURCE_DECLARED       # declared（M1/legacy/benchmark）| backfilled（M3 补漏）
    level: int                          # 引擎判定的 level（评分路径原样，reward 不改判）
    reward: Optional[float] = None      # None = 被 mask（不参与聚合）
    masked: bool = False
    # mask 原因：level_minus_one | revoked | provisional_not_final | no_verdict_record
    mask_reason: Optional[str] = None


def build_step_rewards(
    step_scores: list[dict],
    verdict_map: Optional[dict[str, dict]] = None,
    order: Optional[list[str]] = None,
    source_map: Optional[dict[str, str]] = None,
) -> list[StepReward]:
    """从引擎 step_scores（DecisionScore 的 model_dump）构建时序化 step rewards。

    Parameters
    ----------
    step_scores : list[dict]
        run_audit 输出的 step_scores（含 step_id/decision_type/level）。
    verdict_map : dict[str, dict] | None
        ``{step_id: verdict_record}``（VerdictRecord.as_dict()）；
        None = 无采集会话（legacy/benchmark 轨迹）→ 全部视为 final 消费
        （meta.verdict_mode="all_final"，见 api.py）；
        提供时 **只消费 final**（B4）：revoked → mask(revoked)、
        provisional → mask(provisional_not_final)、无记录 → mask(no_verdict_record)。
    order : list[str] | None
        显式时序（step_id 列表；None = step_scores 出现顺序 = M1 声明时序，
        M3 补漏条目在轨迹文件中位于阶段末尾，天然满足"按阶段末尾聚合"）。
    source_map : dict[str, str] | None
        ``{step_id: "declared"|"backfilled"}``；None = 全部 declared
        （legacy/benchmark 无 M1/M3 区分）。

    Returns
    -------
    list[StepReward]
        时序化步骤列表（order 0-based 递增；masked 步骤 reward=None）。
    """
    if order is None:
        order = [s["step_id"] for s in step_scores]
    by_id = {s["step_id"]: s for s in step_scores}
    steps: list[StepReward] = []
    for pos, step_id in enumerate(order):
        score = by_id.get(step_id)
        if score is None:
            # 时序声明了但分数缺失（理论上不应发生）→ 保守 mask
            steps.append(StepReward(
                step_id=step_id, decision_type="", order=pos,
                level=MASKED_LEVEL, masked=True,
                mask_reason="no_score_record",
            ))
            continue
        level = int(score["level"])
        source = (source_map or {}).get(step_id, SOURCE_DECLARED)
        reward = level_reward(level)
        masked, reason = False, None
        if reward is None:
            # M2.4（窗口 M）：-2 未验证（关键上下文缺失）与 -1 无法评估同掩码、
            # 原因区分（mask_reasons 可审计）
            if level == -2:
                masked, reason = True, MASK_REASON_LEVEL_UNVERIFIED
            else:
                masked, reason = True, MASK_REASON_LEVEL_MINUS_ONE
        elif verdict_map is not None:
            rec = verdict_map.get(step_id)
            if rec is None:
                masked, reason = True, "no_verdict_record"
            elif rec.get("status") == "revoked":
                masked, reason = True, "revoked"
            elif rec.get("status") != "final":
                masked, reason = True, "provisional_not_final"
        steps.append(StepReward(
            step_id=step_id,
            decision_type=score.get("decision_type", ""),
            order=pos, source=source, level=level,
            reward=None if masked else reward,
            masked=masked, mask_reason=reason,
        ))
    return steps


def _unmasked(steps: list[StepReward]) -> list[StepReward]:
    return [s for s in steps if not s.masked and s.reward is not None]


def has_unmasked_l0(steps: list[StepReward]) -> bool:
    """是否存在未 mask 的 L0（配方 B 硬惩罚触发条件）。"""
    return any(not s.masked and s.level == 0 for s in steps)


def trajectory_reward(
    steps: list[StepReward],
    recipe: str = DEFAULT_RECIPE,
    prm_weights: Optional[dict[str, float]] = None,
) -> Optional[float]:
    """轨迹级 reward（配方 A/B/C；全 mask → None）。

    Parameters
    ----------
    steps : list[StepReward]
        :func:`build_step_rewards` 产物。
    recipe : str
        "A"（纯规则分）/ "B"（+L0 硬惩罚）/ "C"（PRM 加权占位）。
    prm_weights : dict[str, float] | None
        仅配方 C：``{step_id: weight}``；None/缺省 → 均匀占位权重
        :data:`PRM_WEIGHT_DEFAULT`（默认下 C ≡ A）。

    Returns
    -------
    float | None
        None = 全部步骤被 mask（不可评估，不给 0 虚假信号）。
    """
    if recipe not in REWARD_RECIPES:
        raise ValueError(f"未知配方 {recipe!r}（合法: {REWARD_RECIPES}）")
    used = _unmasked(steps)
    if not used:
        return None

    if recipe == "C":
        weights = [float((prm_weights or {}).get(s.step_id, PRM_WEIGHT_DEFAULT))
                   for s in used]
        total_w = sum(weights)
        if total_w <= 0:
            return None  # 全零权重 → 无定义，按不可评估处理
        base = sum(w * s.reward for w, s in zip(weights, used)) / total_w
    else:
        base = sum(s.reward for s in used) / len(used)  # mean（A 与 B 同基）

    if recipe == "B" and has_unmasked_l0(steps):
        base = base * HARD_PENALTY_GAMMA  # E2.5：任一 L0 → 轨迹级惩罚系数
    return round(float(base), 6)


def mask_summary(steps: list[StepReward]) -> dict:
    """meta 用 mask 统计：{n_unmasked, n_masked, mask_reasons: {reason: n}}。"""
    reasons: dict[str, int] = {}
    for s in steps:
        if s.masked and s.mask_reason:
            reasons[s.mask_reason] = reasons.get(s.mask_reason, 0) + 1
    return {
        "n_unmasked": sum(1 for s in steps if not s.masked),
        "n_masked": sum(1 for s in steps if s.masked),
        "mask_reasons": reasons,
    }


__all__ = [
    "REWARD_RECIPES",
    "DEFAULT_RECIPE",
    "StepReward",
    "build_step_rewards",
    "has_unmasked_l0",
    "trajectory_reward",
    "mask_summary",
]
