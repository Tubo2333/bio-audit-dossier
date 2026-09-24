"""Golden 回归测试（C4/H1）：20 轨迹 137 决策 vs 冻结基线，必须 0 差异。

- 仓库内基线副本：tests/golden/golden_expected_output_after.json
- 权威基线（旧文档区）：docs/specs/2026-08-13-golden-baseline/golden_expected_output_after.json
  两者哈希一致（B1 迁移时核对），本测试用仓库内副本保证独立可复现。
"""

import json
from pathlib import Path

from bioaudit.regression import REPO_GOLDEN, replay_all

GOLDEN_FILE = Path(REPO_GOLDEN)


def test_golden_zero_diff():
    expected = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    actual = replay_all()

    assert actual["n_trajectories"] == expected["n_trajectories"] == 20
    assert actual["n_decisions"] == expected["n_decisions"] == 137

    exp_by_traj = {t["trajectory"]: t for t in expected["trajectories"]}
    act_by_traj = {t["trajectory"]: t for t in actual["trajectories"]}
    assert set(exp_by_traj) == set(act_by_traj), "轨迹集合不一致"

    for name, e in exp_by_traj.items():
        a = act_by_traj[name]
        assert a["act"] == e["act"]
        assert a["n_decisions"] == e["n_decisions"]
        assert a["trajectory_score"] == e["trajectory_score"]
        assert a["verdict"] == e["verdict"]
        assert a["dimension_scores"] == e["dimension_scores"]
        assert a["step_scores"] == e["step_scores"], f"{name} step_scores 不一致"


def test_golden_summary_consistent():
    """golden_summary.md 的轨迹/分数与基线 JSON 一致（防止文档漂移）。"""
    md = (GOLDEN_FILE.parent / "golden_summary.md").read_text(encoding="utf-8")
    expected = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    for t in expected["trajectories"]:
        row = f"| {t['trajectory']} | {t['act']} | {t['n_decisions']} | {t['trajectory_score']} | {t['verdict']} |"
        assert row in md, f"golden_summary.md 缺行: {row}"
