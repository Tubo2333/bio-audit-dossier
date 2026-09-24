"""DP-01 Spec-B1 structured output contract tests."""
from pathlib import Path
import json
import sys
from dataclasses import FrozenInstanceError
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_models import (
    DP01EvidenceGap, DP01Finding, DP01Limitation, DP01LocalContribution,
)
from bioaudit.dp01_store import DP01JSONLStore

BASE = {
    "audit_id": "parent",
    "analysis_context": {"project_id":"p","analysis_id":"a","comparison":"case_vs_control","data_type":"bulk_rnaseq","audit_scope":"filtering","intended_use":"scientific_analysis", "exclusions": "none — no explicit exclusions declared"},
    "decision_declaration": {"method":"low_count_filter","threshold":10,"sample_rule":"at_least_two_samples","unit":"gene","source":"declared"},
    "integrity_metadata": {**{domain: {"state": "confirmed"} for domain in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")}, "material_ids": ["material-1"], "pending_targets": ["filtering-decision"]},
    "evidence_observations": [
        {"source_type":"declared","verification":"unverified","value":"low_count_filter"},
        {"source_type":"observed","verification":"confirmed","value":"low_count_filter"},
    ],
}
def result(req, store=None):
    return audit_dp01_filtering(req, store or {})

def assert_shape(r):
    assert isinstance(r.findings, tuple)
    assert isinstance(r.limitations, tuple)
    assert isinstance(r.evidence_gaps, tuple)
    assert isinstance(r.local_contribution, DP01LocalContribution)
    assert all(isinstance(x, DP01Finding) for x in r.findings)
    assert all(isinstance(x, DP01Limitation) for x in r.limitations)
    assert all(isinstance(x, DP01EvidenceGap) for x in r.evidence_gaps)
    with pytest.raises(FrozenInstanceError):
        r.local_contribution.status = "x"
    assert not any(hasattr(r, x) for x in ("acceptance_snapshot","release_decision","lane","authority","score","aggregate"))

def test_normal_structured_output_is_immutable_and_point_local():
    r = result(BASE); assert_shape(r)
    assert r.local_contribution.status == "established"
    assert r.local_contribution.scope == "filtering"
    assert r.evidence_gaps == ()
    assert {x.state for x in r.findings} == {"confirmed"}

def test_result_carries_audit_identity_after_persistence(tmp_path):
    from bioaudit.dp01_store import DP01JSONLStore
    store = DP01JSONLStore(tmp_path / "identity.jsonl")
    audit_dp01_filtering(BASE, store)
    record = store.get("parent")
    assert record.result.audit_id == "parent"
    assert set(record.result.version_snapshot) == {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"}


def test_fail_closed_missing_context_has_gap():
    r = result({**BASE, "analysis_context":{"project_id":"p"}}); assert_shape(r)
    assert r.judgment == "not_auditable"
    assert any(x.kind == "missing_critical_context" for x in r.evidence_gaps)
    assert r.local_contribution.status == "unresolved"

def test_limited_no_observation_and_unknown_method_have_structured_limits():
    no_obs = result({**BASE, "evidence_observations": [BASE["evidence_observations"][0]]})
    unknown = result({**BASE, "decision_declaration": {**BASE["decision_declaration"], "method":"new_method"}})
    for r in (no_obs, unknown):
        assert_shape(r); assert r.judgment == "scientifically_limited"; assert r.limitations
    assert any(x.kind == "absent_execution_observation" for x in no_obs.evidence_gaps)

def test_conflict_preserves_evidence_and_gap_state():
    r = result({**BASE, "evidence_observations": [BASE["evidence_observations"][0], {"source_type":"observed","verification":"conflicted","value":"no_filter"}]})
    assert_shape(r); assert r.judgment == "conflicted"
    assert any(x.kind == "declared_observed_conflict" and x.state == "conflicted" for x in r.evidence_gaps)
    assert {e.value for e in r.evidence} == {"low_count_filter", "no_filter"}

def test_child_and_reaudit_preserve_structures_and_jsonl_round_trip(tmp_path):
    store = DP01JSONLStore(tmp_path / "dp01.jsonl")
    result(BASE, store)
    child = {**BASE, "audit_id":"child", "attempt":{"parent_audit_id":"parent","change_kind":"decision_correction","change_reason":"adjust"}}
    child_result = result(child, store)
    assert_shape(child_result); assert child_result.local_contribution.status == "pending"
    re = {"audit_id":"reaudit", "re_audit":{"operation":"re_audit","target_audit_id":"child"}, "decision_declaration":{**BASE["decision_declaration"],"threshold":20}}
    re_result = result(re, store); assert_shape(re_result)
    restored = store.get("reaudit").result
    assert restored == re_result
    serialized = json.loads(store.serialize(store.get("reaudit")))
    assert isinstance(serialized["result"]["findings"], list)

def test_explicit_structures_have_deterministic_serializable_shape():
    finding = DP01Finding("method", "declared", "confirmed", "low_count_filter")
    limitation = DP01Limitation("filtering_support", "unverified", "limited method coverage")
    gap = DP01EvidenceGap("missing_critical_context", "observed", "unverified", "comparison")
    assert finding.to_dict() == {"kind":"method","source":"declared","state":"confirmed","value":"low_count_filter"}
    assert limitation.to_dict() == {"kind":"filtering_support","state":"unverified","detail":"limited method coverage"}
    assert gap.to_dict() == {"kind":"missing_critical_context","source":"observed","state":"unverified","target":"comparison"}
