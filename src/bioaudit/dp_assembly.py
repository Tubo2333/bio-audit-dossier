"""Five-point register calling surface (MVP assembly, first product-facing step).

This module is the human-authorized assembly seam: one assembly request carries
shared analysis context + integrity metadata + five per-point decision
declarations/evidence; the register runs the five existing, already-authorized
point-local public seams (``audit_dp01_filtering`` … ``audit_dp05_threshold``)
over the single shared ledger and returns a plain per-scope register view that
preserves per-point meaning.

Boundary contract (authorization decisions 1–4):
- No new public container / semantics / lane / authority path: the register is a
  plain ``dict`` view over point-local ``DP01FilteringResult`` objects. It is NOT
  a second result-authority container and not an acceptance/release.
- No new ledger / schema: five points persist into the one shared
  ``DP01JSONLStore`` (append-only), each via its own point seam.
- Mixed states are preserved; no combined score and no arbitrary winner.
- The register carries a ``_register_meta`` note stating it is the *first bounded
  set*, not the complete product decision set. ``_register_meta`` is a
  view-layer note only and is NOT written to the ledger.

Non-atomicity: the register delegates persistence to each point seam in turn.
Shape validation is a whole-batch check that fails closed BEFORE any point is
audited (no partial write on malformed input), but a mid-loop seam exception
(non-shape, e.g. store failure) propagates and earlier points may already have
been persisted. Callers must not treat the register as an atomic transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bioaudit.dp01_adapter import audit_dp01_filtering
from bioaudit.dp02_adapter import audit_dp02_normalization
from bioaudit.dp03_adapter import audit_dp03_method
from bioaudit.dp04_adapter import audit_dp04_correction
from bioaudit.dp05_adapter import audit_dp05_threshold

# Single source of truth for the five authorized decision points: register key
# -> (public seam callable, audit_id suffix). Both the shape gate and the
# dispatch loop derive from this one map (no parallel hard-coded lists).
_POINT_SEAMS: dict[str, tuple[Any, str]] = {
    "filtering": (audit_dp01_filtering, "dp01"),
    "normalization": (audit_dp02_normalization, "dp02"),
    "differential_method": (audit_dp03_method, "dp03"),
    "multiple_testing_correction": (audit_dp04_correction, "dp04"),
    "significance_threshold": (audit_dp05_threshold, "dp05"),
}
_REGISTER_KEYS = frozenset(_POINT_SEAMS)


def audit_dp_register(assembly_request: Mapping[str, Any], store: Any) -> dict[str, Any]:
    """Run the five-point register over the shared ledger.

    Args:
        assembly_request: mapping with ``audit_id`` (str), ``analysis_context``
            (mapping), ``integrity_metadata`` (mapping), and ``decision_points``
            (mapping with exactly the five decision-point keys; each value is an
            additional per-point mapping carrying that point's
            ``decision_declaration`` + ``evidence_observations``).
        store: the shared append-only ledger (``DP01JSONLStore``).

    Returns:
        A plain register view (dict): ``{point_key: DP01FilteringResult, ...}``
        plus a ``_register_meta`` mapping. Fail-closed on malformed shape before
        any point is audited; point seam failures propagate. See module docstring
        for the non-atomicity note.

    Raises:
        TypeError: when ``assembly_request`` is not a mapping, when ``store`` is
            ``None``, or when a per-point value is not a mapping.
        ValueError: when ``audit_id``/``analysis_context``/``integrity_metadata``/
            ``decision_points`` are missing or malformed, or when
            ``decision_points`` does not contain exactly the five points.
    """
    if not isinstance(assembly_request, Mapping):
        raise TypeError("assembly request must be a mapping")

    audit_id = assembly_request.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("audit_id is required for the five-point register")

    if store is None:
        raise TypeError("a shared store is required for the five-point register")

    context = assembly_request.get("analysis_context")
    if not isinstance(context, Mapping):
        raise ValueError("analysis_context must be a mapping")

    integrity = assembly_request.get("integrity_metadata")
    if not isinstance(integrity, Mapping):
        raise ValueError("integrity_metadata must be a mapping")

    points = assembly_request.get("decision_points")
    if not isinstance(points, Mapping):
        raise ValueError("decision_points must be a mapping")
    if set(points) != _REGISTER_KEYS:
        missing = _REGISTER_KEYS - set(points)
        extra = set(points) - _REGISTER_KEYS
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unknown: " + ", ".join(sorted(extra)))
        raise ValueError("decision_points must contain exactly the five points (" + "; ".join(detail) + ")")

    register: dict[str, Any] = {}
    for key, (seam, suffix) in _POINT_SEAMS.items():
        point_value = points[key]
        if not isinstance(point_value, Mapping):
            raise TypeError(f"decision_points[{key}] must be a mapping")
        point_request = {
            "audit_id": f"{audit_id}:{suffix}",
            "analysis_context": context,
            "integrity_metadata": integrity,
            **point_value,
        }
        register[key] = seam(point_request, store)

    register["_register_meta"] = {
        "bounded": True,
        "first_bounded_set": True,
        "note": "First bounded set of five decision points, not the complete product decision set.",
        "points": 5,
        "no_combined_score": True,
        "view_only": True,
    }
    return register


__all__ = ["audit_dp_register"]
