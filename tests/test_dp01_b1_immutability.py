"""DP-01 Spec-B1-R2 structured-value immutability boundary tests."""
from pathlib import Path
import sys
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.dp01_models import DP01Finding
from bioaudit.dp01_store import DP01AuditRecord, DP01JSONLStore


def test_nested_structured_values_are_recursively_immutable():
    value = {"outer": [{"inner": ("a", {"n": 1})}]}
    finding = DP01Finding("method", "derived", "confirmed", value)

    assert isinstance(finding.value, MappingProxyType)
    assert isinstance(finding.value["outer"], tuple)
    assert isinstance(finding.value["outer"][0], MappingProxyType)
    with pytest.raises(TypeError):
        finding.value["outer"] = ()
    with pytest.raises(TypeError):
        finding.value["outer"][0]["inner"] = ()
    with pytest.raises(TypeError):
        finding.value["outer"][0]["inner"][1]["n"] = 2


def test_structured_values_reject_unsupported_or_ambiguous_values():
    for value in ({"items"}, {1: "non-string key"}, object(), bytearray(b"x"), float("nan"), float("inf")):
        with pytest.raises((TypeError, ValueError)):
            DP01Finding("method", "declared", "unverified", value)


def test_structured_serialization_is_deterministic_and_round_trips(tmp_path):
    value = {"z": (2, True), "a": [None, 1.5, {"text": "ok"}]}
    finding = DP01Finding("method", "derived", "confirmed", value)
    assert finding.to_dict() == {
        "kind": "method", "source": "derived", "state": "confirmed",
        "value": {"z": [2, True], "a": [None, 1.5, {"text": "ok"}]},
    }

    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    result = type("Result", (), {})()
    # Use the public result model so the dedicated serializer boundary is exercised.
    from bioaudit.dp01_models import DP01FilteringResult
    result = DP01FilteringResult(
        "DP-01/filtering", {}, None, (), "x", (), "scientifically_limited", (),
        findings=(finding,),
    )
    record = DP01AuditRecord("a", {}, result)
    first = store.serialize(record)
    # Independently reconstruct an equivalent record from a separate evaluation
    # of the same input, then compare canonical serialization bytes.
    from bioaudit.dp01_models import DP01Finding as _F
    finding2 = _F("method", "derived", "confirmed", {"z": (2, True), "a": [None, 1.5, {"text": "ok"}]})
    result2 = DP01FilteringResult(
        "DP-01/filtering", {}, None, (), "x", (), "scientifically_limited", (),
        findings=(finding2,),
    )
    record2 = DP01AuditRecord("a", {}, result2)
    second = store.serialize(record2)
    assert first == second
    from bioaudit.dp01_store import _result_from_dict
    restored = _result_from_dict(__import__("json").loads(first)["result"])
    assert restored.findings == result.findings
    assert restored.findings[0].value == finding.value






def test_public_record_and_change_boundaries_isolate_mutable_values(tmp_path):
    from bioaudit.dp01_models import DP01Change, DP01ChangeSummary, DP01FilteringResult
    source = {"nested": [{"value": 1}]}
    result = DP01FilteringResult("DP-01/filtering", {}, None, (), "x", (), "not_auditable", ())
    record = DP01AuditRecord("boundary", source, result, change_summary=DP01ChangeSummary((DP01Change("p", source, source),)))
    source["nested"][0]["value"] = 9
    assert record.input_snapshot["nested"][0]["value"] == 1
    assert record.change_summary.changes[0].before["nested"][0]["value"] == 1
    with pytest.raises(TypeError):
        record.input_snapshot["nested"] = ()
    from bioaudit.api import audit_dp01_filtering
    request = {
        "audit_id": "nested",
        "analysis_context": {"project_id": "p", "analysis_id": "a", "comparison": "case_vs_control", "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared", "nested": {"values": [{"keep": True}]}},
        "decision_declaration": {"method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {**{domain: {"state": "confirmed"} for domain in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")}, "material_ids": ["m"], "pending_targets": ["p"]},
    }
    store = DP01JSONLStore(tmp_path / "nested.jsonl")
    audit_dp01_filtering(request, store)
    request["analysis_context"]["nested"]["values"][0]["keep"] = False
    record = store.get("nested")
    with pytest.raises(TypeError):
        record.input_snapshot["analysis_context"]["nested"]["values"][0]["keep"] = False
    assert store.get("nested").input_snapshot["analysis_context"]["nested"]["values"][0]["keep"] is True
    from bioaudit.dp01_models import DP01FilteringResult
    result = DP01FilteringResult("DP-01/filtering", {}, None, (), "x", (), "not_auditable", ())
    assert result.findings == ()
    assert result.limitations == ()
    assert result.evidence_gaps == ()
    assert result.local_contribution.to_dict() == {
        "status": "unresolved", "scope": "filtering", "detail": "No filtering contribution established"
    }
    assert DP01Finding("method", "derived", "not_applicable", {}).value == {}
    assert DP01Finding("method", "derived", "not_applicable", []).value == ()
