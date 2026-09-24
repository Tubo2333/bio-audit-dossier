"""DP-03 differential-analysis method decision adapter.

Uses the shared, scope-parameterized layer (dp_shared.PointBuilders +
audit_point) instead of a third mirror of the DP-01 builders. Only the
point-local pure audit lives here; orchestration, integrity handling,
attempt/re-audit and persistence come from the shared layer.
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

from bioaudit.dp03_models import DP03_SCOPE
from bioaudit.dp_shared import PointBuilders, audit_point

_BUILDERS = PointBuilders(
    scope=DP03_SCOPE,
    contribution_scope="method",
    method_target="method",
    limitation_detail="Method support is limited for the declared design and comparison",
    contribution_detail="DP-03 differential-analysis method contribution",
)


def _audit_dp03_method(request: Mapping[str, Any]) -> DP01FilteringResult:
    """Build one point-local DP-03 differential-analysis method result without
    persistence side effects."""
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
        return _BUILDERS.input_failure(context, evidence, "decision_declaration is malformed")
    missing = _missing_context(context) + [key for key in _REQUIRED_DECLARATION if key not in declaration]
    if missing:
        return _BUILDERS.fail_closed(context, evidence, "critical method context is missing: " + ", ".join(missing))
    declared_method = declaration["method"]

    # Competing method declarations: any declared evidence naming another method
    # preserves the conflict; no arbitrary winner is selected.
    declared_values = {item.value for item in evidence if item.source_type == "declared" and isinstance(item.value, str) and item.value}
    if len(declared_values | {declared_method}) > 1:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="conflicted",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            conflict_source="declared",
            diagnostic_explanation="competing differential-analysis method declarations; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing method declarations require scientific adjudication", "method", "review_requested"),
                _action("submit_correction", "select and justify a single method for the named design", "decision_declaration", "correction_submitted"),
            ),
        )

    # Competing method executions: multiple distinct confirmed observed values
    # (including the declared one among them) must not silently pick a winner.
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
            diagnostic_explanation="competing method executions observed; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing method executions require adjudication", "method", "review_requested"),
                _action("request_evidence", "resolve which observed methods are the executed pipeline", "evidence_observations", "evidence_requested"),
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
            diagnostic_explanation="declared and observed differential-analysis methods conflict",
            next_actions=(
                _action("request_evidence", "resolve declared/observed method conflict", "evidence_observations", "evidence_requested"),
                _action("request_review", "conflicting method observations require adjudication", "method", "review_requested"),
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
            diagnostic_explanation="method label alone is not evidence for the named design and comparison",
            next_actions=(
                _action("request_evidence", "support the method choice with point-local evidence", "evidence_observations", "evidence_requested"),
                _action("submit_correction", "provide a supported method or rationale", "decision_declaration", "correction_submitted"),
            ),
        )

    return _BUILDERS.result(
        analysis_context=context,
        judgment="auditable",
        declaration=FilteringDeclaration(**declaration),
        evidence=evidence,
        diagnostic_explanation="differential-analysis method is supportable for the declared design and comparison",
    )


def audit_dp03_method(request: Mapping[str, Any], store: Any) -> DP01FilteringResult:
    return audit_point(_audit_dp03_method, _BUILDERS, request, store, verbose_name="DP-03")


__all__ = ["audit_dp03_method"]