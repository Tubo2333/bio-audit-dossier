"""轨迹 v2 schema（B4，refactor-plan-v1.1 C2；audit-report A15）。

v1（隐式）：轨迹文件 = 决策数组 ``list[Decision]``，无版本/来源元数据。
v2（本 schema）：轨迹文件 = 对象：:

    {
      "version": 2,
      "trajectory_id": "deg_correct",
      "act": "deg",                        # 可选；范式（deg/pan/scrna）
      "provenance": {
        "source": "legacy",                # legacy | capture | manual
        "migrated_from": "deg_correct.json",
        "migrated_at": "2026-08-14T...",
        "migrator": "bioaudit.capture.trajectory_migrator",
        "note": "..."
      },
      "decisions": [ ... ]                 # 与 v1 决策内容一致
    }

关键不变量：``version``/``provenance``/``act`` 是纯元数据，**不参与评分**——
评分引擎只消费 ``decisions``（golden 回归保证：迁移后仍 0 差异）。
v2 必填字段：version / trajectory_id / provenance / decisions；缺失任一 →
:class:`bioaudit.errors.BioAuditError`（validation-error）显式报错（A15）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from bioaudit.errors import BioAuditError, validation_error
from bioaudit.models.decision import Decision
from bioaudit.paths import ACT_RULE_SUBDIRS

# ── schema 常量（B4 验收项 1：report/schema 常量定义；report/schema.py 再导出）──
TRAJECTORY_SCHEMA_VERSION = 2          # 当前轨迹 schema 版本（v2）
TRAJECTORY_SCHEMA_VERSION_STR = "2.0"
TRAJECTORY_SCHEMA_NAME = "trajectory.v2"
LEGACY_TRAJECTORY_VERSION = 1          # v1 = 裸决策数组（隐式版本）
PROVENANCE_SOURCE_LEGACY = "legacy"    # C2：旧轨迹来源标注

#: 合法范式集合（与 bioaudit.paths.ACT_RULE_SUBDIRS 同源）
VALID_PARADIGMS: frozenset[str] = frozenset(ACT_RULE_SUBDIRS)


class TrajectoryProvenance(BaseModel):
    """轨迹来源元数据（v1.1 C2：旧轨迹标 legacy/未验证）。"""

    source: str  # "legacy" | "capture"（阶段 2 M1/M3 落地）| "manual"
    migrated_from: Optional[str] = None   # 迁移来源文件名（legacy 迁移必有）
    migrated_at: Optional[str] = None     # ISO 时间戳
    migrator: Optional[str] = None        # 迁移器标识
    note: Optional[str] = None


class Trajectory(BaseModel):
    """轨迹 v2（元数据 + 决策列表；评分只消费 decisions）。

    version 为必填（无默认）：v2 轨迹缺 version 即显式报错（A15/C2）。
    """

    version: int
    trajectory_id: str
    act: Optional[str] = None  # 范式（deg/pan/scrna）；迁移器按文件名前缀推断
    provenance: TrajectoryProvenance
    decisions: list[Decision] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _version_supported(cls, v: int) -> int:
        if v != TRAJECTORY_SCHEMA_VERSION:
            raise ValueError(
                f"不支持的轨迹 schema version {v!r}（当前支持: {TRAJECTORY_SCHEMA_VERSION}）"
            )
        return v

    @field_validator("act")
    @classmethod
    def _act_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_PARADIGMS:
            raise ValueError(
                f"act 必须为 {sorted(VALID_PARADIGMS)} 之一，收到 {v!r}"
            )
        return v


def validate_trajectory(data: dict[str, Any]) -> Trajectory:
    """校验 v2 轨迹对象；缺必填字段/非法值 → BioAuditError(validation-error)。

    供迁移器、``bio-audit trajectory-validate`` 与 API 输入校验复用（A15：
    不再允许缺字段静默通过/静默降级）。
    """
    try:
        return Trajectory(**data)
    except BioAuditError:
        raise
    except Exception as exc:
        raise validation_error(
            "轨迹 schema 校验失败（v2 必填: version/trajectory_id/provenance/decisions）",
            exc,
        ) from exc


__all__ = [
    "Trajectory",
    "TrajectoryProvenance",
    "TRAJECTORY_SCHEMA_VERSION",
    "TRAJECTORY_SCHEMA_VERSION_STR",
    "TRAJECTORY_SCHEMA_NAME",
    "LEGACY_TRAJECTORY_VERSION",
    "PROVENANCE_SOURCE_LEGACY",
    "VALID_PARADIGMS",
    "validate_trajectory",
]
