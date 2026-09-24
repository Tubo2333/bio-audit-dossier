"""DP-04 multiple-testing correction decision adapter.

Reuses the shared scope-parameterized layer (`dp_shared.PointBuilders` +
`audit_point`) — no new builder copies. Only the point-local pure audit lives
here. Signature semantics (authorization decision 7): a missing correction
declaration fails closed (no assumed default); an explicit `none`/`no_correction`
goes through the same named-method path. F1 baseline preserved: competing
confirmed observed corrections are `conflicted` with no arbitrary winner.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bioaudit.dp01_adapter import (
    _REQUIRED_CONTEXT,
    _REQUIRED_DECLARATION,
    _action,
    _as_mapping,
    _evidence,
    _integrity_failure,
    _missing_context,
    _valid_declaration,
)
from bioaudit.dp01_models import DP01FilteringResult, FilteringDeclaration

from bioaudit.dp04_models import DP04_SCOPE
from bioaudit.dp_shared import PointBuilders, audit_point

_BUILDERS = PointBuilders(
    scope=DP04_SCOPE,
    contribution_scope="correction",
    method_target="correction",
    limitation_detail="Correction support is limited for the declared many-tested context",
    contribution_detail="DP-04 multiple-testing correction contribution",
)


def _audit_dp04_correction(request: Mapping[str, Any]) -> DP01FilteringResult:
    """Build one point-local DP-04 correction result without persistence side effects."""
    request = _as_mapping(request, "request")
    context_value = request.get("analysis_context", {})
    context = context_value if isinstance(context_value, Mapping) else {}
    try:
        evidence = _evidence(request)
    except (TypeError, ValueError):
        return _BUILDERS.input_failure(context, (), "evidence_observations is malformed")
    metadata, integrity_failures = _integrity_failure(request)
    if integrity_failures:
        return _BUILDERS.integrity_result(context, evidence, metadata, integrity_failures)
    try:
        declaration = _valid_declaration(request.get("decision_declaration"))
    except (TypeError, ValueError):
        # missing/malformed declaration -> fail closed; no default correction is assumed
        return _BUILDERS.input_failure(context, evidence, "correction declaration is missing or malformed")
    missing = _missing_context(context) + [key for key in _REQUIRED_DECLARATION if key not in declaration]
    if missing:
        return _BUILDERS.fail_closed(context, evidence, "critical correction context is missing: " + ", ".join(missing))
    declared_method = declaration["method"]

    # Competing correction declarations: any declared evidence naming another
    # correction preserves the conflict — no arbitrary winner. Shared with the
    # DP-03 guard so cross-point behavior stays uniform (DP-04 review fix).
    declared_values = {item.value for item in evidence if item.source_type == "declared" and isinstance(item.value, str) and item.value}
    if len(declared_values | {declared_method}) > 1:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="conflicted",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            conflict_source="declared",
            diagnostic_explanation="competing correction declarations; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing correction declarations require scientific adjudication", "correction", "review_requested"),
                _action("submit_correction", "select and justify a single correction for the declared context", "decision_declaration", "correction_submitted"),
            ),
        )

    # Competing correction executions: multiple distinct confirmed observed
    # values (including the declared one among them) must not pick a winner.
    confirmed_observed = {
        item.value for item in evidence
        if item.source_type == "observed" and item.verification == "confirmed"
    }
    if len(confirmed_observed) > 1:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="conflicted",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            conflict_source="observed",
            diagnostic_explanation="competing correction executions observed; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing correction executions require adjudication", "correction", "review_requested"),
                _action("request_evidence", "resolve which observed corrections are the executed pipeline", "evidence_observations", "evidence_requested"),
            ),
        )

    observed_values = {
        item.value for item in evidence
        if item.source_type == "observed" and item.verification in {"confirmed", "conflicted"}
    }
    if observed_values and declared_method not in observed_values:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="conflicted",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            conflict_source="observed",
            diagnostic_explanation="declared and observed correction choices conflict",
            next_actions=(
                _action("request_evidence", "resolve declared/observed correction conflict", "evidence_observations", "evidence_requested"),
                _action("request_review", "conflicting correction observations require adjudication", "correction", "review_requested"),
            ),
        )

    qualified = any(
        item.source_type in ("observed", "referenced")
        and item.verification == "confirmed"
        and item.value == declared_method
        for item in evidence
    )
    if not qualified:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="scientifically_limited",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            diagnostic_explanation="correction label alone is not evidence for the declared many-tested context",
            next_actions=(
                _action("request_evidence", "support the correction choice with point-local evidence", "evidence_observations", "evidence_requested"),
                _action("submit_correction", "provide a supported correction choice or rationale", "decision_declaration", "correction_submitted"),
            ),
        )

    return _BUILDERS.result(
        analysis_context=context,
        judgment="auditable",
        declaration=FilteringDeclaration(**declaration),
        evidence=evidence,
        diagnostic_explanation="multiple-testing correction choice is supportable for the declared context",
    )


def audit_dp04_correction(request: Mapping[str, Any], store: Any) -> DP01FilteringResult:
    return audit_point(_audit_dp04_correction, _BUILDERS, request, store, verbose_name="DP-04")


__all__ = ["audit_dp04_correction"]