# -*- coding: utf-8 -*-
"""2026-09-14 人类裁定执行：待办 3（出处处置）+ 待办 4（override_n2 处置）。

只改规则库数据 + 文件内注释；不改任何判定逻辑。每步都有断言，跑之前/之后
对不上就 FAIL（与项目一改就 FAIL 的纪律一致）。可重跑：幂等（第二次跑各步
的替换目标已不存在 → 会 FAIL 提示已执行过）。
"""
from __future__ import annotations
import io, pathlib, sys, yaml

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = pathlib.Path(__file__).resolve().parent.parent / "src" / "bioaudit" / "rules" / "data"
Y = lambda p: p.read_text(encoding="utf-8")
W = lambda p, t: p.write_text(t, encoding="utf-8", newline="\n")


def read_yaml(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def state_of(sc):
    ov = (sc or {}).get("override_n2")
    if ov is None:
        return "nokey"
    if not ov:
        return "empty"
    return "cond"


# =====================================================================
# 待办 4：override_n2（先动，生成器/测试按新状态重写）
# =====================================================================
scrna = sorted((DATA / "scRNA").glob("*.yaml"))
before = {p.stem: state_of(read_yaml(p).get("scoring")) for p in scrna}
empties = [k for k, v in before.items() if v == "empty"]
print("改前 scRNA 空机制:", len(empties))
assert len(empties) == 21, f"期望 21 条空；实测 {len(empties)}"

FILL_BLOCK = '''  override_n2:
    condition: "n_patients <= 2"
    all_methods: "level_0"
    note: "n<=2 时无法可靠估计生物学变异。降级为仅报告 fold change，不做统计推断。（2026-09-14 处置：沿用 M1.1-DEG-001 整句；触发键按 scRNA 观察单位取 n_patients；出处同 Schurch et al. 2016, PMID 27022035，即 M1.1 支持 override_n2 的那条依据）"
'''

# 组 A：补全机制（沿用 M1.1）
for rid in ["G1.2-DEG-002_multiple_testing", "G1.3-DEG-003_method"]:
    p = DATA / "scRNA" / f"{rid}.yaml"
    t = Y(p)
    assert "  override_n2: {}\n" in t, f"{rid} 缺空 override_n2 行"
    t = t.replace("  override_n2: {}\n", FILL_BLOCK, 1)
    W(p, t)
    print("组A 补全:", rid)

# 组 B：G1.1 补后果与说明
p = DATA / "scRNA" / "G1.1-DEG-001_pseudobulk.yaml"
t = Y(p)
old = '  override_n2:\n    condition: "n_patients <= 2"\n'
assert old in t, "G1.1 override 块文本不符"
t = t.replace(old, old.rstrip("\n") + '\n    all_methods: "level_0"\n    note: "n<=2 时无法可靠估计生物学变异。降级为仅报告 fold change，不做统计推断。（2026-09-14 处置：与 M1.1-DEG-001 对齐补齐后果与说明；触发键保持 n_patients）"\n', 1)
W(p, t)
print("组B 补全: G1.1")

# 组 C/D/E：删空键（19 条）
KEEP_EMPTY = {"G1.2-DEG-002_multiple_testing", "G1.3-DEG-003_method"}
deleted = []
for rid in empties:
    if rid in KEEP_EMPTY:
        continue
    p = DATA / "scRNA" / f"{rid}.yaml"
    t = Y(p)
    assert "  override_n2: {}\n" in t, f"{rid} 空 override_n2 行不在"
    W(p, t.replace("  override_n2: {}\n", "", 1))
    deleted.append(rid)
print(f"组C/D/E 删空键: {len(deleted)} 条")

# 改后核对
after = {p.stem: state_of(read_yaml(p).get("scoring")) for p in scrna}
assert sum(1 for v in after.values() if v == "empty") == 0, "改后仍有空机制"
assert sorted(k for k, v in after.items() if v == "cond") ==        ["G1.1-DEG-001_pseudobulk", "G1.2-DEG-002_multiple_testing", "G1.3-DEG-003_method"], "有条件集合不符"
assert sum(1 for v in after.values() if v == "nokey") == 21, "无键数不符"

# =====================================================================
# 待办 3：出处处置
# =====================================================================
# 1) rule-api-001（pancancer G3.1）：删除该 evidence 行，原地留注释
p = DATA / "pancancer" / "G3.1-GENE-001_cbioportal_projection.yaml"
t = Y(p)
old = '''  - source_type: reference
    title: "rule-api-001: cBioPortal — always use projection=DETAILED"
    confidence: L-Confirmed
    excerpt: "SUMMARY mode returns hugoGeneSymbol as '?'. Use projection=DETAILED on all cBioPortal endpoints."
    supports_levels: ["level_3", "level_0"]
'''
assert old in t, "rule-api-001 证据块文本不符"
new = '''  # 2026-09-14 处置（人类裁定）：原证据条目「rule-api-001: cBioPortal —
  # always use projection=DETAILED」是项目内部规则名、不是外部出处——已从
  # evidence 移除。该口径由上面 cBioPortal 官方文档依据覆盖，并继续作为
  # 项目约定（评级语义不变）。
'''
W(p, t.replace(old, new, 1))
print("todo3 删除 rule-api-001 证据条目 (G3.1)")

# 2) Bonferroni（DEG + pancancer M1.2）：降级为说明
for fam in ["DEG", "pancancer"]:
    p = DATA / fam / "M1.2-DEG-001_multiple_testing.yaml"
    t = Y(p)
    old = '''  - source_type: "math_theorem"
    title: "Multiple testing problem — Bonferroni inequality"
    confidence: "L-Confirmed"
    excerpt: "With 20,000 independent tests at α=0.05, ~1,000 false positives expected under the global null."
'''
    assert old in t, f"{fam} M1.2 Bonferroni 块文本不符"
    new = '''  - source_type: "math_theorem"
    title: "Bonferroni inequality — mathematical fact (not a publication)"   # 2026-09-14 处置：原条目标题并非一篇文献；按「降级为说明」处理，档位依据不变（DEG 族已复核仍成立）
    confidence: "L-Confirmed"
    excerpt: "Arithmetic fact: for n independent tests at level α, the expected number of false positives under the global null is n×α (n=20,000, α=0.05 → ~1,000)。该陈述是数学定理，不存在对应文献条目；本条保留为说明，不冒充出处。"
'''
    W(p, t.replace(old, new, 1))
    print("todo3 Bonferroni 降级为说明:", fam, "M1.2")

# 3) Hollander & Wolfe（pancancer + scRNA I4.1）：更正版次 + 补 DOI/URL
for fam, rid in [("pancancer", "I4.1-IMMU-001_correlation_method"),
                 ("scRNA", "I4.1-IMMU-001_scRNA_correlation_method")]:
    p = DATA / fam / f"{rid}.yaml"
    t = Y(p)
    old = '''  - source_type: method_paper
    title: "Nonparametric Statistical Methods, 3rd ed. — Hollander & Wolfe 1999, Wiley"
    confidence: L-Confirmed
    excerpt: "Spearman rank correlation is robust to non-normality and outliers, making it preferred for non-normal biological data."
    supports_levels: ["level_3"]
'''
    assert old in t, f"{rid} H&W 块文本不符"
    new = '''  - source_type: method_paper
    doi: "10.1002/9781119196037"
    url: "https://onlinelibrary.wiley.com/doi/book/10.1002/9781119196037"
    title: "Nonparametric Statistical Methods (3rd ed.) — Hollander, Wolfe & Chicken 2014, Wiley"   # 2026-09-14 处置：原「3rd ed. — 1999」自相矛盾（第 3 版 2014，1999 是第 2 版）；现改挂现行第 3 版并补 DOI/URL（教科书无 PMID 属正常）
    confidence: L-Confirmed
    excerpt: "Spearman rank correlation is robust to non-normality and outliers, making it preferred for non-normal biological data."
    supports_levels: ["level_3"]
'''
    W(p, t.replace(old, new, 1))
    print("todo3 H&W 更正+补标识:", fam, rid)

print("\n全部规则改动完成，核对通过。")
