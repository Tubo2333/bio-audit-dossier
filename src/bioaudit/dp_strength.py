"""Four-axis structured result VIEW (MVP assembly — combined-account meaning).

This module is the human-authorized four-axis view: a PURE, view-only function
that takes the five-point register (from ``audit_dp_register``) and derives the
combined-account four-axis structured meaning — ``inference_type`` /
``explanation_depth`` / ``scope`` / ``validation`` (P-01 §5.1, P-02 §4.7) — with
each axis carrying the per-axis evaluative dimensions.

Boundary contract (authorization decisions 1–4):
- COMBINED-ACCOUNT placement: the four axes describe the single multi-point
  account, not each point. ``DP01FilteringResult`` stays point-local and is NOT
  modified.
- VIEW-ONLY: derived mechanically from the register's existing per-point
  judgment/evidence/integrity data at render time. Nothing is persisted, no new
  carrier/schema, no store. It is NOT a second result-authority container.
- NO SCALAR: no total score / confidence / ranking / quality / lane / winner /
  summary anywhere.
- Integrity precedence (P-02 §5.2): an integrity/authority failure is surfaced
  as ``not_auditable`` on the validation axis and NEVER relabeled as mere
  scientific insufficiency.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bioaudit.dp_assembly import _POINT_SEAMS

_POINT_ORDER: tuple[str, ...] = tuple(_POINT_SEAMS)
_POINT_SET = frozenset(_POINT_SEAMS)

_PROHIBITED = (
    "No total scientific score.",
    "No confidence score standing in for the result vector.",
    "No universal ranking.",
    "No single quality number.",
    "No lane-as-science interpretation.",
    "No local summary that substitutes for the embedded result authority.",
)

_REQUIRED_RESULT_FIELDS = ("judgment", "scope", "diagnostic_explanation",
                           "limitations", "evidence_gaps", "analysis_context")

_INSUFFICIENT_JUDGMENTS = {"scientifically_limited", "not_auditable"}
_CONFLICT_JUDGMENTS = {"conflicted"}
_INTEGRITY_JUDGMENTS = {"integrity_failed"}

_REQUIRED_SCOPE_KEYS = ("project_id", "analysis_id", "comparison", "audit_scope")


def _strength_meta() -> dict[str, Any]:
    return {"view_only": True, "not_authority": True, "not_acceptance": True,
            "combined_account": True}


def _axis(*, evaluable: bool, completeness: str, scientific_evidence: str,
          auditability: str, conditions, residual_unordered: bool,
          detail: str) -> dict[str, Any]:
    return {
        "evaluable": evaluable,
        "completeness": completeness,
        "scientific_evidence": scientific_evidence,
        "auditability": auditability,
        "conditions": list(conditions),
        "prohibited_interpretations": list(_PROHIBITED),
        "residual_unordered": residual_unordered,
        "detail": detail,
    }


def _derive_validation(records: list[dict[str, Any]]) -> dict[str, Any]:
    integrity = [r["name"] for r in records if r["judgment"] in _INTEGRITY_JUDGMENTS]
    insufficient = [r["name"] for r in records if r["judgment"] in _INSUFFICIENT_JUDGMENTS]
    not_aud = [r["name"] for r in records if r["judgment"] == "not_auditable"]
    conflict = [r["name"] for r in records if r["judgment"] in _CONFLICT_JUDGMENTS]
    pending = [r["name"] for r in records if r["judgment"] == "pending_attempt"]
    all_auditable = all(r["judgment"] == "auditable" for r in records)

    if integrity:
        return _axis(
            evaluable=True,
            completeness="partial",
            scientific_evidence="insufficient",
            auditability="not_auditable",
            conditions=integrity,
            residual_unordered=bool(conflict),
            detail=(
                "A required identity, source, version, binding, snapshot, "
                "permission, or authority fact could not be verified; affected "
                "material is isolated. Integrity failure is not relabeled as "
                "mere scientific insufficiency (P-02 §5.2 precedence)."
            ),
        )

    # A point that is itself not-auditable for a non-integrity reason (or a
    # pending attempt) must not be silently relabeled into a fully auditable
    # combined account; surface all non-auditable / pending points so the
    # validity of the account is not overstated.
    non_auditable = not_aud
    if non_auditable:
        return _axis(
            evaluable=True,
            completeness="partial",
            scientific_evidence="insufficient",
            auditability="not_auditable",
            conditions=non_auditable + insufficient + conflict,
            residual_unordered=bool(conflict),
            detail=(
                "At least one decision point is not-auditable on a non-integrity "
                "basis; the combined account is therefore not fully auditable and "
                "is not relabeled as acceptable."
            ),
        )
    return _axis(
        evaluable=True,
        completeness="complete" if all_auditable else "partial",
        scientific_evidence="sufficient" if (all_auditable and not conflict) else "insufficient",
        auditability="auditable",
        conditions=insufficient + conflict + pending,
        residual_unordered=bool(conflict),
        detail=(
            "Auditability here means the account can be evaluated; it does NOT "
            "mean every decision point is fully supported. Points with "
            "insufficient evidence or unresolved conflict are listed in "
            "conditions and flagged by scientific_evidence "
            "(insufficiency is never relabeled as not-auditable, P-02 §5.2)."
        ),
    )


def _derive_scope(analysis_context: Mapping[str, Any]) -> dict[str, Any]:
    present = {k: analysis_context.get(k) for k in _REQUIRED_SCOPE_KEYS}
    missing = [k for k, v in present.items() if not isinstance(v, str) or not v]
    if missing:
        return _axis(
            evaluable=True,
            completeness="partial",
            scientific_evidence="insufficient",
            auditability="not_auditable",
            conditions=[f"missing scope key: {k}" for k in missing],
            residual_unordered=False,
            detail="The named scope could not be established because required context was not present.",
        )
    return _axis(
        evaluable=True,
        completeness="complete",
        scientific_evidence="sufficient",
        auditability="auditable",
        conditions=[f"{k}={v}" for k, v in present.items()],
        residual_unordered=False,
        detail="The scope names the project, analysis, comparison, and audit scope for this combined account.",
    )


def _derive_inference_type(records: list[dict[str, Any]]) -> dict[str, Any]:
    auditable = [r["name"] for r in records if r["judgment"] == "auditable"]
    conflict = [r["name"] for r in records if r["judgment"] in _CONFLICT_JUDGMENTS]
    # Name every point that does not fully support an inference (DP-STRENGTH-2):
    # insufficient evidence and not-auditable points must be visible here, not
    # only aggregated into `completeness`.
    weak = [r["name"] for r in records if r["judgment"] in _INSUFFICIENT_JUDGMENTS]
    weak += [r["name"] for r in records if r["judgment"] in _INTEGRITY_JUDGMENTS]
    all_auditable = all(r["judgment"] == "auditable" for r in records)
    return _axis(
        evaluable=bool(auditable),
        completeness="complete" if all_auditable else "partial",
        scientific_evidence="sufficient" if all_auditable else "insufficient",
        auditability="auditable" if auditable else "not_auditable",
        conditions=list(dict.fromkeys(weak + conflict)),
        residual_unordered=bool(conflict),
        detail=(
            "Inference is supported where a point is auditable; conflicting "
            "points preserve residual/unordered meaning with no arbitrary winner; "
            "points listed in conditions either lack sufficient evidence or "
            "support no inference."
        ),
    )


def _derive_explanation_depth(records: list[dict[str, Any]]) -> dict[str, Any]:
    gaps = sum(len(r["evidence_gaps"]) for r in records)
    limitations = sum(len(r["limitations"]) for r in records)
    missing_explanation = [r["name"] for r in records
                           if not isinstance(r["diagnostic_explanation"], str)
                           or not r["diagnostic_explanation"]]
    complete = gaps == 0 and limitations == 0 and not missing_explanation
    # Name the points, not generic strings (DP-STRENGTH-2) so a reader can tell
    # exactly which decision point is shallow and why.
    conditions = [f"{n}: missing diagnostic explanation" for n in missing_explanation]
    conditions += [f"{r['name']}: evidence gaps" for r in records if r["evidence_gaps"]]
    conditions += [f"{r['name']}: limitations" for r in records if r["limitations"]]
    return _axis(
        evaluable=True,
        completeness="complete" if complete else "partial",
        scientific_evidence="sufficient" if complete else "insufficient",
        auditability="auditable" if not missing_explanation else "not_auditable",
        conditions=conditions,
        residual_unordered=False,
        detail="Explanation depth reflects per-point diagnostic explanations, evidence gaps, and limitations.",
    )


def derive_dp_strength(register: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the combined-account four-axis structured result VIEW (pure).

    Args:
        register: the view returned by ``audit_dp_register`` — five point-name
            keys mapping to ``DP01FilteringResult`` objects, plus
            ``_register_meta``.

    Returns:
        An ordered ``dict`` with the four axes (``validation``, ``scope``,
        ``inference_type``, ``explanation_depth``), each carrying the §4.7
        evaluative dimensions, plus ``_strength_meta``. View-only: nothing is
        persisted, no store, no new carrier. No scalar/ranking/lane/winner.

    Raises:
        TypeError: if ``register`` is not a mapping, or a stored point value is
            missing required result fields.
        ValueError: if ``_register_meta`` is missing or the register does not
            contain exactly the five decision points.
    """
    if not isinstance(register, Mapping):
        raise TypeError("register must be a mapping")

    register_meta = register.get("_register_meta")
    if not isinstance(register_meta, Mapping):
        raise ValueError("register must include a _register_meta mapping")

    points_keys = set(register)
    points_keys.discard("_register_meta")
    if points_keys != _POINT_SET:
        missing = _POINT_SET - points_keys
        extra = points_keys - _POINT_SET
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unknown: " + ", ".join(sorted(extra)))
        raise ValueError("register must contain exactly the five points (" + "; ".join(detail) + ")")

    first_result = register[_POINT_ORDER[0]]
    analysis_context = getattr(first_result, "analysis_context", None)
    if not isinstance(analysis_context, Mapping):
        raise TypeError("register point results must expose analysis_context as a mapping")

    records = []
    for name in _POINT_ORDER:
        result = register[name]
        missing_fields = [f for f in _REQUIRED_RESULT_FIELDS if not hasattr(result, f)]
        if missing_fields:
            raise TypeError(f"register point '{name}' is missing result fields: {', '.join(missing_fields)}")
        records.append({
            "name": name,
            "judgment": result.judgment,
            "scope": result.scope,
            "diagnostic_explanation": result.diagnostic_explanation,
            "limitations": tuple(result.limitations or ()),
            "evidence_gaps": tuple(result.evidence_gaps or ()),
        })

    strength: dict[str, Any] = {
        "_strength_meta": _strength_meta(),
        "validation": _derive_validation(records),
        "scope": _derive_scope(analysis_context),
        "inference_type": _derive_inference_type(records),
        "explanation_depth": _derive_explanation_depth(records),
    }
    return strength


__all__ = ["derive_dp_strength"]
