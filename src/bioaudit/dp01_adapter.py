"""DP-01 filtering declaration adapter over the legacy diagnostic API."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime, timezone
from itertools import zip_longest
from typing import Any

from bioaudit.api.audit import audit_decision
from bioaudit.dp01_models import (
    _parse_affected_evidence,
    DP01_EVIDENCE_ROLES,
    DP01_EVIDENCE_SOURCE_TYPES,
    DP01_EVIDENCE_STATES,
    DP01AttemptChange,
    DP01Change,
    DP01ChangeSummary,
    DP01FilteringResult,
    EvidenceItem,
    FilteringDeclaration,
    ControlledNextAction,
    DP01EvidenceGap,
    DP01Finding,
    DP01Limitation,
    DP01LocalContribution,
)
from bioaudit.dp01_store import DP01AuditRecord, _current_version_snapshot


_REQUIRED_CONTEXT = (
    "project_id",
    "analysis_id",
    "comparison",
    "data_type",
    "audit_scope",
    "intended_use",
)
# P-03 row133 / P-01 §7.1: what an audit does NOT include must be confirmed BEFORE
# anything downstream is relied upon. Silence is therefore not acceptable, but an
# explicit "none" is — so this key must be PRESENT and NON-BLANK rather than merely
# present. (Kept separate from _REQUIRED_CONTEXT so the reason is visible here.)
_REQUIRED_NONBLANK_CONTEXT = ("exclusions",)


def _missing_context(context: Mapping[str, Any]) -> list[str]:
    """Required context keys that are absent, plus non-blank keys that are blank.

    Single source for all five adapters so the "what is not included must be
    declared" rule (P-03 row133) cannot drift between decision points.
    """
    missing = [key for key in _REQUIRED_CONTEXT if key not in context]
    missing += [key for key in _REQUIRED_NONBLANK_CONTEXT
                if not context.get(key) or not str(context.get(key)).strip()]
    return missing
_REQUIRED_DECLARATION = ("method", "threshold", "sample_rule", "unit", "source")
_INTEGRITY_DOMAINS = ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")
_INTEGRITY_STATES = {"confirmed", "failed", "unverified", "conflicted"}
_INTEGRITY_KEYS = set(_INTEGRITY_DOMAINS) | {"material_ids", "pending_targets"}
_INTEGRITY_ERROR_DOMAIN = "binding"
_INTEGRITY_ERROR_STATE = "unverified"
_ATTEMPT_KEYS = {"parent_audit_id", "change_kind", "change_reason", "submitter", "affected_evidence"}
_REAUDIT_KEYS = {"operation", "target_audit_id", "submitter", "affected_evidence"}
_REAUDIT_CHANGE_KIND = "decision_correction"  # fixed by the re-audit contract
_REAUDIT_CHANGE_REASON = "explicit re-audit"  # fixed by the re-audit contract


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _action(action: str, reason: str, target: str, next_state: str) -> ControlledNextAction:
    return ControlledNextAction(action=action, reason=reason, target=target, next_state=next_state)


#: Evidence qualification (P-02 §4.1): what an observation may carry, and the one
#: place the project's freshness policy lives.
#:
#: Every item is stored with an observation moment and a validity window, so a later
#: reader sees when the material was observed and how long it was claimed to hold —
#: instead of an unqualified "evidence present". A caller that knows better declares
#: its own `valid_for_seconds` per item; a caller that does not is held to the window
#: below rather than to silence, so changing the policy is a one-line change here.
DEFAULT_EVIDENCE_VALIDITY_SECONDS = 365 * 24 * 60 * 60

#: The keys an evidence observation may carry.
_ALLOWED_EVIDENCE = ("source_type", "verification", "value", "observed_at",
                     "valid_for_seconds", "provenance", "provenance_subject",
                     "supports", "evidence_role")


def _as_datetime(value: Any) -> datetime:
    """A caller-declared observation time, as an aware UTC datetime or a clear error.

    Accepts an ISO-8601 string (what a JSON caller can send) or a datetime. A naive
    timestamp is rejected rather than guessed at, because "when" is the whole point.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
    else:
        raise ValueError(f"expected an ISO-8601 string, got {type(value).__name__}")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a timezone offset (e.g. 2026-09-12T10:00:00+00:00)")
    return parsed.astimezone(timezone.utc)


def _evidence(request: Mapping[str, Any],
              *, recorded_at: datetime | None = None) -> tuple[EvidenceItem, ...]:
    """Qualify every observation: source, verification, WHEN, HOW LONG, and role.

    ``observed_at`` defaults to the moment the record is written — a fact this system
    can actually know — rather than to a claim about when the work happened.
    """
    observations = request.get("evidence_observations", ())
    if not isinstance(observations, (list, tuple)):
        raise ValueError("evidence_observations must be a sequence")
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)
    items = []
    for item in observations:
        if not isinstance(item, Mapping):
            raise ValueError("evidence observation has invalid shape")
        unknown = sorted(set(item) - set(_ALLOWED_EVIDENCE))
        if unknown:
            raise ValueError(f"evidence observation has unknown keys: {unknown}")
        if "source_type" not in item or "verification" not in item:
            raise ValueError("evidence observation has invalid shape")
        if item["source_type"] not in DP01_EVIDENCE_SOURCE_TYPES or item["verification"] not in DP01_EVIDENCE_STATES:
            raise ValueError("evidence observation has invalid vocabulary")
        if item.get("evidence_role", "support") not in DP01_EVIDENCE_ROLES:
            raise ValueError("evidence observation has invalid authority role")
        if isinstance(item.get("value"), (Mapping, list, tuple, set)):
            raise ValueError("evidence observation value must be scalar")
        try:
            observed_at = _as_datetime(item.get("observed_at", recorded_at))
        except ValueError as exc:
            raise ValueError(f"evidence observed_at is not a usable timestamp: {exc}") from exc
        window = item.get("valid_for_seconds", DEFAULT_EVIDENCE_VALIDITY_SECONDS)
        if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
            raise ValueError("evidence valid_for_seconds must be a positive integer")
        items.append(EvidenceItem(
            source_type=item["source_type"], verification=item["verification"],
            value=item.get("value"), observed_at=observed_at,
            valid_for_seconds=window,
            provenance=item.get("provenance"),
            provenance_subject=item.get("provenance_subject"),
            supports=item.get("supports"),
            evidence_role=item.get("evidence_role", "support")))
    return tuple(items)


def _valid_declaration(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_REQUIRED_DECLARATION):
        raise ValueError("decision_declaration has invalid shape")
    if not isinstance(value["method"], str) or not value["method"] or not isinstance(value["unit"], str) or not value["unit"] or value["source"] != "declared":
        raise ValueError("decision_declaration has invalid fields")
    try:
        FilteringDeclaration(**value)
    except (TypeError, ValueError) as exc:
        raise ValueError("decision_declaration has unsupported structured values") from exc
    return value


def _integrity_failure(request: Mapping[str, Any]) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    metadata = request.get("integrity_metadata")
    if not isinstance(metadata, Mapping):
        return {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)

    valid_targets = {}
    invalid_target_keys = []
    for key in ("material_ids", "pending_targets"):
        values = metadata.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
            invalid_target_keys.append(key)
        else:
            valid_targets[key] = list(values)

    failures = []
    if set(metadata) != _INTEGRITY_KEYS:
        failures.append(_INTEGRITY_ERROR_DOMAIN)
    for domain in _INTEGRITY_DOMAINS:
        entry = metadata.get(domain)
        if not isinstance(entry, Mapping) or set(entry) != {"state"}:
            failures.append(domain)
            continue
        state = entry["state"]
        if not isinstance(state, str) or state not in _INTEGRITY_STATES:
            failures.append(domain)
        elif state != "confirmed":
            failures.append(domain)

    if invalid_target_keys:
        failures.append(_INTEGRITY_ERROR_DOMAIN)
    normalized = {**metadata, **valid_targets, "_invalid_target_keys": tuple(invalid_target_keys)}
    return normalized, tuple(dict.fromkeys(failures))


def _integrity_result(context: Mapping[str, Any], evidence: tuple[EvidenceItem, ...], metadata: Mapping[str, Any], failures: tuple[str, ...]) -> DP01FilteringResult:
    if not metadata:
        material_ids, pending_targets = (), ()
    else:
        material_value = metadata.get("material_ids", ())
        pending_value = metadata.get("pending_targets", ())
        material_ids = tuple(material_value) if isinstance(material_value, list) and all(isinstance(item, str) and item for item in material_value) else ()
        pending_targets = tuple(pending_value) if isinstance(pending_value, list) and all(isinstance(item, str) and item for item in pending_value) else ()
    def state_for(domain):
        entry = metadata.get(domain) if isinstance(metadata, Mapping) else None
        state = entry.get("state") if isinstance(entry, Mapping) else None
        return state if isinstance(state, str) and state in _INTEGRITY_STATES else _INTEGRITY_ERROR_STATE
    invalid_targets = tuple(metadata.get("_invalid_target_keys", ())) if isinstance(metadata, Mapping) else ()
    targets = material_ids + pending_targets
    if invalid_targets:
        targets += ("integrity_metadata",) * len(invalid_targets)
    elif not targets:
        targets = ("integrity_metadata",)
    findings = tuple(DP01Finding("integrity_failure", domain, state_for(domain), {"domain": domain, "material_ids": list(material_ids), "pending_targets": list(pending_targets)}) for domain in failures)
    gaps = tuple(DP01EvidenceGap("integrity_failure", domain, state_for(domain), target) for domain in failures for target in targets)
    actions = tuple(_action("request_evidence", f"integrity metadata requires resolution: {domain}", target, "evidence_requested") for domain in failures for target in targets)
    return DP01FilteringResult("DP-01/filtering", context, None, evidence, "filtering input binding failure isolated affected material", (), "integrity_failed", actions, findings, (), gaps, DP01LocalContribution("unresolved", "filtering", "Affected filtering material is isolated pending integrity resolution"))


def _structured(judgment, declaration, evidence, reason=""):
    findings = tuple(DP01Finding("method", "declared", "confirmed", declaration.method) for _ in [0]) if declaration and judgment == "auditable" else ()
    limitations = () if judgment == "auditable" else (DP01Limitation("filtering_support", "unverified", reason or "Filtering support is limited"),)
    gaps = []
    if judgment == "not_auditable": gaps.append(DP01EvidenceGap("missing_critical_context", "context", "unverified", "analysis_context"))
    if judgment == "scientifically_limited" and not any(x.source_type == "observed" for x in evidence): gaps.append(DP01EvidenceGap("absent_execution_observation", "observed", "unverified", "evidence_observations"))
    if judgment == "conflicted": gaps.append(DP01EvidenceGap("declared_observed_conflict", "declared_observed", "conflicted", "filtering_method"))
    status = "established" if judgment == "auditable" else ("pending" if judgment == "pending_attempt" else "unresolved")
    return findings, limitations, tuple(gaps), DP01LocalContribution(status, "filtering", reason or "DP-01 filtering contribution")


def _result(**kwargs):
    f, l, g, c = _structured(kwargs["judgment"], kwargs.get("declaration"), kwargs.get("evidence", ()), kwargs.get("diagnostic_explanation", ""))
    return DP01FilteringResult(**kwargs, findings=f, limitations=l, evidence_gaps=g, local_contribution=c)


def _fail_closed(context: Mapping[str, Any], evidence: tuple[EvidenceItem, ...], reason: str) -> DP01FilteringResult:
    return _result(
        scope="DP-01/filtering", analysis_context=context, declaration=None, evidence=evidence,
        diagnostic_explanation=reason, matched_rule_ids=(), judgment="not_auditable",
        next_actions=(_action("request_evidence", reason, "analysis_context", "evidence_requested"), _action("stop", reason, "current_attempt", "stopped")),
    )


def _input_failure(context: Mapping[str, Any], evidence: tuple[EvidenceItem, ...], reason: str) -> DP01FilteringResult:
    return _integrity_result(context, evidence, {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))


def _audit_dp01_filtering(request: Mapping[str, Any]) -> DP01FilteringResult:
    """Build one point-local DP-01 result without persistence side effects."""
    request = _as_mapping(request, "request")
    context_value = request.get("analysis_context", {})
    context = context_value if isinstance(context_value, Mapping) else {}
    try:
        evidence = _evidence(request)
    except (TypeError, ValueError):
        return _input_failure(context, (), "evidence_observations is malformed")
    metadata, integrity_failures = _integrity_failure(request)
    if integrity_failures:
        return _integrity_result(context, evidence, metadata, integrity_failures)
    try:
        declaration_value = _valid_declaration(request.get("decision_declaration"))
    except (TypeError, ValueError):
        return _input_failure(context, evidence, "decision_declaration is malformed")
    declaration = declaration_value
    missing_context = _missing_context(context)
    missing_declaration = [key for key in _REQUIRED_DECLARATION if key not in declaration]
    if missing_context or missing_declaration:
        missing = ", ".join(missing_context + missing_declaration)
        return _fail_closed(context, evidence, f"critical filtering context is missing: {missing}")
    if declaration["source"] != "declared":
        return _fail_closed(context, evidence, "filtering declaration source must be declared")

    observed_values = {
        item.value for item in evidence
        if item.source_type == "observed" and item.verification in {"confirmed", "conflicted"}
    }
    declared_method = declaration["method"]
    conflict = bool(observed_values and declared_method not in observed_values)
    known_method = declared_method == "low_count_filter"
    if not known_method:
        return _result(
            scope="DP-01/filtering",
            analysis_context=context,
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            diagnostic_explanation="filtering method is not covered by the current rule set",
            matched_rule_ids=(),
            judgment="scientifically_limited",
            next_actions=(
                _action("request_review", "method is unclassified", "filtering_method", "review_requested"),
                _action("submit_correction", "provide a supported filtering method", "decision_declaration", "correction_submitted"),
            ),
        )
    if conflict:
        return _result(
            scope="DP-01/filtering",
            analysis_context=context,
            declaration=FilteringDeclaration(**declaration),
            evidence=evidence,
            diagnostic_explanation="declared and observed filtering methods conflict",
            matched_rule_ids=(),
            judgment="conflicted",
            next_actions=(
                _action("request_evidence", "resolve declared/observed conflict", "evidence_observations", "evidence_requested"),
                _action("request_review", "conflicting observations require adjudication", "filtering_method", "review_requested"),
            ),
        )

    legacy_decision = {
        "step_id": f"{context['analysis_id']}:filtering",
        "decision_type": "filtering",
        "choice": declaration["method"],
        "rationale": "DP-01 filtering declaration",
        "context": {
            "sequencing": "bulk_RNA_seq"
            if context["data_type"] in {"bulk_rnaseq", "bulk_RNA_seq"}
            else context["data_type"],
            "n_replicates": 2
            if declaration["sample_rule"] == "at_least_two_samples"
            else declaration["sample_rule"],
        },
    }
    confirmed_observed = any(item.source_type == "observed" and item.verification == "confirmed" for item in evidence)
    diagnosis = audit_decision(legacy_decision, paradigm="deg")
    return _result(
        scope="DP-01/filtering",
        analysis_context=context,
        declaration=FilteringDeclaration(**declaration),
        evidence=evidence,
        diagnostic_explanation=diagnosis["explanation"],
        matched_rule_ids=tuple(diagnosis["matched_rules"]),
        judgment="scientifically_limited" if not confirmed_observed else "auditable",
        next_actions=(
            _action("request_evidence", "support execution observation", "evidence_observations", "evidence_requested"),
        ) if not confirmed_observed else (),
    )


def _child_attempt(request: Mapping[str, Any]) -> tuple[str, DP01AttemptChange] | None:
    value = request.get("attempt")
    if value is None:
        return None
    allowed_keys = {"parent_audit_id", "change_kind", "change_reason", "submitter", "affected_evidence"}
    if not isinstance(value, Mapping) or not _ATTEMPT_KEYS >= set(value) or not {"parent_audit_id", "change_kind", "change_reason"} <= set(value):
        raise ValueError("invalid DP-01 child attempt relation")
    parent_audit_id = value["parent_audit_id"]
    if not isinstance(parent_audit_id, str) or not parent_audit_id:
        raise ValueError("child parent_audit_id is required")
    return parent_audit_id, DP01AttemptChange(value["change_kind"], value["change_reason"], value.get("submitter"), _parse_affected_evidence(value.get("affected_evidence")))


def _try_evidence(request: Mapping[str, Any]) -> tuple[EvidenceItem, ...]:
    try:
        return _evidence(request)
    except (TypeError, ValueError):
        return ()


def _pending_child_result(request: Mapping[str, Any]) -> DP01FilteringResult:
    context_value = request.get("analysis_context", {})
    context = context_value if isinstance(context_value, Mapping) else {}
    try:
        evidence = _evidence(request)
    except (TypeError, ValueError):
        return _integrity_result(context, (), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))
    return _result(
        scope="DP-01/filtering",
        analysis_context=context,
        declaration=None,
        evidence=evidence,
        diagnostic_explanation="child attempt submitted; validation and effectiveness are pending",
        matched_rule_ids=(),
        judgment="pending_attempt",
        next_actions=(),
    )


def _reaudit_shape(request: Mapping[str, Any]) -> str | None:
    """Return target_audit_id for a well-formed re-audit control.

    Malformed shape (non-mapping, wrong key set, empty/non-string target) returns
    None so the caller can fail closed structurally. An explicit wrong `operation`
    value is a caller coding error and raises ValueError.
    """
    op = request.get("re_audit")
    if not isinstance(op, Mapping) or not _REAUDIT_KEYS >= set(op) or not {"operation", "target_audit_id"} <= set(op):
        return None
    if op.get("affected_evidence") is not None and not isinstance(op.get("affected_evidence"), (list, tuple)):
        return None
    if op.get("operation") != "re_audit":
        raise ValueError("invalid re-audit operation")
    target_id = op.get("target_audit_id")
    if not isinstance(target_id, str) or not target_id:
        return None
    return target_id


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _merge_snapshot(target_snapshot: Mapping[str, Any], request: Mapping[str, Any]) -> dict:
    """Deep-copy the target input snapshot and overlay explicitly supplied top-level fields.

    Uses a structural deep copy so nested mutable values in the incoming request
    cannot alias into the effective snapshot (which is later frozen by
    DP01AuditRecord). Only the allowed input fields may be overlaid.
    """
    effective = {}
    for key, value in _thaw(target_snapshot).items():
        effective[key] = copy.deepcopy(value)
    for key in ("analysis_context", "decision_declaration", "evidence_observations", "integrity_metadata"):
        if key in request:
            effective[key] = copy.deepcopy(request[key])
    return effective


def _reaudit(request: Mapping[str, Any], store: Any):
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
        # Omitted integrity metadata is inherited from the targeted child
        # snapshot in a single pass (no recursion).
        effective = _merge_snapshot(target.input_snapshot, request)
        if "integrity_metadata" not in effective:
            effective["integrity_metadata"] = copy.deepcopy(target.input_snapshot.get("integrity_metadata"))
        request = {**request, "integrity_metadata": effective["integrity_metadata"]}
    effective = _merge_snapshot(target.input_snapshot, request)
    result = _audit_dp01_filtering(effective)
    baseline = _audit_dp01_filtering(_thaw(target.input_snapshot))
    allowed = []
    before = _thaw(target.input_snapshot)
    for field in ("method", "threshold", "sample_rule", "unit", "source"):
        a = before.get("decision_declaration", {}).get(field); b = effective.get("decision_declaration", {}).get(field)
        if a != b: allowed.append(DP01Change(f"decision_declaration.{field}", a, b))
    def evidence(v): return [(x.get("source_type"), x.get("verification"), x.get("value")) for x in v.get("evidence_observations", [])]
    if evidence(before) != evidence(effective): allowed.append(DP01Change("evidence_observations", evidence(before), evidence(effective)))
    if before.get("integrity_metadata") != effective.get("integrity_metadata"):
        allowed.append(DP01Change("integrity_metadata", before.get("integrity_metadata"), effective.get("integrity_metadata")))
    old = baseline
    def structured(value):
        return [item.to_dict() if hasattr(item, "to_dict") else item.__dict__ if hasattr(item, "__dict__") else item for item in value] if isinstance(value, (list, tuple)) else value.to_dict() if hasattr(value, "to_dict") else value.__dict__ if hasattr(value, "__dict__") else value
    for path, a, b in (("result.judgment",old.judgment,result.judgment),("result.matched_rule_ids",old.matched_rule_ids,result.matched_rule_ids),("result.diagnostic_explanation",old.diagnostic_explanation,result.diagnostic_explanation), ("result.findings", structured(old.findings), structured(result.findings)), ("result.limitations", structured(old.limitations), structured(result.limitations)), ("result.evidence_gaps", structured(old.evidence_gaps), structured(result.evidence_gaps)), ("result.local_contribution", structured(old.local_contribution), structured(result.local_contribution))):
        if a != b: allowed.append(DP01Change(path, a, b))
    action_pairs = zip_longest(old.next_actions, result.next_actions, fillvalue=None)
    for i, (before_action, after_action) in enumerate(action_pairs):
        before_value = before_action.__dict__ if before_action is not None else None
        after_value = after_action.__dict__ if after_action is not None else None
        if before_value != after_value:
            allowed.append(DP01Change(f"result.next_actions[{i}]", before_value, after_value))
    return effective, result, DP01AttemptChange(_REAUDIT_CHANGE_KIND, _REAUDIT_CHANGE_REASON, request.get("re_audit", {}).get("submitter") if isinstance(request.get("re_audit", {}), Mapping) else None, _parse_affected_evidence(request.get("re_audit", {}).get("affected_evidence") if isinstance(request.get("re_audit", {}), Mapping) else None)), DP01ChangeSummary(tuple(sorted(allowed, key=lambda x: x.path))), target_id, target


def _with_identity(result, audit_id):
    if not isinstance(audit_id, str) or not audit_id:
        return result
    return DP01FilteringResult(
        result.scope, result.analysis_context, result.declaration, result.evidence,
        result.diagnostic_explanation, result.matched_rule_ids, result.judgment, result.next_actions,
        result.findings, result.limitations, result.evidence_gaps, result.local_contribution,
        audit_id, dict(_current_version_snapshot()),
    )


def audit_dp01_filtering(request: Mapping[str, Any], store: Any) -> DP01FilteringResult:
    if not isinstance(request, Mapping):
        return _integrity_result({}, (), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,))
    request_snapshot = copy.deepcopy(dict(request))
    audit_id = request_snapshot.get("audit_id")
    if "target_audit_id" in request_snapshot and "re_audit" not in request_snapshot:
        raise ValueError("re-audit operation must be explicit")
    if "re_audit" in request_snapshot and request_snapshot.get("attempt") is not None:
        raise ValueError("DP-01 request cannot combine re_audit and attempt")
    if "re_audit" in request_snapshot:
        if store is None or not hasattr(store, "get"): raise ValueError("re-audit requires a store")
        if not isinstance(audit_id, str) or not audit_id: raise ValueError("audit_id is required for explicit DP-01 persistence")
        target_id = _reaudit_shape(request_snapshot)
        if target_id is None:
            return _with_identity(_integrity_result(request_snapshot.get("analysis_context", {}) if isinstance(request_snapshot.get("analysis_context", {}), Mapping) else {}, _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        effective, result, change, summary, parent, target = _reaudit(request_snapshot, store)
        source_version = target.version_snapshot
        record = DP01AuditRecord(audit_id, effective, _with_identity(result, audit_id), parent, change, "re_audit", summary, source_version_snapshot=dict(source_version))
        store.append(record)
        return _with_identity(result, audit_id)
    if not isinstance(request_snapshot.get("integrity_metadata"), Mapping):
        context_value = request_snapshot.get("analysis_context", {})
        context = context_value if isinstance(context_value, Mapping) else {}
        return _with_identity(_integrity_result(context, _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
    if request_snapshot.get("attempt") is not None:
        attempt_value = request_snapshot["attempt"]
        def invalid_attempt():
            return not isinstance(attempt_value, Mapping) or not _ATTEMPT_KEYS >= set(attempt_value) or not {"parent_audit_id", "change_kind", "change_reason"} <= set(attempt_value) or not isinstance(attempt_value.get("parent_audit_id"), str) or not attempt_value.get("parent_audit_id") or (attempt_value.get("affected_evidence") is not None and not isinstance(attempt_value.get("affected_evidence"), (list, tuple)))
        if invalid_attempt():
            return _with_identity(_integrity_result(request_snapshot.get("analysis_context", {}) if isinstance(request_snapshot.get("analysis_context", {}), Mapping) else {}, _try_evidence(request_snapshot), {"_invalid_target_keys": ("material_ids", "pending_targets")}, (_INTEGRITY_ERROR_DOMAIN,)), audit_id)
        relation = _child_attempt(request_snapshot)
    else:
        relation = None
    if relation is not None:
        _, integrity_failures = _integrity_failure(request_snapshot)
        if integrity_failures:
            result = _integrity_result(request_snapshot.get("analysis_context", {}), _try_evidence(request_snapshot), request_snapshot.get("integrity_metadata", {}), integrity_failures)
        else:
            result = _pending_child_result(request_snapshot)
    else:
        result = _audit_dp01_filtering(request_snapshot)
    if result.judgment == "integrity_failed" and any(action.target == "integrity_metadata" for action in result.next_actions):
        return _with_identity(result, audit_id)
    if store is not None and not (isinstance(store, Mapping) and not store):
        if not isinstance(audit_id, str) or not audit_id: raise ValueError("audit_id is required for explicit DP-01 persistence")
        if not hasattr(store, "append"): raise TypeError("invalid DP-01 store")
        parent_audit_id = change = None
        if relation is not None:
            parent_audit_id, change = relation
            try: store.get(parent_audit_id)
            except KeyError as exc: raise KeyError(f"missing parent_audit_id: {parent_audit_id}") from exc
        store.append(DP01AuditRecord(audit_id, request_snapshot, _with_identity(result, audit_id), parent_audit_id, change))
    return _with_identity(result, audit_id)


__all__ = ["audit_dp01_filtering"]
