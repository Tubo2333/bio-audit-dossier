"""DP-02 review-fix contract tests (DP02-REVIEW-FIX).

Locks the human-selected findings from the dual-axis review:
- B1: per-domain integrity states identical between DP-01 and DP-02 for the same input.
- Spec-S1: only evidence that names the declared method makes normalization auditable
  (unrelated/cross-point confirmed evidence must NOT).
- S2: nested local_contribution.scope is normalization everywhere; conflicted gap/action
  targets are consistent (normalization_method).
- S3: conflicted and referenced-confirmed paths covered.
- S4: auditable is name-independent; integrity isolation carries material IDs.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import (
    audit_dp01_filtering,
    audit_dp02_normalization,
    replay_dp02_normalization,
)
from bioaudit.dp01_store import DP01JSONLStore

_DOMAINS = ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")
AUDITABLE = "auditable"
SCIENTIFICALLY_LIMITED = "scientifically_limited"
CONFLICTED = "conflicted"
INTEGRITY_FAILED = "integrity_failed"


def _req(audit_id="x", method="tpm", evidence=None, integrity=None):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": method, "threshold": 0, "sample_rule": "none", "unit": "gene", "source": "declared"},
        "evidence_observations": evidence if evidence is not None else [{"source_type": "observed", "verification": "confirmed", "value": method, "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": integrity if integrity is not None else {
            **{d: {"state": "confirmed"} for d in _DOMAINS}, "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def test_integrity_states_match_dp01_per_domain(tmp_path):
    store01 = DP01JSONLStore(tmp_path / "i01.jsonl")
    store02 = DP01JSONLStore(tmp_path / "i02.jsonl")
    bad_meta = _req()["integrity_metadata"]
    bad_meta["version"] = {"state": "failed"}
    bad_meta["snapshot"] = {"state": "conflicted"}
    r01 = audit_dp01_filtering(_req("d01", integrity=bad_meta), store01)
    r02 = audit_dp02_normalization(_req("d02", integrity=bad_meta), store02)
    assert r01.judgment == r02.judgment == INTEGRITY_FAILED
    states01 = {f.state for f in r01.findings}
    states02 = {f.state for f in r02.findings}
    assert states01 == states02
    assert {"failed", "conflicted"} <= states01  # per-domain states preserved, not all "unverified"


def test_unrelated_confirmed_evidence_does_not_make_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "s1a.jsonl")
    # referenced evidence does not enter the declared/observed conflict check,
    # so it is the correct negative probe for the auditable evidence test.
    r = audit_dp02_normalization(
        _req("u1", method="tpm", evidence=[{"source_type": "referenced", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]),
        store,
    )
    assert r.judgment == SCIENTIFICALLY_LIMITED


def test_matching_confirmed_evidence_is_auditable_and_replays(tmp_path):
    store = DP01JSONLStore(tmp_path / "s1b.jsonl")
    r = audit_dp02_normalization(
        _req("m1", method="tpm", evidence=[{"source_type": "observed", "verification": "confirmed", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]),
        store,
    )
    assert r.judgment == AUDITABLE
    assert replay_dp02_normalization(store, "m1").result == store.get("m1").result


def test_nested_contribution_scope_is_normalization_across_paths(tmp_path):
    store = DP01JSONLStore(tmp_path / "s2.jsonl")
    aud = audit_dp02_normalization(_req("a1"), store)
    lim = audit_dp02_normalization(_req("l1", evidence=[{"source_type": "declared", "verification": "unverified", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store)
    cfg = audit_dp02_normalization(_req("c1", evidence=[{"source_type": "observed", "verification": "confirmed", "value": "cpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]), store)
    child = {**_req("p1"), "attempt": {"parent_audit_id": "a1", "change_kind": "evidence_supplement", "change_reason": "x"}}
    pend = audit_dp02_normalization(child, store)
    for r in (aud, lim, cfg, pend):
        assert r.local_contribution.scope == "normalization"
    assert cfg.judgment == CONFLICTED
    assert any(g.target == "normalization_method" for g in cfg.evidence_gaps)
    assert cfg.next_actions
    assert {a.action for a in cfg.next_actions} == {"request_evidence", "request_review"}
    assert any(a.target == "normalization_method" for a in cfg.next_actions)
    assert all(g.target != "filtering_method" for g in cfg.evidence_gaps)
    assert all(a.target != "filtering_method" for a in cfg.next_actions)


def test_referenced_confirmed_evidence_supports_auditable(tmp_path):
    store = DP01JSONLStore(tmp_path / "ref.jsonl")
    r = audit_dp02_normalization(
        _req("r1", evidence=[{"source_type": "referenced", "verification": "confirmed", "value": "tpm", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]),
        store,
    )
    assert r.judgment == AUDITABLE


def test_auditable_is_name_independent_across_methods(tmp_path):
    store = DP01JSONLStore(tmp_path / "names.jsonl")
    for method in ("cpm", "tmm", "quantile"):
        r = audit_dp02_normalization(_req(f"n-{method}", method=method), store)
        assert r.judgment == AUDITABLE


def test_integrity_isolation_targets_material_ids(tmp_path):
    store = DP01JSONLStore(tmp_path / "iso.jsonl")
    bad_meta = _req()["integrity_metadata"]
    bad_meta["binding"] = {"state": "failed"}
    r = audit_dp02_normalization(_req("iso", integrity=bad_meta), store)
    assert r.judgment == INTEGRITY_FAILED
    targets = {g.target for g in r.evidence_gaps} | {a.target for a in r.next_actions}
    assert "m" in targets