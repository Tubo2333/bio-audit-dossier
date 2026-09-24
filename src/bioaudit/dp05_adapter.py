"""DP-05 significance/effect-size threshold decision adapter.

Reuses the shared scope-parameterized layer (`dp_shared.PointBuilders` +
`audit_point`) — no new builder copies. Only the point-local pure audit lives
here. Signature semantics (P-01 §4.5 + authorization decision 7): paired
thresholds live inside the shared five-key declaration (zero new input
fields); a missing threshold declaration fails closed (no conventional
threshold is filled in); competing declarations/observations preserve conflict
with no arbitrary winner (uniform with DP-03/DP-04). The threshold field is
declared payload only — the adapter never reads it as logic input.
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

from bioaudit.dp05_models import DP05_SCOPE
from bioaudit.dp_shared import PointBuilders, audit_point

_BUILDERS = PointBuilders(
    scope=DP05_SCOPE,
    contribution_scope="threshold",
    method_target="threshold",
    limitation_detail="Threshold support is limited for the declared claim and intended use",
    contribution_detail="DP-05 significance/effect-size threshold contribution",
)


def _audit_dp05_threshold(request: Mapping[str, Any]) -> DP01FilteringResult:
    """Build one point-local DP-05 threshold result without persistence side effects."""
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
        # missing/malformed declaration -> fail closed; no conventional threshold is filled
        return _BUILDERS.input_failure(context, evidence, "threshold declaration is missing or malformed")
    missing = _missing_context(context) + [key for key in _REQUIRED_DECLARATION if key not in declaration]
    if missing:
        return _BUILDERS.fail_closed(context, evidence, "critical threshold context is missing: " + ", ".join(missing))
    declared_method = declaration["method"]

    # Competing threshold declarations: any declared evidence naming another
    # threshold family preserves the conflict — no arbitrary winner (uniform
    # with the DP-03/DP-04 guards).
    declared_values = {item.value for item in evidence if item.source_type == "declared" and isinstance(item.value, str) and item.value}
    if len(declared_values | {declared_method}) > 1:
        return _BUILDERS.result(
            analysis_context=context,
            judgment="conflicted",
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            conflict_source="declared",
            diagnostic_explanation="competing threshold declarations; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing threshold declarations require scientific adjudication", "threshold", "review_requested"),
                _action("submit_correction", "select and justify a single threshold family for the declared claim", "decision_declaration", "correction_submitted"),
            ),
        )

    # Competing threshold executions: multiple distinct confirmed observed
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
            diagnostic_explanation="competing threshold executions observed; no arbitrary winner is selected",
            next_actions=(
                _action("request_review", "competing threshold executions require adjudication", "threshold", "review_requested"),
                _action("request_evidence", "resolve which observed thresholds are the executed settings", "evidence_observations", "evidence_requested"),
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
            diagnostic_explanation="declared and observed threshold choices conflict",
            next_actions=(
                _action("request_evidence", "resolve declared/observed threshold conflict", "evidence_observations", "evidence_requested"),
                _action("request_review", "conflicting threshold observations require adjudication", "threshold", "review_requested"),
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
            diagnostic_explanation="threshold label alone is not evidence for the declared claim and intended use",
            next_actions=(
                _action("request_evidence", "support the threshold choice with point-local evidence", "evidence_observations", "evidence_requested"),
                _action("submit_correction", "provide a supported threshold choice or rationale", "decision_declaration", "correction_submitted"),
            ),
        )

    return _BUILDERS.result(
        analysis_context=context,
        judgment="auditable",
        declaration=FilteringDeclaration(**declaration),
        evidence=evidence,
        diagnostic_explanation="significance/effect-size threshold choice is supportable for the declared claim and intended use",
    )


def audit_dp05_threshold(request: Mapping[str, Any], store: Any) -> DP01FilteringResult:
    return audit_point(_audit_dp05_threshold, _BUILDERS, request, store, verbose_name="DP-05")


__all__ = ["audit_dp05_threshold"]