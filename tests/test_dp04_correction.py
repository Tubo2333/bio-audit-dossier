"""DP-04 multiple-testing correction slice tests (DP04-CORRECTION).

Goes through the public seam (`bioaudit.api.audit_dp04_correction` /
`replay_dp04_correction`) on the shared DP-01/02/03 JSONL ledger. Signature
DP-04 behavior (authorization decision 7): a missing correction declaration
fails closed (no assumed default); an explicit `none`/`no_correction`
declaration goes through the same named-method path.
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
    audit_dp04_correction,
    replay_dp01_filtering,
    replay_dp02_normalization,
    replay_dp03_method,
    replay_dp04_correction,
)
from bioaudit.dp01_store import DP01JSONLStore

DP04_SCOPE = "DP-04/multiple_testing_correction"
AUDITABLE = "auditable"
SCIENTIFICALLY_LIMITED = "scientifically_limited"
CONFLICTED = "conflicted"
NOT_AUDITABLE = "not_auditable"
INTEGRITY_FAILED = "integrity_failed"
PENDING = "pending_attempt"
CONTROLLED = {"request_evidence", "submit_correction", "request_review", "stop"}


def _req(audit_id="c1", method="BH", evidence=None, integrity=None):
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


def _dp03_req(audit_id="dp03-1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": "deseq2", "threshold": 0, "sample_rule": "none", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "deseq2", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def test_declared_correction_with_qualified_evidence_is_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "t1.jsonl")
    r = audit_dp04_correction(_req(), store)
    assert r.scope == DP04_SCOPE
    assert r.judgment == AUDITABLE
    assert store.get("c1").result.scope == DP04_SCOPE
    assert replay_dp04_correction(store, "c1").result == store.get("c1").result


def test_explicit_none_correction_is_auditable_with_evidence(tmp_path):
    store = DP01JSONLStore(tmp_path / "t2.jsonl")
    r = audit_dp04_correction(_req("c2", method="none"), store)
    assert r.judgment == AUDITABLE
    assert r.scope == DP04_SCOPE


def test_explicit_none_label_alone_is_scientifically_limited(tmp_path):
    store = DP01JSONLStore(tmp_path / "t3.jsonl")
    r = audit_dp04_correction(_req("c3", method="none", evidence=[{"source_type": "declared", "verification": "unverified", "value": "none", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store)
    assert r.judgment == SCIENTIFICALLY_LIMITED


def test_missing_correction_declaration_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "t4.jsonl")
    req = _req("c4")
    del req["decision_declaration"]
    r = audit_dp04_correction(req, store)
    assert r.judgment == INTEGRITY_FAILED  # 缺声明拒：不假设默认矫正


def test_missing_context_fails_closed_with_stop(tmp_path):
    store = DP01JSONLStore(tmp_path / "t5.jsonl")
    req = _req("c5")
    req["analysis_context"].pop("comparison")
    r = audit_dp04_correction(req, store)
    assert r.judgment == NOT_AUDITABLE
    assert any(a.action == "stop" for a in r.next_actions)


def test_integrity_failure_isolates_material(tmp_path):
    store = DP01JSONLStore(tmp_path / "t6.jsonl")
    bad = _req()["integrity_metadata"]
    bad["binding"] = {"state": "failed"}
    r = audit_dp04_correction(_req("c6", integrity=bad), store)
    assert r.judgment == INTEGRITY_FAILED
    assert any(g.target == "m" for g in r.evidence_gaps)
    assert any(a.target == "m" for a in r.next_actions)


def test_observed_competing_corrections_no_arbitrary_winner(tmp_path):
    store = DP01JSONLStore(tmp_path / "t7.jsonl")
    r = audit_dp04_correction(_req("c7", method="BH", evidence=[
        {"source_type": "observed", "verification": "confirmed", "value": "BH", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "bonferroni", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r.judgment == CONFLICTED
    assert any(a.action == "request_review" for a in r.next_actions)
    assert any(g.source == "observed" for g in r.evidence_gaps)


def test_declared_observed_correction_conflict(tmp_path):
    store = DP01JSONLStore(tmp_path / "t8.jsonl")
    r = audit_dp04_correction(_req("c8", method="BH", evidence=[{"source_type": "observed", "verification": "confirmed", "value": "bonferroni", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store)
    assert r.judgment == CONFLICTED
    assert any(g.source == "observed" for g in r.evidence_gaps)


def test_competing_declared_correction_no_arbitrary_winner(tmp_path):
    store = DP01JSONLStore(tmp_path / "t12.jsonl")
    # declared competitor + matching observed: must not silently pick the winner
    r = audit_dp04_correction(_req("c12a", method="BH", evidence=[
        {"source_type": "declared", "verification": "confirmed", "value": "bonferroni", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
        {"source_type": "observed", "verification": "confirmed", "value": "BH", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r.judgment == CONFLICTED
    assert any(g.source == "declared" for g in r.evidence_gaps)
    assert any(a.action == "request_review" for a in r.next_actions)
    assert any(a.action == "submit_correction" for a in r.next_actions)
    # declared-only competitor also fails closed (Spec S-2 case)
    r2 = audit_dp04_correction(_req("c12b", method="BH", evidence=[
        {"source_type": "declared", "verification": "confirmed", "value": "bonferroni", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000},
    ]), store)
    assert r2.judgment == CONFLICTED
    assert any(g.source == "declared" for g in r2.evidence_gaps)


def test_four_decision_points_coexist_and_replay_independently(tmp_path):
    store = DP01JSONLStore(tmp_path / "t9.jsonl")
    audit_dp01_filtering(_dp01_req(), store)
    audit_dp02_normalization(_dp02_req(), store)
    audit_dp03_method(_dp03_req(), store)
    audit_dp04_correction(_req(), store)
    assert store.get("dp01-1").result.scope == "DP-01/filtering"
    assert store.get("dp02-1").result.scope == "DP-02/normalization"
    assert store.get("dp03-1").result.scope == "DP-03/differential_method"
    assert store.get("c1").result.scope == DP04_SCOPE
    assert replay_dp01_filtering(store, "dp01-1").result == store.get("dp01-1").result
    assert replay_dp02_normalization(store, "dp02-1").result == store.get("dp02-1").result
    assert replay_dp03_method(store, "dp03-1").result == store.get("dp03-1").result
    assert replay_dp04_correction(store, "c1").result == store.get("c1").result


def test_child_attempt_and_reaudit_via_shared_mechanism(tmp_path):
    store = DP01JSONLStore(tmp_path / "t10.jsonl")
    audit_dp04_correction(_req("c10a"), store)
    child = {**_req("c10b"), "attempt": {"parent_audit_id": "c10a", "change_kind": "evidence_supplement", "change_reason": "x"}}
    assert audit_dp04_correction(child, store).judgment == PENDING
    assert store.get("c10b").parent_audit_id == "c10a"
    ra = audit_dp04_correction({"audit_id": "c10c", "re_audit": {"operation": "re_audit", "target_audit_id": "c10b"}}, store)
    assert ra.judgment == AUDITABLE
    rec = store.get("c10c")
    assert rec.operation == "re_audit"
    assert rec.parent_audit_id == "c10b"
    assert rec.source_version_snapshot is not None
    assert rec.change_summary is not None


def test_controlled_actions_with_stop_trigger(tmp_path):
    store = DP01JSONLStore(tmp_path / "t11.jsonl")
    ctx = _req("c11-ctx")
    ctx["analysis_context"].pop("comparison")
    na = audit_dp04_correction(ctx, store)
    assert na.judgment == NOT_AUDITABLE
    assert any(a.action == "stop" for a in na.next_actions)
    bad = _req()["integrity_metadata"]
    bad["binding"] = {"state": "failed"}
    samples = [
        audit_dp04_correction(_req("c11-aud"), store),
        audit_dp04_correction(_req("c11-lim", evidence=[{"source_type": "declared", "verification": "unverified", "value": "BH", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store),
        audit_dp04_correction(_req("c11-cfg", evidence=[{"source_type": "observed", "verification": "confirmed", "value": "bonferroni", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store),
        na,
        audit_dp04_correction(_req("c11-int", integrity=bad), store),
        audit_dp04_correction({**_req("c11-pend"), "attempt": {"parent_audit_id": "c11-aud", "change_kind": "evidence_supplement", "change_reason": "x"}}, store),
    ]
    for r in samples:
        assert {a.action for a in r.next_actions} <= CONTROLLED