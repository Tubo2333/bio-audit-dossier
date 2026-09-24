"""golden 回归入口：复算 20 轨迹 137 决策并与冻结基线 diff。

用法：
  python scripts/golden_replay.py
  python scripts/golden_replay.py --baseline D:\\C-file\\docs\\specs\\2026-08-13-golden-baseline\\golden_expected_output_after.json

逻辑在 bioaudit.regression（包内锚定，本脚本仅薄包装）。
退出码：0 = 0 差异；1 = 存在差异。
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        default=None,
        help="基线 JSON 路径（默认: 仓库内 tests/golden/golden_expected_output_after.json）",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from bioaudit.regression import replay_golden

    ok, summary = replay_golden(Path(args.baseline) if args.baseline else None)
    import json
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if not ok:
        print("\n❌ GOLDEN DIFF: 存在差异，分数漂移必须逐条解释（v1.1 C4/H1）。")
        return 1
    print("\n✅ GOLDEN OK: 0 差异（20 轨迹 137 决策）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
