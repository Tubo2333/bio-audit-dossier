"""把 edgeR User's Guide 的节号改对，并补上持久 URL（人眼核对结果落盘）。

人类打开现行 PDF 核对后给出的事实：
- §2.5 = Pseudoalignment and selective alignment
- §2.6 = The DGEList data class
- **§2.7 = Filtering**            ← 本规则真正要引的那一节
- §2.8 = Normalization
所以文件里原先写的「§2.5」是错的（那一节讲的是 kallisto/Salmon 伪比对）。

同时补 `url`（库内先例：cBioPortal API 文档、GSEA User Guide、BH 1995 均带 `url`），
因为「edgeR User's Guide」这份文档没有 DOI/PMID，没有 URL 就无法持久指认。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\fix_edger_section_and_url.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\fix_edger_section_and_url.py --apply
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

URL = ("https://bioconductor.org/packages/release/bioc/vignettes/"
       "edgeR/inst/doc/edgeRUsersGuide.pdf")

OLD = ('  - source_type: "reference"   # 软件手册，不是共识指南（2026-09-13 复核）\n'
       '    title: "edgeR User\'s Guide Filtering (filterByExpr)"\n')

NEW = ('  - source_type: "reference"   # 软件手册，不是共识指南（2026-09-13 复核）\n'
       '    url: "' + URL + '"\n'
       '    title: "edgeR User\'s Guide §2.7 Filtering (filterByExpr)"   '
       '# 节号经人眼核对现行 PDF（2026-09-13）：§2.5 是伪比对、§2.6 是 DGEList，Filtering 是 §2.7\n')

# 同名副本必须一起改（C2 要求逐字节一致）
TARGETS = ["DEG/D1.2-DEG-001_filtering.yaml",
           "pancancer/D1.2-DEG-001_filtering.yaml"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    # 先全部核对命中数，任一不符就整体不写（避免只改一半导致 C2 分歧）
    for rel in TARGETS:
        p = DATA / rel
        if not p.exists():
            print(f"FAIL 文件不存在：{rel}")
            return 1
        n = p.read_text(encoding="utf-8").count(OLD)
        if n != 1:
            print(f"FAIL {rel}：期望命中 1 处，实测 {n} 处")
            return 1

    for rel in TARGETS:
        p = DATA / rel
        text = p.read_text(encoding="utf-8")
        if args.dry_run:
            print(f"  [dry-run] 将改写 {rel}（节号 → §2.7，补 url）")
        else:
            p.write_text(text.replace(OLD, NEW), encoding="utf-8", newline="\n")
            print(f"  已改写 {rel}")

    if args.dry_run:
        print("\ndry-run：未写盘。")
    else:
        print("\n下一步：regen_ruleset_manifest → 行尾归一 LF → check_rule_lifecycle → "
              "prove_citations_only → refresh_golden_baseline → 重生成页面 → pytest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
