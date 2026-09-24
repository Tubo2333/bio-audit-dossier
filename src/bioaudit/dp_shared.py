"""Shared, scope-parameterized DP-* result builders and public orchestration.

DP-01 hard-codes scope="DP-01/filtering" in its builders, and dp02_adapter
mirrored them for DP-02. This module is the de-duplication home for DP-03 and
later decision points: one set of parameterized builders (`PointBuilders`), a
generic re-audit (`reaudit`), and a generic public audit orchestration
(`audit_point`). DP-01/DP-02 keep their current implementations; retro-fitting
them onto this layer is a separately authorized follow-up window.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from itertools import zip_longest
from types import MappingProxyType
from typing import Any

from bioaudit.dp01_adapter import (
    _ATTEMPT_KEYS,
    _INTEGRITY_ERROR_DOMAIN,
    _INTEGRITY_ERROR_STATE,
    _INTEGRITY_STATES,
    _REAUDIT_CHANGE_KIND,
    _REAUDIT_CHANGE_REASON,
    _REAUDIT_KEYS,
    _action,
    _child_attempt,
    _evidence,
    _integrity_failure,
    _merge_snapshot,
    _reaudit_shape,
    _thaw,
    _try_evidence,
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
)
from bioaudit.dp01_store import DP01AuditRecord

# The frozen shared vocabulary has exactly one limitation kind; new kinds would
# be new public semantics (forbidden). Only wording differs per point.
_LIMITATION_KIND = "filtering_support"
_INVALID_TARGETS = MappingProxyType({"_invalid_target_keys": ("material_ids", "pending_targets")})


def _context_of(request: Mapping[str, Any]) -> Mapping[str, Any]:
    value = request.get("analysis_context", {})
    return value if isinstance(value, Mapping) else {}


class PointBuilders:
    """Scope-parameterized structured-result builders for one decision point."""

    def __init__(self, *, scope: str, contribution_scope: str, method_target: str,
                 limitation_detail: str, contribution_detail: str):
        self._scope = scope
        self._contribution_scope = contribution_scope
        self._method_target = method_target
        self._limitation_detail = limitation_detail
        self._contribution_detail = contribution_detail

    def integrity_result(self, context, evidence, metadata, failures):
        if not metadata:
            material_ids, pending_targets = (), ()
        else:
            material_value = metadata.get("material_ids", ())
            pending_value = metadata.get("pending_targets", ())
            material_ids = tuple(material_value) if isinstance(material_value, list) and all(isinstance(item, str) and item for item in material_value) else ()
            pending_targets = tuple(pending_value) if isinstance(pending_value, list) and all(isinstance(item, str) and item for item in pending_value) else ()
        targets = material_ids + pending_targets
        invalid = tuple(metadata.get("_invalid_target_keys", ())) if isinstance(metadata, Mapping) else ()
        if invalid:
            targets += ("integrity_metadata",) * len(invalid)
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
            self._scope, context, None, evidence,
            "input binding/integrity failure isolated affected material", (),
            "integrity_failed", actions, findings, (), gaps,
            DP01LocalContribution("unresolved", self._contribution_scope, "Affected material is isolated pending integrity resolution"),
        )

    def input_failure(self, context, evidence, reason):
        return self.integrity_result(context, evidence, _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,))

    def fail_closed(self, context, evidence, reason):
        return self.result(
            analysis_context=context, judgment="not_auditable", evidence=evidence, diagnostic_explanation=reason,
            next_actions=(
                _action("request_evidence", reason, "analysis_context", "evidence_requested"),
                _action("stop", reason, "current_attempt", "stopped"),
            ),
        )

    def pending(self, request):
        context = _context_of(request)
        try:
            evidence = _evidence(request)
        except (TypeError, ValueError):
            return self.integrity_result(context, (), _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,))
        return self.result(
            analysis_context=context, judgment="pending_attempt", evidence=evidence,
            diagnostic_explanation="child submission recorded; validation and effectiveness are pending",
        )

    def result(self, *, analysis_context, judgment, declaration=None, evidence=(), diagnostic_explanation="", next_actions=(), conflict_source="declared_observed"):
        findings = (DP01Finding("method", "declared", "confirmed", declaration.method),) if declaration and judgment == "auditable" else ()
        limitations = () if judgment == "auditable" else (DP01Limitation(_LIMITATION_KIND, "unverified", diagnostic_explanation or self._limitation_detail),)
        gaps = []
        if judgment == "not_auditable":
            gaps.append(DP01EvidenceGap("missing_critical_context", "context", "unverified", "analysis_context"))
        if judgment == "scientifically_limited" and not any(x.source_type == "observed" for x in evidence):
            gaps.append(DP01EvidenceGap("absent_execution_observation", "observed", "unverified", "evidence_observations"))
        if judgment == "conflicted":
            gaps.append(DP01EvidenceGap("declared_observed_conflict", conflict_source, "conflicted", self._method_target))
        status = "established" if judgment == "auditable" else ("pending" if judgment == "pending_attempt" else "unresolved")
        contribution = DP01LocalContribution(status, self._contribution_scope, diagnostic_explanation or self._contribution_detail)
        return DP01FilteringResult(
            self._scope, analysis_context, declaration, evidence, diagnostic_explanation, (),
            judgment, next_actions, findings, limitations, tuple(gaps), contribution,
        )


def reaudit(pure_audit, request: Mapping[str, Any], store: Any, *,
            change_kind: str = _REAUDIT_CHANGE_KIND, change_reason: str = _REAUDIT_CHANGE_REASON):
    """Generic re-audit (mirrors dp01 `_reaudit` semantics): single-pass
    integrity inheritance, one target get, diff over the shared field set,
    audit via `pure_audit`."""
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
    result = pure_audit(effective)
    baseline = pure_audit(_thaw(target.input_snapshot))
    allowed = []
    before = _thaw(target.input_snapshot)
    for field in ("method", "threshold", "sample_rule", "unit", "source"):
        a = before.get("decision_declaration", {}).get(field)
        b = effective.get("decision_declaration", {}).get(field)
        if a != b:
            allowed.append(DP01Change(f"decision_declaration.{field}", a, b))

    def evidence(v):
        return [(x.get("source_type"), x.get("verification"), x.get("value")) for x in v.get("evidence_observations", [])]

    if evidence(before) != evidence(effective):
        allowed.append(DP01Change("evidence_observations", evidence(before), evidence(effective)))
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
        if a != b:
            allowed.append(DP01Change(path, a, b))
    for i, (before_action, after_action) in enumerate(zip_longest(old.next_actions, result.next_actions, fillvalue=None)):
        before_value = before_action.__dict__ if before_action is not None else None
        after_value = after_action.__dict__ if after_action is not None else None
        if before_value != after_value:
            allowed.append(DP01Change(f"result.next_actions[{i}]", before_value, after_value))
    submitter = request.get("re_audit", {}).get("submitter") if isinstance(request.get("re_audit", {}), Mapping) else None
    affected = _parse_affected_evidence(request.get("re_audit", {}).get("affected_evidence") if isinstance(request.get("re_audit", {}), Mapping) else None)
    change = DP01AttemptChange(change_kind, change_reason, submitter, affected)
    return effective, result, change, DP01ChangeSummary(tuple(sorted(allowed, key=lambda x: x.path))), target_id, target


def audit_point(pure_audit, builders: PointBuilders, request: Mapping[str, Any], store: Any, *,
                verbose_name: str = "DP"):
    """Generic public audit orchestration (mirrors dp01/dp02 public entry):
    integrity check -> attempt/re-audit -> pure audit -> persist."""
    if not isinstance(request, Mapping):
        return builders.integrity_result({}, (), _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,))
    request_snapshot = copy.deepcopy(dict(request))
    audit_id = request_snapshot.get("audit_id")
    if "target_audit_id" in request_snapshot and "re_audit" not in request_snapshot:
        raise ValueError("re-audit operation must be explicit")
    if "re_audit" in request_snapshot and request_snapshot.get("attempt") is not None:
        raise ValueError(f"{verbose_name} request cannot combine re_audit and attempt")
    if "re_audit" in request_snapshot:
        if store is None or not hasattr(store, "get"):
            raise ValueError("re-audit requires a store")
        if not isinstance(audit_id, str) or not audit_id:
            raise ValueError(f"audit_id is required for explicit {verbose_name} persistence")
        target_id = _reaudit_shape(request_snapshot)
        if target_id is None:
            return _with_identity(builders.integrity_result(
                _context_of(request_snapshot), _try_evidence(request_snapshot), _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        effective, result, change, summary, parent, target = reaudit(pure_audit, request_snapshot, store)
        source_version = target.version_snapshot
        record = DP01AuditRecord(audit_id, effective, _with_identity(result, audit_id), parent, change, "re_audit", summary, source_version_snapshot=dict(source_version))
        store.append(record)
        return _with_identity(result, audit_id)
    if not isinstance(request_snapshot.get("integrity_metadata"), Mapping):
        return _with_identity(builders.integrity_result(
            _context_of(request_snapshot), _try_evidence(request_snapshot), _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
    if request_snapshot.get("attempt") is not None:
        attempt_value = request_snapshot["attempt"]

        def invalid_attempt():
            return not isinstance(attempt_value, Mapping) or not _ATTEMPT_KEYS >= set(attempt_value) or not {"parent_audit_id", "change_kind", "change_reason"} <= set(attempt_value) or not isinstance(attempt_value.get("parent_audit_id"), str) or not attempt_value.get("parent_audit_id") or (attempt_value.get("affected_evidence") is not None and not isinstance(attempt_value.get("affected_evidence"), (list, tuple)))

        if invalid_attempt():
            return _with_identity(builders.integrity_result(
                _context_of(request_snapshot), _try_evidence(request_snapshot), _INVALID_TARGETS, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        relation = _child_attempt(request_snapshot)
    else:
        relation = None
    if relation is not None:
        _, integrity_failures = _integrity_failure(request_snapshot)
        if integrity_failures:
            result = builders.integrity_result(_context_of(request_snapshot), _try_evidence(request_snapshot), request_snapshot.get("integrity_metadata", {}), integrity_failures)
        else:
            result = builders.pending(request_snapshot)
    else:
        result = pure_audit(request_snapshot)
    if result.judgment == "integrity_failed" and any(action.target == "integrity_metadata" for action in result.next_actions):
        return _with_identity(result, audit_id)
    if store is not None and not (isinstance(store, Mapping) and not store):
        if not isinstance(audit_id, str) or not audit_id:
            raise ValueError(f"audit_id is required for explicit {verbose_name} persistence")
        if not hasattr(store, "append"):
            raise TypeError(f"invalid {verbose_name} store")
        parent_audit_id = change = None
        if relation is not None:
            parent_audit_id, change = relation
            try:
                store.get(parent_audit_id)
            except KeyError as exc:
                raise KeyError(f"missing parent_audit_id: {parent_audit_id}") from exc
        store.append(DP01AuditRecord(audit_id, request_snapshot, _with_identity(result, audit_id), parent_audit_id, change))
    return _with_identity(result, audit_id)


__all__ = ["PointBuilders", "reaudit", "audit_point"]