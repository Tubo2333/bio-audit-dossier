"""DP-02 normalization decision adapter over the shared DP-01 audit ledger.

Mirrors the DP-01 public orchestration (integrity check -> attempt/re-audit ->
pure audit -> persist) with a normalization-flavored pure audit and the shared
`DP01JSONLStore` / `DP01AuditRecord` / five-field version snapshot. The DP-01
helpers that are scope-agnostic (evidence parsing, integrity classification,
declaration shape, merge, identity) are imported; the four result builders that
hard-code scope="DP-01/filtering" are mirrored here with DP02_SCOPE — the
deep-copy and scope-parameterization refactor is a follow-up for a separately
authorized window (recorded in DP02-NORMALIZATION-FEEDBACK.md).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from itertools import zip_longest
from typing import Any

from bioaudit.dp01_adapter import (
    _ATTEMPT_KEYS,
    _INTEGRITY_ERROR_DOMAIN,
    _INTEGRITY_ERROR_STATE,
    _INTEGRITY_STATES,
    _REAUDIT_CHANGE_KIND,
    _REAUDIT_CHANGE_REASON,
    _REAUDIT_KEYS,
    _REQUIRED_CONTEXT,
    _REQUIRED_DECLARATION,
    _action,
    _as_mapping,
    _child_attempt,
    _evidence,
    _integrity_failure,
    _merge_snapshot,
    _missing_context,
    _reaudit_shape,
    _thaw,
    _try_evidence,
    _valid_declaration,
    _with_identity,
)
from bioaudit.dp01_models import (
    _parse_affected_evidence,
    DP01AttemptChange,
    DP01Change,
    DP01ChangeSummary,
    DP01EvidenceGap,
    DP01FilteringResult,
    DP01Finding,
    DP01Limitation,
    DP01LocalContribution,
    FilteringDeclaration,
)
from bioaudit.dp01_store import DP01AuditRecord

from bioaudit.dp02_models import DP02_SCOPE


def _norm_integrity_result(context, evidence, metadata, failures):
    """DP-02-scoped mirror of dp01 `_integrity_result` (that builder hard-codes DP-01 scope)."""
    if not metadata:
        material_ids, pending_targets = (), ()
    else:
        material_value = metadata.get("material_ids", ())
        pending_value = metadata.get("pending_targets", ())
        material_ids = tuple(material_value) if isinstance(material_value, list) and all(isinstance(item, str) and item for item in material_value) else ()
        pending_targets = tuple(pending_value) if isinstance(pending_value, list) and all(isinstance(item, str) and item for item in pending_value) else ()
    targets = material_ids + pending_targets
    invalid_targets = tuple(metadata.get("_invalid_target_keys", ())) if isinstance(metadata, Mapping) else ()
    if invalid_targets:
        targets += ("integrity_metadata",) * len(invalid_targets)
    elif not targets:
        targets = ("integrity_metadata",)
    def state_for(domain):
        entry = metadata.get(domain) if isinstance(metadata, Mapping) else None
        state = entry.get("state") if isinstance(entry, Mapping) else None
        return state if isinstance(state, str) and state in _INTEGRITY_STATES else _INTEGRITY_ERROR_STATE

    findings = tuple(
        DP01Finding("integrity_failure", domain, state_for(domain), {"domain": domain, "material_ids": list(material_ids), "pending_targets": list(pending_targets)})
        for domain in failures
    )
    gaps = tuple(DP01EvidenceGap("integrity_failure", domain, state_for(domain), target) for domain in failures for target in targets)
    actions = tuple(
        _action("request_evidence", f"integrity metadata requires resolution: {domain}", target, "evidence_requested")
        for domain in failures for target in targets
    )
    return DP01FilteringResult(
        DP02_SCOPE, context, None, evidence,
        "normalization input binding failure isolated affected material", (),
        "integrity_failed", actions, findings, (), gaps,
        DP01LocalContribution("unresolved", "normalization", "Affected normalization material is isolated pending integrity resolution"),
    )


def _norm_structured(judgment, declaration, evidence, reason=""):
    """DP-02 mirror of dp01 `_structured`: normalization wording everywhere.

    Vocabulary constraint: limitation.kind must stay within the frozen shared
    set `DP01_LIMITATION_KINDS = {"filtering_support"}` (no new public
    vocabulary allowed), so only the detail/scope/target wording differs.
    """
    findings = tuple(DP01Finding("method", "declared", "confirmed", declaration.method) for _ in [0]) if declaration and judgment == "auditable" else ()
    limitations = () if judgment == "auditable" else (DP01Limitation("filtering_support", "unverified", reason or "Normalization support is limited"),)
    gaps = []
    if judgment == "not_auditable": gaps.append(DP01EvidenceGap("missing_critical_context", "context", "unverified", "analysis_context"))
    if judgment == "scientifically_limited" and not any(x.source_type == "observed" for x in evidence): gaps.append(DP01EvidenceGap("absent_execution_observation", "observed", "unverified", "evidence_observations"))
    if judgment == "conflicted": gaps.append(DP01EvidenceGap("declared_observed_conflict", "declared_observed", "conflicted", "normalization_method"))
    status = "established" if judgment == "auditable" else ("pending" if judgment == "pending_attempt" else "unresolved")
    return findings, limitations, tuple(gaps), DP01LocalContribution(status, "normalization", reason or "DP-02 normalization contribution")


def _norm_result(**kwargs):
    f, l, g, c = _norm_structured(kwargs["judgment"], kwargs.get("declaration"), kwargs.get("evidence", ()), kwargs.get("diagnostic_explanation", ""))
    return DP01FilteringResult(**kwargs, findings=f, limitations=l, evidence_gaps=g, local_contribution=c)


def _norm_fail_closed(context, evidence, reason):
    """DP-02-scoped mirror of dp01 `_fail_closed` (that builder hard-codes DP-01 scope)."""
    return _norm_result(
        scope=DP02_SCOPE, analysis_context=context, declaration=None, evidence=evidence,
        diagnostic_explanation=reason, matched_rule_ids=(), judgment="not_auditable",
        next_actions=(_action("request_evidence", reason, "analysis_context", "evidence_requested"), _action("stop", reason, "current_attempt", "stopped")),
    )


def _norm_input_failure(context, evidence, reason):
    return _norm_integrity_result(context, evidence, {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))


def _norm_pending_result(request):
    """DP-02-scoped mirror of dp01 `_pending_child_result` (that builder hard-codes DP-01 scope)."""
    context_value = request.get("analysis_context", {})
    context = context_value if isinstance(context_value, Mapping) else {}
    try:
        evidence = _evidence(request)
    except (TypeError, ValueError):
        return _norm_integrity_result(context, (), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))
    return _norm_result(
        scope=DP02_SCOPE,
        analysis_context=context,
        declaration=None,
        evidence=evidence,
        diagnostic_explanation="normalization child submission recorded; validation and effectiveness are pending",
        matched_rule_ids=(),
        judgment="pending_attempt",
        next_actions=(),
    )


def _audit_dp02_normalization(request: Mapping[str, Any]) -> DP01FilteringResult:
    """Build one point-local DP-02 normalization result without persistence side effects."""
    request = _as_mapping(request, "request")
    context_value = request.get("analysis_context", {})
    context = context_value if isinstance(context_value, Mapping) else {}
    try:
        evidence = _evidence(request)
    except (TypeError, ValueError):
        return _norm_input_failure(context, (), "evidence_observations is malformed")
    metadata, integrity_failures = _integrity_failure(request)
    if integrity_failures:
        return _norm_integrity_result(context, evidence, metadata, integrity_failures)
    try:
        declaration_value = _valid_declaration(request.get("decision_declaration"))
    except (TypeError, ValueError):
        return _norm_input_failure(context, evidence, "decision_declaration is malformed")
    declaration = declaration_value
    missing_context = _missing_context(context)
    missing_declaration = [key for key in _REQUIRED_DECLARATION if key not in declaration]
    if missing_context or missing_declaration:
        missing = ", ".join(missing_context + missing_declaration)
        return _norm_fail_closed(context, evidence, f"critical normalization context is missing: {missing}")

    declared_method = declaration["method"]
    observed_values = {
        item.value for item in evidence
        if item.source_type == "observed" and item.verification in {"confirmed", "conflicted"}
    }
    conflict = bool(observed_values and declared_method not in observed_values)
    if conflict:
        return _norm_result(
            scope=DP02_SCOPE,
            analysis_context=context,
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            diagnostic_explanation="declared and observed normalization methods conflict",
            matched_rule_ids=(),
            judgment="conflicted",
            next_actions=(
                _action("request_evidence", "resolve declared/observed conflict", "evidence_observations", "evidence_requested"),
                _action("request_review", "conflicting normalization observations require adjudication", "normalization_method", "review_requested"),
            ),
        )

    # Evidence must be connected to this analysis (P-01 §4.2): the qualifying
    # observed/referenced confirmed item must name the declared method, so
    # unrelated or cross-point evidence cannot fill the DP-02 gap. A method
    # label alone is never evidence; self-declared execution is trusted at the
    # same ledger-model boundary as DP-01.
    qualified = any(
        item.source_type in ("observed", "referenced")
        and item.verification == "confirmed"
        and item.value == declared_method
        for item in evidence
    )
    if not qualified:
        return _norm_result(
            scope=DP02_SCOPE,
            analysis_context=context,
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            diagnostic_explanation="normalization method label alone is not evidence for this analysis context",
            matched_rule_ids=(),
            judgment="scientifically_limited",
            next_actions=(
                _action("request_evidence", "support the normalization choice with point-local evidence", "evidence_observations", "evidence_requested"),
                _action("submit_correction", "provide rationale or a supported normalization method", "decision_declaration", "correction_submitted"),
            ),
        )

    return _norm_result(
        scope=DP02_SCOPE,
        analysis_context=context,
        declaration=FilteringDeclaration(**declaration),
        evidence=evidence,
        diagnostic_explanation="normalization decision is supportable for the declared context",
        matched_rule_ids=(),
        judgment="auditable",
        next_actions=(),
    )


def _reaudit_norm(request: Mapping[str, Any], store: Any):
    """DP-02 mirror of dp01 `_reaudit` (single-pass integrity inheritance, one target get)."""
    op = request.get("re_audit")
    if not isinstance(op, Mapping) or not _REAUDIT_KEYS >= set(op) or not {"operation", "target_audit_id"} <= set(op) or op.get("operation") != "re_audit":
        raise ValueError("invalid re-audit operation")
    target_id = op.get("target_audit_id")
    if not isinstance(target_id, str) or not target_id:
        raise ValueError("re-audit target is required")
    try:
        target = store.get(target_id)
    except KeyError as exc:
        raise KeyError(f"missing re-audit target: {target_id}") from exc
    if target.parent_audit_id is None:
        raise ValueError("re-audit target must be a child attempt")
    if not isinstance(request.get("integrity_metadata"), Mapping):
        effective = _merge_snapshot(target.input_snapshot, request)
        if "integrity_metadata" not in effective:
            effective["integrity_metadata"] = copy.deepcopy(target.input_snapshot.get("integrity_metadata"))
        request = {**request, "integrity_metadata": effective["integrity_metadata"]}
    effective = _merge_snapshot(target.input_snapshot, request)
    result = _audit_dp02_normalization(effective)
    baseline = _audit_dp02_normalization(_thaw(target.input_snapshot))
    allowed = []
    before = _thaw(target.input_snapshot)
    for field in ("method", "threshold", "sample_rule", "unit", "source"):
        a = before.get("decision_declaration", {}).get(field); b = effective.get("decision_declaration", {}).get(field)
        if a != b: allowed.append(DP01Change(f"decision_declaration.{field}", a, b))

    def evidence(v):
        return [(x.get("source_type"), x.get("verification"), x.get("value")) for x in v.get("evidence_observations", [])]

    if evidence(before) != evidence(effective): allowed.append(DP01Change("evidence_observations", evidence(before), evidence(effective)))
    if before.get("integrity_metadata") != effective.get("integrity_metadata"):
        allowed.append(DP01Change("integrity_metadata", before.get("integrity_metadata"), effective.get("integrity_metadata")))
    old = baseline

    def structured(value):
        return [item.to_dict() if hasattr(item, "to_dict") else item.__dict__ if hasattr(item, "__dict__") else item for item in value] if isinstance(value, (list, tuple)) else value.to_dict() if hasattr(value, "to_dict") else value.__dict__ if hasattr(value, "__dict__") else value

    for path, a, b in (
        ("result.judgment", old.judgment, result.judgment),
        ("result.matched_rule_ids", old.matched_rule_ids, result.matched_rule_ids),
        ("result.diagnostic_explanation", old.diagnostic_explanation, result.diagnostic_explanation),
        ("result.findings", structured(old.findings), structured(result.findings)),
        ("result.limitations", structured(old.limitations), structured(result.limitations)),
        ("result.evidence_gaps", structured(old.evidence_gaps), structured(result.evidence_gaps)),
        ("result.local_contribution", structured(old.local_contribution), structured(result.local_contribution)),
    ):
        if a != b: allowed.append(DP01Change(path, a, b))
    for i, (before_action, after_action) in enumerate(zip_longest(old.next_actions, result.next_actions, fillvalue=None)):
        before_value = before_action.__dict__ if before_action is not None else None
        after_value = after_action.__dict__ if after_action is not None else None
        if before_value != after_value:
            allowed.append(DP01Change(f"result.next_actions[{i}]", before_value, after_value))
    submitter = request.get("re_audit", {}).get("submitter") if isinstance(request.get("re_audit", {}), Mapping) else None
    affected = _parse_affected_evidence(request.get("re_audit", {}).get("affected_evidence") if isinstance(request.get("re_audit", {}), Mapping) else None)
    return effective, result, DP01AttemptChange(_REAUDIT_CHANGE_KIND, _REAUDIT_CHANGE_REASON, submitter, affected), DP01ChangeSummary(tuple(sorted(allowed, key=lambda x: x.path))), target_id, target


def audit_dp02_normalization(request: Mapping[str, Any], store: Any) -> DP01FilteringResult:
    if not isinstance(request, Mapping):
        return _norm_integrity_result({}, (), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))
    request_snapshot = copy.deepcopy(dict(request))
    audit_id = request_snapshot.get("audit_id")
    if "target_audit_id" in request_snapshot and "re_audit" not in request_snapshot:
        raise ValueError("re-audit operation must be explicit")
    if "re_audit" in request_snapshot and request_snapshot.get("attempt") is not None:
        raise ValueError("DP-02 request cannot combine re_audit and attempt")
    if "re_audit" in request_snapshot:
        if store is None or not hasattr(store, "get"): raise ValueError("re-audit requires a store")
        if not isinstance(audit_id, str) or not audit_id: raise ValueError("audit_id is required for explicit DP-02 persistence")
        target_id = _reaudit_shape(request_snapshot)
        if target_id is None:
            return _with_identity(_norm_integrity_result(
                request_snapshot.get("analysis_context", {}) if isinstance(request_snapshot.get("analysis_context", {}), Mapping) else {},
                _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        effective, result, change, summary, parent, target = _reaudit_norm(request_snapshot, store)
        source_version = target.version_snapshot
        record = DP01AuditRecord(audit_id, effective, _with_identity(result, audit_id), parent, change, "re_audit", summary, source_version_snapshot=dict(source_version))
        store.append(record)
        return _with_identity(result, audit_id)
    if not isinstance(request_snapshot.get("integrity_metadata"), Mapping):
        context_value = request_snapshot.get("analysis_context", {})
        context = context_value if isinstance(context_value, Mapping) else {}
        return _with_identity(_norm_integrity_result(context, _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
    if request_snapshot.get("attempt") is not None:
        attempt_value = request_snapshot["attempt"]

        def invalid_attempt():
            return not isinstance(attempt_value, Mapping) or not _ATTEMPT_KEYS >= set(attempt_value) or not {"parent_audit_id", "change_kind", "change_reason"} <= set(attempt_value) or not isinstance(attempt_value.get("parent_audit_id"), str) or not attempt_value.get("parent_audit_id") or (attempt_value.get("affected_evidence") is not None and not isinstance(attempt_value.get("affected_evidence"), (list, tuple)))

        if invalid_attempt():
            return _with_identity(_norm_integrity_result(
                request_snapshot.get("analysis_context", {}) if isinstance(request_snapshot.get("analysis_context", {}), Mapping) else {},
                _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        relation = _child_attempt(request_snapshot)
    else:
        relation = None
    if relation is not None:
        _, integrity_failures = _integrity_failure(request_snapshot)
        if integrity_failures:
            result = _norm_integrity_result(request_snapshot.get("analysis_context", {}), _try_evidence(request_snapshot), request_snapshot.get("integrity_metadata", {}), integrity_failures)
        else:
            result = _norm_pending_result(request_snapshot)
    else:
        result = _audit_dp02_normalization(request_snapshot)
    if result.judgment == "integrity_failed" and any(action.target == "integrity_metadata" for action in result.next_actions):
        return _with_identity(result, audit_id)
    if store is not None and not (isinstance(store, Mapping) and not store):
        if not isinstance(audit_id, str) or not audit_id: raise ValueError("audit_id is required for explicit DP-02 persistence")
        if not hasattr(store, "append"): raise TypeError("invalid DP-02 store")
        parent_audit_id = change = None
        if relation is not None:
            parent_audit_id, change = relation
            try:
                store.get(parent_audit_id)
            except KeyError as exc:
                raise KeyError(f"missing parent_audit_id: {parent_audit_id}") from exc
        store.append(DP01AuditRecord(audit_id, request_snapshot, _with_identity(result, audit_id), parent_audit_id, change))
    return _with_identity(result, audit_id)


__all__ = ["audit_dp02_normalization"]