"""把 DEG 侧已登记的复核字段同步到 pancancer 同名副本（C2 要求逐字节一致）。

背景：`check_rule_lifecycle.py` 的 C2 要求同 rule_id 的多份文件**逐字节相同**。
用 `record_provenance_review.py` 只写了 DEG 侧时，C2 立刻报分歧——这正是它存在的意义。
本脚本把 DEG 侧文件原样同步到 pancancer 侧，保证两侧仍然逐字节相同。

**配对不再手写**：以前这里是一份手写的 `PAIRS` 清单，结果登记 D1.2 时
**清单里没有 D1.2**，同步被静默跳过（幸好 C2 兜住了）。现在改为**每次运行时从规则目录
按 `rule_id` 现场分组**：同一 rule_id 出现在多个家族目录下，就是一对。清单不可能再过期。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\sync_provenance_to_pancancer.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\sync_provenance_to_pancancer.py --apply
"""

from __future__ import annotations

import argparse
import collections
import io
import pathlib
import re
import sys

import yaml

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"
SOURCES = ("DEG", "pancancer")   # 以 DEG 为准，同步到 pancancer


def discover_pairs() -> list[tuple[str, str]]:
    """按 rule_id 现场分组，返回 (DEG 侧, pancancer 侧) 相对路径对。"""
    by_id: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for f in sorted(DATA.rglob("*.yaml")):
        rel = f.relative_to(DATA)
        family = rel.parts[0]
        if family not in SOURCES:
            continue
        m = re.search(r"^rule_id:\s*[\"']?([^\"'\n]+)", f.read_text(encoding="utf-8"), re.M)
        if not m:
            continue
        by_id[m.group(1).strip()][family] = str(rel).replace("\\", "/")

    pairs = []
    for rid, fams in sorted(by_id.items()):
        if all(s in fams for s in SOURCES):
            pairs.append((fams["DEG"], fams["pancancer"]))
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    pairs = discover_pairs()
    print(f"现场发现的同名副本对：{len(pairs)} 对（按 rule_id 从规则目录分组，非手写清单）")

    for src_rel, dst_rel in pairs:
        src, dst = DATA / src_rel, DATA / dst_rel
        if not src.exists() or not dst.exists():
            print(f"FAIL 文件不存在：{src_rel} 或 {dst_rel}")
            return 1
        # 直接整文件复制：C2 要求的是"逐字节相同"，不是"某几行相同"
        before = dst.read_bytes()
        after = src.read_bytes()
        if before == after:
            print(f"  已一致，跳过 {dst_rel}")
            continue
        if args.dry_run:
            print(f"  [dry-run] 将用 {src_rel} 覆盖 {dst_rel}"
                  f"（{len(before)} → {len(after)} 字节）")
        else:
            dst.write_bytes(after)
            print(f"  已同步 {dst_rel} ← {src_rel}（{len(before)} → {len(after)} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
