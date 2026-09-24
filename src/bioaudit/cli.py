"""bio-audit 命令行入口（v1 蓝图：引擎 + CLI；窗口 C 采集命令）。

子命令：
  run <trajectory.json> [--act deg|pan|scrna]  审计一条轨迹，输出 JSON
  golden [--baseline path]                      golden 回归（20 轨迹 137 决策）
  audit-decision <json> --act deg|pan|scrna    单决策审计（B3：paradigm 必填）
  validate-ontology                            P1 校验器三职责（B2：覆盖/语义边界/冲突）
  ruleset-validate                             B5：规则治理三闸（清单+冲突+golden 一条命令）
  migrate-trajectories [--dry-run]             B4：v1 → v2 轨迹迁移（只读迁移器）
  trajectory-validate <path|dir>               B4：v2 轨迹 schema 校验（缺必填字段报错）
  parse-notebook <nb> [--act] [--metadata]     C2：M3 签名驱动解析（三级可信源 + 禁猜）
  cross-validate --m1 <jsonl> --m3 <nb>        C3：M1/M3 交叉验证（四类判定 + verdict 联动）
  trace <session_id>                           C5：引擎审计过程日志（审计者也可审计）
  capture-validate                             C2/C6：签名表校验 + 样例自检（CI 门禁）
  verdict <session_id>                         C3：会话 verdict 清单（final-only 视图）
  benchmark-run [--split] [--act] [--seed]     D4：任务集批量评测 + 功效报告（bootstrap CI）
  benchmark-validate                           D5/E8：任务集三闸（清单+污染+覆盖）+ golden
  reward <trajectory> [--act] [--recipe]       E1：reward API（step_rewards + 轨迹 reward + meta）
  reward-calibrate [--seed] [--n-boot]         E3：30 任务校准（消融 + 排序一致性 + 分层检验）
  reward-validate                              E4：reward 五闸（映射/确定性/锚点/消融/golden）
"""

import argparse
import json
import sys
from pathlib import Path


def _load_trajectory(path: Path) -> list[dict] | dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "decisions" in data:
        return data
    raise ValueError("轨迹文件必须是决策数组或含 decisions 键的对象")


def _print_bioaudit_error(exc) -> None:
    """契约错误输出：{"error": {"code", "message", "details"}}（B3 错误码）。"""
    print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=1))
    print(f"\n❌ {exc.code}: {exc.message}", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    from bioaudit.api import run_audit
    from bioaudit.errors import BioAuditError

    try:
        trajectory = _load_trajectory(Path(args.trajectory))
        result = run_audit(trajectory, act=args.act)
    except BioAuditError as exc:
        _print_bioaudit_error(exc)
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    if result.get("error"):
        print(json.dumps({"error": {
            "code": result.get("error_code") or "internal-error",
            "message": result["error"],
        }}, ensure_ascii=False, indent=1))
        return 1
    print(json.dumps({
        "trajectory_score": result["trajectory_score"],
        "verdict": result["eval_verdict"],
        "dimension_scores": result["dimension_scores"],
        "n_decisions": len(result["step_scores"]),
        "critical_issues": result["critical_issues"],
        "report": result["report"],
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    from bioaudit.regression import replay_golden

    ok, summary = replay_golden(baseline=Path(args.baseline))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def cmd_audit_decision(args: argparse.Namespace) -> int:
    from bioaudit.api import audit_decision
    from bioaudit.errors import BioAuditError

    try:
        decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
        result = audit_decision(decision, paradigm=args.act)
    except BioAuditError as exc:
        _print_bioaudit_error(exc)
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_validate_ontology(args: argparse.Namespace) -> int:
    from bioaudit.ontology.validator import main as validator_main

    return validator_main([
        *(["--rules-dir", args.rules_dir] if args.rules_dir else []),
        *(["--ontology-dir", args.ontology_dir] if args.ontology_dir else []),
        *(["--json"] if args.json else []),
    ])


def cmd_ruleset_validate(args: argparse.Namespace) -> int:
    """B5：规则治理三闸（清单校验 + D2 冲突检查 + golden 重放）一条命令。"""
    from bioaudit.rules.validator import main as ruleset_main

    return ruleset_main([
        *(["--rules-dir", args.rules_dir] if args.rules_dir else []),
        *(["--baseline", args.baseline] if args.baseline else []),
        *(["--json"] if args.json else []),
    ])


def cmd_migrate_trajectories(args: argparse.Namespace) -> int:
    """B4：只读迁移器（v1 → v2）；--dry-run 不写盘。"""
    from bioaudit.capture.trajectory_migrator import TrajectoryMigrator
    from bioaudit.errors import BioAuditError

    try:
        migrator = TrajectoryMigrator(
            src_dir=Path(args.src_dir) if args.src_dir else None,
            dst_dir=Path(args.dst_dir) if args.dst_dir else None,
        )
        rows = migrator.migrate_all(dry_run=args.dry_run)
    except (BioAuditError, OSError, ValueError) as exc:
        print(f"❌ 迁移失败: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "dry_run": args.dry_run,
        "n_migrated": len(rows),
        "trajectories": rows,
    }, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("\n（dry-run：未写任何文件）")
    return 0


def cmd_trajectory_validate(args: argparse.Namespace) -> int:
    """B4：v2 轨迹 schema 校验；缺必填字段 → 显式报错（A15）。"""
    from bioaudit.errors import BioAuditError
    from bioaudit.models.trajectory import validate_trajectory

    path = Path(args.path)
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files:
        print(f"❌ 未找到轨迹文件: {path}", file=sys.stderr)
        return 1

    ok_all = True
    results = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            traj = validate_trajectory(data)
            results.append({
                "file": str(f),
                "ok": True,
                "version": traj.version,
                "trajectory_id": traj.trajectory_id,
                "act": traj.act,
                "provenance_source": traj.provenance.source,
                "n_decisions": len(traj.decisions),
            })
        except (BioAuditError, ValueError, OSError, json.JSONDecodeError) as exc:
            ok_all = False
            results.append({"file": str(f), "ok": False, "error": str(exc)})

    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if ok_all else 1


# ── 窗口 C：采集命令（C2/C3/C5/C6）──


def cmd_parse_notebook(args: argparse.Namespace) -> int:
    """C2：M3 签名驱动解析（signatures + 三级可信源 + 禁猜）。"""
    from bioaudit.capture.m3_parser import M3Parser
    from bioaudit.errors import BioAuditError

    metadata = None
    declared = None
    try:
        if args.metadata:
            metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
        if args.declared:
            declared = json.loads(Path(args.declared).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1

    try:
        parser = M3Parser(act=args.act, metadata=metadata, declared=declared)
        result = parser.parse_notebook(Path(args.notebook))
    except (BioAuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1

    print(json.dumps({
        "parser_version": result.parser_version,
        "paradigm": result.paradigm,
        "n_code_cells": result.n_code_cells,
        "n_candidates": len(result.candidates),
        "n_uncertain": len(result.uncertain),
        "candidates": [c.model_dump(mode="json") for c in result.candidates],
        "uncertain": [u.model_dump(mode="json") for u in result.uncertain],
        "warnings": result.warnings,
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_cross_validate(args: argparse.Namespace) -> int:
    """C3：M1/M3 交叉验证（四类判定 + verdict 联动 + final-only）。"""
    from bioaudit.capture.cross_validator import CrossValidator
    from bioaudit.capture.m3_parser import M3Parser
    from bioaudit.capture.verdict import VerdictStore
    from bioaudit.errors import BioAuditError

    if not args.m3 and not args.m3_json:
        print(json.dumps({"error": {"code": "bad-request",
                                    "message": "cross-validate 需 --m3 或 --m3-json"}},
                         ensure_ascii=False, indent=1))
        return 1
    try:
        m1_path = Path(args.m1)
        if m1_path.suffix == ".jsonl":
            m1 = [
                json.loads(line)
                for line in m1_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            data = json.loads(m1_path.read_text(encoding="utf-8"))
            m1 = data if isinstance(data, list) else data.get("decisions", [])

        metadata = None
        declared = None
        if args.metadata:
            metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
        if args.declared:
            declared = json.loads(Path(args.declared).read_text(encoding="utf-8"))

        if args.m3_json:
            data = json.loads(Path(args.m3_json).read_text(encoding="utf-8"))
            m3 = data if isinstance(data, list) else data.get("candidates", [])
        else:
            parser = M3Parser(act=args.act, metadata=metadata, declared=declared)
            m3 = parser.parse_notebook(Path(args.m3))

        expected = None
        if args.expected:
            expected_path = Path(args.expected)
            if expected_path.suffix.lower() in (".yaml", ".yml"):
                # M1.1（窗口 M）：expected_types 评测配置（per 范式×平台，B7 豁免）
                from bioaudit.capture.expected_types import expected_types_for

                expected = expected_types_for(
                    args.act or "scrna", facts=declared or {},
                )
            else:
                expected = json.loads(expected_path.read_text(encoding="utf-8"))

        store = None
        if not args.no_verdicts:
            store = VerdictStore()
        session_id = args.session or "crossval"
        result = CrossValidator(act=args.act).validate(
            m1, m3, session_id=session_id,
            expected_types=expected, verdict_store=store,
        )
    except (BioAuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1

    print(json.dumps({
        "session_id": result.session_id,
        "act": result.act,
        "stats": result.stats,
        "alignments": [a.model_dump(mode="json") for a in result.alignments],
        "added_decisions": result.added_decisions,
        "verdict_updates": result.verdict_updates,
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """C5：引擎审计过程日志（审计者也可审计）。"""
    from bioaudit.capture.engine_trace import render_trace, trace_session

    events = trace_session(args.session_id, log_dir=args.log_dir)
    print(render_trace(args.session_id, events))
    return 0


def cmd_capture_validate(args: argparse.Namespace) -> int:
    """C2/C6：签名表校验（类型∈本体/模式编译/choice 方式互斥）+ 样例自检。"""
    from bioaudit.capture.m3_parser import M3Parser
    from bioaudit.capture.signatures import SignatureTable, validate_table

    try:
        table = SignatureTable()
        report = validate_table(table)
        failures = []
        if not report["ok"]:
            failures.extend(report["errors"])
        # 样例自检：解析样例 notebook → 必须有候选
        nb = Path(args.notebook) if args.notebook else None
        if nb is not None and nb.exists():
            result = M3Parser(act="scrna").parse_notebook(nb)
            if not result.candidates:
                failures.append(f"样例 notebook 自检失败：0 候选（{nb}）")
        print(json.dumps({
            "ok": not failures,
            "signatures_version": report["signatures_version"],
            "n_types": report["n_types"],
            "n_types_with_signatures": report["n_types_with_signatures"],
            "covered_types": report["covered_types"],
            "errors": failures or report["errors"],
            "warnings": report["warnings"],
        }, ensure_ascii=False, indent=1))
        return 0 if not failures else 1
    except Exception as exc:  # 不裸抛（B1）
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


def cmd_verdict(args: argparse.Namespace) -> int:
    """C3：会话 verdict 清单（final-only 视图 + 全量）。"""
    from bioaudit.capture.verdict import VerdictStatus, VerdictStore

    try:
        store = VerdictStore()
        records = store.get(args.session_id)
    except Exception as exc:
        print(json.dumps({"error": {"code": "internal-error", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    print(json.dumps({
        "session_id": args.session_id,
        "n_final": sum(1 for r in records if r.status == VerdictStatus.FINAL),
        "n_provisional": sum(1 for r in records if r.status == VerdictStatus.PROVISIONAL),
        "n_revoked": sum(1 for r in records if r.status == VerdictStatus.REVOKED),
        "final": [r.as_dict() for r in records if r.status == VerdictStatus.FINAL],
        "all": [r.as_dict() for r in records],
    }, ensure_ascii=False, indent=1))
    return 0


# ── 窗口 D：benchmark 命令（D4.9 运行器 / D5.13 任务集三闸）──


def cmd_benchmark_run(args: argparse.Namespace) -> int:
    """D4：批量评测 → 结果表 + 功效报告（bootstrap CI + 多重比较 + gap 检查）。"""
    from bioaudit.benchmark.runner import run_benchmark

    try:
        report = run_benchmark(
            tasks_dir=args.tasks_dir,
            split=args.split,
            act=args.act,
            seed=args.seed,
            n_boot=args.n_boot,
        )
    except Exception as exc:
        print(json.dumps({"error": {"code": "internal-error", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def cmd_benchmark_validate(args: argparse.Namespace) -> int:
    """D5.13/E8：任务集三闸（清单+污染+覆盖）+ golden 回归（评分路径保护）。

    任一闸失败 → exit 1（与 ruleset-validate 同门禁风格）。
    """
    from bioaudit.benchmark.contamination import collect_rule_fragments, scan_dir, scan_file
    from bioaudit.benchmark.coverage import audit as coverage_audit
    from bioaudit.benchmark.manifest import validate_taskset
    from bioaudit.benchmark.paths import GENERATOR_PROMPT, TASKS_DIR
    from bioaudit.regression import replay_golden

    errors = []
    gates = {}

    # 闸 1：taskset 清单 + Task schema + split 完整性
    m = validate_taskset(args.tasks_dir)
    gates["taskset"] = m
    if not m["ok"]:
        errors.extend(m["errors"])

    # 闸 2：污染扫描（E2：规则标识/标题命中即标记；生成器提示词零规则内容 E6）
    fragments = collect_rule_fragments(args.rules_dir)
    cont = scan_dir(TASKS_DIR if args.tasks_dir is None else args.tasks_dir, fragments)
    prompt_cont = scan_file(GENERATOR_PROMPT, fragments)
    gates["contamination"] = {"tasks": cont, "generator_prompt": prompt_cont}
    if not cont["ok"]:
        errors.append({"kind": "contamination", "detail": cont["files_with_rule_hits"]})
    if not prompt_cont["ok"]:
        errors.append({"kind": "generator_prompt_contamination", "detail": "E6 违规"})

    # 闸 3：覆盖审计（E5：34 类型 + 39 规则；零触发 = 0 或显式豁免）
    cov = coverage_audit(args.tasks_dir, exemptions={})
    gates["coverage"] = cov
    if not cov["ok"]:
        errors.append({"kind": "coverage",
                       "detail": {"missing_types": cov["missing_types"],
                                  "remaining_missing_rules": cov["remaining_missing_rules"]}})

    # 闸 4：golden 回归（D6.14：benchmark 是外围层，评分路径零改动）
    ok, golden = replay_golden(baseline=args.baseline)
    gates["golden"] = golden
    if not ok:
        errors.append({"kind": "golden_diff", "detail": golden["diffs"][:5]})

    out = {
        "ok": not errors,
        "gates": gates,
        "errors": errors,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


# ── 窗口 E：reward 命令（E1 API / E3 校准 / E4 门禁）──


def cmd_reward(args: argparse.Namespace) -> int:
    """E1：reward API 的 CLI 入口（外围输出层，零评分路径改动）。"""
    from bioaudit.errors import BioAuditError
    from bioaudit.reward.api import reward

    try:
        trajectory = _load_trajectory(Path(args.trajectory))
        prm_weights = None
        if args.prm_weights:
            prm_weights = json.loads(Path(args.prm_weights).read_text(encoding="utf-8"))
        result = reward(
            trajectory, act=args.act, recipe=args.recipe,
            session_id=args.session, prm_weights=prm_weights,
        )
    except BioAuditError as exc:
        _print_bioaudit_error(exc)
        return 1
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(json.dumps({"error": {"code": "bad-request", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_reward_calibrate(args: argparse.Namespace) -> int:
    """E3：30 条任务校准（三组消融 + 排序一致性 + 分层均值检验 + 多种子）。"""
    from bioaudit.reward.calibration import run_calibration

    try:
        report = run_calibration(
            tasks_dir=args.tasks_dir, seed=args.seed, n_boot=args.n_boot,
        )
    except Exception as exc:
        print(json.dumps({"error": {"code": "internal-error", "message": str(exc)}},
                         ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def cmd_reward_validate(args: argparse.Namespace) -> int:
    """E4.13：reward 自检五闸（映射/确定性/spike-in 锚点/消融/golden）+ 校准证据。"""
    from bioaudit.reward.validate import main as reward_validate_main

    return reward_validate_main([
        *(["--baseline", args.baseline] if args.baseline else []),
        *(["--json"] if args.json else []),
    ])


def main(argv: list[str] | None = None) -> int:
    # JSON 输出含中文 → 强制 UTF-8（Windows 控制台 GBK 会破坏管道捕获）
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="bio-audit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="审计一条轨迹（JSON）")
    p_run.add_argument("trajectory", help="轨迹 JSON 文件路径")
    p_run.add_argument("--act", choices=["deg", "pan", "scrna"], default=None)
    p_run.set_defaults(func=cmd_run)

    p_golden = sub.add_parser("golden", help="golden 回归（0 差异验收）")
    _golden_default = (
        Path(__file__).resolve().parent.parent.parent / "tests" / "golden"
        / "golden_expected_output_after.json"
    )
    p_golden.add_argument("--baseline", default=str(_golden_default))
    p_golden.add_argument(
        "--json", action="store_true",
        help="输出原始 JSON 报告（默认输出即 JSON，此开关与其它子命令对齐，供 CI 使用）",
    )
    p_golden.set_defaults(func=cmd_golden)

    p_ad = sub.add_parser("audit-decision", help="单决策审计")
    p_ad.add_argument("decision", help="决策 JSON 文件路径")
    p_ad.add_argument("--act", required=True, choices=["deg", "pan", "scrna"],
                      help="范式（B3 契约：必填，deg_method 同名异构消歧）")
    p_ad.set_defaults(func=cmd_audit_decision)

    p_vo = sub.add_parser(
        "validate-ontology",
        help="P1 校验器三职责（覆盖报告 / 语义边界 / 冲突完整性）",
    )
    p_vo.add_argument("--rules-dir", default=None, help="规则目录（默认包内）")
    p_vo.add_argument("--ontology-dir", default=None, help="本体目录（默认包内）")
    p_vo.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    p_vo.set_defaults(func=cmd_validate_ontology)

    p_rs = sub.add_parser(
        "ruleset-validate",
        help="B5 规则治理三闸：清单校验 + D2 冲突检查 + golden 重放（D1 变更流程）",
    )
    p_rs.add_argument("--rules-dir", default=None, help="规则目录（默认包内）")
    p_rs.add_argument("--baseline", default=None, help="golden 基线路径（默认包内副本）")
    p_rs.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    p_rs.set_defaults(func=cmd_ruleset_validate)

    p_mig = sub.add_parser("migrate-trajectories", help="B4：v1 → v2 轨迹迁移（只读）")
    p_mig.add_argument("--src-dir", default=None, help="v1 旧轨迹目录（默认 data/trajectories）")
    p_mig.add_argument("--dst-dir", default=None, help="v2 输出目录（默认 data/trajectories/v2）")
    p_mig.add_argument("--dry-run", action="store_true", help="只生成清单，不写盘")
    p_mig.set_defaults(func=cmd_migrate_trajectories)

    p_tv = sub.add_parser("trajectory-validate", help="B4：v2 轨迹 schema 校验")
    p_tv.add_argument("path", help="v2 轨迹 JSON 文件或目录")
    p_tv.set_defaults(func=cmd_trajectory_validate)

    # ── 窗口 C：采集命令 ──
    p_pn = sub.add_parser(
        "parse-notebook", help="C2：M3 签名驱动解析（signatures + 三级可信源 + 禁猜）",
    )
    p_pn.add_argument("notebook", help=".ipynb / .py 产物文件")
    p_pn.add_argument("--act", choices=["deg", "pan", "scrna"], default=None,
                      help="范式（限定签名集，同名异构消歧）")
    p_pn.add_argument("--metadata", default=None, help="数据元数据 JSON（二级可信源）")
    p_pn.add_argument("--declared", default=None, help="环境声明 JSON（三级可信源）")
    p_pn.set_defaults(func=cmd_parse_notebook)

    p_cv = sub.add_parser(
        "cross-validate", help="C3：M1/M3 交叉验证（四类判定 + verdict 联动）",
    )
    p_cv.add_argument("--m1", required=True, help="M1 声明 JSON/JSONL（可含 verdict_id）")
    p_cv.add_argument("--m3", default=None, help="M3 产物（.ipynb/.py，签名解析）")
    p_cv.add_argument("--m3-json", default=None, help="M3 候选 JSON（parse-notebook 输出）")
    p_cv.add_argument("--act", choices=["deg", "pan", "scrna"], default=None)
    p_cv.add_argument("--metadata", default=None, help="数据元数据 JSON（M3 解析用）")
    p_cv.add_argument("--declared", default=None,
                      help="评测者/数据事实声明 JSON（三级可信源；与 Agent 自证严格区分）")
    p_cv.add_argument("--expected", default=None,
                      help="预期决策类型（JSON 数组文件，或 expected_types 评测配置 YAML——"
                           "按范式×平台解析 + B7 豁免；窗口 M M1.1）")
    p_cv.add_argument("--session", default=None, help="会话 id（默认 crossval）")
    p_cv.add_argument("--no-verdicts", action="store_true", help="不联动 verdict store")
    p_cv.set_defaults(func=cmd_cross_validate)

    p_tr = sub.add_parser("trace", help="C5：引擎审计过程日志（审计者也可审计）")
    p_tr.add_argument("session_id", help="审计会话 id")
    p_tr.add_argument("--log-dir", default=None, help="事件目录（默认环境变量/用户目录）")
    p_tr.set_defaults(func=cmd_trace)

    p_cap = sub.add_parser(
        "capture-validate", help="C2/C6：签名表校验 + 样例自检（CI 门禁）",
    )
    p_cap.add_argument("--notebook", default=None,
                       help="样例 notebook（可选；存在则自检必须产出候选）")
    p_cap.set_defaults(func=cmd_capture_validate)

    p_vd = sub.add_parser("verdict", help="C3：会话 verdict 清单（final-only 视图）")
    p_vd.add_argument("session_id", help="会话 id")
    p_vd.set_defaults(func=cmd_verdict)

    # ── 窗口 D：benchmark 命令 ──
    p_br = sub.add_parser(
        "benchmark-run", help="D4：任务集批量评测 + 功效报告（bootstrap CI + gap）",
    )
    p_br.add_argument("--tasks-dir", default=None, help="任务目录（默认包内 data/tasks）")
    p_br.add_argument("--split", choices=["public", "hidden"], default=None,
                      help="评测子集（默认全量；None 时输出 gap 检查）")
    p_br.add_argument("--act", choices=["deg", "pan", "scrna"], default=None)
    p_br.add_argument("--seed", type=int, default=42, help="bootstrap 种子（默认 42）")
    p_br.add_argument("--n-boot", type=int, default=2000, help="bootstrap 重采样数")
    p_br.set_defaults(func=cmd_benchmark_run)

    p_bv = sub.add_parser(
        "benchmark-validate", help="D5/E8：任务集三闸（清单+污染+覆盖）+ golden",
    )
    p_bv.add_argument("--tasks-dir", default=None, help="任务目录（默认包内 data/tasks）")
    p_bv.add_argument("--rules-dir", default=None, help="规则目录（默认包内）")
    p_bv.add_argument("--baseline", default=None, help="golden 基线（默认包内副本）")
    p_bv.add_argument("--json", action="store_true",
                      help="输出原始 JSON 报告（默认输出即 JSON，供 CI 使用）")
    p_bv.set_defaults(func=cmd_benchmark_validate)

    # ── 窗口 E：reward 命令 ──
    p_rw = sub.add_parser(
        "reward", help="E1：reward API（step_rewards + trajectory_reward + meta）",
    )
    p_rw.add_argument("trajectory", help="轨迹 JSON 文件路径（v1 数组或 v2 对象/任务）")
    p_rw.add_argument("--act", choices=["deg", "pan", "scrna"], default=None,
                      help="范式（默认从轨迹 act 键推断；B2 同名异构消歧）")
    p_rw.add_argument("--recipe", choices=["A", "B", "C"], default="B",
                      help="配方：A=纯规则分 / B=+L0 硬惩罚（默认）/ C=PRM 预留")
    p_rw.add_argument("--session", default=None,
                      help="采集会话 id（只消费 final verdict；revoked → mask）")
    p_rw.add_argument("--prm-weights", default=None,
                      help="配方 C 权重 JSON（{step_id: weight}，PRM 预留接口）")
    p_rw.set_defaults(func=cmd_reward)

    p_rc = sub.add_parser(
        "reward-calibrate", help="E3：30 任务校准（消融 + 排序一致性 + 分层检验 + 多种子）",
    )
    p_rc.add_argument("--tasks-dir", default=None, help="任务目录（默认包内 data/tasks）")
    p_rc.add_argument("--seed", type=int, default=42, help="bootstrap 种子（默认 42）")
    p_rc.add_argument("--n-boot", type=int, default=2000, help="bootstrap 重采样数")
    p_rc.set_defaults(func=cmd_reward_calibrate)

    p_rv = sub.add_parser(
        "reward-validate", help="E4：reward 自检（映射/确定性/spike-in 锚点/消融/golden）",
    )
    p_rv.add_argument("--baseline", default=None, help="golden 基线（默认包内副本）")
    p_rv.add_argument("--json", action="store_true",
                      help="输出原始 JSON 报告（默认输出即 JSON，供 CI 使用）")
    p_rv.set_defaults(func=cmd_reward_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
