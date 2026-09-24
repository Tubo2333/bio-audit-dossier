"""轨迹迁移器（B4，refactor-plan-v1.1 C2）——**只读迁移器**。

职责：输入 v1 旧轨迹（裸决策数组）→ 输出 v2 轨迹对象（version/provenance
元数据），**绝不修改原文件**；原 v1 文件原位保留（即备份）。

- 迁移 = 包一层元数据：``decisions`` 内容逐条保留（语义不变），因此
  **迁移后 golden 重放仍 0 差异**（version/provenance 是元数据，不参与评分）；
- 输出默认写入 ``bioaudit.paths.TRAJECTORIES_DIR``（data/trajectories/v2/），
  与旧文件（data/trajectories/，TRAJECTORIES_LEGACY_DIR）分目录共存；
- 输入若已是 v2 对象 → 仅校验（幂等），不重复包装；
- 每个迁移/校验步骤都过 ``validate_trajectory``（A15：v2 缺必填字段显式报错）。

CLI：``bio-audit migrate-trajectories [--dry-run]``；
脚本：``python scripts/migrate_trajectories.py``。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bioaudit.models.trajectory import (
    PROVENANCE_SOURCE_LEGACY,
    TRAJECTORY_SCHEMA_VERSION,
    VALID_PARADIGMS,
    validate_trajectory,
)
from bioaudit.paths import TRAJECTORIES_DIR, TRAJECTORIES_LEGACY_DIR

MIGRATOR_ID = "bioaudit.capture.trajectory_migrator"


def infer_act(name: str) -> Optional[str]:
    """按轨迹文件名前缀推断范式（deg_/pan_/scrna_）；无法推断 → None。"""
    prefix = name.split("_", 1)[0]
    return prefix if prefix in VALID_PARADIGMS else None


class TrajectoryMigrator:
    """只读迁移器：src（v1 旧轨迹目录）→ dst（v2 目录），不写 src 任何文件。"""

    def __init__(
        self,
        src_dir: Optional[str | Path] = None,
        dst_dir: Optional[str | Path] = None,
        migrated_at: Optional[str] = None,
    ):
        self.src_dir = Path(src_dir) if src_dir else TRAJECTORIES_LEGACY_DIR
        self.dst_dir = Path(dst_dir) if dst_dir else TRAJECTORIES_DIR
        self.migrated_at = migrated_at or datetime.now(timezone.utc).isoformat()

    # ── 单条迁移（纯函数：读 src、产 v2 对象，不写盘）──

    def build_v2(self, src: Path) -> dict[str, Any]:
        """读一条 v1（或 v2）轨迹文件 → v2 对象（只读，不写盘）。"""
        if not src.is_file():
            raise FileNotFoundError(f"轨迹文件不存在: {src}")
        data = json.loads(src.read_text(encoding="utf-8"))

        # 幂等：已是 v2 对象 → 仅校验（不重复包装）
        if isinstance(data, dict) and "version" in data:
            validate_trajectory(data)
            return data

        if isinstance(data, list):
            decisions = data
        elif isinstance(data, dict) and "decisions" in data:
            decisions = data["decisions"]
        else:
            raise ValueError(
                f"{src.name}: 轨迹必须是决策数组或含 decisions 键的对象"
            )

        name = src.stem
        v2: dict[str, Any] = {
            "version": TRAJECTORY_SCHEMA_VERSION,
            "trajectory_id": name,
            "act": infer_act(name),
            "provenance": {
                "source": PROVENANCE_SOURCE_LEGACY,   # C2: 旧轨迹标 legacy
                "migrated_from": src.name,
                "migrated_at": self.migrated_at,
                "migrator": MIGRATOR_ID,
                "note": "v1 → v2 迁移：新增 version/provenance/act 元数据；"
                        "decisions 内容逐条保留，不参与评分（golden 0 差异不变量）",
            },
            "decisions": decisions,
        }
        validate_trajectory(v2)  # A15: 迁移产物必须过 schema 校验
        return v2

    # ── 批量迁移 ──

    def migrate_all(self, dry_run: bool = False) -> list[dict]:
        """迁移 src 目录全部轨迹（*.json）→ dst 目录。

        - 只写 dst（dry_run=True 时完全不写盘）；src 文件保持字节不变；
        - 返回迁移清单（B4 验收项 6 的数据源）：每条含
          source / target / version / provenance / n_decisions。
        """
        src_files = sorted(self.src_dir.glob("*.json"))
        if not src_files:
            raise FileNotFoundError(f"src 目录无轨迹文件: {self.src_dir}")

        rows: list[dict] = []
        for src in src_files:
            v2 = self.build_v2(src)
            target = self.dst_dir / f"{src.stem}.json"
            row = {
                "source": str(src.relative_to(self.src_dir)) if src.is_relative_to(self.src_dir) else src.name,
                "target": str(target.relative_to(self.dst_dir)) if target.is_relative_to(self.dst_dir) else target.name,
                "version": v2["version"],
                "provenance": v2["provenance"],
                "n_decisions": len(v2["decisions"]),
                "act": v2.get("act"),
                "written": False,
            }
            if not dry_run:
                self.dst_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(v2, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                row["written"] = True
            rows.append(row)
        return rows


__all__ = ["TrajectoryMigrator", "MIGRATOR_ID", "infer_act"]
