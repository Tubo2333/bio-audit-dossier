"""API 契约（B3）：请求 schema 与输入校验（refactor-plan-v1.1 B1/B2；audit-report A5/A7/A15）。

- :class:`TrajectoryPayload`：run_audit 轨迹输入（决策数组 或 v2 对象）→ 统一为
  ``list[Decision]``，pydantic 逐条校验（A15：缺字段/拼错字段 → validation-error）；
- :class:`AuditDecisionRequest`：audit_decision 请求（decision + **必填 paradigm**，
  B2：deg_method 同名异构消歧）；
- :func:`validate_human_overrides`：A7 校验（int 且 -1..4），非法拒绝并记录事件。

完整契约（请求/响应 schema + 错误码 + 示例）见 docs/api-contract.md。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, field_validator

from bioaudit.errors import BioAuditError, ErrorCode, validation_error
from bioaudit.models.decision import Decision
from bioaudit.models.trajectory import VALID_PARADIGMS

#: human_overrides 合法 level 范围（audit-report A7：-1 无法评估 … 4 示范级）
HUMAN_OVERRIDE_MIN = -1
HUMAN_OVERRIDE_MAX = 4


def validate_paradigm(paradigm: Optional[str]) -> None:
    """校验范式；None 允许（run_audit/match_details 的 legacy 全量语义），
    非 None 时必须 ∈ {deg, pan, scrna}，否则 paradigm-not-found（不再静默回退）。"""
    if paradigm is not None and paradigm not in VALID_PARADIGMS:
        raise BioAuditError(
            ErrorCode.PARADIGM_NOT_FOUND,
            f"未知范式 {paradigm!r}（合法: {sorted(VALID_PARADIGMS)}）",
            details={"paradigm": paradigm, "valid": sorted(VALID_PARADIGMS)},
        )


class TrajectoryPayload(BaseModel):
    """run_audit 的轨迹负载（归一化后的决策数组；逐条 pydantic 校验，A15）。"""

    decisions: list[Decision]


def parse_trajectory_payload(trajectory: Any) -> list[Decision]:
    """校验并归一化 run_audit 的轨迹输入；失败 → BioAuditError（带错误码）。

    - 决策数组（v1）→ 直接校验；
    - 含 ``decisions`` 键的对象（轨迹 v2）→ 取 decisions 校验；
    - 其他结构 → bad-request；字段级失败 → validation-error（A15）。
    """
    if isinstance(trajectory, list):
        raw_decisions = trajectory
    elif isinstance(trajectory, dict) and "decisions" in trajectory:
        raw_decisions = trajectory["decisions"]
    else:
        raise BioAuditError(
            ErrorCode.BAD_REQUEST,
            "trajectory 必须是决策数组（list[dict]）或含 decisions 键的对象（v2）",
            details={"actual_type": type(trajectory).__name__},
        )
    try:
        payload = TrajectoryPayload(decisions=raw_decisions)
    except BioAuditError:
        raise
    except Exception as exc:
        raise validation_error(
            "轨迹输入校验失败（每条决策需满足 Decision schema: "
            "step_id/decision_type/choice 必填，rationale/context/tool_call/code_snippet 可选）",
            exc,
        ) from exc
    return payload.decisions


class AuditDecisionRequest(BaseModel):
    """audit_decision 请求（B2：paradigm 必填——deg_method 同名异构消歧）。"""

    decision: Decision
    paradigm: str

    @field_validator("paradigm")
    @classmethod
    def _paradigm_valid(cls, v: str) -> str:
        if v not in VALID_PARADIGMS:
            raise ValueError(
                f"paradigm 必须为 {sorted(VALID_PARADIGMS)} 之一，收到 {v!r}"
            )
        return v


def validate_human_overrides(
    overrides: Optional[dict[str, Any]],
    record_event,
) -> dict[str, int]:
    """A7 校验：human_overrides 必须为 {step_id: int(-1..4)}。

    - 非 dict → bad-request；
    - 值非 int（bool 除外）或不在 -1..4 → **拒绝**并调用 ``record_event``
      （记录 ``invalid_override_rejected`` 事件）后抛 validation-error；
    - 返回校验通过后的副本（供管道使用，避免调用方对象被篡改）。
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise BioAuditError(
            ErrorCode.BAD_REQUEST,
            "human_overrides 必须是对象（{{step_id: level}}），"
            f"收到 {type(overrides).__name__}",
            details={"actual_type": type(overrides).__name__},
        )
    validated: dict[str, int] = {}
    for step_id, value in overrides.items():
        ok = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and HUMAN_OVERRIDE_MIN <= value <= HUMAN_OVERRIDE_MAX
        )
        if not ok:
            record_event({
                "step_id": step_id,
                "value": repr(value),
                "reason": f"level 必须为 {HUMAN_OVERRIDE_MIN}..{HUMAN_OVERRIDE_MAX} 的整数",
                "valid_range": [HUMAN_OVERRIDE_MIN, HUMAN_OVERRIDE_MAX],
            })
            raise BioAuditError(
                ErrorCode.VALIDATION_ERROR,
                f"human_overrides[{step_id!r}] 非法: {value!r}"
                f"（必须为 {HUMAN_OVERRIDE_MIN}..{HUMAN_OVERRIDE_MAX} 的整数）",
                details={
                    "step_id": step_id,
                    "value": repr(value),
                    "valid_range": [HUMAN_OVERRIDE_MIN, HUMAN_OVERRIDE_MAX],
                },
            )
        validated[step_id] = value
    return validated


__all__ = [
    "HUMAN_OVERRIDE_MIN",
    "HUMAN_OVERRIDE_MAX",
    "VALID_PARADIGMS",
    "validate_paradigm",
    "TrajectoryPayload",
    "parse_trajectory_payload",
    "AuditDecisionRequest",
    "validate_human_overrides",
]
