"""M1.3-DEG-001 的 ENCODE 假归因 → 改挂到 Human Genomics 2026 20:63（一次性变更）。

背景与证据（全部可核）：
- 原依据标题「ENCODE RNA-seq standards」**不存在**为任何 ENCODE 文档名（子代理 C，
  且 2026-09-14 实测 encodeproject.org 对自动访问回 405，无法读原文）。
- 三份候选中，Human Genomics 2026;20:63（Wang & Gao，DOI 10.1186/s40246-026-00930-1，
  Springer/BMC 同行评审正刊）正文**字面支撑**那句：
  「we recommend reporting results from both (A) statistical and (B) effect-size
   perspectives ... report adjusted p-values (FDR) ... report fold change as log2FC」
  —— 从 PDF 抽取的原文，第一人称 we recommend，非转引。
- 置信档取 L-Evidenced（有直接原文依据；单篇建议、2026 年发表，故不给 L-Consensus）。
  这是**保守取值**，如人类觉得应升 L-Consensus 可再改。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\fix_m13_encode_attribution.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\fix_m13_encode_attribution.py --apply
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

OLD = (
    '  - source_type: "consensus_guideline"\n'
    '    title: "ENCODE RNA-seq standards"\n'
    '    confidence: "L-Consensus"\n'
    '    excerpt: "Differential expression should be reported with both adjusted p-value and fold change."\n'
    '    supports_levels: ["level_3"]\n'
)

NEW = (
    '  - source_type: "method_paper"\n'
    '    doi: "10.1186/s40246-026-00930-1"\n'
    '    url: "https://doi.org/10.1186/s40246-026-00930-1"\n'
    '    title: "Wang & Gao (2026) Human Genomics 20:63 — pipeline DE reporting recommendations"\n'
    '    confidence: "L-Evidenced"\n'
    '    excerpt: "we recommend reporting results from both (A) statistical and (B) effect-size '
    'perspectives... report adjusted p-values (FDR)... report fold change as log2FC"\n'
    '    supports_levels: ["level_3"]\n'
    '    # 2026-09-14 核查：原归因「ENCODE RNA-seq standards」不是任何 ENCODE 文档名（404/无此标题），\n'
    '    # 改挂本文（正文原话为 we recommend 第一人称建议；DOI/URL 见上）。档位与判定未动。\n'
)

TARGETS = ["DEG/M1.3-DEG-001_threshold.yaml",
           "pancancer/M1.3-DEG-001_threshold.yaml"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

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
            print(f"  [dry-run] 将改写 {rel}")
        else:
            p.write_text(text.replace(OLD, NEW), encoding="utf-8", newline="\n")
            print(f"  已改写 {rel}")

    if args.dry_run:
        print("\ndry-run：未写盘。")
    else:
        print("\n下一步：regen_ruleset_manifest（显式递增）→ 行尾归一 LF → check_rule_lifecycle → "
              "prove_citations_only → refresh_golden_baseline → 重生成页面/交付文档 → 全量测试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())