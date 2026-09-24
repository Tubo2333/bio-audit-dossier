"""K2（窗口 K，execution-plan §六.十五 K2）：未知方法 → -1 规则级跳过语义。

A2 修复（2026-08-16）：evaluator 对未识别 choice 不再兜底 L0"危险"——
不认识 ≠ 错误。覆盖：
1. 未识别 choice + 匹配规则 → 决策 -1（不误判 L0）
2. 多规则混合：一条命中 L1 + 一条未识别 → 取 L1（不被拉低）
3. 全部规则未识别 → -1
4. 无规则匹配 → -1（既有语义保持）
5. evaluate_all_rules 跳过未识别规则（冲突检测不参与）
6. override_n2 仍生效（n≤2 → L0，即使 choice 未识别）
7. t-test 家族拼写别名（Student_t_test → ttest_equal_variance）保持 M1.1 L0 语义
"""

import json

from bioaudit.api import audit_decision, run_audit
from bioaudit.engine.evaluator import RuleEvaluator
from bioaudit.paths import RULES_DIR, TRAJECTORIES_DIR
from bioaudit.storage.rule_registry import RuleRegistry


def _load(name: str) -> list[dict]:
    data = json.loads((TRAJECTORIES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["decisions"]


# ── 1. 未识别 choice → -1（不误判 L0）──


def test_unrecognized_choice_is_minus_one_not_l0():
    """匹配规则存在但 choice 不在任何 level 词表 → -1（不再兜底 L0）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "dim_reduction",
        "choice": "some_future_method_xyz",
        "rationale": "unknown",
        "context": {"sequencing": "10X_scRNA_seq", "method": "PCA"},
    }, paradigm="scrna")
    assert d["level"] == -1
    assert d["matched_rules"] == ["D2.1-DIMR-001_reduction"]  # 规则被考虑但未适用
    assert "Unable to evaluate" in d["explanation"]  # 2C 笔 2 成稿句（2026-09-15）


def test_unrecognized_choice_not_in_detection_set():
    """-1 不参与检出（benchmark 检出定义 level∈{0,1} 不变，K2 预注册口径）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "qc_filtering",
        "choice": "quantile_clipping_v99",
        "rationale": "unknown",
        "context": {"sequencing": "10X_scRNA_seq"},
    }, paradigm="scrna")
    assert d["level"] == -1
    assert d["numeric_score"] == 0.5  # 占位值，不参与聚合


# ── 2. 多规则混合：一条命中 + 一条未识别 → 不被拉低 ──


def test_mixed_rules_take_hit_level_not_dragged_down():
    """G1.1+G1.3 双规则：choice 命中 G1.1 L1 词表、G1.3 未识别 →
    取 L1，不被未识别规则的旧兜底 L0 拉低。"""
    d = audit_decision({
        "step_id": "s10",
        "decision_type": "deg_method",
        "choice": "wilcoxon_rank_sum",  # G1.1 L1；G1.3 词表已含（J1）——用 K2 前后对照见报告
        "rationale": "wilcoxon on cells",
        "context": {"sequencing": "10X_scRNA_seq", "n_patients": 5},
    }, paradigm="scrna")
    assert d["level"] == 1
    assert "G1.1-DEG-001_pseudobulk" in d["matched_rules"]


def test_mixed_rules_one_recognized_one_skipped_take_recognized():
    """多规则混合：choice 命中其中一条规则的 level 词表 → 取该评级
    （未识别规则级跳过，不贡献旧兜底 L0）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "immune_correlation_method",
        "choice": "Spearman",
        "rationale": "cell-level spearman",
        "context": {},
    }, paradigm="scrna")
    assert d["level"] == 1
    assert d["matched_rules"] == ["I4.1-IMMU-001_scRNA_correlation_method"]


def test_all_rules_skip_is_minus_one():
    """全部匹配规则未识别 choice → 决策 -1（即使匹配了多条规则）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "deg_method",
        "choice": "magic_rank_test_9000",
        "rationale": "unknown",
        "context": {"sequencing": "10X_scRNA_seq", "n_patients": 5},
    }, paradigm="scrna")
    assert d["level"] == -1
    # G1.1 + G1.3 都匹配（condition 命中）但都未识别 → 两条都留在 matched_rules（溯源）
    assert set(d["matched_rules"]) == {"G1.1-DEG-001_pseudobulk", "G1.3-DEG-003_method"}


# ── 4. 无规则匹配 → -1（既有语义保持）──


def test_no_rule_matched_still_minus_one():
    d = audit_decision({
        "step_id": "s9",
        "decision_type": "totally_unknown_type",
        "choice": "whatever",
        "context": {},
    }, paradigm="deg")
    assert d["level"] == -1
    assert d["matched_rules"] == []


# ── 5. evaluate_all_rules 跳过未识别 ──


def test_evaluate_all_rules_skips_unrecognized():
    reg = RuleRegistry(RULES_DIR / "scRNA")
    reg.load_all()
    from bioaudit.engine.matcher import RuleMatcher
    from bioaudit.models.decision import Decision

    matcher = RuleMatcher(reg)
    parsed, rules = matcher.match(Decision(
        step_id="s10", decision_type="deg_method", choice="magic_rank_test_9000",
        context={"sequencing": "10X_scRNA_seq", "n_patients": 5},
    ))
    assert len(rules) == 2  # G1.1 + G1.3 condition 均命中
    scores = RuleEvaluator().evaluate_all_rules(parsed, rules)
    assert scores == {}  # 全部规则级跳过 → 无评级参与冲突检测


# ── 6. override_n2 仍生效 ──


def test_override_n2_still_applies_for_unrecognized_choice():
    """override_n2（D4，n≤2 → 全部方法 L0）仍生效，即使 choice 未识别。

    M2.5（窗口 M）：键映射修复——scrna G1.1 override 键为 **n_patients**
    （旧实现硬编码 n_replicates：该场景下 n_replicates=2 会误触发，n_patients=5
    本不应触发）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "deg_method",
        "choice": "magic_rank_test_9000",
        "rationale": "unknown",
        "context": {"sequencing": "10X_scRNA_seq", "n_patients": 2},
    }, paradigm="scrna")
    assert d["level"] == 0  # override_n2（D4）：n≤2 全部方法 L0（choice 未识别也生效）
    # 键映射修复：n_patients=5（不满足）→ override 不触发 → 未知方法 -1
    d2 = audit_decision({
        "step_id": "s1",
        "decision_type": "deg_method",
        "choice": "magic_rank_test_9000",
        "rationale": "unknown",
        "context": {"sequencing": "10X_scRNA_seq", "n_patients": 5,
                    "n_replicates": 2},  # 旧实现误读 n_replicates 的现场
    }, paradigm="scrna")
    assert d2["level"] == -1


# ── 7. t-test 家族拼写别名（K2 附带归一化补齐）──


def test_student_t_test_alias_keeps_pan_m11_l0():
    """pan_error D3 'Student_t_test'：拼写别名归一 → M1.1 t-test 家族 L0
    （语义保持：M1.1 明确将 t-test 判 L0 危险，非兜底）。"""
    d = audit_decision({
        "step_id": "D3",
        "decision_type": "deg_method",
        "choice": "Student_t_test",
        "rationale": "student t-test for DEG",
        "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                    "design": "simple_two_group", "n_replicates": 6},
    }, paradigm="pan")
    assert d["level"] == 0
    assert d["matched_rules"] == ["M1.1-DEG-001"]


def test_student_t_test_normalize_mapping():
    ev = RuleEvaluator()
    assert ev._normalize_choice("Student_t_test") == "ttest_equal_variance"
    assert ev._normalize_choice("Welch's t-test") == "ttest_unequal_variance"


# ── 端到端：golden 轨迹中兜底决策的 K2 现状（scrna_melanoma_cellvoyager）──


def test_golden_fallback_decisions_now_minus_one():
    """demo 轨迹兜底决策的 K2→M 演变（C4 漂移记录的对象）。

    K2：兜底 L0 → -1（词表缺口）；M2.6（窗口 M）：词表补齐——
    PCA_arbitrary → L1、no_trajectory → L0（B7 豁免在评测配置层判定，
    引擎无研究范围证据时保守"该做没做"）。"""
    result = run_audit(_load("scrna_melanoma_cellvoyager"), act="scrna")
    by_id = {s["step_id"]: s for s in result["step_scores"]}
    assert by_id["S7"]["level"] == 1       # PCA_arbitrary（M 窗口词表补齐 → L1）
    assert by_id["S11"]["level"] == 0      # no_trajectory（M 窗口词表补齐 → L0）
    # S10 Kruskal_Wallis_cell_level：词表补齐后为 L1（K3 裁决），未补齐前为 -1
    assert by_id["S10"]["level"] in (-1, 1)
    # 词表内 L0 决策保持 L0（不被 K2 误伤）
    assert by_id["S3"]["level"] == 0       # no_doublet_detection
    assert by_id["S6"]["level"] == 0       # no_integration
