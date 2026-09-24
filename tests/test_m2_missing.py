"""窗口 M / M2.4-M2.6：missing 三档运行时强制 + override_n2 键映射 + 词表补齐测试。

覆盖（execution-plan §六.十七 M2.4/M2.5/M2.6，方案 M2.7 2026-08-16 项目负责人确认）：
- Option B 规则引用驱动：fail-closed 键缺失且被候选规则引用 → 未验证（level=-2）；
  缺失但无规则引用（如 doublet 的 unit）→ 正常评分（不凭空降级）
- A5 顺序：先最严档位定决策状态，再规则求值；A3 类型/枚举校验 → 键 unverified
- skip 档：依赖缺失键的规则被跳过（matched_rules_skipped 溯源）
- A2 运行时断言：fail-open 键被候选规则引用 → 警告留痕
- override_n2 键映射修复：读规则 override 配置键（n_patients vs n_replicates）+ n=0 修复
- 词表补齐：PCA_arbitrary→L1、no_trajectory→L0
- reward：-2 与 -1 同掩码、原因区分
"""

from bioaudit.api.audit import audit_decision, run_audit
from bioaudit.engine.context_guard import (
    LEVEL_UNVERIFIED,
    resolve,
    rule_referenced_keys,
)
from bioaudit.engine.evaluator import RuleEvaluator
from bioaudit.engine.matcher import RuleMatcher
from bioaudit.models.decision import Decision, ParsedStep
from bioaudit.paths import rules_dir_for
from bioaudit.reward.mapping import (
    MASK_REASON_LEVEL_UNVERIFIED,
    is_masked_level,
    level_reward,
)
from bioaudit.reward.recipes import build_step_rewards
from bioaudit.storage.rule_registry import RuleRegistry


def _decision(dtype, choice, context=None, step="s1"):
    return {
        "step_id": step, "decision_type": dtype, "choice": choice,
        "context": context or {},
    }


def _scrna_eval(decision: dict) -> dict:
    return audit_decision(decision, paradigm="scrna")


# ── 1. Option B：fail-closed 键缺失（规则引用驱动）──


def test_fail_closed_missing_referenced_unverified():
    """doublet_detection 缺 n_cells（D1.1 约束引用）→ 未验证 level=-2。"""
    score = _scrna_eval(_decision(
        "doublet_detection", "scDblFinder", {"sequencing": "10X_scRNA_seq"},
    ))
    assert score["level"] == LEVEL_UNVERIFIED
    assert "n_cells" in score["missing_keys"]
    assert "未验证" in score["explanation"]
    assert score["matched_rules"] == []  # 不进行规则求值（A5 顺序）


def test_fail_closed_missing_not_referenced_scored():
    """doublet_detection 缺 unit（fail-closed 但 D1.1 不引用）→ 正常评分（Option B）。"""
    score = _scrna_eval(_decision(
        "doublet_detection", "scDblFinder",
        {"sequencing": "10X_scRNA_seq", "n_cells": 59399},
    ))
    assert score["level"] == 3
    assert score["missing_keys"] == []


def test_enum_out_of_value_unverified():
    """A3：枚举外值 → 键标 unverified → fail-closed + 引用 → 未验证。"""
    score = _scrna_eval(_decision(
        "doublet_detection", "scDblFinder",
        {"sequencing": "10X", "n_cells": 59399},  # sequencing 拼写外值
    ))
    assert score["level"] == LEVEL_UNVERIFIED
    assert any("sequencing" in k for k in score["missing_keys"])


def test_unverified_distinct_from_minus_one_in_aggregation():
    """-2 未验证与 -1 无法评估：都不进维度分/verdict，但报告可区分。"""
    traj = {
        "version": 2, "trajectory_id": "t", "act": "scrna",
        "decisions": [
            _decision("doublet_detection", "scDblFinder",
                      {"sequencing": "10X_scRNA_seq"}, step="S1"),  # 缺 n_cells → -2
            _decision("dim_reduction", "PCA_fixed_999",
                      {"sequencing": "10X_scRNA_seq", "method": "PCA"}, step="S2"),  # 未识别 → -1
            _decision("qc_filtering", "MAD5_adaptive_threshold",
                      {"sequencing": "10X_scRNA_seq"}, step="S3"),  # 正常 → L3
        ],
    }
    result = run_audit(traj, act="scrna")
    by_step = {s["step_id"]: s for s in result["step_scores"]}
    assert by_step["S1"]["level"] == -2
    assert by_step["S2"]["level"] == -1
    assert by_step["S3"]["level"] == 3
    # -2/-1 同掩码：data_handling 仅由 S3 贡献（0.85）
    assert result["dimension_scores"]["data_handling"] == 0.85
    assert result["eval_verdict"] == "pass"  # 无 L0/L1 → 不因 -2/-1 判错


def test_skip_tier_rule_skipped():
    """skip 档：clustering_method 缺 graph_type（C1.1 引用）→ 该规则被跳过并溯源。"""
    reg = RuleRegistry(rules_dir_for("scrna"))
    reg.load_all()
    matcher = RuleMatcher(reg)
    parsed, rules = matcher.match(Decision(**_decision(
        "clustering_method", "Leiden", {"sequencing": "10X_scRNA_seq"},
    )))
    candidate = reg.rules_for_type("clustering_method")
    res = resolve(parsed, candidate)
    assert res.unverified is False
    assert "C1.1-CLUS-001_method" in res.skipped_rule_ids
    # 规则被剔除后无适用规则 → -1（无法评估，不误判）
    score = RuleEvaluator().evaluate(parsed, [])
    assert score.level == -1


def test_a2_fail_open_referenced_warning():
    """A2 运行时断言：fail-open 键被候选规则引用且缺失 → 警告留痕（静态已禁，防绕过）。"""

    class _StubOnt:
        def get_type(self, tid):
            return {"context_schema": [
                {"key": "harmless_note", "type": "string", "required": False,
                 "missing": "fail-open"},
            ]}

    class _StubRule:
        rule_id = "FAKE-001"
        condition = type("C", (), {
            "required_context": {"harmless_note": "x"},
            "context_constraints": [],
        })()
        scoring = type("S", (), {"override_n2": {}})()

    parsed = ParsedStep(
        step_id="s1", decision_type="fake",
        original=Decision(**_decision("fake", "x", {})),
        normalized_context={},
    )
    res = resolve(parsed, [_StubRule()], ontology=_StubOnt())
    assert res.unverified is False
    assert any("fail-open" in w for w in res.warnings)


# ── 2. override_n2 键映射修复（M2.5）──


def test_override_g11_reads_n_patients_key():
    """G1.1 override 条件为 n_patients<=2：修复前硬编码 n_replicates 不触发，
    修复后读规则配置键 → L0。"""
    score = _scrna_eval(_decision(
        "deg_method", "pseudobulk_DESeq2",
        {"sequencing": "10X_scRNA_seq", "n_patients": 2},  # n_patients<=2 → override
    ))
    assert score["level"] == 0  # override L0（不再误评 L3）


def test_override_g11_high_n_patients_not_triggered():
    score = _scrna_eval(_decision(
        "deg_method", "pseudobulk_DESeq2",
        {"sequencing": "10X_scRNA_seq", "n_patients": 11},
    ))
    assert score["level"] == 3


def test_override_bulk_n_replicates_zero_falsy_fixed():
    """fix-tracking A3：n=0 falsy 漏判修复——n_replicates=0 正确触发 override。"""
    score = audit_decision(_decision(
        "deg_method", "DESeq2",
        {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
         "design": "simple_two_group", "n_replicates": 0},
    ), paradigm="deg")
    assert score["level"] == 0  # override n_replicates<=2（旧实现 falsy 漏判）


def test_override_missing_key_not_triggered():
    """键缺失 → override 不触发（不猜默认）；若 fail-closed 且被引用由未验证层拦截。"""
    score = _scrna_eval(_decision(
        "deg_method", "pseudobulk_DESeq2",
        {"sequencing": "10X_scRNA_seq"},  # 无 n_patients
    ))
    # G1.1 约束 n_patients>=3 引用 n_patients（fail-closed）→ 未验证（Option B）
    assert score["level"] == LEVEL_UNVERIFIED
    assert "n_patients" in score["missing_keys"]


# ── 3. 词表补齐（M2.6，B7 联动）──


def test_vocabulary_pca_arbitrary_l1():
    score = _scrna_eval(_decision(
        "dim_reduction", "PCA_arbitrary",
        {"sequencing": "10X_scRNA_seq", "method": "PCA", "n_comps": 50},
    ))
    assert score["level"] == 1
    assert "D2.1-DIMR-001_reduction" in score["matched_rules"]


def test_vocabulary_no_trajectory_l0():
    """no_trajectory：该做没做 → L0（B7 豁免在评测配置层判定，引擎无证据保守评级）。"""
    score = _scrna_eval(_decision(
        "trajectory_inference", "no_trajectory", {"sequencing": "10X_scRNA_seq"},
    ))
    assert score["level"] == 0
    assert "T1.1-TRAJ-001_inference" in score["matched_rules"]


# ── 4. reward：-2 mask ──


def test_reward_masks_unverified_distinct_reason():
    steps = [
        {"step_id": "s1", "decision_type": "doublet_detection", "level": -2},
        {"step_id": "s2", "decision_type": "qc_filtering", "level": 3},
        {"step_id": "s3", "decision_type": "dim_reduction", "level": -1},
    ]
    built = build_step_rewards(steps, order=["s1", "s2", "s3"])
    by_id = {s.step_id: s for s in built}
    assert by_id["s1"].masked is True
    assert by_id["s1"].mask_reason == MASK_REASON_LEVEL_UNVERIFIED
    assert by_id["s3"].mask_reason == "level_minus_one"
    assert by_id["s2"].masked is False
    assert level_reward(-2) is None
    assert is_masked_level(-2) is True


def test_rule_referenced_keys_extraction():
    reg = RuleRegistry(rules_dir_for("scrna"))
    reg.load_all()
    refs = rule_referenced_keys(reg.rules_for_type("deg_method"))
    assert "n_patients" in refs  # G1.1 约束
    assert "sequencing" in refs  # G1.1 required_context
