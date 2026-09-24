"""应用 2026-09-13 领域复核的**文字更正**（不改任何判定/档位/方法列表）。

背景
----
人类逐条复核 DEG 族 14 条依据后，确认下列更正。**这些是"依据的引用文字更正"，
不是"判定变更"**：
- Schurch 2016 的 excerpt 把 n=3 的检出率写成 ~50%；原文摘要实测为 20–40%
  （>4 倍变化时 >85%）。数字改对，依据指向的档位不变。
- DEG / pancancer 的同名副本必须**同步改到逐字节相同**，否则
  `check_rule_lifecycle.py` 的 C2（同 rule_id 内容一致）会失败。

本脚本是**一次性工具**：把人类批准的替换写成显式字面量，逐处断言"必须命中"，
命中数不对就整体不写（避免半途而废的脏改）。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\apply_deg_review_corrections.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\apply_deg_review_corrections.py --apply
"""

from __future__ import annotations

import argparse
import io
import pathlib
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"

# (相对路径 或 一对路径, 原文, 新文, 期望命中次数)
# 同名副本一律成对处理，保证 C2 仍然逐字节相同。
CORRECTIONS: list[tuple[list[str], str, str, int]] = [
    # ---- 1. Schurch 2016 的 excerpt：n=3 检出率 50% → 20–40%（原文摘要实测）----
    (
        ["DEG/M1.1-DEG-001_method_selection.yaml",
         "pancancer/M1.1-DEG-001_method_selection.yaml"],
        "At n=3, sensitivity is ~50% for all methods. n>=6 recommended for adequate power.",
        "At n=3, nine of 11 tools found only 20-40% of SDE genes "
        "(>85% for genes changing >4-fold). n>=6 recommended for adequate power.",
        1,
    ),
    (
        ["DEG/M1.3-DEG-001_threshold.yaml",
         "pancancer/M1.3-DEG-001_threshold.yaml"],
        "At n=3, sensitivity is low across methods.",
        "At n=3, nine of 11 tools found only 20-40% of SDE genes; "
        "sensitivity stays low across methods.",
        1,
    ),
    # ---- 2. edgeR User's Guide：source_type 从共识指南改为既有先例 reference ----
    # math_theorem / reference 都是**不在 schema 词汇表里也已被用过**的先例；
    # 不改 schema、不加新公共语义（source_type 本就是自由字符串，只写在注释里）。
    (
        ["DEG/D1.2-DEG-001_filtering.yaml",
         "pancancer/D1.2-DEG-001_filtering.yaml"],
        '  - source_type: "consensus_guideline"\n'
        '    title: "edgeR User\'s Guide §2.5 Filtering"',
        '  - source_type: "reference"   # 软件手册，不是共识指南（2026-09-13 复核）\n'
        '    title: "edgeR User\'s Guide Filtering (filterByExpr)"',
        1,
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="应用 DEG 族复核的文字更正")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    plan: list[tuple[pathlib.Path, str]] = []
    failed = False

    for rels, old, new, expect in CORRECTIONS:
        # 先全组检查命中数，再决定是否写（避免只改一半导致同名副本分歧）
        group_ok = True
        for rel in rels:
            p = DATA / rel
            if not p.exists():
                print(f"FAIL 文件不存在：{rel}")
                failed = True
                group_ok = False
                continue
            text = p.read_text(encoding="utf-8")
            n = text.count(old)
            if n != expect:
                print(f"FAIL {rel}：期望命中 {expect} 处，实测 {n} 处")
                print(f"     查找：{old[:70]!r}")
                failed = True
                group_ok = False
        if not group_ok:
            continue
        for rel in rels:
            p = DATA / rel
            text = p.read_text(encoding="utf-8")
            plan.append((p, text.replace(old, new)))

    if failed:
        print("\n有更正未按预期命中 → 不改任何文件（避免改一半）。")
        return 1

    for p, new_text in plan:
        rel = p.relative_to(DATA)
        if args.dry_run:
            print(f"  [dry-run] 将改写 {rel}")
        else:
            p.write_text(new_text, encoding="utf-8", newline="\n")
            print(f"  已改写 {rel}")

    if args.dry_run:
        print(f"\ndry-run：共 {len(plan)} 个文件待改，未写盘。")
    else:
        print(f"\n已改写 {len(plan)} 个文件。")
        print("下一步**必须**：重跑清单生成器（显式递增 ruleset_version）→ "
              "check_rule_lifecycle.py → ui/verify_report.py → 全量 pytest。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
