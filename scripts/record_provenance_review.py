"""按单条依据写入复核结论（reviewed_at / reviewed_by / review_kind）。

**仅供窗口在人类逐条确认后调用。** 它不是无人值守的批量改写工具：
必须显式给出要写的 (规则文件, 复核日期, 复核人, review_kind, 说明)。

**本脚本绝不改判定、档位、方法列表、依据内容、标题或 identifier** ——
只写 `provenance` 块里的三个复核字段。这是"档位只告知、不判定"之外的
另一条边界：复核改的是**这条依据有没有被看过**，不是**它判什么**。

用法（在仓库根目录）：

    .\\.venv\\Scripts\\python.exe scripts\\record_provenance_review.py ^
        --date 2026-09-14 --reviewer "<署名>" --kind literature_check ^
        --note "人类逐条确认仍成立" ^
        --rule DEG/D1.2-DEG-001_filtering.yaml ^
        --rule DEG/D1.3-DEG-001_normalization.yaml

不加 `--rule` 时只**报告**将要改哪些文件（dry-run），不改任何东西。
"""

from __future__ import annotations

import argparse
import io
import pathlib
import re
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"

VALID_KINDS = ("none", "authoring", "domain_review", "literature_check", "correction")

# 只碰这三行；其余一律不动
FIELD_RE = {
    "reviewed_at": re.compile(r"^(\s*)reviewed_at:.*$", re.M),
    "reviewed_by": re.compile(r"^(\s*)reviewed_by:.*$", re.M),
    "review_kind": re.compile(r"^(\s*)review_kind:.*$", re.M),
}


def patch_text(text: str, date: str, reviewer: str, kind: str, note: str) -> tuple[str, list[str]]:
    """只替换 provenance 块里的三行。返回 (新文本, 改了哪些行)。"""
    changed: list[str] = []
    comment = f"  # {note}" if note else ""

    new = FIELD_RE["reviewed_at"].sub(rf'\1reviewed_at: "{date}"{comment}', text, count=1)
    if new != text:
        changed.append("reviewed_at")
    text = new

    new = FIELD_RE["reviewed_by"].sub(rf'\1reviewed_by: "{reviewer}"', text, count=1)
    if new != text:
        changed.append("reviewed_by")
    text = new

    new = FIELD_RE["review_kind"].sub(rf'\1review_kind: "{kind}"', text, count=1)
    if new != text:
        changed.append("review_kind")
    text = new

    return text, changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="记录规则依据的人工复核（只写 provenance 三字段）")
    ap.add_argument("--date", required=True, help="复核当天日期 YYYY-MM-DD（不是生成日）")
    ap.add_argument("--reviewer", required=True, help="复核人署名")
    ap.add_argument("--kind", required=True, choices=VALID_KINDS)
    ap.add_argument("--note", default="", help="写进 reviewed_at 行尾注释的一句话说明")
    ap.add_argument("--rule", action="append", default=[],
                    help="相对 rules/data 的规则文件路径（可重复）；缺省 = dry-run")
    ap.add_argument("--apply", action="store_true", help="真正写盘（缺省为 dry-run）")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print(f"FAIL 日期必须是 YYYY-MM-DD：{args.date!r}")
        return 1

    targets = [DATA / r for r in args.rule]
    for t in targets:
        if not t.exists():
            print(f"FAIL 规则文件不存在：{t}")
            return 1

    if not targets:
        print("dry-run：未指定 --rule，未改任何文件。")
        print("（要写入请加 --rule <相对路径> --apply）")
        return 0

    for t in targets:
        original = t.read_text(encoding="utf-8")
        updated, changed = patch_text(original, args.date, args.reviewer, args.kind, args.note)
        rel = t.relative_to(DATA)
        if not changed:
            print(f"  跳过 {rel}（没有可写的 provenance 字段）")
            continue
        if not args.apply:
            print(f"  [dry-run] 将改 {rel}：{changed}")
            continue
        # 保留行尾约定：显式 newline="\n"（规则文件是 LF）
        t.write_text(updated, encoding="utf-8", newline="\n")
        print(f"  已写 {rel}：{changed}")

    if not args.apply:
        print("\ndry-run 结束，未改任何文件。确认无误后加 --apply。")
        return 0

    print("\n注意：改完规则文件**必须**重跑清单生成器并显式递增 ruleset_version，")
    print("然后跑 scripts\\check_rule_lifecycle.py 确认 C1/C4。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
