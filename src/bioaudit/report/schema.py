"""报告/轨迹 schema 常量（B4 验收项 1：report/schema 常量定义）。

- 轨迹 schema 常量的事实源在 ``bioaudit.models.trajectory``；
  本模块再导出（report 包视角的 schema 常量入口），保持单一事实源。
- ``REPORT_SCHEMA_VERSION`` 与 report/__init__.py 保持一致（C3：报告 schema 迁移策略）。
"""

from bioaudit.models.trajectory import (
    LEGACY_TRAJECTORY_VERSION,
    PROVENANCE_SOURCE_LEGACY,
    TRAJECTORY_SCHEMA_NAME,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_STR,
)

REPORT_SCHEMA_VERSION = "1.0"  # C3：报告 schema 版本（major 破坏性变更策略见 refactor-plan-v1.1）

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "TRAJECTORY_SCHEMA_VERSION",
    "TRAJECTORY_SCHEMA_VERSION_STR",
    "TRAJECTORY_SCHEMA_NAME",
    "LEGACY_TRAJECTORY_VERSION",
    "PROVENANCE_SOURCE_LEGACY",
]
