"""reward 包测试（窗口 E：阶段 4 reward 训练信号；E4.12 新增测试）。

覆盖：映射定稿（数值/单调/-1 mask/饱和）/ 配方 A/B/C（含硬惩罚数学与
PRM 接口）/ 时序化与 final-only（B4 状态位）/ F4 守卫（交叉验证判定不进
reward）/ 确定性（F6）/ 排序一致性 + 分层均值检验（拍板 #2）/
spike-in 强锚点（E3.9）/ 报告集成（E4.10）/ CLI。
"""

import inspect
import json

import pytest

from bioaudit.paths import trajectory_path
from bioaudit.reward.mapping import (
    CEILING_REWARD,
    HARD_PENALTY_GAMMA,
    MASK_REASON_LEVEL_MINUS_ONE,
    MASK_REASON_NO_VERDICT,
    MASK_REASON_PROVISIONAL,
    MASK_REASON_REVOKED,
    REWARD_BY_LEVEL,
    level_reward,
)
from bioaudit.reward.recipes import (
    DEFAULT_RECIPE,
    StepReward,
    has_unmasked_l0,
    trajectory_reward,
)

CLEAN_SCRNA = json.loads(trajectory_path("scrna_correct").read_text(encoding="utf-8"))
CLEAN_DEG = json.loads(trajectory_path("deg_correct").read_text(encoding="utf-8"))
CLEAN_PAN = json.loads(trajectory_path("pan_correct").read_text(encoding="utf-8"))


# ── E1.2：映射定稿（数值 + 单调 + -1 mask + 饱和）────────────────────────────


def test_mapping_values_frozen():
    """映射表定稿数值（docs/reward-mapping.md §3）：非线性，天花板 0.85。"""
    assert REWARD_BY_LEVEL == {4: 1.00, 3: 0.85, 2: 0.60, 1: 0.30, 0: 0.00}
    assert CEILING_REWARD == 0.85
    assert level_reward(-1) is None, "-1 必须 mask（F1）"
    assert level_reward(3) == 0.85
    assert level_reward(0) == 0.0


def test_mapping_monotone_non_decreasing():
    levels = sorted(REWARD_BY_LEVEL)
    for i in range(len(levels) - 1):
        assert REWARD_BY_LEVEL[levels[i]] <= REWARD_BY_LEVEL[levels[i + 1]]


def test_ceiling_saturation_all_l3_is_085():
    """85.0 天花板饱和：全 L3 轨迹 reward = 0.85（不做 evidence 微调，决策记录 §4）。"""
    from bioaudit.reward.api import reward

    for traj, act in ((CLEAN_SCRNA, "scrna"), (CLEAN_DEG, "deg"), (CLEAN_PAN, "pan")):
        r = reward(traj, act=act)
        assert r["trajectory_reward"] == 0.85
        assert r["meta"]["saturation"] == "ceiling_0.85_no_micro_adjustment"
        assert all(s["level"] == 3 for s in r["step_rewards"])


# ── E1.2：min vs mean 聚合（credit assignment，决策记录 §5）───────────────────


def test_aggregation_is_mean_not_min():
    """定稿：mean（+硬惩罚），非 min——min 使 credit assignment 塌缩（决策记录 §5）。"""
    steps = [
        StepReward(step_id="a", decision_type="t", order=0, level=3, reward=0.85),
        StepReward(step_id="b", decision_type="t", order=1, level=3, reward=0.85),
        StepReward(step_id="c", decision_type="t", order=2, level=2, reward=0.60),
    ]
    assert trajectory_reward(steps, recipe="A") == pytest.approx((0.85 + 0.85 + 0.60) / 3)
    assert trajectory_reward(steps, recipe="A") != pytest.approx(0.60)  # 不是 min


# ── E1.1：API 结构（step_rewards / trajectory_reward / meta + 三元组快照）─────


def test_reward_api_structure_and_snapshot():
    from bioaudit.reward.api import reward

    r = reward(CLEAN_SCRNA, act="scrna")
    assert set(r) == {"step_rewards", "trajectory_reward", "meta"}
    assert r["trajectory_reward"] == 0.85
    assert len(r["step_rewards"]) == len(CLEAN_SCRNA["decisions"])
    assert r["meta"]["reward_schema"] == "reward.v1"
    assert r["meta"]["status"] == "experimental_uncalibrated"
    snap = r["meta"]["snapshot"]
    for key in ("ruleset_version", "ontology_version", "engine_version"):
        assert key in snap, f"快照三元组缺 {key}（C1/P2）"


def test_reward_api_accepts_v1_array_and_infers_act():
    from bioaudit.reward.api import reward

    decisions = CLEAN_SCRNA["decisions"]
    r = reward(decisions, act="scrna")
    assert r["trajectory_reward"] == 0.85
    r2 = reward(CLEAN_SCRNA, act=None)  # v2 对象带 act 键 → 推断
    assert r2["trajectory_reward"] == 0.85


def test_reward_api_rejects_bad_paradigm():
    from bioaudit.errors import BioAuditError
    from bioaudit.reward.api import reward

    with pytest.raises(BioAuditError):
        reward(CLEAN_SCRNA, act="not_a_paradigm")


# ── E1.3：时序化 + final-only（B4 状态位）────────────────────────────────────


def test_temporal_order_and_sources():
    """时序化：order 保留声明顺序；M3 补漏（backfilled）按阶段末尾聚合。"""
    from bioaudit.reward.api import reward

    r = reward(CLEAN_SCRNA, act="scrna")
    orders = [s["order"] for s in r["step_rewards"]]
    assert orders == list(range(len(orders)))
    assert all(s["source"] == "declared" for s in r["step_rewards"])

    # 显式时序 + 来源（M1 声明为主 + M3 补漏条目在末尾）
    order = [s["step_id"] for s in r["step_rewards"]][:6] + ["M3_EXTRA"]
    source_map = {"M3_EXTRA": "backfilled"}
    from bioaudit.api import run_audit
    from bioaudit.reward.api import reward_from_scores

    state = run_audit(CLEAN_SCRNA, act="scrna")
    steps = state["step_scores"] + [{
        **state["step_scores"][0], "step_id": "M3_EXTRA",
    }]
    rr = reward_from_scores(steps, order=order, source_map=source_map)
    ids = [s["step_id"] for s in rr["step_rewards"]]
    assert ids == order
    m3 = next(s for s in rr["step_rewards"] if s["step_id"] == "M3_EXTRA")
    assert m3["source"] == "backfilled"


def _verdict(step_id, status):
    return {
        "verdict_id": f"v_{step_id}", "session_id": "sess", "step_id": step_id,
        "decision_type": "t", "choice": "c", "paradigm": "scrna",
        "status": status, "provenance_source": "M1声明",
        "created_at": "2026-08-16T00:00:00", "updated_at": "2026-08-16T00:00:00",
        "history": [], "score_snapshot": {},
    }


def test_final_only_verdict_masking():
    """B4：只消费 final——revoked/provisional/无记录 → mask；无会话 → 全 final。"""
    from bioaudit.api import run_audit
    from bioaudit.reward.api import reward_from_scores

    state = run_audit(CLEAN_SCRNA, act="scrna")
    steps = state["step_scores"]
    verdicts = [
        _verdict(steps[0]["step_id"], "final"),
        _verdict(steps[1]["step_id"], "revoked"),
        _verdict(steps[2]["step_id"], "provisional"),
        _verdict(steps[3]["step_id"], "final"),
        # steps[4] 无记录
    ]
    rr = reward_from_scores(steps, verdicts=verdicts)
    by_id = {s["step_id"]: s for s in rr["step_rewards"]}
    assert by_id[steps[0]["step_id"]]["masked"] is False
    assert by_id[steps[1]["step_id"]]["masked"] is True
    assert by_id[steps[1]["step_id"]]["mask_reason"] == MASK_REASON_REVOKED
    assert by_id[steps[2]["step_id"]]["mask_reason"] == MASK_REASON_PROVISIONAL
    assert by_id[steps[4]["step_id"]]["mask_reason"] == MASK_REASON_NO_VERDICT
    assert rr["meta"]["verdict_mode"] == "final_only"
    # 12 步 − 2 条 final = 10 步被 mask（revoked 1 + provisional 1 + 无记录 8）
    assert rr["meta"]["n_masked"] == 10

    # 无会话（legacy/benchmark）：全部视为 final
    rr2 = reward_from_scores(steps)
    assert rr2["meta"]["verdict_mode"] == "all_final"
    assert rr2["meta"]["n_masked"] == 0


def test_final_only_via_verdict_store_session(tmp_path, monkeypatch):
    """集成 B4：reward(session_id=...) 走 VerdictStore，revoked 步骤被 mask。"""

    from bioaudit.capture.verdict import VerdictStatus, VerdictStore
    from bioaudit.reward.api import reward

    monkeypatch.setenv("BIOAUDIT_VERDICT_DIR", str(tmp_path))
    store = VerdictStore()
    sid = "reward_sess"
    steps = CLEAN_SCRNA["decisions"]
    store.create(sid, steps[0]["step_id"], steps[0]["decision_type"],
                 steps[0]["choice"], "scrna", "M1声明",
                 status=VerdictStatus.FINAL)
    store.create(sid, steps[1]["step_id"], steps[1]["decision_type"],
                 steps[1]["choice"], "scrna", "M1声明",
                 status=VerdictStatus.REVOKED, reason="M3 判虚报")
    r = reward(CLEAN_SCRNA, act="scrna", session_id=sid)
    assert r["meta"]["verdict_mode"] == "final_only"
    by_id = {s["step_id"]: s for s in r["step_rewards"]}
    assert by_id[steps[0]["step_id"]]["masked"] is False
    assert by_id[steps[1]["step_id"]]["masked"] is True
    assert by_id[steps[1]["step_id"]]["mask_reason"] == MASK_REASON_REVOKED


def test_minus_one_masked_in_aggregation():
    """-1（无法评估）必须 mask：不参与分子也不参与分母（F1 纪律）。"""
    from bioaudit.reward.api import reward

    decisions = CLEAN_SCRNA["decisions"] + [{
        "step_id": "UNK1", "decision_type": "no_such_type_xyz",
        "choice": "whatever", "rationale": "unmatchable",
    }]
    r = reward(decisions, act="scrna")
    unk = next(s for s in r["step_rewards"] if s["step_id"] == "UNK1")
    assert unk["level"] == -1
    assert unk["masked"] is True
    assert unk["mask_reason"] == MASK_REASON_LEVEL_MINUS_ONE
    assert unk["reward"] is None
    # 全对 12 步 + 1 个 -1 → reward 仍为 0.85（-1 不参与聚合，分母 = 12）
    assert r["trajectory_reward"] == 0.85
    assert r["meta"]["n_masked"] == 1


def test_all_masked_returns_none():
    """全部被 mask → trajectory_reward = None（不可评估，不给 0 虚假信号）。"""
    from bioaudit.api import run_audit
    from bioaudit.reward.api import reward_from_scores

    state = run_audit(CLEAN_SCRNA, act="scrna")
    verdicts = [_verdict(s["step_id"], "provisional") for s in state["step_scores"]]
    rr = reward_from_scores(state["step_scores"], verdicts=verdicts)
    assert rr["trajectory_reward"] is None


# ── E2.5：配方 A/B/C + 硬惩罚 + PRM 预留接口 ─────────────────────────────────


def test_recipe_A_pure_rule_mean():
    from bioaudit.reward.api import reward

    rA = reward(CLEAN_SCRNA, act="scrna", recipe="A")
    assert rA["trajectory_reward"] == 0.85
    assert rA["meta"]["aggregation"] == "mean"


def test_recipe_B_hard_penalty_math():
    """B = A × γ 当且仅当存在未 mask L0；二元惩罚（不随 L0 数量复利）。"""
    steps = [
        StepReward(step_id="a", decision_type="t", order=0, level=3, reward=0.85),
        StepReward(step_id="b", decision_type="t", order=1, level=0, reward=0.0),
        StepReward(step_id="c", decision_type="t", order=2, level=3, reward=0.85),
    ]
    a = trajectory_reward(steps, recipe="A")
    b = trajectory_reward(steps, recipe="B")
    assert b == pytest.approx(a * HARD_PENALTY_GAMMA, abs=1e-6)
    assert has_unmasked_l0(steps) is True

    # 两个 L0 不复合（二元语义：一个 L0 已使结论失效）
    steps2 = [
        StepReward(step_id="a", decision_type="t", order=0, level=0, reward=0.0),
        StepReward(step_id="b", decision_type="t", order=1, level=0, reward=0.0),
        StepReward(step_id="c", decision_type="t", order=2, level=3, reward=0.85),
    ]
    b2 = trajectory_reward(steps2, recipe="B")
    assert b2 == pytest.approx(
        trajectory_reward(steps2, recipe="A") * HARD_PENALTY_GAMMA, abs=1e-6
    )


def test_recipe_B_penalty_only_for_unmasked_l0():
    """revoked 的 L0 不触发惩罚（只消费 final 纪律）。"""
    steps = [
        StepReward(step_id="a", decision_type="t", order=0, level=0, reward=None,
                   masked=True, mask_reason=MASK_REASON_REVOKED),
        StepReward(step_id="b", decision_type="t", order=1, level=3, reward=0.85),
    ]
    assert has_unmasked_l0(steps) is False
    assert trajectory_reward(steps, recipe="B") == pytest.approx(0.85)


def test_recipe_C_prm_placeholder_interface():
    """C：PRM 预留接口——默认均匀权重（占位）→ C≡A；非均匀权重改变结果。"""
    from bioaudit.reward.api import reward, reward_from_scores

    # 真实轨迹（全 L3）：占位权重下 C ≡ A
    rA = reward(CLEAN_SCRNA, act="scrna", recipe="A")
    rC = reward(CLEAN_SCRNA, act="scrna", recipe="C")
    assert rC["trajectory_reward"] == rA["trajectory_reward"]

    # 混合等级轨迹：接口生效（非均匀权重改变输出，证明接口是活的）
    scores = [
        {"step_id": "a", "decision_type": "t", "level": 3},
        {"step_id": "b", "decision_type": "t", "level": 2},
        {"step_id": "c", "decision_type": "t", "level": 0},
    ]
    mC = reward_from_scores(scores, recipe="C")
    mA = reward_from_scores(scores, recipe="A")
    assert mC["trajectory_reward"] == mA["trajectory_reward"]
    mC2 = reward_from_scores(scores, recipe="C", prm_weights={"a": 10.0})
    assert mC2["trajectory_reward"] > mC["trajectory_reward"]
    assert mC2["meta"]["aggregation"] == "weighted_mean"


def test_prm_weights_do_not_resurrect_masked_steps():
    """mask 先于加权：被 mask 步骤即使给了权重也不参与（-1/final-only 纪律）。"""
    steps = [
        StepReward(step_id="a", decision_type="t", order=0, level=-1, reward=None,
                   masked=True, mask_reason=MASK_REASON_LEVEL_MINUS_ONE),
        StepReward(step_id="b", decision_type="t", order=1, level=3, reward=0.85),
    ]
    assert trajectory_reward(steps, recipe="C", prm_weights={"a": 100.0}) == pytest.approx(0.85)


# ── E2.4：F4 守卫（交叉验证四类判定不进 reward）──────────────────────────────


def test_f4_reward_does_not_consume_cross_validation_stats():
    """F4 守卫（源码级）：reward 包不消费交叉验证器（四类判定不进 reward）。

    B4 final-only 消费（verdict 状态位）是 reward 的合法输入；被禁止的是
    交叉验证四类判定（虚报/漏报/未验证）——两者以模块边界区分。
    """
    from bioaudit.reward import api, calibration, mapping, recipes, validate

    for mod in (api, calibration, mapping, recipes, validate):
        src = inspect.getsource(mod)
        assert "cross_validator" not in src, f"{mod.__name__} 引用交叉验证器（F4 违规）"
        assert "CrossValidator" not in src, f"{mod.__name__} 引用交叉验证器（F4 违规）"
        assert "cross_validation" not in src, f"{mod.__name__} 引用交叉验证统计（F4 违规）"
        assert "m3_parser" not in src, f"{mod.__name__} 引用 M3 解析器（F4 违规）"


def test_f4_reward_output_has_no_four_judgment_stats():
    """F4 守卫（输出级）：reward 输出不含虚报/漏报/未验证等判定字段。"""
    from bioaudit.reward.api import reward

    r = reward(CLEAN_SCRNA, act="scrna")
    blob = json.dumps(r, ensure_ascii=False)
    for kw in ("false_report", "missed", "unverified", "cross_validation",
               "judgement", "false_report_count"):
        assert kw not in blob, f"reward 输出泄漏交叉验证判定字段: {kw}（F4 违规）"


# ── E2.6 / E3.8：消融可运行 + 确定性（F6）────────────────────────────────────


def test_ablation_three_recipes_comparable_on_same_input():
    """E2.6：A/B/C 同一输入三组输出可比（同 schema、同任务数）。"""
    from bioaudit.benchmark.manifest import load_tasks
    from bioaudit.benchmark.paths import TASKS_DIR
    from bioaudit.reward.calibration import ablate

    tasks = load_tasks(TASKS_DIR)
    ab = ablate(tasks, seed=42, n_boot=200)
    assert set(ab["recipes"]) == {"A", "B", "C"}
    tables = [ab["recipes"][k]["reward_table"] for k in ("A", "B", "C")]
    assert len(tables[0]) == len(tables[1]) == len(tables[2]) == len(tasks)
    for recipe in ("A", "B", "C"):
        rc = ab["recipes"][recipe]["rank_consistency"]
        assert rc["n"] >= 20, "排序一致性配对样本不足"
    # B 的硬惩罚应整体压低含 L0 任务（均值 B < A 在含 L0 任务上逐条成立）
    rows_a = {r["trajectory_id"]: r["reward"] for r in tables[0]}
    rows_b = {r["trajectory_id"]: r["reward"] for r in tables[1]}
    penalized = [tid for tid in rows_a if rows_a[tid] != rows_b[tid]]
    assert penalized, "配方 B 至少应惩罚一条轨迹（硬惩罚生效）"
    for tid in penalized:
        assert rows_b[tid] < rows_a[tid]


def test_reward_determinism():
    """F6：同输入两次 reward() 输出逐字节一致（无随机源）。"""
    from bioaudit.reward.api import reward

    a = json.dumps(reward(CLEAN_SCRNA, act="scrna"), sort_keys=True)
    b = json.dumps(reward(CLEAN_SCRNA, act="scrna"), sort_keys=True)
    assert a == b


def test_multi_seed_stability():
    """F6：多种子下点估计恒定（确定性）+ bootstrap CI 边界稳定。"""
    from bioaudit.benchmark.manifest import load_tasks
    from bioaudit.benchmark.paths import TASKS_DIR
    from bioaudit.reward.calibration import multi_seed_report

    tasks = load_tasks(TASKS_DIR)
    ms = multi_seed_report(tasks, n_boot=200)
    assert ms["deterministic_point_estimates"] is True
    assert ms["ci_stable_within_0_05"] is True
    assert len(ms["rows"]) == 5


# ── E3.7：排序一致性 + 分层均值检验（拍板 #2）────────────────────────────────


def test_rank_consistency_reported_with_ci():
    """F5/F7：ρ/τ + bootstrap CI 如实报告（证据，非点估计门槛）。"""
    from bioaudit.benchmark.manifest import load_tasks
    from bioaudit.benchmark.paths import TASKS_DIR
    from bioaudit.reward.calibration import (
        gold_quality,
        rank_consistency,
        task_reward,
    )

    tasks = load_tasks(TASKS_DIR)
    q = [gold_quality(t) for t in tasks]
    r = [task_reward(t, "B") for t in tasks]
    rc = rank_consistency(q, r, seed=42, n_boot=200)
    assert rc["n"] == len(tasks)
    assert rc["spearman"]["point"] is not None
    assert rc["kendall_tau_b"]["point"] is not None
    assert rc["spearman"]["ci"][0] <= rc["spearman"]["point"] <= rc["spearman"]["ci"][1]
    k_ci, k_pt = rc["kendall_tau_b"]["ci"], rc["kendall_tau_b"]["point"]
    assert k_ci[0] <= k_pt <= k_ci[1]


def test_stratified_mean_test_significant_separation():
    """拍板 #2 分层均值检验：好/坏任务组 reward 显著分离（预注册分组）。"""
    from bioaudit.benchmark.manifest import load_tasks
    from bioaudit.benchmark.paths import TASKS_DIR
    from bioaudit.reward.calibration import stratified_mean_test, task_reward

    tasks = load_tasks(TASKS_DIR)
    r = [task_reward(t, "B") for t in tasks]
    st = stratified_mean_test(tasks, r, seed=42, n_boot=200)
    assert st["ok"] is True
    assert st["significant_separation"] is True, (
        f"好/坏任务组 reward 未显著分离（拍板 #2 验收）: {st}"
    )
    assert st["mean_diff_good_minus_bad"] > 0
    assert st["permutation_p"] < 0.05


# ── E3.9：spike-in 强锚点 ─────────────────────────────────────────────────────


def test_spike_in_known_l0_drops_reward():
    """强锚点：干净轨迹注入已知 L0 → reward 显著下降（drop ≥ 0.30）。"""
    from bioaudit.api import audit_decision
    from bioaudit.reward.calibration import spike_in

    injection = {
        "step_id": "X0", "decision_type": "doublet_detection",
        "choice": "no_doublet_detection",
        "rationale": "spike-in 强锚点注入",
        "context": {"sequencing": "10X_scRNA_seq", "n_cells": 10000},
    }
    # 注入自校验：引擎必须判 L0（否则锚点无效）
    assert audit_decision(injection, paradigm="scrna")["level"] == 0
    res = spike_in(CLEAN_SCRNA, injection, "scrna", recipe="B")
    assert res["injected_level"] == 0
    assert res["clean_reward"] == 0.85
    assert res["drop"] >= res["min_drop"] >= 0.30
    assert res["penalty_applied"] is True
    assert res["pass_"] is True


def test_spike_in_l1_injection_smaller_drop_than_l0():
    """锚点敏感性：L1 注入的下降远小于 L0（硬惩罚针对"导致错误结论"）。"""
    from bioaudit.api import audit_decision
    from bioaudit.reward.calibration import spike_in

    l1_injection = {
        "step_id": "X0", "decision_type": "doublet_detection",
        "choice": "Solo",  # D1.1 level_1
        "rationale": "spike-in L1 对照",
        "context": {"sequencing": "10X_scRNA_seq", "n_cells": 10000},
    }
    assert audit_decision(l1_injection, paradigm="scrna")["level"] == 1
    res_l1 = spike_in(CLEAN_SCRNA, l1_injection, "scrna", recipe="B")
    res_l0 = spike_in(CLEAN_SCRNA, {
        "step_id": "X0", "decision_type": "doublet_detection",
        "choice": "no_doublet_detection",
        "rationale": "spike-in L0",
        "context": {"sequencing": "10X_scRNA_seq", "n_cells": 10000},
    }, "scrna", recipe="B")
    assert res_l1["drop"] < res_l0["drop"]


def test_spike_in_anchor_works_all_paradigms():
    """强锚点三范式落地（deg/pan/scrna 各一，注入自校验 L0）。"""
    from bioaudit.api import audit_decision
    from bioaudit.reward.calibration import spike_in

    cases = [
        (CLEAN_DEG, "deg", {"step_id": "X0", "decision_type": "multiple_testing_correction",
                            "choice": "no_correction", "rationale": "x",
                            "context": {"analysis_type": "differential_expression"}}),
        (CLEAN_PAN, "pan", {"step_id": "X0", "decision_type": "cox_ph_assumption",
                            "choice": "no_ph_test", "rationale": "x",
                            "context": {"analysis_type": "survival_analysis",
                                        "method": "Cox_regression"}}),
    ]
    for traj, act, injection in cases:
        assert audit_decision(injection, paradigm=act)["level"] == 0
        res = spike_in(traj, injection, act, recipe="B")
        assert res["pass_"] is True, f"{act} spike-in 失败: {res}"


# ── E4.10：报告集成（experimental 标注，C3 语义不变）─────────────────────────


def test_report_contains_experimental_reward_block():
    from bioaudit.api import run_audit

    state = run_audit(CLEAN_SCRNA, act="scrna")
    block = state["report"]["reward"]
    assert block["meta"]["status"] == "experimental_uncalibrated"
    assert block["meta"]["reward_schema"] == "reward.v1"
    assert block["trajectory_reward"] == 0.85
    # C3 语义不变：既有报告字段与引擎版本不受影响
    assert state["report"]["trajectory_score"] == 85.0
    import bioaudit
    assert state["report"]["engine_version"] == bioaudit.__version__


def test_report_reward_block_failure_is_non_fatal():
    """外围层纪律：reward 块异常不得拖垮报告（降级块 + 主报告完整）。"""
    from bioaudit.api import run_audit
    from bioaudit.reward import api as reward_api

    original = reward_api.report_reward_block
    try:
        def broken(*a, **k):
            raise RuntimeError("simulated reward failure")
        reward_api.report_reward_block = broken
        state2 = run_audit(CLEAN_SCRNA, act="scrna")
        assert state2["report"]["trajectory_score"] == 85.0  # 主报告不受影响
        assert state2["report"]["reward"]["trajectory_reward"] is None
        assert "error" in state2["report"]["reward"]
    finally:
        reward_api.report_reward_block = original


# ── E4.12：CLI ───────────────────────────────────────────────────────────────


def test_cli_reward_smoke(capsys, tmp_path):
    from bioaudit.cli import main

    path = tmp_path / "traj.json"
    path.write_text(json.dumps(CLEAN_SCRNA), encoding="utf-8")
    rc = main(["reward", str(path), "--act", "scrna"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["trajectory_reward"] == 0.85
    assert out["meta"]["recipe"] == DEFAULT_RECIPE


def test_cli_reward_calibrate_smoke(capsys):
    from bioaudit.cli import main

    rc = main(["reward-calibrate", "--n-boot", "100"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["n_tasks"] == 60
    assert set(out["ablation"]["recipes"]) == {"A", "B", "C"}


def test_cli_reward_validate_gates(capsys):
    """E4.13：reward-validate 五闸全绿（映射/确定性/spike-in/消融/golden）。"""
    from bioaudit.cli import main

    rc = main(["reward-validate"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, f"reward-validate 失败: {out.get('errors')}"
    assert out["ok"] is True
    for gate in ("mapping", "determinism", "spike_in_anchor", "ablation", "golden"):
        assert gate in out["gates"]
        if gate != "calibration":
            assert out["gates"][gate]["ok"] is True, f"门禁 {gate} 未通过"
    assert out["gates"]["golden"]["n_diffs"] == 0  # E4.11 硬验收
