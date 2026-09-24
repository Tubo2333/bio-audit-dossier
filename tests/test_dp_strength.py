"""DP-STRENGTH — four-axis structured result view tests (authorized seam).

Exercises ``bioaudit.api.derive_dp_strength``: a pure, view-only function that
derives the combined-account four-axis meaning (inference_type / explanation_depth
/ scope / validation) from the five-point register, with per-axis evaluative
dimensions per P-02 §4.7 / P-01 §5.1. It is NOT a persisted carrier / NOT a
second result-authority container; it introduces no new container/semantic on
DP01FilteringResult and contains no scalar / ranking / lane / confidence.

Runs via the project pytest `pythonpath=["src"]`.
"""

import pytest

from bioaudit.api import audit_dp_register, derive_dp_strength
from bioaudit.dp01_store import DP01JSONLStore

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


_FIVE_AUDITABLE = {
    "filtering": _point(_declaration("low_count_filter"), [_obs("low_count_filter")]),
    "normalization": _point(_declaration("tmm"), [_obs("tmm")]),
    "differential_method": _point(_declaration("deseq2"), [_obs("deseq2")]),
    "multiple_testing_correction": _point(_declaration("BH"), [_obs("BH")]),
    "significance_threshold": _point(_declaration("padj_0.05"), [_obs("padj_0.05")]),
}

_MIXED = {
    "filtering": _point(_declaration("low_count_filter"), [_obs("low_count_filter")]),
    "normalization": _point(_declaration("tmm"),
                            [_obs("tmm", verification="unverified")]),       # scientifically_limited
    "differential_method": _point(_declaration("deseq2"),
                                  [_obs("deseq2"), _obs("edgeR")]),          # conflicted
    "multiple_testing_correction": _point({"method": "BH", "threshold": 5,
                                           "unit": "gene", "source": "declared"},
                                          [_obs("BH")]),                     # integrity_failed
    "significance_threshold": _point(_declaration("padj_0.05"), [_obs("padj_0.05")]),
}


@pytest.fixture
def ledger(tmp_path):
    return DP01JSONLStore(tmp_path / "ledger.jsonl")


@pytest.fixture
def five_auditable_register(ledger):
    return audit_dp_register(
        {"audit_id": "s-1", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)


@pytest.fixture
def mixed_register(ledger):
    return audit_dp_register(
        {"audit_id": "s-m", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _MIXED},
        ledger)


def _no_scalar_keys(node):
    banned = {"total_score", "confidence", "ranking", "quality", "lane",
              "winner", "summary", "overall_score", "combined_judgment",
              "confidence_score"}
    if isinstance(node, dict):
        for k in node:
            assert k not in banned, k
            _no_scalar_keys(node[k])
    elif isinstance(node, list):
        for v in node:
            _no_scalar_keys(v)


# ---------------------------------------------------------------------------
# 1. Four axes present + _strength_meta
# ---------------------------------------------------------------------------

def test_four_axes_present(five_auditable_register):
    strength = derive_dp_strength(five_auditable_register)
    assert {"validation", "scope", "inference_type", "explanation_depth"} <= set(strength)
    assert strength["_strength_meta"]["view_only"] is True
    assert strength["_strength_meta"]["combined_account"] is True


# ---------------------------------------------------------------------------
# 2. All-auditable -> fully evaluable, complete, auditable
# ---------------------------------------------------------------------------

def test_all_auditable_axes(five_auditable_register):
    s = derive_dp_strength(five_auditable_register)
    assert s["validation"]["evaluable"] is True
    assert s["validation"]["auditability"] == "auditable"
    assert s["validation"]["completeness"] in ("complete", "partial")
    assert s["scope"]["auditability"] == "auditable"
    assert s["inference_type"]["completeness"] == "complete"
    assert s["explanation_depth"]["completeness"] in ("complete", "partial")
    for axis in ("validation", "scope", "inference_type", "explanation_depth"):
        assert s[axis]["evaluable"] is True, axis


# ---------------------------------------------------------------------------
# 3. Mixed with integrity_failed -> validation not auditable (P-02 §5.2)
# ---------------------------------------------------------------------------

def test_mixed_validation_integrity_precedence(mixed_register):
    s = derive_dp_strength(mixed_register)
    assert s["validation"]["auditability"] == "not_auditable"
    assert s["validation"]["evaluable"] is True


# ---------------------------------------------------------------------------
# 4. Mixed with conflicted -> residual/unordered, no arbitrary winner
# ---------------------------------------------------------------------------

def test_mixed_inference_residual_unordered(mixed_register):
    s = derive_dp_strength(mixed_register)
    assert s["inference_type"]["residual_unordered"] is True


# ---------------------------------------------------------------------------
# 5. No scalar / confidence / ranking / lane / winner anywhere
# ---------------------------------------------------------------------------

def test_no_scalar_anywhere(mixed_register):
    s = derive_dp_strength(mixed_register)
    _no_scalar_keys(s)


# ---------------------------------------------------------------------------
# 6. Missing context -> scope not auditable (fail-closed)
# ---------------------------------------------------------------------------

def test_scope_missing_context_not_auditable(ledger):
    register = audit_dp_register(
        {"audit_id": "s-x", "analysis_context": {},
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)
    s = derive_dp_strength(register)
    assert s["scope"]["auditability"] == "not_auditable"


# ---------------------------------------------------------------------------
# 7. _strength_meta not-authority / not-acceptance
# ---------------------------------------------------------------------------

def test_strength_meta_boundaries(five_auditable_register):
    meta = derive_dp_strength(five_auditable_register)["_strength_meta"]
    assert meta["not_authority"] is True
    assert meta["not_acceptance"] is True


# ---------------------------------------------------------------------------
# 8. Fail-closed on malformed input
# ---------------------------------------------------------------------------

def test_invalid_register_fail_closed(ledger):
    full = audit_dp_register(
        {"audit_id": "s-y", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)
    with pytest.raises((ValueError, TypeError)):
        derive_dp_strength("not-a-mapping")
    missing_meta = {k: v for k, v in full.items() if k != "_register_meta"}
    with pytest.raises((ValueError, TypeError)):
        derive_dp_strength(missing_meta)


# ---------------------------------------------------------------------------
# 9. Purity: no mutation of input register, no store
# ---------------------------------------------------------------------------

def test_purity_no_mutation(five_auditable_register):
    # The register holds immutable (frozen) result objects whose analysis_context
    # is a mappingproxy, which cannot be deep-copied — so verify purity by
    # checking the register's key set and object identities are unchanged and
    # the _register_meta mapping is not replaced.
    keys_before = list(five_auditable_register.keys())
    ids_before = {k: id(v) for k, v in five_auditable_register.items()}
    meta_before = dict(five_auditable_register["_register_meta"])

    derive_dp_strength(five_auditable_register)

    assert list(five_auditable_register.keys()) == keys_before
    assert {k: id(v) for k, v in five_auditable_register.items()} == ids_before
    assert dict(five_auditable_register["_register_meta"]) == meta_before


# ---------------------------------------------------------------------------
# 10. Review-fix #4: a non-integrity not-auditable point is NOT relabeled to an
#     auditable combined account on the validation axis
# ---------------------------------------------------------------------------

def test_validation_not_auditable_for_non_integrity_point(ledger):
    from types import SimpleNamespace

    def fake(judgment):
        return SimpleNamespace(
            judgment=judgment,
            scope="DP-01/filtering",
            diagnostic_explanation="x",
            limitations=(),
            evidence_gaps=(),
            analysis_context=_CONTEXT,
        )

    # filtering is not-auditable for a non-integrity reason; others auditable.
    register = {
        "filtering": fake("not_auditable"),
        "normalization": fake("auditable"),
        "differential_method": fake("auditable"),
        "multiple_testing_correction": fake("auditable"),
        "significance_threshold": fake("auditable"),
        "_register_meta": {"first_bounded_set": True, "note": "first bounded set"},
    }
    s = derive_dp_strength(register)
    assert s["validation"]["auditability"] == "not_auditable"
    assert "filtering" in s["validation"]["conditions"]


# ---------------------------------------------------------------------------
# DP-STRENGTH-2: axis conditions name the weak points; insufficiency is NOT
# relabeled as not-auditable (P-02 §5.2 distinction preserved)
# ---------------------------------------------------------------------------

_INSUFFICIENT = {
    "filtering": _point(_declaration("low_count_filter"), [_obs("low_count_filter")]),
    # label only -> scientifically_limited
    "normalization": _point(_declaration("tmm"), [_obs("tmm", verification="unverified")]),
    "differential_method": _point(_declaration("deseq2"), [_obs("deseq2")]),
    "multiple_testing_correction": _point(_declaration("BH"), [_obs("BH")]),
    "significance_threshold": _point(_declaration("padj_0.05"), [_obs("padj_0.05")]),
}


@pytest.fixture
def insufficient_register(ledger):
    return audit_dp_register(
        {"audit_id": "s-ins", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _INSUFFICIENT},
        ledger)


def test_inference_type_conditions_name_weak_point(insufficient_register):
    s = derive_dp_strength(insufficient_register)
    assert "normalization" in s["inference_type"]["conditions"]


def test_explanation_depth_conditions_name_point(insufficient_register):
    s = derive_dp_strength(insufficient_register)
    assert any("normalization" in c for c in s["explanation_depth"]["conditions"])


def test_insufficiency_not_relabeled_as_not_auditable(insufficient_register):
    s = derive_dp_strength(insufficient_register)
    # no integrity failure -> the account stays auditable (not relabeled)
    assert s["validation"]["auditability"] == "auditable"
    # ...but the insufficiency is unmistakable on the same axis
    assert s["validation"]["scientific_evidence"] == "insufficient"
    assert "normalization" in s["validation"]["conditions"]

