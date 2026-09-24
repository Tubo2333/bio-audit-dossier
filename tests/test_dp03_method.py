"""DP-03 differential-analysis method slice tests (DP03-METHOD).

Goes through the public seam (`bioaudit.api.audit_dp03_method` /
`replay_dp03_method`) on the shared DP-01/DP-02 JSONL ledger. Signature DP-03
behavior: competing method declarations are preserved as `conflicted` with no
arbitrary winner (T-01 §7 / P-01 §4.3).
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import (
    audit_dp01_filtering,
    audit_dp02_normalization,
    audit_dp03_method,
    replay_dp01_filtering,
    replay_dp02_normalization,
    replay_dp03_method,
)
from bioaudit.dp01_store import DP01JSONLStore

DP03_SCOPE = "DP-03/differential_method"
AUDITABLE = "auditable"
SCIENTIFICALLY_LIMITED = "scientifically_limited"
CONFLICTED = "conflicted"
NOT_AUDITABLE = "not_auditable"
INTEGRITY_FAILED = "integrity_failed"
PENDING = "pending_attempt"
CONTROLLED = {"request_evidence", "submit_correction", "request_review", "stop"}


def _req(audit_id="m1", method="deseq2", evidence=None, integrity=None):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": method, "threshold": 0, "sample_rule": "none", "unit": "gene", "source": "declared"},
        "evidence_observations": evidence if evidence is not None else [{"source_type": "observed", "verification": "confirmed", "value": method, "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": integrity if integrity is not None else {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def _dp01_req(audit_id="dp01-1"):
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


def _dp02_req(audit_id="dp02-1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": "tpm", "threshold": 0, "sample_rule": "none", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def test_declared_method_with_qualified_evidence_is_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "t1.jsonl")
    r = audit_dp03_method(_req(), store)
    assert r.scope == DP03_SCOPE
    assert r.judgment == AUDITABLE
    assert store.get("m1").result.scope == DP03_SCOPE
    assert replay_dp03_method(store, "m1").result == store.get("m1").result


def test_competing_declarations_preserve_conflict_no_winner(tmp_path):
    store = DP01JSONLStore(tmp_path / "t2.jsonl")
    r = audit_dp03_method(_req(method="deseq2", evidence=[
        {"source_type": "declared", "verification": "unverified", "value": "deseq2", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "declared", "verification": "unverified", "value": "edger", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r.judgment == CONFLICTED
    assert any(a.action == "request_review" for a in r.next_actions)
    assert any(a.action == "submit_correction" for a in r.next_actions)
    assert not any(a.action == "stop" for a in r.next_actions)
    assert any(g.source == "declared" for g in r.evidence_gaps)
    assert r.local_contribution.scope == "method"


def test_observed_competing_methods_no_arbitrary_winner(tmp_path):
    store = DP01JSONLStore(tmp_path / "f1.jsonl")
    r = audit_dp03_method(_req("f1", method="deseq2", evidence=[
        {"source_type": "observed", "verification": "confirmed", "value": "deseq2", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "edger", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r.judgment == CONFLICTED
    assert any(a.action == "request_review" for a in r.next_actions)
    assert any(g.source == "observed" for g in r.evidence_gaps)
    assert r.local_contribution.scope == "method"


def test_declared_observed_method_conflict(tmp_path):
    store = DP01JSONLStore(tmp_path / "t3.jsonl")
    r = audit_dp03_method(_req(evidence=[
        {"source_type": "observed", "verification": "confirmed", "value": "edger", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ], integrity=_req()["integrity_metadata"]), store)
    assert r.judgment == CONFLICTED


def test_method_label_alone_is_not_evidence(tmp_path):
    store = DP01JSONLStore(tmp_path / "t4.jsonl")
    r = audit_dp03_method(_req(evidence=[{"source_type": "declared", "verification": "unverified", "value": "deseq2", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store)
    assert r.judgment == SCIENTIFICALLY_LIMITED
    assert {a.action for a in r.next_actions} <= CONTROLLED


def test_missing_context_fails_closed_and_missing_declaration_input_failure(tmp_path):
    store = DP01JSONLStore(tmp_path / "t5.jsonl")
    ctx_bad = _req("m5a")
    ctx_bad["analysis_context"].pop("comparison")
    assert audit_dp03_method(ctx_bad, store).judgment == NOT_AUDITABLE
    dec_bad = _req("m5b")
    del dec_bad["decision_declaration"]
    assert audit_dp03_method(dec_bad, store).judgment == INTEGRITY_FAILED


def test_integrity_failure_isolates_material(tmp_path):
    store = DP01JSONLStore(tmp_path / "t6.jsonl")
    bad = _req()["integrity_metadata"]
    bad["binding"] = {"state": "failed"}
    r = audit_dp03_method(_req("m6", integrity=bad), store)
    assert r.judgment == INTEGRITY_FAILED
    assert any(g.target == "m" for g in r.evidence_gaps)
    assert any(a.target == "m" for a in r.next_actions)


def test_unknown_method_never_judged_by_name(tmp_path):
    store = DP01JSONLStore(tmp_path / "t7.jsonl")
    r = audit_dp03_method(_req("m7", method="mystery_de"), store)
    assert r.judgment == AUDITABLE
    assert r.scope == DP03_SCOPE


def test_three_decision_points_coexist_and_replay_independently(tmp_path):
    store = DP01JSONLStore(tmp_path / "t8.jsonl")
    audit_dp01_filtering(_dp01_req(), store)
    audit_dp02_normalization(_dp02_req(), store)
    audit_dp03_method(_req(), store)
    assert store.get("dp01-1").result.scope == "DP-01/filtering"
    assert store.get("dp02-1").result.scope == "DP-02/normalization"
    assert store.get("m1").result.scope == DP03_SCOPE
    assert replay_dp01_filtering(store, "dp01-1").result == store.get("dp01-1").result
    assert replay_dp02_normalization(store, "dp02-1").result == store.get("dp02-1").result
    assert replay_dp03_method(store, "m1").result == store.get("m1").result


def test_child_attempt_and_reaudit_via_shared_mechanism(tmp_path):
    store = DP01JSONLStore(tmp_path / "t9.jsonl")
    audit_dp03_method(_req("m9a"), store)
    child = {**_req("m9b"), "attempt": {"parent_audit_id": "m9a", "change_kind": "evidence_supplement", "change_reason": "x"}}
    assert audit_dp03_method(child, store).judgment == PENDING
    assert store.get("m9b").parent_audit_id == "m9a"
    ra = audit_dp03_method({"audit_id": "m9c", "re_audit": {"operation": "re_audit", "target_audit_id": "m9b"}}, store)
    assert ra.judgment == AUDITABLE
    rec = store.get("m9c")
    assert rec.operation == "re_audit"
    assert rec.parent_audit_id == "m9b"
    assert rec.source_version_snapshot is not None
    assert rec.change_summary is not None


def test_unrelated_referenced_evidence_does_not_make_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "t10.jsonl")
    r = audit_dp03_method(_req("m10", evidence=[
        {"source_type": "referenced", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r.judgment == SCIENTIFICALLY_LIMITED


def test_controlled_actions_with_stop_trigger(tmp_path):
    store = DP01JSONLStore(tmp_path / "ctl.jsonl")
    ctx = _req("ctl-ctx")
    ctx["analysis_context"].pop("comparison")
    na = audit_dp03_method(ctx, store)
    assert na.judgment == NOT_AUDITABLE
    assert any(a.action == "stop" for a in na.next_actions)
    bad = _req()["integrity_metadata"]
    bad["binding"] = {"state": "failed"}
    samples = [
        audit_dp03_method(_req("ctl-aud"), store),
        audit_dp03_method(_req("ctl-lim", evidence=[{"source_type": "declared", "verification": "unverified", "value": "deseq2", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store),
        audit_dp03_method(_req("ctl-cfg", evidence=[{"source_type": "observed", "verification": "confirmed", "value": "edger", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store),
        na,
        audit_dp03_method(_req("ctl-int", integrity=bad), store),
        audit_dp03_method({**_req("ctl-pend"), "attempt": {"parent_audit_id": "ctl-aud", "change_kind": "evidence_supplement", "change_reason": "x"}}, store),
    ]
    for r in samples:
        assert {a.action for a in r.next_actions} <= CONTROLLED