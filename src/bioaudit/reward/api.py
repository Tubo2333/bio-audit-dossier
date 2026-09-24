"""reward API（窗口 E / E1.1：``reward(trajectory) -> {step_rewards, trajectory_reward, meta}``）。

- **外围输出层纪律**：本模块只消费 run_audit 的 step_scores（评分结果），
  **不触碰任何评分路径**（matcher/evaluator/aggregator 零改动；
  golden 0 差异为硬验收，E4.11）；
- **快照三元组**（C1/P2）：meta.snapshot = ruleset + ontology + engine
  版本（默认 ``current_snapshot()``，可显式传入 → 可复现）；
- **只消费 final verdict**（B4）：传 ``session_id`` 或 ``verdicts`` 时，
  revoked → mask(revoked)、provisional → mask(provisional_not_final)、
  无记录 → mask(no_verdict_record)；不传 = legacy/benchmark 模式
  （meta.verdict_mode="all_final"，无采集会话，全部视为 final）；
- **F4 纪律（E2.4）**：本模块与整个 reward 包**不消费交叉验证四类判定**
  （虚报/漏报/未验证）——那些只进报告；测试双守卫（源码引用 + 输出 schema）；
- **-1 必须 mask**（F1）：level -1（无法评估）→ reward=None，不参与聚合。

返回结构（E1.1 定稿）::

    {
      "step_rewards": [ {step_id, decision_type, order, source, level,
                         reward, masked, mask_reason}, ... ],
      "trajectory_reward": float | None,     # None = 全 mask（不可评估）
      "meta": {
        "reward_schema": "reward.v1",
        "status": "experimental_uncalibrated",   # E4.10：C3 语义不变
        "recipe": "B",
        "verdict_mode": "all_final" | "final_only",
        "n_decisions", "n_unmasked", "n_masked",
        "mask_reasons": {...}, "n_l0", "n_l1",
        "has_l0_penalty_applied": bool,
        "aggregation": "mean" | "weighted_mean",
        "saturation": "ceiling_0.85_no_micro_adjustment",
        "snapshot": {...三元组...},
      }
    }
"""

from __future__ import annotations

from typing import Any, Optional

from bioaudit.reward.mapping import (
    CEILING_REWARD,
    EVIDENCE_ADJUSTMENT_HOOK,
    REWARD_SCHEMA_VERSION,
)
from bioaudit.reward.recipes import (
    DEFAULT_RECIPE,
    build_step_rewards,
    mask_summary,
    trajectory_reward,
)

#: 报告/reward 状态标注（E4.10：experimental / 未校准，C3 语义不变）
REWARD_STATUS = "experimental_uncalibrated"

#: 饱和处理标注（E1.2：明确不做微调，理由见 docs/reward-mapping.md §4）
SATURATION_NOTE = "ceiling_0.85_no_micro_adjustment"


def _verdict_map(verdicts: list[dict]) -> dict[str, dict]:
    """verdict 记录列表 → {step_id: 最新记录}（按 created_at 取最新）。

    同一 step_id 多条记录（迭代调参，B5）：最新一条为 operative。
    """
    by_step: dict[str, dict] = {}
    for rec in verdicts:
        step_id = rec.get("step_id")
        if not step_id:
            continue
        cur = by_step.get(step_id)
        if cur is None or rec.get("created_at", "") >= cur.get("created_at", ""):
            by_step[step_id] = rec
    return by_step


def _load_verdict_map(
    session_id: Optional[str], verdicts: Optional[list[dict]],
) -> tuple[Optional[dict[str, dict]], str]:
    """加载 verdict 映射；返回 (map, verdict_mode)。"""
    if verdicts is not None:
        return _verdict_map(verdicts), "final_only"
    if session_id is not None:
        from bioaudit.capture.verdict import VerdictStore

        records = VerdictStore().get(session_id)  # 全量（含 provisional/revoked，用于 mask 判定）
        return _verdict_map([r.as_dict() for r in records]), "final_only"
    return None, "all_final"


def reward_from_scores(
    step_scores: list[dict],
    recipe: str = DEFAULT_RECIPE,
    verdicts: Optional[list[dict]] = None,
    prm_weights: Optional[dict[str, float]] = None,
    order: Optional[list[str]] = None,
    source_map: Optional[dict[str, str]] = None,
    snapshot: Optional[dict] = None,
) -> dict:
    """核心纯函数：从引擎 step_scores 计算 reward（报告集成与 CLI 共用）。

    不重新运行审计管道（评分路径零触碰）；step_scores 即 run_audit 的
    ``state["step_scores"]``（DecisionScore.model_dump 列表）。

    Returns
    -------
    dict
        ``{step_rewards, trajectory_reward, meta}``（结构见模块 docstring）。
    """
    verdict_map, verdict_mode = _load_verdict_map(None, verdicts)
    steps = build_step_rewards(
        step_scores, verdict_map=verdict_map,
        order=order, source_map=source_map,
    )
    traj_reward = trajectory_reward(steps, recipe=recipe, prm_weights=prm_weights)
    summary = mask_summary(steps)

    step_dicts = []
    for s in steps:
        d = s.model_dump()
        step_dicts.append({
            "step_id": d["step_id"], "decision_type": d["decision_type"],
            "order": d["order"], "source": d["source"], "level": d["level"],
            "reward": d["reward"], "masked": d["masked"], "mask_reason": d["mask_reason"],
        })

    n_l0 = sum(1 for s in steps if not s.masked and s.level == 0)
    n_l1 = sum(1 for s in steps if not s.masked and s.level == 1)
    return {
        "step_rewards": step_dicts,
        "trajectory_reward": traj_reward,
        "meta": {
            "reward_schema": REWARD_SCHEMA_VERSION,
            "status": REWARD_STATUS,
            "recipe": recipe,
            "verdict_mode": verdict_mode,
            "aggregation": "weighted_mean" if recipe == "C" else "mean",
            "saturation": SATURATION_NOTE,
            "n_decisions": len(steps),
            "n_unmasked": summary["n_unmasked"],
            "n_masked": summary["n_masked"],
            "mask_reasons": summary["mask_reasons"],
            "n_l0": n_l0,
            "n_l1": n_l1,
            "has_l0_penalty_applied": bool(
                recipe == "B" and traj_reward is not None and any(
                    not s.masked and s.level == 0 for s in steps
                )
            ),
            "ceiling_reward": CEILING_REWARD,
            "evidence_adjustment_enabled": EVIDENCE_ADJUSTMENT_HOOK is not None,
            "snapshot": dict(snapshot or {}),
        },
    }


def reward(
    trajectory: list[dict] | dict,
    act: Optional[str] = None,
    recipe: str = DEFAULT_RECIPE,
    session_id: Optional[str] = None,
    verdicts: Optional[list[dict]] = None,
    prm_weights: Optional[dict[str, float]] = None,
    snapshot: Optional[Any] = None,
) -> dict:
    """**reward API（E1.1 定稿签名）**：
    ``reward(trajectory) -> {step_rewards, trajectory_reward, meta}``。

    Parameters
    ----------
    trajectory : list[dict] | dict
        决策数组（v1）或含 ``decisions`` 键的对象（轨迹 v2 / benchmark 任务）。
    act : str | None
        范式（deg/pan/scrna）；None → 从轨迹 dict 的 ``act`` 键推断
        （legacy 数组无 act 时需显式提供，否则 audit 无法消歧）。
    recipe : str
        "A"（纯规则分）/ "B"（规则分 + L0 硬惩罚，默认）/ "C"（PRM 预留）。
    session_id : str | None
        采集会话（B4）：只消费 final verdict（revoked/provisional/无记录 → mask）。
    verdicts : list[dict] | None
        VerdictRecord.as_dict() 列表（离线/测试用；与 session_id 二选一）。
    prm_weights : dict[str, float] | None
        配方 C：{step_id: weight}（PRM 预留接口，见 docs/reward-mapping.md §7）。
    snapshot : SnapshotTriple | None
        显式三元组快照；None → ``current_snapshot()``（可复现性底线，C1/P2）。

    Returns
    -------
    dict
        ``{step_rewards, trajectory_reward, meta}``（meta.snapshot 三元组在场）。

    Raises
    ------
    BioAuditError
        输入校验失败（bad-request / validation-error / paradigm-not-found，
        B3 契约复用；非法输入显式报错，不静默降级）。
    """
    from bioaudit.api.audit import run_audit  # 局部导入：避免与 audit 包循环引用

    if act is None and isinstance(trajectory, dict):
        act = trajectory.get("act")
    if recipe not in ("A", "B", "C"):
        raise ValueError(f"未知配方 {recipe!r}（合法: A/B/C）")

    state = run_audit(trajectory, act=act)
    if state.get("error"):
        code = state.get("error_code") or "internal-error"
        raise RuntimeError(f"reward: 审计管道失败 [{code}]: {state['error']}")

    from bioaudit.report import current_snapshot

    snap = snapshot.as_dict() if snapshot is not None else current_snapshot().as_dict()
    verdict_map, _ = _load_verdict_map(session_id, verdicts)
    if verdicts is None and verdict_map is not None:
        verdicts = list(verdict_map.values())  # final_only 模式：把会话记录传给纯函数
    return reward_from_scores(
        state["step_scores"], recipe=recipe,
        verdicts=verdicts, prm_weights=prm_weights, snapshot=snap,
    )


def report_reward_block(
    step_scores: list[dict],
    act: Optional[str] = None,
    recipe: str = DEFAULT_RECIPE,
    snapshot: Optional[dict] = None,
) -> dict:
    """run_audit 报告集成（E4.10）：从已算出的 step_scores 生成 reward 块。

    - 纯函数：不重跑管道（评分路径零改动）；report 新增键，既有键不变
      （golden 0 差异保持，E4.11）；
    - **experimental/未校准标注**（C3 语义不变）：report.reward.status =
      "experimental_uncalibrated"，禁止任何消费方当作校准信号。
    """
    block = reward_from_scores(step_scores, recipe=recipe, snapshot=snapshot)
    block["meta"]["act"] = act
    return block


__all__ = [
    "REWARD_STATUS",
    "SATURATION_NOTE",
    "reward",
    "reward_from_scores",
    "report_reward_block",
]
