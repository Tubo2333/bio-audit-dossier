"""DP-02 normalization decision slice tests (DP02-NORMALIZATION).

All calls go through the public seam (`bioaudit.api.audit_dp02_normalization` /
`replay_dp02_normalization`). The shared DP-01 JSONL ledger is reused as-is:
DP-01 and DP-02 records coexist and each replays through its own seam.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import audit_dp01_filtering, replay_dp01_filtering
from bioaudit.api import audit_dp02_normalization, replay_dp02_normalization
from bioaudit.dp01_store import DP01JSONLStore

DP02_SCOPE = "DP-02/normalization"
CONTROLLED_ACTIONS = {"request_evidence", "submit_correction", "request_review", "stop"}


def _norm_request(audit_id="norm-1", method="tpm", evidence=None):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": method, "threshold": 0, "sample_rule": "none", "unit": "gene", "source": "declared"},
        "evidence_observations": evidence if evidence is not None else [{"source_type": "observed", "verification": "confirmed", "value": method, "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def _dp01_request(audit_id="dp01-1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def test_normalization_declared_with_qualified_evidence_is_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm1.jsonl")
    result = audit_dp02_normalization(_norm_request(), store)
    assert result.scope == DP02_SCOPE
    assert result.judgment == "auditable"
    record = store.get("norm-1")
    assert record.audit_id == "norm-1"
    assert record.result.scope == DP02_SCOPE


def test_method_label_alone_is_not_evidence(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm2.jsonl")
    req = _norm_request(evidence=[{"source_type": "declared", "verification": "unverified", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}])
    result = audit_dp02_normalization(req, store)
    assert result.judgment == "scientifically_limited"
    assert result.matched_rule_ids == ()


def test_missing_critical_context_fails_closed_not_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm3a.jsonl")
    req = _norm_request()
    req["analysis_context"].pop("comparison")
    result = audit_dp02_normalization(req, store)
    assert result.judgment == "not_auditable"


def test_missing_declaration_maps_to_input_failure(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm3b.jsonl")
    req = _norm_request()
    del req["decision_declaration"]
    result = audit_dp02_normalization(req, store)
    # mirrors DP-01 convention: malformed/missing declaration -> input failure
    assert result.judgment == "integrity_failed"


def test_integrity_failure_isolates_material(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm4.jsonl")
    req = _norm_request()
    req["integrity_metadata"]["version"] = {"state": "failed"}
    result = audit_dp02_normalization(req, store)
    assert result.judgment == "integrity_failed"
    assert any(g.kind == "integrity_failure" for g in result.evidence_gaps)


def test_unknown_method_is_never_judged_by_name(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm5.jsonl")
    result = audit_dp02_normalization(_norm_request(method="mystery_norm"), store)
    # no whitelist: an explicit method with qualified point-local evidence is
    # supportable within scope; the label alone is never a verdict.
    assert result.judgment == "auditable"
    assert result.scope == DP02_SCOPE


def test_shared_ledger_coexists_and_replays_independently(tmp_path):
    store = DP01JSONLStore(tmp_path / "shared.jsonl")
    audit_dp01_filtering(_dp01_request(), store)
    audit_dp02_normalization(_norm_request(), store)
    assert store.get("dp01-1").result.scope == "DP-01/filtering"
    assert store.get("norm-1").result.scope == DP02_SCOPE
    assert replay_dp01_filtering(store, "dp01-1").result == store.get("dp01-1").result
    assert replay_dp02_normalization(store, "norm-1").result == store.get("norm-1").result


def test_next_actions_are_controlled_vocabulary(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm7.jsonl")
    req = _norm_request(evidence=[{"source_type": "declared", "verification": "unverified", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}])
    result = audit_dp02_normalization(req, store)
    assert result.judgment == "scientifically_limited"
    assert result.next_actions
    assert {a.action for a in result.next_actions} <= CONTROLLED_ACTIONS


def test_child_attempt_records_pending_in_shared_ledger(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm8.jsonl")
    audit_dp02_normalization(_norm_request(), store)
    child = {
        **_norm_request("norm-child"),
        "attempt": {"parent_audit_id": "norm-1", "change_kind": "evidence_supplement", "change_reason": "x"},
    }
    result = audit_dp02_normalization(child, store)
    assert result.judgment == "pending_attempt"
    record = store.get("norm-child")
    assert record.parent_audit_id == "norm-1"
    assert record.change.kind == "evidence_supplement"


def test_reaudit_creates_linked_record_with_source_version(tmp_path):
    store = DP01JSONLStore(tmp_path / "norm9.jsonl")
    audit_dp02_normalization(_norm_request(), store)
    audit_dp02_normalization(
        {**_norm_request("norm-child"), "attempt": {"parent_audit_id": "norm-1", "change_kind": "evidence_supplement", "change_reason": "x"}},
        store,
    )
    result = audit_dp02_normalization({"audit_id": "norm-ra", "re_audit": {"operation": "re_audit", "target_audit_id": "norm-child"}}, store)
    assert result.judgment == "auditable"
    record = store.get("norm-ra")
    assert record.operation == "re_audit"
    assert record.parent_audit_id == "norm-child"
    assert record.source_version_snapshot is not None
    assert record.change_summary is not None