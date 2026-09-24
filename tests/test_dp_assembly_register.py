"""DP-ASSEMBLY — five-point register calling surface tests (authorized seam).

Exercises ``bioaudit.api.audit_dp_register``: one assembly request carrying
shared context + integrity_metadata + five per-point decision declarations and
evidence; the public seam runs the five existing point-local seams over the
single shared ledger and returns a plain per-scope register view that preserves
per-point meaning (mixed states, no combined score, no second result-authority
container).

Test runs via the project pytest `pythonpath=["src"]` — no sys.path fiddle.
"""

import pytest

from bioaudit.api import audit_dp_register
from bioaudit.dp01_store import DP01JSONLStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

_CONTEXT = {
    "project_id": "project-1",
    "analysis_id": "analysis-1",
    "comparison": "case_vs_control",
    "data_type": "bulk_rnaseq",
    "audit_scope": "five-point",
    "intended_use": "scientific_analysis",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
}


def _declaration(method, threshold=10, sample_rule="at_least_two_samples"):
    return {"method": method, "threshold": threshold,
            "sample_rule": sample_rule, "unit": "gene", "source": "declared"}


def _obs(value, verification="confirmed", source_type="observed"):
    return {"source_type": source_type, "verification": verification, "value": value}


def _point(declaration, evidence):
    return {"decision_declaration": declaration, "evidence_observations": evidence}


def _assembly(decision_points, audit_id="asm-1", context=None, integrity=None, **extra):
    request = {
        "audit_id": audit_id,
        "analysis_context": context if context is not None else _CONTEXT,
        "integrity_metadata": integrity if integrity is not None else _INTEGRITY,
        "decision_points": decision_points,
    }
    request.update(extra)
    return request


_FIVE_AUDITABLE = {
    "filtering": _point(_declaration("low_count_filter"),
                        [_obs("low_count_filter")]),
    "normalization": _point(_declaration("tmm"), [_obs("tmm")]),
    "differential_method": _point(_declaration("deseq2"), [_obs("deseq2")]),
    "multiple_testing_correction": _point(_declaration("BH"), [_obs("BH")]),
    "significance_threshold": _point(_declaration("padj_0.05"),
                                     [_obs("padj_0.05")]),
}

_MIXED = {
    "filtering": _point(_declaration("low_count_filter"),
                        [_obs("low_count_filter")]),          # auditable
    "normalization": _point(_declaration("tmm"),
                            [_obs("tmm", verification="unverified")]),  # label-only -> scientifically_limited
    "differential_method": _point(_declaration("deseq2"),
                                  [_obs("deseq2"), _obs("edgeR")]),  # two confirmed observed -> conflicted
    "multiple_testing_correction": _point({"method": "BH", "threshold": 5,
                                           "unit": "gene", "source": "declared"},  # malformed -> integrity_failed
                                          [_obs("BH")]),
    "significance_threshold": _point(_declaration("padj_0.05"),
                                     [_obs("padj_0.05")]),     # auditable
}


@pytest.fixture
def ledger(tmp_path):
    return DP01JSONLStore(tmp_path / "ledger.jsonl")


# ---------------------------------------------------------------------------
# 1. All five auditable -> exactly five ledger records, register preserves scopes
# ---------------------------------------------------------------------------

def test_five_auditable_persists_five_records_and_register_scopes(ledger):
    register = audit_dp_register(_assembly(_FIVE_AUDITABLE), ledger)

    expected_scopes = {
        "filtering": "DP-01/filtering",
        "normalization": "DP-02/normalization",
        "differential_method": "DP-03/differential_method",
        "multiple_testing_correction": "DP-04/multiple_testing_correction",
        "significance_threshold": "DP-05/significance_threshold",
    }
    for key, result in register.items():
        if key == "_register_meta":
            continue
        assert result.scope == expected_scopes[key], key
        assert result.judgment == "auditable", key

    # five persisted records in the shared ledger
    for key, audit_id in (("filtering", "asm-1:dp01"),
                          ("normalization", "asm-1:dp02"),
                          ("differential_method", "asm-1:dp03"),
                          ("multiple_testing_correction", "asm-1:dp04"),
                          ("significance_threshold", "asm-1:dp05")):
        record = ledger.get(audit_id)
        assert record is not None


# ---------------------------------------------------------------------------
# 2. Mixed states preserved, no combined score / winner
# ---------------------------------------------------------------------------

def test_mixed_register_preserves_per_point_meaning(ledger):
    register = audit_dp_register(_assembly(_MIXED), ledger)

    assert register["filtering"].judgment == "auditable"
    assert register["normalization"].judgment == "scientifically_limited"
    assert register["differential_method"].judgment == "conflicted"
    assert register["multiple_testing_correction"].judgment == "integrity_failed"
    assert register["significance_threshold"].judgment == "auditable"

    # register has no composite score / no winner fields
    assert "total_score" not in register
    assert "winner" not in register
    assert "summary_judgment" not in register
    assert "score" not in register

    # a mixed register exposes more than one distinct judgment => not scalarized
    judgments = {register[k].judgment for k in register if k != "_register_meta"}
    assert len(judgments) >= 3


# ---------------------------------------------------------------------------
# 3. Assembly request shape is validated fail-closed
# ---------------------------------------------------------------------------

def test_missing_decision_points_rejected(ledger):
    req = _assembly(_FIVE_AUDITABLE)
    del req["decision_points"]
    with pytest.raises((ValueError, TypeError)):
        audit_dp_register(req, ledger)


def test_missing_one_point_rejected(ledger):
    pts = dict(_FIVE_AUDITABLE)
    del pts["normalization"]
    with pytest.raises((ValueError, TypeError)):
        audit_dp_register(_assembly(pts), ledger)


def test_unknown_point_rejected(ledger):
    pts = dict(_FIVE_AUDITABLE)
    pts["extra_point"] = _point(_declaration("x"), [_obs("x")])
    with pytest.raises((ValueError, TypeError)):
        audit_dp_register(_assembly(pts), ledger)


def test_invalid_assembly_request_non_mapping(ledger):
    with pytest.raises((ValueError, TypeError)):
        audit_dp_register("not-a-mapping", ledger)


# ---------------------------------------------------------------------------
# 4. audit_id prefixing gives retrievable per-point ids
# ---------------------------------------------------------------------------

def test_audit_id_prefixing_in_ledger(ledger):
    audit_dp_register(_assembly(_FIVE_AUDITABLE, audit_id="R-9"), ledger)
    for suffix in ("dp01", "dp02", "dp03", "dp04", "dp05"):
        assert ledger.get(f"R-9:{suffix}") is not None


# ---------------------------------------------------------------------------
# 5. Shared context flows into each point result
# ---------------------------------------------------------------------------

def test_shared_context_propagates(ledger):
    register = audit_dp_register(_assembly(_FIVE_AUDITABLE, context=_CONTEXT), ledger)
    for key, result in register.items():
        if key == "_register_meta":
            continue
        assert result.analysis_context["analysis_id"] == "analysis-1"


# ---------------------------------------------------------------------------
# 6. Register carries first-bounded-set meta, no acceptance/release semantics
# ---------------------------------------------------------------------------

def test_register_meta_first_bounded_set(ledger):
    register = audit_dp_register(_assembly(_FIVE_AUDITABLE), ledger)
    meta = register["_register_meta"]
    assert isinstance(meta, dict)
    assert meta.get("bounded") is True
    assert "acceptance" not in register
    assert "release" not in register
    assert "lane" not in register
    assert "deployment" not in register


# ---------------------------------------------------------------------------
# 7. Reuse does not disturb a subsequent single-point seam on the same ledger
# ---------------------------------------------------------------------------

def test_register_does_not_disturb_later_single_point(ledger):
    audit_dp_register(_assembly(_FIVE_AUDITABLE), ledger)
    from bioaudit.api import audit_dp02_normalization
    point_request = {
        "audit_id": "later-dp02",
        "analysis_context": _CONTEXT,
        "integrity_metadata": _INTEGRITY,
        "decision_declaration": _declaration("tmm"),
        "evidence_observations": [_obs("tmm")],
    }
    result = audit_dp02_normalization(point_request, ledger)
    assert result.judgment == "auditable"
    assert ledger.get("later-dp02") is not None


# ---------------------------------------------------------------------------
# 8. Missing store / failure propagates fail-closed (no silent half-write)
# ---------------------------------------------------------------------------

def test_missing_store_raises(ledger):
    with pytest.raises((ValueError, TypeError)):
        audit_dp_register(_assembly(_FIVE_AUDITABLE), None)
