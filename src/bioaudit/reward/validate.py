"""reward-validate 门禁（窗口 E / E4.13：CI 双矩阵 reward 自检，离线可运行）。

五闸（任一失败 → exit 1，与 ruleset-validate / benchmark-validate 同风格）：
1. **映射健全**：REWARD_BY_LEVEL 单调非降、-1 mask、天花板 0.85（E1.2 定稿守卫）；
2. **确定性**（F6）：同输入两次 reward() 输出逐字节一致（reward 无随机源）；
3. **spike-in 强锚点**（E3.9/F7）：干净轨迹注入已知 L0 → drop ≥ 0.30 且
   注入步骤被引擎判 L0（L0 注入自校验：audit_decision 实测验证）；
4. **三组消融可运行**（E2.6）：A/B/C 同一 60 条任务三组输出可比（同 schema）；
5. **排序一致性 + 分层均值检验**（拍板 #2）：60 任务 ρ/τ + CI + good/bad
   分层检验——**如实报告为证据，不做点估计门槛**（F5/F7 改验收）；
6. **golden 0 差异**（E4.11 硬验收：评分路径零改动）。

弱锚点（gold 排序一致性）与强锚点（spike-in）同时落地（F7）。
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from bioaudit.benchmark.manifest import load_tasks
from bioaudit.benchmark.paths import TASKS_DIR
from bioaudit.paths import trajectory_path
from bioaudit.reward.calibration import (
    ablate,
    gold_quality,
    multi_seed_report,
    rank_consistency,
    stratified_mean_test,
)
from bioaudit.reward.mapping import (
    CEILING_REWARD,
    MASKED_LEVEL,
    REWARD_BY_LEVEL,
    level_reward,
)

#: 每个范式的干净轨迹（spike-in 底物；全 L3，score 85.0，已实测验证）
CLEAN_TRAJECTORIES = {
    "scrna": "scrna_correct",
    "deg": "deg_correct",
    "pan": "pan_correct",
}

#: spike-in 注入候选（step_id 用 X0 避免与底物冲突；choice 为规则 level_0 方法；
#: context 必须满足该规则 required_context——matcher 条件不满足 → 无规则命中 → -1）
INJECTION_CANDIDATES: dict[str, list[dict]] = {
    "scrna": [
        {"step_id": "X0", "decision_type": "doublet_detection",
         "choice": "no_doublet_detection",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"sequencing": "10X_scRNA_seq", "n_cells": 10000}},
        {"step_id": "X0", "decision_type": "qc_filtering",
         "choice": "no_qc",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"sequencing": "10X_scRNA_seq"}},
        {"step_id": "X0", "decision_type": "deg_method",
         "choice": "ttest_on_raw_counts",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"sequencing": "10X_scRNA_seq"}},
    ],
    "deg": [
        {"step_id": "X0", "decision_type": "multiple_testing_correction",
         "choice": "no_correction",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"analysis_type": "differential_expression"}},
        {"step_id": "X0", "decision_type": "deg_method",
         "choice": "ttest_equal_variance",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                     "design": "simple_two_group", "n_replicates": 3}},
    ],
    "pan": [
        {"step_id": "X0", "decision_type": "cox_ph_assumption",
         "choice": "no_ph_test",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"analysis_type": "survival_analysis",
                     "method": "Cox_regression"}},
        {"step_id": "X0", "decision_type": "events_per_variable",
         "choice": "EPV_less_than_5",
         "rationale": "spike-in 强锚点注入（窗口 E E3.9）",
         "context": {"analysis_type": "survival_analysis"}},
    ],
}


def find_l0_injection(act: str) -> Optional[dict]:
    """自校验选择注入决策：第一个被引擎实测判为 L0 的候选（audit_decision 验证）。"""
    from bioaudit.api import audit_decision

    for cand in INJECTION_CANDIDATES.get(act, []):
        score = audit_decision(cand, paradigm=act)
        if score.get("level") == 0:
            return cand
    return None


def _gate_mapping() -> dict:
    levels = sorted(REWARD_BY_LEVEL)
    monotone = all(
        REWARD_BY_LEVEL[levels[i]] <= REWARD_BY_LEVEL[levels[i + 1]]
        for i in range(len(levels) - 1)
    )
    mask_ok = level_reward(MASKED_LEVEL) is None
    ceiling_ok = CEILING_REWARD == 0.85
    return {
        "ok": bool(monotone and mask_ok and ceiling_ok),
        "monotone_non_decreasing": bool(monotone),
        "minus_one_masked": bool(mask_ok),
        "ceiling": CEILING_REWARD,
        "mapping": REWARD_BY_LEVEL,
    }


def _gate_determinism(act: str = "scrna") -> dict:
    from bioaudit.reward.api import reward

    traj = json.loads(trajectory_path(CLEAN_TRAJECTORIES[act]).read_text(encoding="utf-8"))
    a = reward(traj, act=act)
    b = reward(traj, act=act)
    identical = json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    return {
        "ok": bool(identical), "identical": bool(identical),
        "trajectory": CLEAN_TRAJECTORIES[act],
    }


def _gate_spike_in(act: str = "scrna") -> dict:
    from bioaudit.reward.calibration import spike_in

    traj = json.loads(trajectory_path(CLEAN_TRAJECTORIES[act]).read_text(encoding="utf-8"))
    injection = find_l0_injection(act)
    if injection is None:
        return {"ok": False, "error": f"范式 {act}: 无候选注入被引擎判 L0（候选表失效）"}
    result = spike_in(traj, injection, act, recipe="B")
    result["ok"] = bool(result.pop("pass_"))
    return result


def _gate_ablation() -> dict:
    tasks = load_tasks(TASKS_DIR)
    ab = ablate(tasks)
    ok = True
    errors = []
    table = ab["summary_table"]
    if len(table) != 3:
        ok, errors = False, ["消融配方数 != 3"]
    if len({r["n"] for r in table}) != 1:
        ok = False
        errors.append("三组配方任务数不一致（同输入要求）")
    return {"ok": bool(ok), "summary_table": table, "errors": errors, "n_tasks": len(tasks)}


def _gate_calibration() -> dict:
    """排序一致性 + 分层均值检验（证据报告，非点估计门槛，拍板 #2）。"""
    tasks = load_tasks(TASKS_DIR)
    from bioaudit.reward.calibration import task_reward

    qualities = [gold_quality(t) for t in tasks]
    rewards = [task_reward(t, "B") for t in tasks]
    rc = rank_consistency(qualities, rewards)
    st = stratified_mean_test(tasks, rewards)
    ms = multi_seed_report(tasks)
    return {
        "ok": True,  # 证据闸：计算成功即通过；结论由人工验收（完成报告）
        "rank_consistency": rc,
        "stratified_mean_test": st,
        "multi_seed": ms,
        "n_tasks": len(tasks),
    }


def main(argv: Optional[list[str]] = None) -> int:
    """reward-validate 主入口（exit 0/1；--json 输出原始报告）。"""
    import argparse

    parser = argparse.ArgumentParser(prog="bio-audit reward-validate")
    parser.add_argument("--json", action="store_true",
                        help="输出原始 JSON（默认输出即 JSON，供 CI 使用）")
    parser.add_argument("--baseline", default=None, help="golden 基线路径")
    args = parser.parse_args(argv)

    gates = {}
    errors = []

    g1 = _gate_mapping()
    gates["mapping"] = g1
    if not g1["ok"]:
        errors.append("mapping")

    g2 = _gate_determinism()
    gates["determinism"] = g2
    if not g2["ok"]:
        errors.append("determinism")

    g3 = _gate_spike_in()
    gates["spike_in_anchor"] = g3
    if not g3["ok"]:
        errors.append("spike_in_anchor")

    g4 = _gate_ablation()
    gates["ablation"] = g4
    if not g4["ok"]:
        errors.append("ablation")

    g5 = _gate_calibration()
    gates["calibration"] = g5

    from bioaudit.regression import replay_golden

    ok_golden, golden = replay_golden(baseline=args.baseline)
    gates["golden"] = golden
    if not ok_golden:
        errors.append("golden_diff")

    out = {
        "ok": not errors,
        "gates": gates,
        "errors": errors,
        "generated_at": __import__("datetime").date.today().isoformat(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
