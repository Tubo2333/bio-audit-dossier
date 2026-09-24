"""B5 规则治理单命令：ruleset 校验器 + D2 冲突检查 + golden replay（一条命令三闸）。

对应 refactor-plan-v1.1 D1（规则变更强制校验进 CI）+ B5 验收项 3：

    bio-audit ruleset-validate [--json] [--baseline PATH]

三道闸：
1. **清单校验**：ruleset.json semver / 45 文件内容哈希 / 40 唯一 rule_id /
   YAML→Rule schema（bioaudit.rules.manifest.verify_manifest）
2. **冲突完整性**：同规则集内 同 decision_type + choice 不同 level（D2，
   范式感知；B5 裁决后预期 0 冲突——bioaudit.ontology.validator）
3. **golden 重放**：20 轨迹 137 决策与冻结基线 0 差异（bioaudit.regression）

退出码：0 = 三闸全绿；1 = 任一失败（CI 即红，D1"失败自动回退"语义：
CI 门禁拦截合并，规则变更不通过不生效）。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from bioaudit.ontology.loader import OntologyError, get_ontology
from bioaudit.ontology.validator import validate as validate_ontology
from bioaudit.paths import RULES_DIR
from bioaudit.regression import replay_golden
from bioaudit.rules.manifest import verify_manifest


def validate_ruleset(
    rules_dir: Optional[Path | str] = None,
    baseline: Optional[Path | str] = None,
) -> dict:
    """运行规则治理三闸，返回合并报告。"""
    rd = Path(rules_dir) if rules_dir else RULES_DIR

    # 闸 1：清单校验
    manifest = verify_manifest(rules_dir=rd)
    manifest_ok = manifest["ok"]

    # 闸 2：冲突完整性（D2，范式感知；含覆盖/语义边界完整校验）
    try:
        ont_report = validate_ontology(get_ontology(), rules_dir=rd)
    except OntologyError as exc:
        ont_report = {
            "ok": False,
            "errors": [{"kind": "ontology_load_failed", "detail": str(exc)}],
            "conflicts": {"n_conflicts": -1, "conflicts": []},
        }
    conflicts_ok = ont_report["ok"] and ont_report["conflicts"]["n_conflicts"] == 0

    # 闸 3：golden 重放（137 决策 0 差异）
    try:
        golden_ok, golden_summary = replay_golden(Path(baseline) if baseline else None)
    except FileNotFoundError as exc:
        golden_ok, golden_summary = False, {"error": str(exc)}

    ok = manifest_ok and conflicts_ok and golden_ok
    return {
        "ok": ok,
        "ruleset_version": manifest.get("ruleset_version"),
        "stages": {
            "manifest": "PASS" if manifest_ok else "FAIL",
            "conflicts": "PASS" if conflicts_ok else "FAIL",
            "golden": "PASS" if golden_ok else "FAIL",
        },
        "manifest": manifest,
        "ontology": {
            "ok": ont_report["ok"],
            "n_errors": ont_report.get("n_errors", 0),
            "n_warnings": ont_report.get("n_warnings", 0),
            "conflicts": ont_report["conflicts"],
        },
        "golden": golden_summary,
    }


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="bio-audit ruleset-validate",
        description="规则治理三闸：清单校验 + 冲突检查 + golden 重放（D1）",
    )
    parser.add_argument("--rules-dir", default=None, help="规则目录（默认包内）")
    parser.add_argument("--baseline", default=None, help="golden 基线路径（默认包内副本）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    args = parser.parse_args(argv)

    report = validate_ruleset(rules_dir=args.rules_dir, baseline=args.baseline)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_human(report)

    return 0 if report["ok"] else 1


def _print_human(report: dict) -> None:
    print(f"规则治理校验 — ruleset v{report['ruleset_version']}")
    print("─" * 72)
    m = report["manifest"]
    print(f"★ 闸 1·清单校验: {report['stages']['manifest']}  "
          f"({m['n_rule_files']} 文件 / {m['n_unique_rule_ids']} 唯一 rule_id / "
          f"{m.get('duplicate_copies', 0)} 对预期副本)")
    for e in m["errors"]:
        print(f"   ❌ [{e['kind']}] {e.get('path', e.get('detail', e))}")
    print("─" * 72)
    on = report["ontology"]
    print(f"★ 闸 2·冲突完整性: {report['stages']['conflicts']}  "
          f"(D2 同规则集内同 choice 不同 level = {on['conflicts']['n_conflicts']} 处, "
          f"scope={on['conflicts']['scope']})")
    for c in on["conflicts"]["conflicts"]:
        print(f"   ⚠ {c['decision_type']} / {c['choice']} ({c['rule_set']}): {c['entries']}")
    if not on["ok"]:
        for e in on.get("errors", []):
            print(f"   ❌ [{e['kind']}] {e}")
    print("─" * 72)
    g = report["golden"]
    print(f"★ 闸 3·golden 重放: {report['stages']['golden']}  "
          f"({g.get('n_trajectories_replayed')} 轨迹 / "
          f"{g.get('n_decisions_replayed')} 决策 / {g.get('n_diffs')} 差异)")
    for d in g.get("diffs", [])[:10]:
        print(f"   ❌ diff: {d}")
    print("─" * 72)
    if report["ok"]:
        print("✅ 规则治理校验通过（三闸全绿）")
    else:
        print(f"❌ 规则治理校验失败（{report['stages']}）— CI 门禁拦截（D1）")


__all__ = ["validate_ruleset", "main"]
