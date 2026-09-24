"""DP-ROLES — authored / reviewed / receipted / adjudicated role+scope records.

Exercises the review/receipt entity: named role + named target + named scope
records in their own ledger. The system does NOT perform review or adjudication,
and these records must never become result authority
(T-01 §11.2 L675, §4.8 L290/L296/L297, L532, L583; P-01 §8.1; P-02 §6.3).

Runs via the project pytest `pythonpath=["src"]`.
"""

import json

import pytest

from bioaudit.api import (
    audit_dp_register,
    build_dp_acceptance_snapshot,
    dp_acceptance_snapshot_ref,
    dp_authority_store,
    dp_role_store,
    list_dp_roles,
    record_dp_role,
)
from bioaudit.dp01_store import DP01JSONLStore

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

_CONTEXT = {
    "project_id": "project-roles-1", "analysis_id": "analysis-roles-1",
    "comparison": "case_vs_control", "data_type": "bulk_rnaseq",
    "audit_scope": "five-point-mvp", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
}


def _decl(m):
    return {"method": m, "threshold": 10, "sample_rule": "at_least_two_samples",
            "unit": "gene", "source": "declared"}


def _obs(v):
    return {"source_type": "observed", "verification": "confirmed", "value": v, "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}


_POINTS = {k: {"decision_declaration": _decl(m), "evidence_observations": [_obs(m)]}
           for k, m in (("filtering", "low_count_filter"), ("normalization", "tmm"),
                        ("differential_method", "deseq2"),
                        ("multiple_testing_correction", "BH"),
                        ("significance_threshold", "padj_0.05"))}

_ASSEMBLY = {"audit_id": "roles-1", "analysis_context": _CONTEXT,
             "integrity_metadata": _INTEGRITY, "decision_points": _POINTS}


@pytest.fixture
def role_ledger(tmp_path):
    return dp_role_store(tmp_path / "roles.jsonl")


def _record(store, **kw):
    params = {"record_id": "r1", "role": "reviewed", "actor": "reviewer-A",
              "target": "acceptance_snapshot:snapshot:roles-1",
              "scope": "DP-01..DP-05 declarations and evidence",
              "receipt_kind": None}
    params.update(kw)
    params = {k: v for k, v in params.items() if v is not None}
    return record_dp_role(store, **params)


# 1. four roles recorded and distinguishable
def test_four_roles_distinguishable(role_ledger):
    for idx, role in enumerate(("authored", "reviewed", "receipted", "adjudicated")):
        extra = {"receipt_kind": "administrative"} if role == "receipted" else {}
        _record(role_ledger, record_id=f"r{idx}", role=role, **extra)
    roles = {role_ledger.get(f"r{i}").role for i in range(4)}
    assert roles == {"authored", "reviewed", "receipted", "adjudicated"}


# 2. listing by target
def test_list_by_target(role_ledger):
    _record(role_ledger, record_id="a", role="authored", target="report:account-1")
    _record(role_ledger, record_id="b", role="reviewed", target="report:account-2")
    assert [r.record_id for r in list_dp_roles(role_ledger, target="report:account-1")] == ["a"]
    assert len(list_dp_roles(role_ledger)) == 2


# 3. NON-AUTHORITY guard: a role record can never be consumed as authority
def test_role_record_cannot_become_authority(role_ledger, tmp_path):
    record = _record(role_ledger)
    with pytest.raises(TypeError):
        dp_acceptance_snapshot_ref(record)
    authority = dp_authority_store(tmp_path / "authority.jsonl")
    with pytest.raises(TypeError):
        authority.append(record)


# 4. closed schema: approval/lane/release/result keys are rejected
def test_approval_style_keys_rejected(role_ledger):
    record = _record(role_ledger)
    payload = record.to_dict()
    payload["record_id"] = "r-extra"
    payload["lane"] = "validated"
    with open(role_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        role_ledger.get("r-extra")


def test_result_carrier_keys_rejected(role_ledger):
    record = _record(role_ledger)
    payload = record.to_dict()
    payload["record_id"] = "r-result"
    payload["structured_strength_result"] = {"validation": {}}
    with open(role_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        role_ledger.get("r-result")


# 5. role vocabulary is restricted to the four inherited roles
def test_invalid_role_value_rejected(role_ledger):
    with pytest.raises(ValueError):
        _record(role_ledger, role="approved")


# 6. "exists" is not "reviewed": building a snapshot creates no role records
def test_snapshot_build_creates_no_role_records(tmp_path):
    point_store = DP01JSONLStore(tmp_path / "points.jsonl")
    authority = dp_authority_store(tmp_path / "authority.jsonl")
    roles = dp_role_store(tmp_path / "roles.jsonl")
    build_dp_acceptance_snapshot(_ASSEMBLY, point_store, authority)
    assert list_dp_roles(roles) == []
    assert not (tmp_path / "roles.jsonl").exists()


# 7. named actor / target / scope are mandatory
@pytest.mark.parametrize("field", ["actor", "target", "scope"])
def test_named_fields_required(role_ledger, field):
    base = {"record_id": "r1", "role": "reviewed", "actor": "author-A",
            "target": "acceptance_snapshot:snapshot:roles-1", "scope": "DP-01..DP-05"}
    for bad in ("", None):
        with pytest.raises(ValueError):
            record_dp_role(role_ledger, **{**base, field: bad})


def test_record_id_required(role_ledger):
    with pytest.raises((ValueError, TypeError)):
        _record(role_ledger, record_id="")


# 8. ledger discipline parity with the authority ledger
def test_duplicate_id_rejected(role_ledger):
    _record(role_ledger)
    with pytest.raises(ValueError):
        _record(role_ledger)


def test_cross_line_duplicate_rejected(role_ledger):
    record = _record(role_ledger)
    with open(role_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        role_ledger.get("r1")


def test_bad_json_line_reports_line_number(role_ledger):
    _record(role_ledger)
    with open(role_ledger.path, "a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
    with pytest.raises(ValueError) as excinfo:
        role_ledger.get("r1")
    assert "line" in str(excinfo.value).lower()


def test_version_mismatch_rejected(role_ledger):
    record = _record(role_ledger)
    payload = record.to_dict()
    payload["record_id"] = "r-ver"
    payload["version_snapshot"] = {**payload["version_snapshot"], "engine_version": "0.0.1"}
    with open(role_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        role_ledger.get("r-ver")


def test_append_lock_present(role_ledger):
    assert hasattr(role_ledger, "_append_lock")


# 9. records are immutable (no in-place mutation can rewrite what was recorded)
def test_records_are_immutable(role_ledger):
    import dataclasses

    record = _record(role_ledger)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.scope = "TAMPERED"
    assert role_ledger.get("r1").scope == "DP-01..DP-05 declarations and evidence"


# ---------------------------------------------------------------------------
# DP-ROLES review-fix: nested freeze, receipt_kind, target vocabulary, lock use
# ---------------------------------------------------------------------------

def test_nested_version_snapshot_is_frozen(role_ledger):
    """Standards S2: frozen must cover nested content, not just attributes."""
    record = _record(role_ledger)
    with pytest.raises(TypeError):
        record.version_snapshot["engine_version"] = "TAMPERED"
    # and the ledger itself is untouched either way
    assert role_ledger.get("r1").version_snapshot["engine_version"] != "TAMPERED"


def test_receipt_kind_required_for_receipted(role_ledger):
    """Spec: receipt != substantive review must be an explicit constraint."""
    with pytest.raises(ValueError):
        record_dp_role(role_ledger, record_id="k1", role="receipted",
                       actor="coordinator-A",
                       target="acceptance_snapshot:snapshot:roles-1",
                       scope="administrative check")


def test_receipt_kind_closed_domain(role_ledger):
    with pytest.raises(ValueError):
        record_dp_role(role_ledger, record_id="k2", role="receipted",
                       actor="coordinator-A",
                       target="acceptance_snapshot:snapshot:roles-1",
                       scope="administrative check", receipt_kind="substantive")
    # the closed administrative values are accepted
    record_dp_role(role_ledger, record_id="k3", role="receipted",
                   actor="coordinator-A",
                   target="acceptance_snapshot:snapshot:roles-1",
                   scope="administrative check", receipt_kind="point_in_time")


def test_receipt_kind_forbidden_for_non_receipt_roles(role_ledger):
    with pytest.raises(ValueError):
        record_dp_role(role_ledger, record_id="k4", role="reviewed",
                       actor="reviewer-A",
                       target="acceptance_snapshot:snapshot:roles-1",
                       scope="DP-01..DP-05", receipt_kind="administrative")


def test_target_prefix_vocabulary_is_closed(role_ledger):
    """The named target must use an authorized kind prefix (no id existence claim)."""
    with pytest.raises(ValueError):
        _record(role_ledger, record_id="t1", target="somewhere-else:whatever")
    for prefix in ("report:", "point:"):
        _record(role_ledger, record_id=f"t-{prefix.strip(':')}", target=f"{prefix}artifact-1")


def test_append_goes_through_the_lock(role_ledger, monkeypatch):
    """Standards N2: the lock must actually serialize append (not just exist)."""
    import contextlib

    entered = {"count": 0}

    @contextlib.contextmanager
    def spy():
        entered["count"] += 1
        yield

    monkeypatch.setattr(role_ledger, "_append_lock", spy)
    _record(role_ledger)
    assert entered["count"] == 1


def test_api_accessors_expose_explicit_parameters():
    """Standards S4: api accessors must not hide the keyword-only signature."""
    import inspect

    from bioaudit import api

    params = inspect.signature(api.record_dp_role).parameters
    for name in ("role_store", "record_id", "role", "actor", "target", "scope", "note"):
        assert name in params, name
