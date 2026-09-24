"""P1 校验器三职责（refactor-plan-v1.1 合并 G2/G8/D2）— B2 落地。

三职责：
1. **覆盖报告**（范式 × 阶段 × 类型）：决策类型本体矩阵 vs 范式声明阶段
   （流程正推）vs 规则集反推（规则 condition.decision_type），缺覆盖 → 待补清单；
   bulk-DEG "骨架待补全"标记对照（G2/G8）。
2. **语义边界**（missing 档位 / 枚举外值）：missing ∈ {fail-closed, skip, fail-open}；
   A4 design→fail-closed；A2 罚分规则引用的键禁止 fail-open；G3 unit 键；
   G4 confound 键；G5 optional ⇒ when_not_applicable；depends_on 不悬空；
   aliases 对称性（per-type ↔ aliases.yaml 分组）。
3. **冲突完整性**（D2）：同 decision_type + 同 choice（归一化后）在不同 level
   的规则 → 冲突清单（如 G1.1 vs G1.3 对 MAST 评 L1 vs L2）。

出口：``validate()`` 返回结构化报告 dict（errors/warnings/coverage/
semantic_boundaries/conflicts）；``main()`` 供 CLI 与 scripts 使用。
退出码：0 = 校验完成（含 finding）；1 = 本体结构错误（加载失败）。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

from bioaudit.engine.evaluator import RuleEvaluator
from bioaudit.models.rule import Rule
from bioaudit.ontology.loader import (
    VALID_MISSING,
    Ontology,
    OntologyError,
    get_ontology,
)
from bioaudit.paths import RULES_DIR

# G3 裁决：unit 键必须覆盖的 scRNA 分析单位敏感类型（伪重复判定根）
G3_UNIT_TYPES = {
    "deg_method": "scRNA 伪重复判定的根（cell/sample/pseudobulk）",
    "scRNA_normalization": "per-cell vs per-sample 归一化",
    "batch_correction": "整合为 sample 级操作",
    "doublet_detection": "doublet 判定为 cell 级",
}


def _load_rules(rules_dir: Path) -> tuple[dict[str, Rule], dict[str, str]]:
    """按 C2 语义加载唯一规则集（同 rule_id 保留先加载者）。

    B5 变更（2026-08-14 D2 裁决②）：同时返回每个规则所属的规则集
    （规则文件相对 rules_dir 的第一级目录；deg/DEG、pan/pancancer、
    scrna/scRNA 各自独立）。范式感知冲突检测据此只比较同一规则集内的规则。
    """
    rules: dict[str, Rule] = {}
    sources: dict[str, str] = {}
    for yaml_file in sorted(rules_dir.rglob("*.yaml")):
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "rule_id" not in data:
            continue
        rule = Rule(**data)
        if rule.status == "active" and rule.rule_id not in rules:
            rules[rule.rule_id] = rule
            try:
                rel = yaml_file.relative_to(rules_dir)
                sources[rule.rule_id] = rel.parts[0] if len(rel.parts) > 1 else "."
            except ValueError:
                sources[rule.rule_id] = "."
    return rules, sources


def _rule_referenced_keys(rule: Rule) -> set[str]:
    keys = set(rule.condition.required_context)
    keys |= set(rule.condition.forbidden_context)
    for constraint in rule.condition.context_constraints or []:
        tokens = constraint.split()
        if tokens:
            keys.add(tokens[0])
    return keys


def _is_penalty_rule(rule: Rule) -> bool:
    return bool(rule.scoring.level_0.methods) or bool(rule.scoring.level_1.methods)


def _missing_tier(ontology: Ontology, tid: str, key: str) -> Optional[str]:
    t = ontology.get_type(tid)
    if not t:
        return None
    for item in t["context_schema"]:
        if item["key"] == key:
            return item["missing"]
    return None


def validate(
    ontology: Optional[Ontology] = None,
    rules_dir: Optional[Path | str] = None,
) -> dict:
    """运行 P1 校验器三职责，返回结构化报告。"""
    ont = ontology or get_ontology()
    rules, rule_sources = _load_rules(Path(rules_dir) if rules_dir else RULES_DIR)

    errors: list[dict] = []
    warnings: list[dict] = []

    # ══ 职责一：覆盖报告（范式 × 阶段 × 类型） ══
    matrix = ont.coverage_matrix()
    coverage = {
        "version": ont.version,
        "n_types": len(ont.types),
        "types": sorted(ont.types),
        "n_rules_active": len(rules),
        "n_stages": len(ont.stages),
        "paradigm_matrix": {p: {s: types for s, types in m.items() if types}
                            for p, m in matrix.items()},
        "paradigm_stage_counts": {
            p: {s: len(types) for s, types in m.items()}
            for p, m in matrix.items()
        },
    }

    # 范式声明阶段 vs 类型推导阶段（流程正推对比，G2）
    for pname, pconf in ont.paradigms.items():
        declared = set(pconf.get("declared_stages", []))
        derived = {s for s, types in matrix[pname].items() if types}
        missing_declared = declared - derived
        extra_derived = derived - declared
        if pconf.get("skeleton"):
            # 骨架待补全范式：不报错，仅记录
            warnings.append({
                "section": "coverage",
                "kind": "skeleton_paradigm",
                "paradigm": pname,
                "detail": pconf.get("skeleton_note", ""),
                "missing_stages": sorted(missing_declared),
            })
        elif missing_declared or extra_derived:
            errors.append({
                "section": "coverage",
                "kind": "paradigm_stage_mismatch",
                "paradigm": pname,
                "declared": sorted(declared),
                "derived": sorted(derived),
            })

    # 规则 → 本体：规则引用的决策类型必须存在
    rule_types = {r.condition.decision_type for r in rules.values()}
    unknown_rule_types = sorted(rule_types - set(ont.types))
    if unknown_rule_types:
        errors.append({
            "section": "coverage",
            "kind": "rule_type_not_in_ontology",
            "types": unknown_rule_types,
        })
    coverage["rule_types_all_known"] = not unknown_rule_types

    # 本体 → 规则：无规则覆盖的类型 → 待补（规则集增长入口）
    types_without_rules = sorted(set(ont.types) - rule_types)
    coverage["types_without_rules"] = types_without_rules

    # 待补清单（G2/G6）
    coverage["backlog"] = ont.backlog

    # ══ 职责二：语义边界 ══
    sem = {"missing_tier_usage": {}, "checks": []}

    for tid in sorted(ont.types):
        t = ont.types[tid]
        # 1. missing 档位合法（loader 已挡非法值，此处统计使用分布）
        tiers = [item["missing"] for item in t["context_schema"]]
        for tier in VALID_MISSING:
            sem["missing_tier_usage"].setdefault(tier, 0)
            sem["missing_tier_usage"][tier] += tiers.count(tier)

        # 2. A4：deg_method 的 design 键必须 fail-closed
        if tid == "deg_method":
            tier = _missing_tier(ont, tid, "design")
            if tier != "fail-closed":
                errors.append({
                    "section": "semantic",
                    "kind": "a4_design_must_be_fail_closed",
                    "type": tid, "actual": tier,
                })

        # 3. G3：unit 键覆盖
        if tid in G3_UNIT_TYPES:
            unit_item = next(
                (i for i in t["context_schema"] if i["key"] == "unit"), None
            )
            if unit_item is None:
                errors.append({
                    "section": "semantic",
                    "kind": "g3_unit_key_missing",
                    "type": tid, "expected_reason": G3_UNIT_TYPES[tid],
                })
            elif unit_item["missing"] == "fail-open":
                errors.append({
                    "section": "semantic",
                    "kind": "g3_unit_key_fail_open",
                    "type": tid,
                })

        # 4. G4：batch_correction 必须有 confound 键
        if tid == "batch_correction":
            confound_item = next(
                (i for i in t["context_schema"] if i["key"] == "confound"), None
            )
            if confound_item is None:
                errors.append({
                    "section": "semantic",
                    "kind": "g4_confound_key_missing",
                    "type": tid,
                })

        # 5. G5：optional ⇒ when_not_applicable（适用性谓词）
        if t.get("optional") and not t.get("when_not_applicable"):
            errors.append({
                "section": "semantic",
                "kind": "g5_optional_requires_when_not_applicable",
                "type": tid,
            })
        if not t.get("optional") and t.get("when_not_applicable"):
            warnings.append({
                "section": "semantic",
                "kind": "g5_when_not_applicable_without_optional",
                "type": tid,
            })

        # 6. depends_on 不悬空
        for dep in t.get("depends_on", []):
            if dep not in ont.types:
                errors.append({
                    "section": "semantic",
                    "kind": "depends_on_dangling",
                    "type": tid, "depends_on": dep,
                })

        # 7. aliases（同源）成员必须存在于本体
        for alias in t.get("aliases", []):
            if alias not in ont.types:
                errors.append({
                    "section": "semantic",
                    "kind": "alias_dangling",
                    "type": tid, "alias": alias,
                })

        # 8. internal_ref（G1）成员必须存在
        for ref in t.get("internal_ref", []):
            if ref not in ont.types:
                errors.append({
                    "section": "semantic",
                    "kind": "internal_ref_dangling",
                    "type": tid, "ref": ref,
                })

        # 9. context_schema 内键重复 / enum 非空（loader 已挡，双保险）
        keys = [i["key"] for i in t["context_schema"]]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            errors.append({
                "section": "semantic",
                "kind": "context_schema_duplicate_key",
                "type": tid, "keys": sorted(dup),
            })

    # 10. A2：罚分规则引用的键禁止 fail-open（防凭空制造 L0）
    a2_violations = []
    for rid, rule in sorted(rules.items()):
        if not _is_penalty_rule(rule):
            continue
        tid = rule.condition.decision_type
        for key in sorted(_rule_referenced_keys(rule)):
            tier = _missing_tier(ont, tid, key)
            if tier == "fail-open":
                a2_violations.append({
                    "rule_id": rid, "type": tid, "key": key,
                })
    if a2_violations:
        errors.append({
            "section": "semantic",
            "kind": "a2_penalty_rule_fail_open_key",
            "violations": a2_violations,
        })
    sem["a2_violations"] = a2_violations

    # 11. aliases.yaml 分组与 per-type 声明对称（双向核对）
    alias_issues = []
    declared_pairs: set[tuple[str, str]] = set()
    for tid in ont.types:
        for a in ont.aliases_for(tid):
            pair = tuple(sorted((tid, a)))
            declared_pairs.add(pair)
    for group in ont.aliases.get("homology_groups", []):
        members = group.get("members", [])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = tuple(sorted((members[i], members[j])))
                if pair not in declared_pairs:
                    alias_issues.append({
                        "kind": "group_not_in_per_type",
                        "group": group.get("group_id"),
                        "pair": list(pair),
                    })
    for pair in sorted(declared_pairs):
        found = any(
            pair == tuple(sorted(g.get("members", []))) or
            pair == tuple(sorted((g.get("members", [])[0], g.get("members", [])[1])))
            for g in ont.aliases.get("homology_groups", [])
            if len(g.get("members", [])) == 2
        )
        if not found:
            alias_issues.append({
                "kind": "per_type_not_in_group",
                "pair": list(pair),
            })
    # deg_method 同源注释核对
    dm = ont.aliases.get("deg_method", {})
    if ont.aliases_for("deg_method") and dm.get("homologous") is False:
        alias_issues.append({
            "kind": "deg_method_aliases_declared_but_marked_non_homologous",
        })
    if alias_issues:
        errors.append({
            "section": "semantic",
            "kind": "alias_declaration_inconsistency",
            "issues": alias_issues,
        })
    sem["alias_issues"] = alias_issues

    # ══ 职责三：冲突完整性（D2：同 choice 不同 level） ══
    # B5 裁决②（2026-08-14）：检测范围为**同一规则集**（规则文件第一级目录，
    # 即 deg/DEG、pan/pancancer、scrna/scRNA）。理由：运行时按范式建独立
    # registry（bioaudit.paths.ACT_RULE_SUBDIRS），跨范式规则不会同时命中
    # 同一决策；G1.2(scRNA) 与 M1.2(bulk/pan) 对 Bonferroni 的差异评级是
    # 范式语境差异（各带 conditions_when_acceptable），不是规则间冲突。
    # 裁决文档：docs/specs/2026-08-14-d2-adjudication.md。
    normalizer = RuleEvaluator()._normalize_choice
    choice_levels: dict[tuple[str, str, str], list[tuple[str, int]]] = {}
    for rid, rule in sorted(rules.items()):
        source = rule_sources.get(rid, ".")
        scoring = rule.scoring
        for level_key, level in (
            ("level_4", 4), ("level_3", 3), ("level_2", 2),
            ("level_1", 1), ("level_0", 0),
        ):
            lvl = getattr(scoring, level_key, None)
            if not lvl:
                continue
            for method in lvl.methods:
                norm = normalizer(method)
                choice_levels.setdefault(
                    (source, rule.condition.decision_type, norm), []
                ).append((rid, level))

    conflicts = []
    for (source, tid, choice), entries in sorted(choice_levels.items()):
        levels = {lv for _, lv in entries}
        if len(levels) > 1:
            conflicts.append({
                "decision_type": tid,
                "choice": choice,
                "rule_set": source,
                "entries": [
                    {"rule_id": rid, "level": lv} for rid, lv in entries
                ],
            })
    sem_conflicts = {
        "n_conflicts": len(conflicts),
        "conflicts": conflicts,
        "scope": "same-rule-set",  # B5: 范式感知（同规则集内才比较）
    }

    report = {
        "ok": not errors,
        "ontology_version": ont.version,
        "n_errors": len(errors),
        "n_warnings": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "semantic_boundaries": sem,
        "conflicts": sem_conflicts,
    }
    return report


def main(argv: Optional[list[str]] = None) -> int:
    # Windows GBK 控制台兼容：与 golden_replay.py 同款处理
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="bio-audit validate-ontology", description=__doc__)
    parser.add_argument("--rules-dir", default=None, help="规则目录（默认包内 RULES_DIR）")
    parser.add_argument("--ontology-dir", default=None, help="本体目录（默认包内 ONTOLOGY_DIR）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    args = parser.parse_args(argv)

    try:
        ont = Ontology(args.ontology_dir) if args.ontology_dir else get_ontology()
        report = validate(ont, rules_dir=args.rules_dir)
    except OntologyError as e:
        print(f"❌ 本体结构错误: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_human(report)
    return 0


def _print_human(report: dict) -> None:
    print(f"本体校验报告 — ontology v{report['ontology_version']}  "
          f"({report['coverage']['n_types']} 类型 / "
          f"{report['coverage']['n_rules_active']} 规则 / "
          f"{report['coverage']['n_stages']} 阶段)")
    print("─" * 72)
    print(f"★ 职责一·覆盖报告：范式 × 阶段 × 类型 "
          f"（{report['coverage']['n_types']} 类型）")
    for p, m in report["coverage"]["paradigm_stage_counts"].items():
        nonempty = {s: c for s, c in m.items() if c}
        print(f"   {p:12s} {nonempty}")
    if report["coverage"]["types_without_rules"]:
        print(f"   ⚠ 无规则覆盖的类型（待补）: "
              f"{report['coverage']['types_without_rules']}")
    else:
        print("   ✅ 全部 34 类型均有规则覆盖")
    for b in report["coverage"].get("backlog", []):
        print(f"   ◌ 待补清单: {b['id']} — {b['title']}")
    print("─" * 72)
    print("★ 职责二·语义边界：missing 三档使用 "
          f"{report['semantic_boundaries']['missing_tier_usage']}")
    print("─" * 72)
    print(f"★ 职责三·冲突完整性（D2 同 choice 不同 level）: "
          f"{report['conflicts']['n_conflicts']} 处")
    for c in report["conflicts"]["conflicts"]:
        print(f"   ⚠ {c['decision_type']} / {c['choice']}: "
              f"{c['entries']}")
    print("─" * 72)
    for w in report["warnings"]:
        print(f"  ⚠ [{w['section']}] {w['kind']}: {w.get('detail', w)}")
    for e in report["errors"]:
        print(f"  ❌ [{e['section']}] {e['kind']}: {e}")
    print("─" * 72)
    status = "✅ 校验完成（0 错误）" if report["ok"] else \
        f"❌ 校验完成（{report['n_errors']} 错误 / {report['n_warnings']} 警告）"
    print(status)


__all__ = ["validate", "main", "G3_UNIT_TYPES"]
