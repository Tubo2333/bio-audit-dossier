# -*- coding: utf-8 -*-
"""override_n2 处置记录与现状核对（DP-OVERRIDE-N2-REVIEW-PACKET）。

历史
----
2026-09-14 之前：scRNA 家族 21 条规则的 scoring.override_n2 是空壳（键在、内容 {}），
机制定义了、内容没填。当时的产物是「分组材料」，只提问、不给答案。

2026-09-14 人类裁定并执行（见本文件 §二）：
- 组 A（G1.2 / G1.3）补全：沿用 M1.1-DEG-001 整句，触发键 n_patients；
- 组 B（G1.1）补全：补 all_methods + note；
- 组 C/D/E 共 19 条：删除空机制（n_patients <= 2 对注释/批次/预处理/一致性规则不成立）；
- 组 F 的 2 条：维持「无此键」（正确状态）。
空机制自此归零。

本脚本现在的职责
----------------
把「现在是什么状态」从规则文件读出来，并与裁定后的期望逐条核对：
- 任何地方再出现空 override_n2 → FAIL（这是它存在的理由）；
- 每组的实测状态必须等于裁定后的期望状态 → 对不上就 FAIL，不出过期材料。

产物
----
- DP-OVERRIDE-N2-REVIEW-PACKET.md   —— 处置记录 + 现状核对表
- DP-OVERRIDE-N2-REVIEW-PACKET.csv  —— 同样内容，Excel 可开（带 BOM）

本脚本不做的事：不改任何规则文件；不新增产品语义/状态域/判定词/lane 值/分数/权威路径。
"""

from __future__ import annotations

import csv
import glob
import io
import pathlib
import re
import sys

import yaml

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"
MD_OUT = REPO / "DP-OVERRIDE-N2-REVIEW-PACKET.md"
CSV_OUT = REPO / "DP-OVERRIDE-N2-REVIEW-PACKET.csv"

RULING_DATE = "2026-09-14"

# 人类裁定（2026-09-14）。组是当时的分组，处置是裁定的结果。
GROUPS: list[dict] = [
    {"id": "A", "name": "DEG 家族：触发条件与后果直接沿用 M1.1",
     "ruling": "补全（沿用 M1.1-DEG-001 整句；触发键 n_patients）", "want_state": "cond",
     "why": "判的是用哪个方法做差异表达 / 怎么校正多重检验；M1.1 的理由句是统计学的，不是 bulk 专属。",
     "rules": ["G1.2-DEG-002_multiple_testing", "G1.3-DEG-003_method"]},
    {"id": "B", "name": "G1.1：原缺「后果」那一半",
     "ruling": "补全（补 all_methods + note，与 M1.1 对齐；触发键保持 n_patients）", "want_state": "cond",
     "why": "原有 condition 是活的（引擎真的解析它），缺的是读者能看到的「降到哪、为什么」。",
     "rules": ["G1.1-DEG-001_pseudobulk"]},
    {"id": "C", "name": "注释 / 批次整合：触发条件不适用",
     "ruling": "删除空机制", "want_state": "nokey",
     "why": "注释靠 marker 表达、批次校正靠样本间结构，可靠性不由「有几个患者」决定。",
     "rules": ["A1.1-ANNO-001_method", "A1.2-ANNO-002_marker_validation",
               "B1.1-BATC-001_integration", "B1.2-BATC-002_requirement"]},
    {"id": "D", "name": "预处理 / 聚类 / 降维：触发量不是「患者数」",
     "ruling": "删除空机制", "want_state": "nokey",
     "why": "质量取决于细胞数、测序深度、平台，不取决于患者数。",
     "rules": ["Q1.1-QC-001", "Q1.2-QC-002", "D1.1-DOUB-001", "N1.1-NORM-001",
               "H1.1-HVG-001", "C1.1-CLUS-001_method", "C1.2-CLUS-002_resolution",
               "D2.1-DIMR-001_reduction", "D2.2-DIMR-002_pca_dimension",
               "T1.1-TRAJ-001_inference", "A1.1-API-001_data_integrity"]},
    {"id": "E", "name": "跨模块一致性：没有「方法」可降",
     "ruling": "删除空机制", "want_state": "nokey",
     "why": "这几条是对别处结论的交叉核对，不是方法选择。",
     "rules": ["S1.1-CONS-001_cluster_annotation", "S1.2-CONS-002_annotation_deg",
               "S1.3-CONS-003_trajectory_annotation", "T1.2-TRAJ-002_annotation_precondition"]},
    {"id": "F", "name": "从未有过此键的两条",
     "ruling": "维持无键（确认这是正确状态）", "want_state": "nokey",
     "why": "不是「定义了没填」，而是「没定义」——与 A–E 不同类。",
     "rules": ["G1.4-DEG-004_significance_threshold",
               "I4.1-IMMU-001_scRNA_correlation_method"]},
]

YEAR_RE = re.compile(r"(19|20)[0-9]{2}")


def read_yaml(p: pathlib.Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def index_rules() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(DATA / "**" / "*.yaml"), recursive=True)):
        p = pathlib.Path(f)
        d = read_yaml(p)
        rid = d.get("rule_id")
        if rid and rid not in out:
            out[rid] = {"path": p, "doc": d, "rel": str(p.relative_to(DATA)).replace("\\", "/")}
    return out


def state_of(doc: dict) -> str:
    sc = doc.get("scoring") or {}
    if "override_n2" not in sc:
        return "nokey"
    return "cond" if sc.get("override_n2") else "empty"


def tier_counts(doc: dict) -> dict[str, int]:
    sc = doc.get("scoring") or {}
    out = {}
    for lvl in ("level_4", "level_3", "level_2", "level_1", "level_0"):
        blk = sc.get(lvl) or {}
        out[lvl] = len(blk.get("methods") or []) if isinstance(blk, dict) else len(blk or [])
    return out


def evidence_summary(doc: dict) -> tuple[int, str]:
    ev = doc.get("evidence") or []
    yrs = []
    for e in ev:
        m = YEAR_RE.search(e.get("title", "") or "")
        if m:
            yrs.append(int(m.group(0)))
    return len(ev), ("—" if not yrs else f"{min(yrs)}–{max(yrs)}")


def main() -> int:
    rules = index_rules()
    scrna = sorted(rid for rid, i in rules.items() if i["rel"].startswith("scRNA/"))
    states = {rid: state_of(rules[rid]["doc"]) for rid in rules}

    # ---- 核对 1：处置后不得再有任何空机制 --------------------------------
    empty = sorted(rid for rid, s in states.items() if s == "empty")
    if empty:
        print("FAIL 处置后仍存在空 override_n2（本次裁定的目的就是清零）")
        for r in empty:
            print("  " + r)
        return 1

    # ---- 核对 2：分组必须恰好覆盖 scRNA 24 条、不重复、状态符合裁定 -------
    grouped = [r for g in GROUPS for r in g["rules"]]
    if len(set(grouped)) != len(grouped):
        print("FAIL 分组里有规则被重复归类")
        return 1
    if sorted(grouped) != scrna:
        print("FAIL 分组与 scRNA 规则集合不一致")
        print(f"  分组 {len(grouped)} 条；scRNA 实测 {len(scrna)} 条")
        print(f"  分组漏掉: {sorted(set(scrna) - set(grouped))}")
        print(f"  分组多出: {sorted(set(grouped) - set(scrna))}")
        return 1
    bad = [f"组{g['id']} {r}: 期望「{g['want_state']}」实测「{states[r]}」"
           for g in GROUPS for r in g["rules"] if states[r] != g["want_state"]]
    if bad:
        print("FAIL 分组期望状态与实测不一致——材料已过期，请先修 GROUPS 或规则库")
        for b in bad:
            print("  " + b)
        return 1

    # ---- 核对 3：补全的三条必须真有 condition + all_methods + note --------
    cond_rules = sorted(r for r in scrna if states[r] == "cond")
    if cond_rules != ["G1.1-DEG-001_pseudobulk", "G1.2-DEG-002_multiple_testing",
                      "G1.3-DEG-003_method"]:
        print(f"FAIL scRNA 有条件集合不符: {cond_rules}")
        return 1
    for r in cond_rules:
        ov = rules[r]["doc"]["scoring"]["override_n2"]
        missing = [k for k in ("condition", "all_methods", "note") if not ov.get(k)]
        if missing:
            print(f"FAIL {r} 的 override_n2 缺字段: {missing}")
            return 1
        if ov.get("condition") != "n_patients <= 2":
            print(f"FAIL {r} 触发条件非预期: {ov.get('condition')}")
            return 1

    nokey = sorted(r for r in scrna if states[r] == "nokey")
    filled_all = sorted(r for r, s in states.items() if s == "cond")

    o = io.StringIO()
    w = o.write
    w("# override_n2：处置记录与现状核对（2026-09-14 裁定执行后）\n\n")
    w("> 由 scripts/make_override_n2_review_packet.py 从规则文件读出来，可重跑。\n")
    w("> 2026-09-14 之前的「分组材料（只提问、不给答案）」已被本记录取代。\n")
    w("> 本页同时是核对器：任何地方再出现空 override_n2，或分组状态对不上，脚本直接 FAIL。\n\n")
    w("## 一、现状（从规则文件实测）\n\n")
    w("| 口径 | 数字 | 说明 |\n|---|---|---|\n")
    w(f"| 空的 override_n2 | **{len(empty)}** | 裁定执行后应恒为 0 |\n")
    w(f"| scRNA 有触发条件（内容完整） | {len(cond_rules)} | "
      f"{'、'.join(cond_rules)}（condition: n_patients <= 2）|\n")
    w(f"| scRNA 无此键 | {len(nokey)} | 19 条删除 + 2 条从未有过 |\n")
    w(f"| scRNA 家族合计 | {len(scrna)} | {len(cond_rules)} 有条件 + {len(nokey)} 无键 |\n")
    w(f"| 全库有完整内容 | {len(filled_all)} | "
      f"{'、'.join(filled_all)}（M1.1 在 DEG / pancancer 各一份文件）|\n\n")
    w("## 二、处置记录（人类裁定 2026-09-14）\n\n")
    w("| 组 | 组名 | 条数 | 处置 | 当时为什么要判 |\n|---|---|---|---|---|\n")
    for g in GROUPS:
        w(f"| {g['id']} | {g['name']} | {len(g['rules'])} | {g['ruling']} | {g['why']} |\n")
    w("\n")
    w("## 三、逐条现状\n\n")
    w("| 规则 ID | 组 | 处置 | 触发条件 | 后果（all_methods） | 说明（note） | level_3/2/1/0 | 依据条数/年份 | 复核状态 |\n")
    w("|---|---|---|---|---|---|---|---|---|\n")
    for g in GROUPS:
        for rid in g["rules"]:
            info = rules[rid]
            sc = info["doc"].get("scoring") or {}
            ov = sc.get("override_n2") or {}
            cond_txt = ov.get("condition", "无此键") if ov else "无此键"
            act_txt = ov.get("all_methods", "—") if ov else "—"
            note_txt = " ".join(str(ov.get("note", "—")).split()) if ov else "—"
            t = tier_counts(info["doc"])
            nev, span = evidence_summary(info["doc"])
            ra = (info["doc"].get("provenance") or {}).get("reviewed_at") or "null"
            w(f"| {rid} | {g['id']} | {g['ruling'].split('（')[0]} | {cond_txt} | {act_txt} | "
              f"{note_txt} | {t['level_3']}/{t['level_2']}/{t['level_1']}/{t['level_0']} | "
              f"{nev} 条 / {span} | {ra} |\n")
    w("\n")
    w("## 四、机制是什么（读代码得出，不是读注释）\n\n")
    w("引擎读的字段只有 condition（src/bioaudit/engine/evaluator.py::_check_overrides）：\n\n")
    w("从 condition 抽出「键 运算符 数值」→ 该键进入 fail-closed 必填键集合（context_guard）；\n")
    w("命中 → 该决策点返回 level 0（写死 return 0，不读 all_methods）；键缺失 → 不触发。\n\n")
    w("- all_methods 与 note 没有任何代码读取（全库 grep 可核）；补它们补的是可读性，不是功能。\n")
    w("- 因此组 A/B 的补全不改变任何判定；组 C/D/E 的删除同样不改变判定（空壳本就不触发）。\n")
    w("- golden 基线在本次改动后已刷新：先由 scripts/prove_citations_only.py 证明判定层 0 差异，\n")
    w("  再由 scripts/refresh_golden_baseline.py --apply 写入（含刷新后回读复验）。\n\n")
    w("## 五、复跑与 FAIL 条件\n\n")
    w(".\\.venv\\Scripts\\python.exe scripts\\make_override_n2_review_packet.py\n\n")
    w("脚本在以下任一情况下 FAIL 且不写文件：\n")
    w("① 任何地方出现空 override_n2；② 分组与 scRNA 规则集合不一致；\n")
    w("③ 某组实测状态与裁定期望不符；④ 补全的三条缺 condition / all_methods / note。\n")

    MD_OUT.write_text(o.getvalue(), encoding="utf-8", newline="\n")

    cols = ["组", "组名", "处置决定", "规则ID", "文件", "规则名", "触发条件",
            "后果(all_methods)", "说明(note)", "level_3方法数", "level_2方法数",
            "level_1方法数", "level_0方法数", "依据条数", "依据年份跨度", "规则复核状态", "裁定日期"]
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(cols)
        for g in GROUPS:
            for rid in g["rules"]:
                info = rules[rid]
                sc = info["doc"].get("scoring") or {}
                ov = sc.get("override_n2") or {}
                t = tier_counts(info["doc"])
                nev, span = evidence_summary(info["doc"])
                ra = (info["doc"].get("provenance") or {}).get("reviewed_at") or "null"
                wr.writerow([g["id"], g["name"], g["ruling"], rid, info["rel"],
                             info["doc"].get("title", ""),
                             ov.get("condition", "无此键") if ov else "无此键",
                             ov.get("all_methods", "—") if ov else "—",
                             " ".join(str(ov.get("note", "—")).split()) if ov else "—",
                             t["level_3"], t["level_2"], t["level_1"], t["level_0"],
                             nev, span, ra, RULING_DATE])

    print(f"核对通过：空机制 {len(empty)} 条；scRNA {len(scrna)} 条 = "
          f"{len(cond_rules)} 有条件 + {len(nokey)} 无键；全库有内容 {len(filled_all)} 条")
    print(f"写入 {MD_OUT.name} 与 {CSV_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
