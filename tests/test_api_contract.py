"""B3 API 契约测试（refactor-plan-v1.1 B1/B2；audit-report A5/A7/A15）。

覆盖（B3 验收项 6，各 ≥1 例）：
- 非法输入 → 显式错误码（bad-request / validation-error / paradigm-not-found），
  不静默降级（A5/A15：未知 act 不再静默回退全量规则）；
- audit_decision **必填 paradigm**（B2：缺参 TypeError；未知范式 paradigm-not-found）；
- paradigm 消歧：同 decision_type/choice 不同范式 → 不同评分（deg_method/DESeq2）；
- human_overrides：int 且 -1..4，非法拒绝 + 记录 invalid_override_rejected 事件（A7）；
- match_details 输入校验；错误码体系与文档集合一致性守卫。
"""

import json

import pytest

from bioaudit.api import audit_decision, match_details, run_audit
from bioaudit.errors import ERROR_CODES, ERROR_HTTP_STATUS, BioAuditError, ErrorCode
from bioaudit.paths import TRAJECTORIES_DIR, TRAJECTORIES_LEGACY_DIR

# 合法决策/轨迹
DEG_CORRECT = json.loads(
    (TRAJECTORIES_LEGACY_DIR / "deg_correct.json").read_text(encoding="utf-8")
)
VALID_DECISION = {
    "step_id": "s3",
    "decision_type": "deg_method",
    "choice": "DESeq2",
    "rationale": "DESeq2 Wald test",
    "context": {
        "data_category": "raw_counts",
        "sequencing": "bulk_RNA_seq",
        "design": "simple_two_group",
        "n_replicates": 6,
    },
}
SCRNA_DECISION = {
    "step_id": "s3",
    "decision_type": "deg_method",
    "choice": "DESeq2",
    "rationale": "DESeq2 on cells",
    "context": {"sequencing": "10X_scRNA_seq", "n_patients": 5},
}


# ── 1. 非法输入 → 显式错误码（不静默降级）──


def test_run_audit_missing_required_field_validation_error():
    with pytest.raises(BioAuditError) as ei:
        run_audit([{"decision_type": "deg_method", "choice": "DESeq2"}], act="deg")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR
    assert ei.value.details["field_errors"]  # pydantic 字段级明细


def test_run_audit_typo_field_not_silently_ignored():
    """A15：decisionType 拼错（extra 字段）→ 显式报错，不再静默变"无法评估"。"""
    with pytest.raises(BioAuditError) as ei:
        run_audit([{"step_id": "s1", "decisionType": "deg_method", "choice": "x"}], act="deg")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


def test_run_audit_bad_request_payload():
    for bad in ["not-a-list", 42, None, {"foo": 1}]:
        with pytest.raises(BioAuditError) as ei:
            run_audit(bad, act="deg")
        assert ei.value.code == ErrorCode.BAD_REQUEST, bad


def test_run_audit_unknown_paradigm_rejected():
    """旧行为：act 未知静默回退全量规则 → 现在 paradigm-not-found。"""
    with pytest.raises(BioAuditError) as ei:
        run_audit(DEG_CORRECT, act="banana")
    assert ei.value.code == ErrorCode.PARADIGM_NOT_FOUND


# ── 2. audit_decision 必填 paradigm（B2）──


def test_audit_decision_requires_paradigm():
    with pytest.raises(TypeError):
        audit_decision(VALID_DECISION)  # 缺 paradigm 参数


def test_audit_decision_unknown_paradigm():
    with pytest.raises(BioAuditError) as ei:
        audit_decision(VALID_DECISION, paradigm="banana")
    assert ei.value.code == ErrorCode.PARADIGM_NOT_FOUND


def test_audit_decision_invalid_decision():
    with pytest.raises(BioAuditError) as ei:
        audit_decision({"choice": "DESeq2"}, paradigm="deg")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


# ── 3. paradigm 消歧：同 choice 不同范式不同评分（B3 验收项 6）──


def test_paradigm_disambiguation_same_choice_different_scores():
    deg = audit_decision(VALID_DECISION, paradigm="deg")
    scrna = audit_decision(SCRNA_DECISION, paradigm="scrna")

    # deg_method/DESeq2：bulk 规则 M1.1 → L3；scRNA 规则词表仅收 pseudobulk_* 形式，
    # 裸 DESeq2 未识别 → 规则级跳过 → -1（K2 语义：未知方法 ≠ 错误，不再兜底 L0）
    assert deg["level"] == 3
    assert scrna["level"] == -1
    assert deg["level"] != scrna["level"]  # paradigm 真正改变评分
    assert "M1.1-DEG-001" in deg["matched_rules"]
    assert "M1.1-DEG-001" not in scrna["matched_rules"]


# ── 4. human_overrides 校验（A7：int 且 -1..4，非法拒绝 + 记录事件）──


@pytest.mark.parametrize("bad_value", [5, -2, "3", 3.0, True, None])
def test_human_overrides_out_of_range_rejected(bad_value):
    with pytest.raises(BioAuditError) as ei:
        run_audit(DEG_CORRECT, act="deg", human_overrides={"s1": bad_value})
    assert ei.value.code == ErrorCode.VALIDATION_ERROR
    assert ei.value.details["step_id"] == "s1"


def test_human_overrides_non_dict_rejected():
    with pytest.raises(BioAuditError) as ei:
        run_audit(DEG_CORRECT, act="deg", human_overrides=["s1"])
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_human_overrides_invalid_records_event(tmp_path, monkeypatch):
    """A7：非法 override 拒绝并记录 invalid_override_rejected 事件。"""
    monkeypatch.setenv("BIOAUDIT_LOG_DIR", str(tmp_path))
    with pytest.raises(BioAuditError):
        run_audit(DEG_CORRECT, act="deg", human_overrides={"s1": 5})

    events = []
    for f in tmp_path.glob("*.jsonl"):
        events += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
    rejected = [e for e in events if e["event_type"] == "invalid_override_rejected"]
    assert rejected, "未记录 invalid_override_rejected 事件"
    assert rejected[0]["payload"]["step_id"] == "s1"
    assert rejected[0]["payload"]["value"] == "5"


def test_human_overrides_valid_range_applies():
    result = run_audit(DEG_CORRECT, act="deg", human_overrides={"s1": 4})
    assert result["error"] is None
    s1 = next(s for s in result["step_scores"] if s["step_id"] == "s1")
    assert s1["level"] == 4
    assert "human override" in s1["explanation"]


# ── 5. match_details 输入校验 ──


def test_match_details_invalid_inputs():
    with pytest.raises(BioAuditError) as ei:
        match_details("", {}, act="deg")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(BioAuditError) as ei:
        match_details("deg_method", "not-a-dict", act="deg")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(BioAuditError) as ei:
        match_details("deg_method", {}, act="banana")
    assert ei.value.code == ErrorCode.PARADIGM_NOT_FOUND


def test_match_details_ok():
    details = match_details("deg_method", VALID_DECISION["context"], act="deg")
    assert details
    assert details[0]["rule_id"] == "M1.1-DEG-001"
    assert all("checks" in d for d in details)


# ── 6. 错误码体系守卫（B3 验收项 4：定义并文档化，文档与代码一致）──


def test_error_code_set_documented():
    documented = {
        "bad-request",
        "validation-error",
        "paradigm-not-found",
        "rule-not-found",
        "internal-error",
    }
    assert ERROR_CODES == documented
    assert set(ERROR_HTTP_STATUS) == documented  # 每个错误码都有 HTTP 映射


# ── 7. run_audit 接受 v2 轨迹对象（B4 联动）──


def test_run_audit_accepts_v2_object():
    v2 = json.loads((TRAJECTORIES_DIR / "deg_correct.json").read_text(encoding="utf-8"))
    assert v2["version"] == 2
    r_list = run_audit(DEG_CORRECT, act="deg")
    r_v2 = run_audit(v2, act="deg")
    assert r_v2["error"] is None
    assert r_v2["trajectory_score"] == r_list["trajectory_score"] == 85.0
    assert r_v2["eval_verdict"] == r_list["eval_verdict"] == "pass"
