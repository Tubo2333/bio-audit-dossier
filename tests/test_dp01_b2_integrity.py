"""DP-01 Spec-B2 integrity/authority contract tests."""
from pathlib import Path
import json
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore

DOMAINS = ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")

BASE = {
    "audit_id": "audit-1",
    "analysis_context": {
        "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
        "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
    },
    "decision_declaration": {
        "method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples",
        "unit": "gene", "source": "declared",
    },
    "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "referenced", "verification": "confirmed", "value": "D1.2-DEG-001", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ],
    "integrity_metadata": {
        **{domain: {"state": "confirmed"} for domain in DOMAINS},
        "material_ids": ["material-1"],
        "pending_targets": ["filtering-decision"],
    },
}


def test_non_mapping_requests_fail_closed_without_raise_or_append(tmp_path):
    for request in (None, 7, "request", [], ("x",)):
        store = DP01JSONLStore(tmp_path / f"{type(request).__name__}.jsonl")
        result = audit_dp01_filtering(request, store)
        assert result.judgment == "integrity_failed"
        assert result.scope == "DP-01/filtering"
        assert result.declaration is None
        assert all(gap.source in DOMAINS for gap in result.evidence_gaps)
        assert not store.path.exists()


def test_malformed_evidence_and_declaration_fail_closed_without_append(tmp_path):
    cases = [
        {**BASE, "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": [], "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]},
        {**BASE, "evidence_observations": [{"source_type": "invented", "verification": "confirmed", "value": "x", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]},
        {**BASE, "decision_declaration": {**BASE["decision_declaration"], "extra": "bad"}},
        {**BASE, "decision_declaration": {**BASE["decision_declaration"], "threshold": object()}},
    ]
    for index, request in enumerate(cases):
        store = DP01JSONLStore(tmp_path / f"r12-{index}.jsonl")
        result = audit_dp01_filtering(request, store)
        assert result.judgment == "integrity_failed"
        assert result.declaration is None
        assert result.scope == "DP-01/filtering"
        assert not store.path.exists()




def test_missing_metadata_and_malformed_evidence_fail_closed_without_raise(tmp_path):
    request = {k: v for k, v in BASE.items() if k != "integrity_metadata"}
    request["evidence_observations"] = [{"source_type": "invented", "verification": "confirmed", "value": "bad", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]
    store = DP01JSONLStore(tmp_path / "combined.jsonl")
    result = audit_dp01_filtering(request, store)
    assert result.judgment == "integrity_failed"
    assert result.declaration is None
    assert not store.path.exists()
    result = audit_dp01_filtering(BASE, {})
    assert result.judgment == "auditable"
    assert result.declaration is not None




def test_missing_metadata_and_non_sequence_evidence_fail_closed_without_raise(tmp_path):
    request = {k: v for k, v in BASE.items() if k != "integrity_metadata"}
    request["evidence_observations"] = {"source_type": "observed", "verification": "confirmed", "value": "x", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}
    store = DP01JSONLStore(tmp_path / "combined-non-sequence.jsonl")
    result = audit_dp01_filtering(request, store)
    assert result.judgment == "integrity_failed"
    assert result.declaration is None
    assert not store.path.exists()
    for request in [
        {k: v for k, v in BASE.items() if k != "integrity_metadata"},
        {**BASE, "integrity_metadata": {**BASE["integrity_metadata"], "identity": {"state": "bogus"}}},
        {**BASE, "integrity_metadata": {**BASE["integrity_metadata"], "material_ids": []}},
    ]:
        result = audit_dp01_filtering(request, {})
        assert result.judgment == "integrity_failed"
        assert result.scope == "DP-01/filtering"





def test_missing_or_non_mapping_metadata_gets_two_fallbacks():
    requests = [
        {k: v for k, v in BASE.items() if k != "integrity_metadata"},
        {**BASE, "integrity_metadata": None},
        {**BASE, "integrity_metadata": "bad"},
        {**BASE, "integrity_metadata": []},
        {**BASE, "integrity_metadata": 3},
    ]
    for request in requests:
        result = audit_dp01_filtering(request, {})
        assert result.judgment == "integrity_failed"
        assert sum(gap.target == "integrity_metadata" for gap in result.evidence_gaps) == 2
        assert sum(action.target == "integrity_metadata" for action in result.next_actions) == 2
        assert all(gap.source in DOMAINS for gap in result.evidence_gaps)


def test_reaudit_incomplete_child_snapshot_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "legacy-child.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-legacy", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "legacy"}}
    audit_dp01_filtering(child, store)
    lines = store.path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1]); payload["input_snapshot"].pop("integrity_metadata", None)
    store.path.write_text(lines[0] + "\n" + json.dumps(payload) + "\n", encoding="utf-8")
    request = {"audit_id": "reaudit-legacy", "re_audit": {"operation": "re_audit", "target_audit_id": "child-legacy"}}
    with pytest.raises(ValueError, match="integrity|metadata|version"):
        audit_dp01_filtering(request, store)


def test_material_and_pending_targets_are_explicit_and_deterministic():
    metadata = {**BASE["integrity_metadata"], "source": {"state": "failed"}, "material_ids": ["m2", "m1"], "pending_targets": ["p2", "p1"]}
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    values = [item.value for item in result.findings if item.kind == "integrity_failure"]
    assert list(values[0]["material_ids"]) == ["m2", "m1"]
    assert list(values[0]["pending_targets"]) == ["p2", "p1"]
    assert "authority" not in {name.lower() for name in vars(result)}


def test_jsonl_round_trip_and_child_reaudit_preserve_b2_output(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    result = audit_dp01_filtering(BASE, store)
    record = store.get("audit-1")
    assert record.result == result
    assert dict(record.input_snapshot["integrity_metadata"]) == {**{key: dict(value) for key, value in BASE["integrity_metadata"].items() if key in DOMAINS}, "material_ids": ("material-1",), "pending_targets": ("filtering-decision",)}
    child = {**BASE, "audit_id": "child-1", "attempt": {"parent_audit_id": "audit-1", "change_kind": "evidence_supplement", "change_reason": "add integrity record"}}
    child_result = audit_dp01_filtering(child, store)
    assert store.get("child-1").result == child_result
    assert child_result.judgment == "pending_attempt"
    raw = store.serialize(record)
    formal = raw.split('"result":', 1)[1]
    for forbidden in ("acceptance", "release", "lane", "authority", "score", "aggregate"):
        assert forbidden not in formal


def test_malformed_metadata_shapes_are_structured_failures():
    malformed = [
        {domain: BASE["integrity_metadata"][domain] for domain in DOMAINS if domain != "identity"} | {"material_ids": ["m"], "pending_targets": ["p"]},
        {**BASE["integrity_metadata"], "identity": None},
        {**BASE["integrity_metadata"], "identity": {"state": "confirmed", "value": "x"}},
        {**BASE["integrity_metadata"], "pending_targets": "p"},
        {**BASE["integrity_metadata"], "material_ids": ["", 2]},
        {**BASE["integrity_metadata"], "extra": {"state": "confirmed"}},
    ]
    for metadata in malformed:
        result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
        assert result.judgment == "integrity_failed"
        assert result.evidence_gaps
        assert all(item.kind == "integrity_failure" for item in result.findings)
        assert all(item.source in DOMAINS for item in result.findings)
        assert all(gap.source in DOMAINS for gap in result.evidence_gaps)


def test_child_attempt_validates_integrity_before_pending():
    child = {**BASE, "audit_id": "child-bad", "attempt": {"parent_audit_id": "audit-1", "change_kind": "evidence_supplement", "change_reason": "pending"}}
    result = audit_dp01_filtering({k: v for k, v in child.items() if k != "integrity_metadata"}, {})
    assert result.judgment == "integrity_failed"


def test_reaudit_replaces_integrity_metadata_and_records_diff(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-b2", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    corrected = json.loads(json.dumps(BASE["integrity_metadata"]))
    corrected["source"] = {"state": "failed"}
    request = {"audit_id": "reaudit-b2", "re_audit": {"operation": "re_audit", "target_audit_id": "child-b2"}, "integrity_metadata": corrected}
    result = audit_dp01_filtering(request, store)
    record = store.get("reaudit-b2")
    assert result.judgment == "integrity_failed"
    assert dict(record.input_snapshot["integrity_metadata"])["source"] == {"state": "failed"}



def test_malformed_child_attempt_shape_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "bad-attempt.jsonl")
    malformed_attempts = [
        "bad",
        [],
        {"parent_audit_id": "audit-1"},
        {"parent_audit_id": "audit-1", "change_kind": "decision_correction"},
        {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "extra": "x"},
        {"parent_audit_id": 5, "change_kind": "decision_correction", "change_reason": "x"},
    ]
    for attempt in malformed_attempts:
        request = {**BASE, "attempt": attempt}
        result = audit_dp01_filtering(request, store)
        assert result.judgment == "integrity_failed"
        assert result.declaration is None
    assert not store.path.exists()


def test_malformed_child_evidence_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "bad-child-evidence.jsonl")
    request = {**BASE, "attempt": {"parent_audit_id": "audit-1", "change_kind": "evidence_supplement", "change_reason": "x"}, "evidence_observations": [{"source_type": "invented", "verification": "confirmed", "value": "x", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]}
    result = audit_dp01_filtering(request, store)
    assert result.judgment == "integrity_failed"
    assert result.declaration is None
    assert not store.path.exists()


def test_malformed_reaudit_shape_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "bad-reaudit.jsonl")
    malformed_ops = [
        "bad",
        [],
        {"target_audit_id": "child-1"},
        {"operation": "re_audit"},
        {"operation": "re_audit", "target_audit_id": ""},
        {"operation": "re_audit", "target_audit_id": 5},
    ]
    for op in malformed_ops:
        result = audit_dp01_filtering({"audit_id": "r", "re_audit": op}, store)
        assert result.judgment == "integrity_failed"
    assert not store.path.exists()


def test_reaudit_diff_includes_all_structured_result_fields(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-r13", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    request = {**BASE, "audit_id": "reaudit-r13", "re_audit": {"operation": "re_audit", "target_audit_id": "child-r13"}, "integrity_metadata": {**BASE["integrity_metadata"], "identity": {"state": "failed"}}}
    audit_dp01_filtering(request, store)
    paths = {change.path for change in store.get("reaudit-r13").change_summary.changes}
    assert "result.findings" in paths
    assert "result.evidence_gaps" in paths
    assert "result.limitations" not in paths

    store = DP01JSONLStore(tmp_path / "dp01-actions.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-actions", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    request = {"audit_id": "reaudit-actions", "re_audit": {"operation": "re_audit", "target_audit_id": "child-actions"}, "integrity_metadata": {**BASE["integrity_metadata"], "source": {"state": "failed"}}}
    audit_dp01_filtering(request, store)
    paths = [change.path for change in store.get("reaudit-actions").change_summary.changes]
    assert "result.next_actions[0]" in paths


def test_reaudit_records_next_action_field_changes(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-field", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    request = {"audit_id": "reaudit-field", "re_audit": {"operation": "re_audit", "target_audit_id": "child-field"}, "integrity_metadata": {**BASE["integrity_metadata"], "source": {"state": "failed"}}}
    audit_dp01_filtering(request, store)
    summary = store.get("reaudit-field").change_summary
    assert any(change.path.startswith("result.next_actions[") for change in summary.changes)


def test_missing_metadata_never_uses_synthetic_domain():
    result = audit_dp01_filtering({k: v for k, v in BASE.items() if k != "integrity_metadata"}, {})
    assert all(item.source in DOMAINS for item in result.findings)
    assert all(gap.source in DOMAINS for gap in result.evidence_gaps)


def test_partial_target_metadata_preserves_valid_side_and_marks_missing_side():
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata["pending_targets"] = "malformed"
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    assert result.judgment == "integrity_failed"
    assert any(gap.target == "material-1" for gap in result.evidence_gaps)
    assert any(gap.target == "integrity_metadata" for gap in result.evidence_gaps)
    assert all(gap.target != "m" for gap in result.evidence_gaps)


def test_partial_target_metadata_preserves_pending_side_and_marks_missing_side():
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata["material_ids"] = 2
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    assert result.judgment == "integrity_failed"
    assert any(gap.target == "filtering-decision" for gap in result.evidence_gaps)
    assert any(gap.target == "integrity_metadata" for gap in result.evidence_gaps)


    for key, value in (("material_ids", 2), ("pending_targets", "pending"), ("material_ids", None), ("pending_targets", {"x": 1})):
        metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
        metadata[key] = value
        result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
        assert result.judgment == "integrity_failed"
        assert all(target not in {"p", "pending", "x", "1"} for gap in result.evidence_gaps for target in (gap.target,))



def test_mixed_malformed_members_invalidate_only_their_target_dimension():
    cases = [("material_ids", ["valid-material", 2], "filtering-decision"), ("pending_targets", ["valid-pending", {"x": 1}], "material-1")]
    for key, value, valid_target in cases:
        metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
        metadata[key] = value
        result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
        formal_targets = [gap.target for gap in result.evidence_gaps]
        assert result.judgment == "integrity_failed"
        assert valid_target in formal_targets
        assert "integrity_metadata" in formal_targets
        assert 2 not in formal_targets
        assert "x" not in formal_targets
        assert "valid-material" not in formal_targets if key == "material_ids" else "valid-pending" not in formal_targets


    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata.pop("identity")
    metadata["pending_targets"] = "bad"
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    assert result.judgment == "integrity_failed"
    assert any(gap.target == "material-1" for gap in result.evidence_gaps)
    assert any(gap.target == "integrity_metadata" for gap in result.evidence_gaps)


def test_combined_malformed_shape_and_partial_targets_keep_fallbacks():
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata["material_ids"] = 2
    metadata["extra"] = {"state": "confirmed"}
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    assert result.judgment == "integrity_failed"
    assert any(gap.target == "filtering-decision" for gap in result.evidence_gaps)
    assert any(gap.target == "integrity_metadata" for gap in result.evidence_gaps)



def test_both_malformed_target_dimensions_get_two_fallbacks():
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata["material_ids"] = 2
    metadata["pending_targets"] = "bad"
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    fallback_gaps = [gap for gap in result.evidence_gaps if gap.target == "integrity_metadata"]
    assert len(fallback_gaps) == 2
    assert all(target not in {"2", "b", "a", "d"} for target in (gap.target for gap in result.evidence_gaps))


def test_combined_error_with_both_malformed_target_dimensions_gets_two_fallbacks():
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata.pop("identity")
    metadata["material_ids"] = 2
    metadata["pending_targets"] = "bad"
    result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
    assert result.judgment == "integrity_failed"
    assert sum(gap.target == "integrity_metadata" for gap in result.evidence_gaps) == 2 * len(result.findings)

    for state in (None, [], {}, 1):
        metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
        metadata["identity"] = {"state": state}
        result = audit_dp01_filtering({**BASE, "integrity_metadata": metadata}, {})
        assert result.judgment == "integrity_failed"
        assert result.findings


def test_reaudit_missing_integrity_metadata_inherits_child_snapshot(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-omit", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    result = audit_dp01_filtering({"audit_id": "reaudit-omit", "re_audit": {"operation": "re_audit", "target_audit_id": "child-omit"}}, store)
    assert result.judgment in {"auditable", "conflicted", "scientifically_limited", "integrity_failed"}
    assert dict(store.get("reaudit-omit").input_snapshot["integrity_metadata"]) == dict(store.get("child-omit").input_snapshot["integrity_metadata"])


def test_reaudit_explicit_failed_metadata_does_not_use_old_confirmed_metadata(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-explicit", "attempt": {"parent_audit_id": "audit-1", "change_kind": "decision_correction", "change_reason": "pending"}}
    audit_dp01_filtering(child, store)
    metadata = json.loads(json.dumps(BASE["integrity_metadata"]))
    metadata["identity"] = {"state": "failed"}
    result = audit_dp01_filtering({"audit_id": "reaudit-explicit", "re_audit": {"operation": "re_audit", "target_audit_id": "child-explicit"}, "integrity_metadata": metadata}, store)
    assert result.judgment == "integrity_failed"
    assert dict(store.get("reaudit-explicit").input_snapshot["integrity_metadata"]) == {**{key: dict(value) for key, value in metadata.items() if key in DOMAINS}, "material_ids": tuple(metadata["material_ids"]), "pending_targets": tuple(metadata["pending_targets"])}
