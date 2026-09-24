"""P1 本体校验器三职责 CLI 入口（B2）。

用法：
  python scripts/validate_ontology.py
  python scripts/validate_ontology.py --json

逻辑在 bioaudit.ontology.validator（包内锚定，本脚本仅薄包装）。
退出码：0 = 校验完成（含 findings）；1 = 本体结构错误。
"""

import argparse
import sys
from pathlib import Path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from bioaudit.ontology.validator import main as validator_main

    return validator_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
