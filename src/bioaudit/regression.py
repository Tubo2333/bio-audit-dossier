"""golden 回归引擎（包内实现，路径全部锚定；C4/H1：基线冻结 + 漂移检测）。

- replay_all()：用包内规则/轨迹复算 20 条轨迹 137 决策，输出 golden 同构结构
- replay_golden()：与基线文件（默认包内副本）逐字段 diff，返回 (ok, summary)
- 基线权威副本：docs/specs/2026-08-13-golden-baseline/golden_expected_output_after.json
  （旧文档区）；仓库内副本：tests/golden/golden_expected_output_after.json（哈希一致）
"""

import json
from pathlib import Path

from bioaudit.engine.aggregator import ScoreAggregator
from bioaudit.engine.evaluator import RuleEvaluator
from bioaudit.engine.matcher import RuleMatcher
from bioaudit.models.decision import Decision
from bioaudit.models.score import DecisionScore
from bioaudit.paths import ACT_RULE_SUBDIRS, TRAJECTORIES_DIR, rules_dir_for
from bioaudit.storage.rule_registry import RuleRegistry

# 仓库内冻结基线副本（tests/golden/）
REPO_GOLDEN = Path(__file__).resolve().parent.parent.parent / "tests" / "golden" / "golden_expected_output_after.json"


def replay_all() -> dict:
    """用包内资产复算全部轨迹（与 golden 生成脚本同构）。"""
    registries = {}
    for act in ACT_RULE_SUBDIRS:
        reg = RuleRegistry(rules_dir_for(act))
        reg.load_all()
        registries[act] = reg

    evaluator = RuleEvaluator()
    aggregator = ScoreAggregator()
    # M2.4（窗口 M）：missing 三档强制（fail-closed 缺失 → 未验证）
    from bioaudit.engine.context_guard import score_decision

    trajectories = []
    total_decisions = 0
    for traj_file in sorted(TRAJECTORIES_DIR.glob("*.json")):
        name = traj_file.stem
        act = name.split("_")[0]
        if act not in ACT_RULE_SUBDIRS:
            continue
        matcher = RuleMatcher(registries[act])
        traj = json.loads(traj_file.read_text(encoding="utf-8"))
        decisions = traj if isinstance(traj, list) else traj.get("decisions", [])
        scores = []
        for item in decisions:
            d = Decision(**item)
            parsed, rules = matcher.match(d)
            candidate_rules = registries[act].rules_for_type(parsed.decision_type)
            sc = score_decision(parsed, rules, candidate_rules, evaluator)
            # M2.4（窗口 M）：missing_keys 为 -2 的伴随字段——golden 比较载荷排除
            # （语义变化由 level/explanation 捕获；避免 137 条空数组机械漂移）
            scores.append(sc.model_dump(exclude={"missing_keys"}))
            total_decisions += 1
        result = aggregator.aggregate([DecisionScore(**s) for s in scores])
        trajectories.append({
            "trajectory": name,
            "act": act,
            "n_decisions": len(decisions),
            "trajectory_score": result.trajectory_score,
            "verdict": result.verdict,
            "dimension_scores": {k: round(v, 4) for k, v in result.dimension_scores.items()},
            "step_scores": scores,
        })

    return {
        "n_trajectories": len(trajectories),
        "n_decisions": total_decisions,
        "trajectories": trajectories,
    }


def replay_golden(baseline: Path | str | None = None) -> tuple[bool, dict]:
    """重放并与基线 diff（逐字段）。返回 (0 差异?, 汇总)。"""
    baseline_path = Path(baseline) if baseline else REPO_GOLDEN
    if not baseline_path.exists():
        raise FileNotFoundError(f"基线文件不存在: {baseline_path}")

    expected = json.loads(baseline_path.read_text(encoding="utf-8"))
    actual = replay_all()

    diffs: list[dict] = []
    exp_by_traj = {t["trajectory"]: t for t in expected["trajectories"]}
    act_by_traj = {t["trajectory"]: t for t in actual["trajectories"]}

    for name in sorted(set(exp_by_traj) | set(act_by_traj)):
        if name not in exp_by_traj:
            diffs.append({"trajectory": name, "kind": "missing_in_baseline"})
            continue
        if name not in act_by_traj:
            diffs.append({"trajectory": name, "kind": "missing_in_replay"})
            continue
        e, a = exp_by_traj[name], act_by_traj[name]
        for key in ("act", "n_decisions", "trajectory_score", "verdict", "dimension_scores"):
            if e.get(key) != a.get(key):
                diffs.append({"trajectory": name, "kind": key, "expected": e.get(key), "actual": a.get(key)})
        exp_steps = {s["step_id"]: s for s in e["step_scores"]}
        act_steps = {s["step_id"]: s for s in a["step_scores"]}
        for sid in sorted(set(exp_steps) | set(act_steps)):
            if sid not in exp_steps:
                diffs.append({"trajectory": name, "step": sid, "kind": "missing_in_baseline"})
                continue
            if sid not in act_steps:
                diffs.append({"trajectory": name, "step": sid, "kind": "missing_in_replay"})
                continue
            if exp_steps[sid] != act_steps[sid]:
                diffs.append({
                    "trajectory": name, "step": sid, "kind": "step_score",
                    "expected": exp_steps[sid], "actual": act_steps[sid],
                })

    ok = not diffs
    summary = {
        "ok": ok,
        "n_trajectories_expected": expected.get("n_trajectories"),
        "n_decisions_expected": expected.get("n_decisions"),
        "n_trajectories_replayed": actual["n_trajectories"],
        "n_decisions_replayed": actual["n_decisions"],
        "n_diffs": len(diffs),
        "diffs": diffs[:20],
        "baseline": str(baseline_path),
    }
    return ok, summary


__all__ = ["replay_all", "replay_golden", "REPO_GOLDEN"]
