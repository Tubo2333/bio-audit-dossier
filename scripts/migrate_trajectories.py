"""B4 轨迹迁移入口（薄包装）：v1 旧轨迹 → v2（version/provenance 元数据）。

用法：
  python scripts/migrate_trajectories.py [--src-dir data/trajectories]
                                        [--dst-dir data/trajectories/v2]
                                        [--dry-run]

迁移器只读：绝不修改 src 目录任何文件（原 v1 文件保留 = 备份）。
逻辑在 bioaudit.capture.trajectory_migrator（包内锚定，本脚本仅薄包装）。
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-dir", default=None, help="v1 旧轨迹目录（默认包内 data/trajectories）")
    parser.add_argument("--dst-dir", default=None, help="v2 输出目录（默认包内 data/trajectories/v2）")
    parser.add_argument("--dry-run", action="store_true", help="只生成迁移清单，不写盘")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from bioaudit.capture.trajectory_migrator import TrajectoryMigrator

    migrator = TrajectoryMigrator(
        src_dir=Path(args.src_dir) if args.src_dir else None,
        dst_dir=Path(args.dst_dir) if args.dst_dir else None,
    )
    rows = migrator.migrate_all(dry_run=args.dry_run)

    import json

    print(json.dumps({
        "dry_run": args.dry_run,
        "src_dir": str(migrator.src_dir),
        "dst_dir": str(migrator.dst_dir),
        "n_migrated": len(rows),
        "trajectories": rows,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
