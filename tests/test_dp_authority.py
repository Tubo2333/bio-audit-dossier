"""DP-AUTHORITY — sole result-authority container tests (inherited C-03 route).

Exercises the inherited authority container:
``stage_package -> acceptance_snapshot`` (exactly one embedded
``structured_strength_result``) plus a one-way ``acceptance_snapshot_ref``.
Forbidden shortcuts must fail closed: second snapshot / second result / copied
result / alternative carrier / ``structured_strength_result_ref`` / back-reference.

Runs via the project pytest `pythonpath=["src"]`.
"""

import json

import pytest

from bioaudit.api import (
    audit_dp_register,
    build_dp_acceptance_snapshot,
    derive_dp_strength,
    dp_acceptance_snapshot_ref,
    dp_authority_store,
)
from bioaudit.dp01_store import DP01JSONLStore

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

_CONTEXT = {
    "project_id": "project-auth-1", "analysis_id": "analysis-auth-1",
    "comparison": "case_vs_control", "data_type": "bulk_rnaseq",
    "audit_scope": "five-point-mvp", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
}


def _decl(method):
    return {"method": method, "threshold": 10, "sample_rule": "at_least_two_samples",
            "unit": "gene", "source": "declared"}


def _obs(value, verification="confirmed"):
    return {"source_type": "observed", "verification": verification, "value": value}


def _point(method, evidence):
    return {"decision_declaration": _decl(method), "evidence_observations": evidence}


# mixed: 4 auditable + 1 scientifically limited (label only)
_POINTS = {
    "filtering": _point("low_count_filter", [_obs("low_count_filter")]),
    "normalization": _point("tmm", [_obs("tmm")]),
    "differential_method": _point("deseq2", [_obs("deseq2")]),
    "multiple_testing_correction": _point("BH", [_obs("BH", verification="unverified")]),
    "significance_threshold": _point("padj_0.05", [_obs("padj_0.05")]),
}

_ASSEMBLY = {"audit_id": "auth-1", "analysis_context": _CONTEXT,
             "integrity_metadata": _INTEGRITY, "decision_points": _POINTS}


def _no_scalar(node):
    banned = {"total_score", "confidence", "ranking", "quality", "lane",
              "winner", "summary", "overall_score", "confidence_score"}
    if isinstance(node, dict):
        for k in node:
            assert k not in banned, k
            _no_scalar(node[k])
    elif isinstance(node, list):
        for v in node:
            _no_scalar(v)


@pytest.fixture
def stores(tmp_path):
    return (DP01JSONLStore(tmp_path / "points.jsonl"),
            dp_authority_store(tmp_path / "authority.jsonl"))


# 1. construction: exactly one embedded result, embedded (not referenced)
def test_snapshot_has_one_embedded_result(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    result = snap.structured_strength_result
    assert {"validation", "scope", "inference_type", "explanation_depth"} <= set(result)
    assert len(snap.audit_ids) == 5
    assert set(snap.version_snapshot) == {"ruleset_version", "ontology_version",
                                          "engine_version", "input_format_version",
                                          "adapter_version"}
    # stage_package carries the FULL input snapshot (embedded, not a ref)
    assert set(snap.stage_package.input_snapshot["decision_points"]) == set(_POINTS)


# 2. embedded result provenance is authoritative (not view-only)
def test_embedded_provenance_is_authoritative(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    prov = snap.structured_strength_result["_provenance"]
    assert prov["authoritative"] is True
    assert prov["sole_container"] is True


# 3. no scalar anywhere in the embedded result
def test_embedded_result_has_no_scalar(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    _no_scalar(snap.structured_strength_result)


# 4. separate authority ledger; point ledger unaffected and readable
def test_ledgers_are_separate_and_point_ledger_intact(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    assert authority_store.path != point_store.path
    again = authority_store.get(snap.snapshot_id)
    assert again.snapshot_id == snap.snapshot_id
    # point ledger still holds the five point records
    for suffix in ("dp01", "dp02", "dp03", "dp04", "dp05"):
        assert point_store.get(f"auth-1:{suffix}") is not None


# 5. forbidden shortcut rejected at construction, BEFORE any write
def test_forbidden_ref_key_rejected_before_write(stores, tmp_path):
    point_store, authority_store = stores
    bad = dict(_ASSEMBLY)
    bad["structured_strength_result_ref"] = "somewhere-else"
    with pytest.raises((ValueError, TypeError)):
        build_dp_acceptance_snapshot(bad, point_store, authority_store)
    assert not (tmp_path / "authority.jsonl").exists()


# 6. forbidden shortcut rejected on read-back (payload level)
def test_payload_with_forbidden_ref_rejected_on_read(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    payload = snap.to_dict()
    payload["structured_strength_result_ref"] = "elsewhere"
    with open(authority_store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
    with pytest.raises(ValueError):
        authority_store.get(snap.snapshot_id)


# 7. duplicate snapshot_id rejected; version mismatch rejected
def test_duplicate_id_and_version_mismatch_rejected(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    with pytest.raises(ValueError):
        authority_store.append(snap)

    payload = snap.to_dict()
    payload["snapshot_id"] = "snap-other"
    payload["version_snapshot"] = {**payload["version_snapshot"], "engine_version": "0.0.1"}
    with open(authority_store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
    with pytest.raises(ValueError):
        authority_store.get("snap-other")


# 7b. CARDINALITY: one acceptance snapshot per governed account (P-03 row142)
def test_second_snapshot_for_the_same_account_is_rejected(stores, tmp_path):
    """A different snapshot id must not produce a second container for the SAME
    account (same five governed point records + same pre-snapshot input)."""
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    first = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                         snapshot_id="snap-first", register=register)
    with pytest.raises(ValueError, match="already has an acceptance snapshot"):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     snapshot_id="snap-second", register=register)
    # fail-closed must not have written a second line
    assert len([line for line in (tmp_path / "authority.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]) == 1
    assert authority_store.get("snap-first").snapshot_id == first.snapshot_id


def test_different_input_snapshot_may_have_its_own_snapshot(stores):
    """A genuinely different pre-snapshot input (its own governed point records)
    is a different account, so the cardinality guard does not block it."""
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                 snapshot_id="snap-a", register=register)
    later = {"audit_id": "auth-2",
             "analysis_context": {**_CONTEXT, "intended_use": "bounded_reuse", "exclusions": "none — no explicit exclusions declared"},
             "integrity_metadata": _INTEGRITY, "decision_points": _POINTS}
    build_dp_acceptance_snapshot(later, point_store, authority_store,
                                 snapshot_id="snap-b")
    assert authority_store.get("snap-b") is not None
    assert authority_store.get("snap-a") is not None


# 8. the ref is one-way and exposes no result path
def test_ref_is_one_way_without_result(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    ref = dp_acceptance_snapshot_ref(snap)
    assert set(ref) == {"acceptance_snapshot_ref"}
    inner = ref["acceptance_snapshot_ref"]
    assert inner["snapshot_id"] == snap.snapshot_id
    assert "version_snapshot" in inner
    # no axes / result content leaked through the ref
    for axis in ("validation", "scope", "inference_type", "explanation_depth"):
        assert axis not in inner
    _no_scalar(ref)


# 9. mixed meaning preserved inside the embedded result
def test_mixed_meaning_preserved_in_embedded_result(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    result = snap.structured_strength_result
    # one point is insufficient -> the account is partial, not collapsed
    assert result["validation"]["scientific_evidence"] == "insufficient"
    assert result["inference_type"]["completeness"] == "partial"


# 10. fail-closed on malformed assembly input, nothing written
def test_malformed_input_fail_closed(stores, tmp_path):
    point_store, authority_store = stores
    with pytest.raises((ValueError, TypeError)):
        build_dp_acceptance_snapshot("not-a-mapping", point_store, authority_store)
    bad = {k: v for k, v in _ASSEMBLY.items() if k != "decision_points"}
    with pytest.raises((ValueError, TypeError)):
        build_dp_acceptance_snapshot(bad, point_store, authority_store)
    assert not (tmp_path / "authority.jsonl").exists()


# ---------------------------------------------------------------------------
# DP-AUTHORITY review-fix: ledger integrity, binding, authority basis, hygiene
# ---------------------------------------------------------------------------

_ALL_UNVERIFIED = {**{d: {"state": "unverified"} for d in
                      ("identity", "version", "source", "binding", "snapshot",
                       "permission", "unique_authority")},
                   "material_ids": ["material-1"], "pending_targets": ["decision"]}


def test_duplicate_lines_rejected_on_read(stores):
    """Standards B1: cross-line duplicate detection (sole authority must be unique)."""
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    with open(authority_store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap.to_dict(), sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        authority_store.get(snap.snapshot_id)


def test_append_has_concurrency_lock(stores):
    """Standards B1: append serialization exists (dp01_store parity)."""
    _point_store, authority_store = stores
    assert hasattr(authority_store, "_append_lock")


def test_audit_ids_must_bind_to_one_audit(stores):
    """Standards B2: five unrelated audit_ids must be rejected."""
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    payload = snap.to_dict()
    payload["snapshot_id"] = "snap-unbound"
    payload["audit_ids"] = ["totally", "unrelated", "ids", "of", "five"]
    with open(authority_store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        authority_store.get("snap-unbound")


def test_snapshot_audit_ids_resolve_in_point_ledger(stores):
    """Standards B2: the recorded audit_ids are the real point records."""
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    for audit_id in snap.audit_ids:
        assert point_store.get(audit_id) is not None


def test_authority_basis_recorded_when_unconfirmed(stores):
    """Spec F1 (b)+(c): container persists, but records the authority basis and
    does NOT unconditionally assert authoritative."""
    point_store, authority_store = stores
    request = {**_ASSEMBLY, "integrity_metadata": _ALL_UNVERIFIED}
    snap = build_dp_acceptance_snapshot(request, point_store, authority_store)
    basis = snap.authority_basis
    assert basis["confirmed"] is False
    assert "unique_authority" in basis["unconfirmed_domains"]
    prov = snap.structured_strength_result["_provenance"]
    assert prov["sole_container"] is True
    assert prov["authoritative"] is False


def test_authority_basis_confirmed_when_all_confirmed(stores):
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    assert snap.authority_basis["confirmed"] is True
    assert snap.authority_basis["unconfirmed_domains"] == []
    assert snap.structured_strength_result["_provenance"]["authoritative"] is True


def test_get_returns_independent_copy(stores):
    """Standards: read path must not hand out internal references."""
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    first = authority_store.get(snap.snapshot_id)
    first.structured_strength_result["validation"]["completeness"] = "TAMPERED"
    second = authority_store.get(snap.snapshot_id)
    assert second.structured_strength_result["validation"]["completeness"] != "TAMPERED"


def test_plain_rejects_non_string_keys():
    from bioaudit.dp_authority import StagePackage
    with pytest.raises(TypeError):
        StagePackage({1: "x"}).to_dict()


def test_json_error_reports_line_number(stores):
    point_store, authority_store = stores
    build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    with open(authority_store.path, "a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
    with pytest.raises(ValueError) as excinfo:
        authority_store.get("snapshot:auth-1")
    assert "line" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# DP-BUILD-REGISTER: the integration defect fix — build accepts a prebuilt register
# ---------------------------------------------------------------------------

def test_natural_order_register_then_build_with_register(stores):
    """The two entry points now compose in the natural order."""
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                        register=register)
    axes = derive_dp_strength(register)
    embedded = snap.structured_strength_result
    for axis in ("validation", "scope", "inference_type", "explanation_depth"):
        assert embedded[axis] == axes[axis], axis


def test_prebuilt_register_mismatch_is_rejected(stores, tmp_path):
    """Strict per-point guard: a register that disagrees with the ledger fails closed."""
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    tampered = dict(register)
    tampered["normalization"] = register["filtering"]  # wrong point result
    with pytest.raises(ValueError, match="does not match the persisted record"):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     register=tampered)
    assert not (tmp_path / "authority.jsonl").exists()


def test_prebuilt_register_request_must_match_ledger(stores, tmp_path):
    """B1 (both review axes, empirically reproduced): the passed request must
    match the input snapshot the ledger actually registered. Otherwise the
    snapshot's stage_package and its embedded result would come from two
    different inputs."""
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    other_request = {**_ASSEMBLY,
                     "analysis_context": {**_CONTEXT, "project_id": "OTHER-PROJECT"}}
    with pytest.raises(ValueError, match="does not match the persisted input snapshot"):
        build_dp_acceptance_snapshot(other_request, point_store, authority_store,
                                     register=register)
    assert not (tmp_path / "authority.jsonl").exists()


def test_point_store_with_wrong_record_type_raises_type_error(stores):
    """S1: a duck-typed store returning the wrong record shape must fail with a
    Type/Value error, not an AttributeError."""
    from types import SimpleNamespace

    _point_store, authority_store = stores

    class StubStore:
        def get(self, record_id):
            return {"not": "a record"}

    fake_register = {name: SimpleNamespace(judgment="auditable")
                     for name in ("filtering", "normalization", "differential_method",
                                  "multiple_testing_correction", "significance_threshold")}
    fake_register["_register_meta"] = {"first_bounded_set": True}
    with pytest.raises((TypeError, ValueError)):
        build_dp_acceptance_snapshot(_ASSEMBLY, StubStore(), authority_store,
                                     register=fake_register)


def test_prebuilt_register_from_other_audit_is_rejected(stores):
    """The register's audit ids must exist in the point ledger."""
    point_store, authority_store = stores
    other = audit_dp_register({**_ASSEMBLY, "audit_id": "other-1"}, point_store)
    with pytest.raises(ValueError):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     register=other)


def test_prebuilt_register_shape_guards(stores):
    point_store, authority_store = stores
    register = audit_dp_register(_ASSEMBLY, point_store)
    without_meta = {k: v for k, v in register.items() if k != "_register_meta"}
    with pytest.raises(ValueError):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     register=without_meta)
    missing_point = {k: v for k, v in register.items() if k != "filtering"}
    with pytest.raises(ValueError, match="missing: filtering"):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     register=missing_point)
    unknown_point = {**register, "extra_point": register["filtering"]}
    with pytest.raises(ValueError, match="unknown: extra_point"):
        build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store,
                                     register=unknown_point)


def test_build_without_register_unchanged(stores):
    """Backward compatibility: omitting `register` keeps the previous behaviour."""
    point_store, authority_store = stores
    snap = build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority_store)
    assert snap.authority_basis["confirmed"] is True
    assert authority_store.get(snap.snapshot_id) is not None


def test_api_exposes_register_parameter():
    import inspect

    from bioaudit import api

    params = inspect.signature(api.build_dp_acceptance_snapshot).parameters
    assert "register" in params
    assert "snapshot_id" in params
