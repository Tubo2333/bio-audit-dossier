"""DP-01 W5B-1 immutable child-attempt contract tests.

Citations: DP01-W5B-1-INSTRUCTION.md:55-71; authorization package
§3 lines 66-71 and §6 lines 149-169.
"""

from pathlib import Path
import json
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_models import DP01AttemptChange
from bioaudit.dp01_store import DP01AuditRecord, DP01JSONLStore


REQUEST = {
    "audit_id": "parent-1",
    "analysis_context": {
        "project_id": "project-1", "analysis_id": "analysis-1",
        "comparison": "case_vs_control", "data_type": "bulk_rnaseq",
        "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
    },
    "decision_declaration": {
        "method": "low_count_filter", "threshold": 10,
        "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared",
    },
    "integrity_metadata": {
        **{domain: {"state": "confirmed"} for domain in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
        "material_ids": ["material-1"], "pending_targets": ["filtering-decision"],
    },
    "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ],
}
def test_first_record_has_no_parent_relation(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(REQUEST, store)
    record = store.get("parent-1")
    assert record.parent_audit_id is None
    assert record.change is None
    assert "parent_audit_id" not in record.input_snapshot


def test_evidence_supplement_child_is_snapshot_and_submission_only(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(REQUEST, store)
    child_request = {**REQUEST, "audit_id": "child-e", "evidence_observations": REQUEST["evidence_observations"] + [{"source_type": "observed", "verification": "confirmed", "value": "log-ref", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}], "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "Add execution log"}}
    result = audit_dp01_filtering(child_request, store)
    child = store.get("child-e")
    assert child.result == result
    assert child.parent_audit_id == "parent-1"
    assert child.change == DP01AttemptChange("evidence_supplement", "Add execution log")
    assert child.input_snapshot["evidence_observations"][-1]["value"] == "log-ref"
    assert child.input_snapshot is not store.get("parent-1").input_snapshot
    assert child.result.diagnostic_explanation != "correction validated"


def test_decision_correction_child_and_unique_id(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(REQUEST, store)
    child_request = {**REQUEST, "audit_id": "child-d", "decision_declaration": {**REQUEST["decision_declaration"], "threshold": 5}, "attempt": {"parent_audit_id": "parent-1", "change_kind": "decision_correction", "change_reason": "Correct threshold"}}
    audit_dp01_filtering(child_request, store)
    child = store.get("child-d")
    assert child.audit_id not in {"parent-1"}
    assert child.input_snapshot["decision_declaration"]["threshold"] == 5
    assert child.change.kind == "decision_correction"


def test_missing_parent_rejected_and_parent_bytes_preserved(tmp_path):
    path = tmp_path / "a.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(REQUEST, store)
    before = path.read_bytes()
    bad = {**REQUEST, "audit_id": "orphan", "attempt": {"parent_audit_id": "missing", "change_kind": "evidence_supplement", "change_reason": "x"}}
    with pytest.raises(KeyError, match="parent"):
        audit_dp01_filtering(bad, store)
    assert path.read_bytes() == before


def test_duplicate_child_rejected_and_parent_preserved(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(REQUEST, store)
    child = {**REQUEST, "audit_id": "child", "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "x"}}
    audit_dp01_filtering(child, store)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="duplicate"):
        audit_dp01_filtering(child, store)
    assert store.path.read_bytes() == before


def test_affected_evidence_string_is_rejected_not_char_split(tmp_path):
    store = DP01JSONLStore(tmp_path / "ae.jsonl")
    audit_dp01_filtering(REQUEST, store)
    # child attempt path: bare string / non-iterable must fail closed as integrity_failed, not char-split or TypeError
    for bad_value in ("obs-1", 5):
        bad_child = {**REQUEST, "audit_id": "child-ae", "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "x", "affected_evidence": bad_value}}
        result = audit_dp01_filtering(bad_child, store)
        assert result.judgment == "integrity_failed"
        assert "child-ae" not in store.path.read_text(encoding="utf-8") if store.path.exists() else True
    # valid list of non-empty strings is accepted on the child path
    good = {**REQUEST, "audit_id": "child-ok", "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "x", "affected_evidence": ["obs-1"]}}
    audit_dp01_filtering(good, store)
    assert json.loads(store.serialize(store.get("child-ok")))["change"]["affected_evidence"] == ["obs-1"]
    # re-audit path: bare string must fail closed
    bad_re = {"audit_id": "re-ae", "re_audit": {"operation": "re_audit", "target_audit_id": "child-ok", "affected_evidence": "obs-1"}}
    result_re = audit_dp01_filtering(bad_re, store)
    assert result_re.judgment == "integrity_failed"
    assert "re-ae" not in store.path.read_text(encoding="utf-8")


def test_invalid_change_relation_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    for attempt in [
        {"parent_audit_id": "parent-1", "change_kind": "other", "change_reason": "x"},
        {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": ""},
    ]:
        bad = {**REQUEST, "audit_id": "bad-" + attempt["change_kind"], "attempt": attempt}
        with pytest.raises(ValueError):
            audit_dp01_filtering(bad, store)
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    for attempt in [
        {"parent_audit_id": "parent-1", "change_kind": "other", "change_reason": "x"},
        {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": ""},
    ]:
        bad = {**REQUEST, "audit_id": "bad-" + attempt["change_kind"], "attempt": attempt}
        with pytest.raises(ValueError):
            audit_dp01_filtering(bad, store)


def test_attempt_model_serializes_explicit_metadata():
    change = DP01AttemptChange("evidence_supplement", "new log")
    assert change.kind == "evidence_supplement"
    assert change.reason == "new log"
    assert set(change.to_dict()) == {"change_kind", "change_reason"}


def test_submission_does_not_validate_or_reaudit(monkeypatch, tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(REQUEST, store)
    called = False
    def fail(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("child must not re-audit")
    monkeypatch.setattr("bioaudit.dp01_adapter.audit_decision", fail)
    child = {**REQUEST, "audit_id": "pending", "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "pending validation"}}
    result = audit_dp01_filtering(child, store)
    assert result.judgment == "pending_attempt"
    assert not called
