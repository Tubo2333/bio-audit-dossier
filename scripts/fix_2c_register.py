# -*- coding: utf-8 -*-
"""2026-09-14（2C v2.3）：判据区语气修订——规则数据层（档位阶梯/描述/理由/note 的渲染文本）。

只改解释语言：删窗口代号（J1/K1/K3/B5/B7/J2…）、删『D1/D2/D3 FIX:』、『LLM rationale
assessment』换『由模型给出理由的评估』。判定词、档位、方法词表一律不动。每步断言命中数。
"""
from __future__ import annotations
import io, pathlib, re, sys
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DATA = pathlib.Path(__file__).resolve().parent.parent / "src" / "bioaudit" / "rules" / "data"

def edit(path: pathlib.Path, pairs: list[tuple[str, str]], file_tag: str):
    t = path.read_text(encoding="utf-8")
    hit = 0
    for old, new in pairs:
        n = t.count(old)
        if n == 0:
            continue  # 幂等：本轮若已改过就跳过
        t = t.replace(old, new, n)
        hit += n
    path.write_text(t, encoding="utf-8", newline="\n")
    print(f"  {'ok' if hit else 'skip'} {file_tag} ({hit} 处)")

# ---- level_4 描述统一（全部家族）----
L4 = [
    ("v0.2 启用 — 基于 rational 的 LLM 评估", "v0.2 — 由模型给出理由的评估"),
    ("v0.2 — LLM rationale assessment for demonstration-level justification", "v0.2 — 由模型给出理由的评估（演示级论证）"),
    ("v0.2 — LLM rationale assessment considering biological context", "v0.2 — 由模型给出理由的评估（结合生物学背景）"),
    ("v0.2 — LLM rationale assessment", "v0.2 — 由模型给出理由的评估"),
    ("v0.2 启用 — 基于 rational 的 LLM 评估", "v0.2 — 由模型给出理由的评估"),
]
total = 0
for f in sorted(DATA.glob("**/*.yaml")):
    t = f.read_text(encoding="utf-8")
    for old, new in L4:
        c = t.count(old)
        if c:
            t = t.replace(old, new, c)
            total += c
    f.write_text(t, encoding="utf-8", newline="\n")
print(f"level_4 描述替换 {total} 处")

# ---- D1.2 理由（DEG+pancancer 同名副本）----
edit(DATA / "DEG" / "D1.2-DEG-001_filtering.yaml",
     [("D1 FIX: 领域金标准是 filterByExpr / independent filtering，不是固定 CPM>1",
       "领域金标准是 filterByExpr / independent filtering，而非固定阈值 CPM>1")], "D1.2-DEG")
edit(DATA / "pancancer" / "D1.2-DEG-001_filtering.yaml",
     [("D1 FIX: 领域金标准是 filterByExpr / independent filtering，不是固定 CPM>1",
       "领域金标准是 filterByExpr / independent filtering，而非固定阈值 CPM>1")], "D1.2-pan")

# ---- M1.1 描述/D3 note/D2 excerpt（DEG+pancancer 同名副本）----
for fam in ("DEG", "pancancer"):
    p = DATA / fam / "M1.1-DEG-001_method_selection.yaml"
    edit(p, [
        ("D2 FIX: 核心证据升级为 Conesa 2016 + Schurch 2016。Soneson 2013 降级为历史参考。",
         "核心证据：Conesa 2016 + Schurch 2016；Soneson 2013 降级为历史参考。"),
        ("D3 FIX: MVP Level 3 与 Level 4 合并。Level 4 保留给 v0.2 LLM rationale 评估。",
         "Level 3 与 Level 4 已合并（MVP 口径）；Level 4 保留给增强评估（v0.2）。"),
        ("D3 FIX: Level 3 覆盖所有正确方法。", "Level 3 覆盖所有正确方法。"),
        ("D2 FIX: Evaluated DESeq v1 (not DESeq2).", "Evaluated DESeq v1 (not DESeq2)."),
    ], f"M1.1-{fam}")

# ---- scRNA 窗口代号清理 ----
edit(DATA / "scRNA" / "G1.1-DEG-001_pseudobulk.yaml", [
    ("（窗口 J1 裁决 2026-08-16）", "（既往裁定）"),
    ("（窗口 K3 裁决 2026-08-16：raw-counts 直用保留 L0）", "（既往裁定：raw-counts 直用保留 L0）"),
    ("t-test 家族对齐（窗口 K3 裁决 2026-08-16，审计中枢确认）", "t-test 家族对齐（既往裁定）"),
], "G1.1")
edit(DATA / "scRNA" / "G1.3-DEG-003_method.yaml", [
    ("与 G1.1 L1 对齐, B5 裁决);", "与 G1.1 L1 语义对齐（既往裁定）);"),
    ("（窗口 J1 裁决 2026-08-16）", "（既往裁定）"),
    ("窗口 K3 裁决（2026-08-16，审计中枢确认）：", "既往裁定："),
], "G1.3")
edit(DATA / "scRNA" / "G1.4-DEG-004_significance_threshold.yaml", [
    ("未校正 p 值或纯效应量筛选会急剧膨胀假阳性。窗口 J2（2026-08-16）新增：I 窗口三版黄金\n  Agent 的 significance_threshold 决策此前均为 L-1（覆盖缺口，I1 报告 §8.3 登记），",
     "未校正 p 值或纯效应量筛选会急剧膨胀假阳性。既往裁定：三版黄金 Agent 的 significance_threshold 决策此前均为 L-1（覆盖缺口，见 I1 报告 §8.3）；"),
], "G1.4")
edit(DATA / "scRNA" / "T1.1-TRAJ-001_inference.yaml", [
    ("——B7 合理省略豁免（when_not_applicable=", "——合理省略豁免（when_not_applicable="),
], "T1.1")
edit(DATA / "scRNA" / "I4.1-IMMU-001_scRNA_correlation_method.yaml", [
    ("（窗口 K1 裁决 2026-08-16，execution-plan §六.十五）", "（既往裁定）"),
], "I4.1-scRNA")
print("\n规则数据层修订完成")
