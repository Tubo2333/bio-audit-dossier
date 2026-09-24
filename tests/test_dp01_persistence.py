"""DP-01 W5 persistence contract tests.

Citations: DP01-W5-INSTRUCTION.md:39-59, 84-89; authorization package
§4 lines 96-119 and §7 lines 173-185.
"""

from pathlib import Path
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

_ISOLATED_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ISOLATED_ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_store import DP01AuditRecord, DP01JSONLStore


def _no_identity(result):
    """Strip identity from a result so fixtures building records under a different
    audit_id remain valid (identity is attached at public-entry/store level)."""
    from bioaudit.dp01_models import DP01FilteringResult
    return DP01FilteringResult(
        result.scope, result.analysis_context, result.declaration, result.evidence,
        result.diagnostic_explanation, result.matched_rule_ids, result.judgment, result.next_actions,
        result.findings, result.limitations, result.evidence_gaps, result.local_contribution,
    )


_REQUEST = {
    "audit_id": "audit-1",
    "analysis_context": {
        "project_id": "project-1",
        "analysis_id": "analysis-1",
        "comparison": "case_vs_control",
        "data_type": "bulk_rnaseq",
        "audit_scope": "filtering",
        "intended_use": "scientific_analysis",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
        "nested": {"values": [1, {"x": "y"}]},
    },
    "decision_declaration": {
        "method": "low_count_filter",
        "threshold": 10,
        "sample_rule": "at_least_two_samples",
        "unit": "gene",
        "source": "declared",
    },
    "integrity_metadata": {
        "identity": {"state": "confirmed"}, "version": {"state": "confirmed"},
        "source": {"state": "confirmed"}, "binding": {"state": "confirmed"},
        "snapshot": {"state": "confirmed"}, "permission": {"state": "confirmed"},
        "unique_authority": {"state": "confirmed"},
        "material_ids": ["material-1"], "pending_targets": ["filtering-decision"],
    },
    "evidence_observations": [
        {"source_type": "declared", "verification": "unverified", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "referenced", "verification": "confirmed", "value": "D1.2-DEG-001", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ],
}
def test_explicit_path_first_append_get_and_complete_snapshot_round_trip(tmp_path):
    # W5 STORAGE_CONTRACT and SNAPSHOT_CONTRACT; authorization §4.
    path = tmp_path / "nested" / "dp01.jsonl"
    store = DP01JSONLStore(path)
    result = audit_dp01_filtering(_REQUEST, store)
    record = store.get("audit-1")
    assert record.audit_id == "audit-1"
    assert dict(record.input_snapshot["analysis_context"]["nested"]) == {"values": (1, {"x": "y"})}
    assert record.result == result
    assert record.result.scope == "DP-01/filtering"
    assert not hasattr(record.result, "score")
    assert not hasattr(record.result, "authority")


def test_snapshot_is_independent_and_old_line_is_preserved(tmp_path):
    # W5 SNAPSHOT_CONTRACT lines 48-52 and append-only lines 40-44.
    path = tmp_path / "dp01.jsonl"
    store = DP01JSONLStore(path)
    request = json.loads(json.dumps(_REQUEST))
    audit_dp01_filtering(request, store)
    first_bytes = path.read_bytes()
    request["analysis_context"]["nested"]["values"].append("later")
    second = {**_REQUEST, "audit_id": "audit-2", "analysis_context": {**_REQUEST["analysis_context"], "analysis_id": "analysis-2"}}
    audit_dp01_filtering(second, store)
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[0] == first_bytes
    assert len(lines) == 2
    assert "later" not in store.get("audit-1").input_snapshot["analysis_context"]["nested"]["values"]


def test_duplicate_id_rejected(tmp_path):
    # W5 STORAGE_CONTRACT line 44: duplicate IDs are errors.
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("audit-1", _REQUEST, result)
    store.append(record)
    with pytest.raises(ValueError, match="duplicate"):
        store.append(record)


def test_malformed_line_fails_closed(tmp_path):
    # W5 STORAGE_CONTRACT line 44: malformed JSONL is never skipped or repaired.
    path = tmp_path / "dp01.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    store = DP01JSONLStore(path)
    with pytest.raises(ValueError, match="malformed"):
        store.get("audit-1")


def test_missing_id_fails_closed(tmp_path):
    # W5 STORAGE_CONTRACT lines 43-44: missing IDs are errors.
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    with pytest.raises(KeyError, match="missing"):
        store.get("missing")


def test_no_cwd_fallback(tmp_path, monkeypatch):
    # W5 STORAGE_CONTRACT line 40 and SNAPSHOT_CONTRACT line 52.
    monkeypatch.chdir(tmp_path)
    result = audit_dp01_filtering(_REQUEST, {})
    assert result.scope == "DP-01/filtering"
    assert not (tmp_path / "dp01.jsonl").exists()


def test_invalid_record_shape_fails_closed(tmp_path):
    # W5 STORAGE_CONTRACT line 44: invalid record shape is an error.
    path = tmp_path / "dp01.jsonl"
    path.write_text(json.dumps({"audit_id": "audit-1"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="record|version"):
        DP01JSONLStore(path).get("audit-1")




def test_duplicate_ids_in_existing_file_are_rejected_by_get(tmp_path):
    path = tmp_path / "dp01.jsonl"
    store = DP01JSONLStore(path)
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("audit-1", _REQUEST, result)
    line = store.serialize(record)
    path.write_text(line + line, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        store.get("audit-1")


def test_malformed_nested_result_fails_before_lookup(tmp_path):
    path = tmp_path / "dp01.jsonl"
    path.write_text(json.dumps({"audit_id": "audit-1", "input_snapshot": {}, "result": {"scope": "DP-01/filtering"}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="record|version"):
        DP01JSONLStore(path).get("audit-1")


def test_malformed_result_vocabularies_fail_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "vocab.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("audit-1", _REQUEST, result)
    base_payload = json.loads(store.serialize(record))
    # invalid judgment
    bad_judgment = json.loads(json.dumps(base_payload))
    bad_judgment["result"]["judgment"] = "bogus_judgment"
    path = tmp_path / "bad-judgment.jsonl"
    path.write_text(json.dumps(bad_judgment) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="judgment"):
        DP01JSONLStore(path).get("audit-1")
    # invalid next action vocabulary
    bad_action = json.loads(json.dumps(base_payload))
    bad_action["result"]["next_actions"] = [{"action": "bogus_action", "reason": "r", "target": "t", "next_state": "s"}]
    path = tmp_path / "bad-action.jsonl"
    path.write_text(json.dumps(bad_action) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="action"):
        DP01JSONLStore(path).get("audit-1")
    # invalid finding kind vocabulary
    bad_finding = json.loads(json.dumps(base_payload))
    bad_finding["result"]["findings"] = [{"kind": "bogus_kind", "source": "declared", "state": "confirmed", "value": "x"}]
    path = tmp_path / "bad-finding.jsonl"
    path.write_text(json.dumps(bad_finding) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="finding"):
        DP01JSONLStore(path).get("audit-1")


def test_malformed_unrelated_line_blocks_valid_lookup(tmp_path):
    path = tmp_path / "dp01.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        DP01JSONLStore(path).get("other")




def test_concurrent_unique_appends_remain_complete_lines(tmp_path):
    store = DP01JSONLStore(tmp_path / "concurrent.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    records = [DP01AuditRecord(f"concurrent-{i}", {**_REQUEST, "audit_id": f"concurrent-{i}"}, _no_identity(result)) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(store.append, records))
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    assert {json.loads(line)["audit_id"] for line in lines} == {r.audit_id for r in records}


def test_concurrent_duplicate_append_has_one_success(tmp_path):
    store = DP01JSONLStore(tmp_path / "duplicate-race.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("same", {**_REQUEST, "audit_id": "same"}, _no_identity(result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _append_outcome(store, record), range(2)))
    assert outcomes.count("ok") == 1
    assert outcomes.count("duplicate") == 1


def _append_outcome(store, record):
    try:
        store.append(record)
        return "ok"
    except ValueError as exc:
        if "duplicate" in str(exc):
            return "duplicate"
        raise


def test_serialization_is_deterministic(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("audit-1", _REQUEST, result)
    first = store.serialize(record)
    second = store.serialize(record)
    assert first == second
    assert first.endswith("\n")
    assert '"audit_id":"audit-1"' in first


def test_get_uses_same_lock_protocol_as_append(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store = DP01JSONLStore(tmp_path / "read-lock.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    store.append(DP01AuditRecord("read-lock", _REQUEST, _no_identity(result)))
    entered = []

    @contextmanager
    def probe_lock():
        entered.append(True)
        yield

    monkeypatch.setattr(store, "_append_lock", probe_lock)
    store.get("read-lock")
    assert entered == [True]




def test_change_values_use_strict_json_boundary(tmp_path):
    from bioaudit.dp01_models import DP01Change, DP01ChangeSummary
    result = audit_dp01_filtering(_REQUEST, {})
    store = DP01JSONLStore(tmp_path / "change-strict.jsonl")
    with pytest.raises(TypeError, match="key"):
        DP01Change("p", {1: "x"}, {})
    with pytest.raises(ValueError, match="finite"):
        DP01Change("p", float("inf"), {})
    path = tmp_path / "bad-integrity.jsonl"
    store = DP01JSONLStore(path)
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("bad", _REQUEST, result)
    payload = json.loads(store.serialize(record)); payload["input_snapshot"].pop("integrity_metadata", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.get("bad")


def test_record_field_combinations_are_strict(tmp_path):
    from bioaudit.dp01_models import DP01AttemptChange, DP01Change, DP01ChangeSummary
    result = audit_dp01_filtering(_REQUEST, {})
    store = DP01JSONLStore(tmp_path / "combos.jsonl")
    base_payload = json.loads(store.serialize(DP01AuditRecord("a", _REQUEST, _no_identity(result))))
    # root record carrying change without parent is rejected (not silently dropped)
    root_with_change = {**base_payload, "change": DP01AttemptChange("decision_correction", "x").to_dict()}
    with pytest.raises(ValueError, match="change|relation"):
        store._record_from_payload(root_with_change)
    # change_summary without re_audit operation is rejected
    summary_only = {**base_payload, "change_summary": DP01ChangeSummary((DP01Change("p", 1, 2),)).to_dict()}
    with pytest.raises(ValueError, match="re_audit|summary"):
        store._record_from_payload(summary_only)
    # re_audit without change_summary is rejected
    reaudit_no_summary = {**base_payload, "parent_audit_id": "parent", "change": DP01AttemptChange("decision_correction", "x").to_dict(), "operation": "re_audit"}
    with pytest.raises(ValueError, match="summary"):
        store._record_from_payload(reaudit_no_summary)
    # child with change but operation re_audit must carry summary + source version
    vs = json.loads(store.serialize(DP01AuditRecord("x", _REQUEST, _no_identity(result))))["version_snapshot"]
    valid = {**base_payload, "parent_audit_id": "parent", "change": DP01AttemptChange("decision_correction", "x").to_dict(), "operation": "re_audit", "change_summary": DP01ChangeSummary().to_dict(), "source_version_snapshot": vs}
    parsed = store._record_from_payload(valid)
    assert parsed.parent_audit_id == "parent"
    assert parsed.operation == "re_audit"


def test_append_validates_candidate_payload_before_write(tmp_path, monkeypatch):
    store = DP01JSONLStore(tmp_path / "roundtrip-guard.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    original = store._record_from_payload

    def guarded(payload):
        if payload.get("audit_id") == "bad":
            raise ValueError("invalid candidate")
        return original(payload)

    monkeypatch.setattr(store, "_record_from_payload", staticmethod(guarded))
    store.append(DP01AuditRecord("ok", _REQUEST, _no_identity(result)))
    with pytest.raises(ValueError, match="candidate"):
        store.append(DP01AuditRecord("bad", _REQUEST, _no_identity(result)))
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["audit_id"] for line in lines] == ["ok"]


def test_result_level_identity_tamper_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "id-tamper.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    payload = json.loads(store.serialize(DP01AuditRecord("audit-1", _REQUEST, result)))
    # result-level audit_id mismatching record-level is rejected
    bad_id = json.loads(json.dumps(payload))
    bad_id["result"]["audit_id"] = "other"
    with pytest.raises(ValueError, match="identity"):
        store._record_from_payload(bad_id)
    # result-level version_snapshot with wrong shape is rejected
    bad_vs = json.loads(json.dumps(payload))
    bad_vs["result"]["version_snapshot"] = {"engine_version": "0.3.0"}
    with pytest.raises(ValueError, match="version snapshot"):
        store._record_from_payload(bad_vs)
    # result-level version_snapshot mismatching the record-level is rejected
    bad_vs2 = json.loads(json.dumps(payload))
    bad_vs2["result"]["version_snapshot"] = {**bad_vs2["version_snapshot"], "engine_version": "0.9.9"}
    with pytest.raises(ValueError, match="mismatch"):
        store._record_from_payload(bad_vs2)


def test_source_version_snapshot_requires_reaudit(tmp_path):
    from bioaudit.dp01_models import DP01AttemptChange, DP01ChangeSummary
    store = DP01JSONLStore(tmp_path / "s2.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    # re-audit payload without source_version_snapshot is rejected
    re_no_source = {**json.loads(store.serialize(DP01AuditRecord("r", _REQUEST, _no_identity(result)))), "parent_audit_id": "p", "change": DP01AttemptChange("decision_correction", "x").to_dict(), "operation": "re_audit", "change_summary": DP01ChangeSummary().to_dict()}
    with pytest.raises(ValueError, match="source_version_snapshot"):
        store._record_from_payload(re_no_source)
    # non-re-audit payload with source_version_snapshot is rejected
    root_with_source = {**json.loads(store.serialize(DP01AuditRecord("r2", _REQUEST, _no_identity(result)))), "source_version_snapshot": json.loads(store.serialize(DP01AuditRecord("x", _REQUEST, _no_identity(result))))["version_snapshot"]}
    with pytest.raises(ValueError, match="requires re_audit"):
        store._record_from_payload(root_with_source)
    # re-audit with valid source_version_snapshot is accepted
    vs = json.loads(store.serialize(DP01AuditRecord("x", _REQUEST, _no_identity(result))))["version_snapshot"]
    valid = {**json.loads(store.serialize(DP01AuditRecord("r3", _REQUEST, _no_identity(result)))), "parent_audit_id": "p", "change": DP01AttemptChange("decision_correction", "x").to_dict(), "operation": "re_audit", "change_summary": DP01ChangeSummary().to_dict(), "source_version_snapshot": vs}
    parsed = store._record_from_payload(valid)
    assert parsed.operation == "re_audit"
    assert dict(parsed.source_version_snapshot) == vs
    # constructor enforces the same rule
    with pytest.raises(ValueError, match="source version snapshot"):
        DP01AuditRecord("r4", _REQUEST, _no_identity(result), "p", DP01AttemptChange("decision_correction", "x"), "re_audit")


def test_result_declaration_and_scalar_types_fail_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "s4.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    payload = json.loads(store.serialize(DP01AuditRecord("audit-1", _REQUEST, result)))
    # declaration source must be "declared"
    bad_dec = json.loads(json.dumps(payload))
    bad_dec["result"]["declaration"]["source"] = "observed"
    with pytest.raises(ValueError, match="declaration"):
        store._record_from_payload(bad_dec)
    # analysis_context must be a dict
    bad_ctx = json.loads(json.dumps(payload))
    bad_ctx["result"]["analysis_context"] = "nope"
    with pytest.raises(ValueError, match="context"):
        store._record_from_payload(bad_ctx)
    # diagnostic_explanation must be a string
    bad_exp = json.loads(json.dumps(payload))
    bad_exp["result"]["diagnostic_explanation"] = 5
    with pytest.raises(ValueError, match="explanation"):
        store._record_from_payload(bad_exp)
    # evidence value must be scalar
    bad_ev = json.loads(json.dumps(payload))
    if bad_ev["result"]["evidence"]:
        bad_ev["result"]["evidence"][0]["value"] = {"nested": 1}
        with pytest.raises(ValueError, match="evidence value"):
            store._record_from_payload(bad_ev)
        bad_ev["result"]["evidence"][0]["value"] = ["list"]
        with pytest.raises(ValueError, match="evidence value"):
            store._record_from_payload(bad_ev)


def test_persisted_change_affected_evidence_tamper_fails_closed(tmp_path):
    from bioaudit.dp01_models import DP01AttemptChange
    store = DP01JSONLStore(tmp_path / "ae-tamper.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    record = DP01AuditRecord("child-1", _REQUEST, _no_identity(result), "parent", DP01AttemptChange("evidence_supplement", "x", "alice", ["obs-1"]))
    payload = json.loads(store.serialize(record))
    # bare string must fail closed in _record_from_payload (no char-split, no TypeError)
    payload["change"]["affected_evidence"] = "obs-1"
    with pytest.raises(ValueError, match="evidence"):
        store._record_from_payload(payload)
    # non-iterable also fails closed
    payload["change"]["affected_evidence"] = 5
    with pytest.raises(ValueError, match="evidence"):
        store._record_from_payload(payload)
    # valid list round-trips
    payload["change"]["affected_evidence"] = ["obs-1"]
    parsed = store._record_from_payload(payload)
    assert parsed.change.affected_evidence == ("obs-1",)


def test_persisted_nonfinite_and_nonstring_key_fail_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "strict.jsonl")
    result = audit_dp01_filtering(_REQUEST, {})
    with pytest.raises(TypeError, match="key"):
        DP01AuditRecord("bad", {**_REQUEST, "extra": {1: "x"}}, result)
    with pytest.raises(ValueError, match="finite"):
        DP01AuditRecord("bad-number", {**_REQUEST, "extra": {"x": float("nan")}}, result)
