"""重跑清单生成器（DP-* 纪律：改任何规则文件后必须重跑，且显式递增版本）。

**默认参数是 1.0.0**，不显式传会把版本降回去——所以这里把版本与说明都写死。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\regen_ruleset_manifest.py
"""

from __future__ import annotations

import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bioaudit.rules.manifest import generate_manifest  # noqa: E402

# 显式递增（1.8.9 → 1.8.10：2C 判据区语气修订——删窗口代号/FIX 前缀/rational 词）
RULESET_VERSION = "1.8.10"

NOTE = (
    "2026-09-14 待办 3/4 处置：rule-api-001 移除、Bonferroni 降级为说明、Hollander &"
    "Wolfe 更正为第 3 版并补 DOI；override_n2 空机制归零（3 补全 / 19 删键 / 2 无键）。"
    "2026-09-14（2C v2.3）：判据区语气修订——档位阶梯/描述/理由/note 删窗口代号"
    "（J1/K1/K3/B5/B7/J2）、删 D1/D2/D3 FIX 前缀、LLM rationale assessment 改"
    "「由模型给出理由的评估」。判定词与档位语义一律未改。"
)


def main() -> int:
    m = generate_manifest(ruleset_version=RULESET_VERSION, note=NOTE)
    print(f"ruleset_version = {m['ruleset_version']}")
    print(f"n_rule_files    = {m['n_rule_files']}")
    print(f"n_unique        = {m['n_unique_rule_ids']}")
    print(f"generated_at    = {m['generated_at']}")
    print("已写入 src/bioaudit/rules/ruleset.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
