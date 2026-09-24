"""Five-point register report layer (MVP assembly — first product-facing step).

This module is the human-authorized report renderer: a PURE function that turns
the register view (produced by ``audit_dp_register``) into an ordered,
explanatory report structure following the P-01 §7.1–§7.9 / P-02 §6.2 ten-item
meaning order (context → five decision-point register → per-point evidence/result
→ findings/limitations → bounded-use vs release/lane → review/admin boundary →
history/change → dependable / must-stop / next human decision).

Boundary contract (authorization decisions 1–4):
- PURE: no side effects, no ledger write, no core mutation. It reads the register
  and returns a new ordered ``dict``.
- EXPLANATORY, NOT AUTHORITY: the report is a readable explanation of the
  authoritative account; it is NOT a second result-authority container, NOT an
  acceptance/release. It introduces no new public container/status/lane/authority
  path, and contains no total score / winner / summary judgment.
- Mixed per-point meaning is preserved; scientific insufficiency is kept SEPARATE
  from integrity/authority failure (they are never relabeled into one bucket).
- The register's `_register_meta` first-bounded-set statement passes through.

Design note (Standards review-fix): the canonical ordered five-point set is
reused from ``dp_assembly._POINT_SEAMS`` — no duplicated hard-coded point lists.
Point purpose labels are report-only metadata keyed by the same names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bioaudit.dp01_models import DP01_JUDGMENTS
from bioaudit.dp01_store import _current_version_snapshot  # runtime version binding
from bioaudit.dp_assembly import _POINT_SEAMS
from bioaudit.dp_change import (
    _DIFFERENCE_KEYS,  # the difference entry shape
    ACCOUNT_PREFIX,  # an account reference prefix (histories relate accounts)
    CHANGE_CLASSES,  # P-02 §6.4 closed change vocabulary
    ESCALATION_REASONS,  # P-02 §6.5 closed stop-and-escalate vocabulary
)
from bioaudit.dp_coherence import derive_cross_step_coherence
from bioaudit.dp_roles import (
    _VERSION_KEYS,  # shared five-field version binding (single source)
    RECEIPT_KINDS,  # closed administrative receipt domain (mirrored, not re-invented)
    ROLES,  # the four role categories
    TARGET_PREFIXES,  # authorized target-kind prefixes
)
from bioaudit.dp_roles import (
    _plain as _strict_plain,  # strict projection: raises on unsupported values
)
from bioaudit.dp_strength import derive_dp_strength

_MISSING = object()

# Single ordered source of the five decision-point keys (reused, not re-listed).
_POINT_ORDER: tuple[str, ...] = tuple(_POINT_SEAMS)
_POINT_SET = frozenset(_POINT_SEAMS)

# Report-only metadata: purpose label per point (keyed by the same names).
_POINT_PURPOSES: dict[str, str] = {
    "filtering": "low-expression filtering",
    "normalization": "normalization",
    "differential_method": "differential-analysis method",
    "multiple_testing_correction": "multiple-testing correction",
    "significance_threshold": "significance / effect-size threshold",
}
# Findings that map to scientific insufficiency vs integrity/authority failure.
#
# `not_auditable` belongs to the SECOND set, not the first: every path that emits
# it means a REQUIRED FACT could not be verified — the point's critical context or
# input binding is missing, or the declaration's provenance (source) is not
# "declared" (`dp01_adapter._fail_closed`, `dp02_adapter._norm_fail_closed`,
# `dp_shared.PointBuilders.fail_closed`). P-02 gives that its own meaning
# ("a required identity, provenance, authority, binding, freshness or integrity
# fact cannot be verified") and explicitly forbids relabeling it as ordinary
# *scientific* insufficiency; the strength layer already honours that direction
# (`dp_strength` never relabels insufficiency as not-auditable). Folding it into
# insufficiency here would compress the two distinct meanings into one bucket and
# would also route the stop to the wrong authority.
_INSUFFICIENCY_JUDGMENTS = {"scientifically_limited", "conflicted"}
_INTEGRITY_JUDGMENTS = {"integrity_failed", "not_auditable"}

# Fields every point result must expose; absent/typo → fail-closed TypeError.
_REQUIRED_RESULT_FIELDS = ("judgment", "scope", "diagnostic_explanation",
                           "declaration", "limitations", "evidence_gaps",
                           "next_actions", "analysis_context", "evidence", "findings")

# The four combined-account structured-result axes surfaced in the report
# (P-01 §5.1 / P-02 §4.7). Shared by the embedding below and the tests.
_REPORT_AXES = ("validation", "scope", "inference_type", "explanation_depth")

# DP-REPORT-AUTHORITY: the administrative statement keeps its exact wording; the
# authority attribution and the role records are rendered alongside it so the
# report can answer P-03 row142 (which result is authoritative) and row146 (who
# authored / reviewed / adjudicated / receipted, and within what scope).
_REVIEW_STATEMENT = ("This report has not itself performed independent review and "
                     "does not constitute a receipt, approval, or adjudication.")
# Wording is bounded by what the INPUT supports: the report knows which records it
# was given, not what exists in the world (review-fix Spec S1 / Standards #7).
_NO_ROLES_SUPPLIED_STATEMENT = (
    "No role records were provided to this report, so it cannot say who authored, "
    "independently reviewed, adjudicated, or administratively receipted this account.")
_ROLE_CATEGORIES = ("authored", "reviewed", "receipted", "adjudicated")

# P-01 §5.2 / P-03 row142 minimum meaning: the sole result-authority container is
# an acceptance_snapshot whose stage_package is pre-snapshot input and which holds
# EXACTLY ONE embedded structured_strength_result. The report states this meaning
# and points at the container — without copying the embedded result.
_AUTHORITY_CONTAINER_KIND = "acceptance_snapshot"
_AUTHORITY_MINIMUM_MEANING = (
    "acceptance_snapshot is the sole result-authority container; stage_package is "
    "its pre-snapshot input; the container holds exactly one embedded "
    "structured_strength_result. This report explains that account; it is not the "
    "container and not a second result.")
_AUTHORITY_BASIS_KEYS = frozenset({"confirmed", "unconfirmed_domains", "basis"})
_AUTHORITY_ATTRIBUTION_KEYS = frozenset({
    "container_kind", "snapshot_id", "minimum_meaning", "stage_package_role",
    "embedded_result_count", "authority_basis", "version_binding",
    "not_this_report", "not_a_second_container",
})
_AUTHORITY_SOURCE_ATTRS = ("snapshot_id", "stage_package", "structured_strength_result",
                           "version_snapshot", "authority_basis")
_ROLE_ENTRY_KEYS = frozenset({"role", "actor", "target", "scope", "receipt_kind"})

# DP-CHANGE: history/change meaning (P-02 §6.4/§6.5, P-03 row147).
_HISTORY_STATEMENT = (
    "A later audited account is a new bounded judgment related to, not a silent "
    "rewrite of, the current account. Prior material keeps its original scope and "
    "time meaning and is never current result authority.")
_NO_HISTORY_STATEMENT = (
    "No change context was provided to this report, so it cannot say what changed "
    "relative to any earlier account.")
_HISTORY_PRIOR_STATEMENT = (
    "Earlier accounts are shown as historical-only; they are not current.")
_HISTORY_CHANGE_STATEMENT = (
    "A change is recorded relative to the current account. The proposed/current "
    "difference is shown below and the authority that must decide whether it is "
    "acceptable is named.")
_HISTORY_EMPTY_STATEMENT = (
    "Change context was supplied, but it contains no earlier accounts and no "
    "proposed change.")
_OTHER_CHANGES_NOTE = (
    "This report was given one change record; the change ledger may hold more.")
# The change's state is a LOCAL sub-state of this report section, not a governed
# public status word (P-03 recognises awaiting human decision / blocked / not
# accepted as governed meanings).
_CHANGE_STATE_SCOPE = "change-section local sub-state"
_DECLARATION_BASIS = "declared_by_submitter"
_PROPOSED_CHANGE_KEYS = frozenset({
    "from_account", "to_account", "state", "state_scope", "current_account",
    "prior_account", "prior_meaning", "change_classes", "differences", "reason",
    "submitter", "requires_adjudication", "adjudication_authority",
    "escalation_required", "escalation_reason", "declaration_basis",
})

# NOTE: ``dp_roles`` is imported for CONSTANTS ONLY (its role vocabulary and its
# mirrored closed domains). The report layer performs no ledger IO; the only
# transitive module load is dp_roles' own dependencies.
# Review-fix notes (Standards #1 / #2, Spec N1):
# - D. Double register-validation between render_dp_report and derive_dp_strength
#     is accepted: on a five-point register the cost is negligible, and each seam
#     keeps its own "register -> axes" contract. Not factored out.
# - E. _REQUIRED_RESULT_FIELDS differs between dp_report (10 fields, incl.
#     declaration/next_actions/evidence/findings) and dp_strength (6 fields):
#     report runs first and is stricter, so no runtime conflict; a shared contract
#     constant is left as future hardening, not applied here.


def _report_meta(register_meta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "explanatory": True,
        "not_authority": True,
        "not_acceptance": True,
        "source_first_bounded_set": bool(register_meta.get("first_bounded_set")),
    }


def _decision_moment() -> Any:
    """The moment this report judges freshness against, taken ONCE per render.

    Captured once so every item in one report is judged against the same instant —
    two items observed a second apart cannot land on different sides of the line
    because the renderer happened to read the clock twice.
    """
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc)


def _point_to_record(point_name: str, result: Any,
                     *, now: Any = None) -> dict[str, Any]:
    """Render one point-local result into a report record (pure).

    Requires ``result`` to expose the documented ``DP01FilteringResult`` fields;
    a missing/mistyped field fails closed (TypeError) instead of silently
    producing ``None`` (Standards review-fix S1).
    """
    missing = [f for f in _REQUIRED_RESULT_FIELDS if not hasattr(result, f)]
    if missing:
        raise TypeError(f"register point '{point_name}' is missing result fields: {', '.join(missing)}")

    limitations = [{"kind": x.kind, "state": x.state, "detail": x.detail}
                   for x in result.limitations] if result.limitations else []
    gaps = [{"kind": x.kind, "source": x.source, "state": x.state, "target": x.target}
            for x in result.evidence_gaps] if result.evidence_gaps else []
    actions = [{"action": x.action, "reason": x.reason, "target": x.target,
                "next_state": x.next_state} for x in result.next_actions] if result.next_actions else []
    declaration = result.declaration
    declared_method = declaration.method if declaration is not None else None
    # P-02 §6.2 item 3 / §4.1: render per-point evidence QUALIFIED — source,
    # verification, value, and the freshness meaning the surface has to show.
    #
    # Freshness is DERIVED here (never stored): the ledger keeps when an item was
    # observed and how long it was claimed to hold, so the same account rendered at
    # two different moments yields two honest answers instead of one frozen claim.
    # A stale item is NOT an error: it simply no longer actively supports the claim
    # it was offered for, and the report says exactly that.
    evidence = []
    if now is None:
        now = _decision_moment()
    for item in (result.evidence or ()):
        current = item.is_current(now=now)
        evidence.append({
            "source_type": item.source_type,
            "verification": item.verification,
            "value": item.value,
            "observed_at": item.observed_at.isoformat(timespec="seconds"),
            "valid_for_seconds": item.valid_for_seconds,
            "freshness": "current" if current else "stale",
            "freshness_meaning": (
                "this observation is within the validity window the submitter "
                "declared, so it can actively support the claim it was offered for"
                if current else
                "this observation is past the validity window the submitter "
                "declared: it no longer actively supports the claim it was offered "
                "for, and what it can still establish is historical meaning only"),
            "provenance": item.provenance,
            "provenance_subject": item.provenance_subject,
            "supports": item.supports,
            "evidence_role": item.evidence_role,
        })
    stale_evidence = [e for e in evidence if e["freshness"] == "stale"]
    findings = [{"kind": x.kind, "source": x.source, "state": x.state,
                 "value": x.value} for x in (result.findings or ())]
    return {
        "name": point_name,
        "purpose": _POINT_PURPOSES.get(point_name, point_name),
        "scope": result.scope,
        "judgment": result.judgment,
        "declared_method": declared_method,
        "diagnostic_explanation": result.diagnostic_explanation,
        "limitations": limitations,
        "evidence_gaps": gaps,
        "evidence": evidence,
        "findings": findings,
        "next_actions": actions,
        # Surfaced so staleness is OBSERVABLE (P-02 "stale"): the point's own
        # version binding is compared with the current runtime in state_meanings.
        # Empty when the result object does not carry one.
        "version_snapshot": (dict(result.version_snapshot)
                             if isinstance(getattr(result, "version_snapshot", None), Mapping)
                             else {}),
    }


def _render_authority_attribution(authority: Any) -> dict[str, Any] | None:
    """Render the authority pointer (P-03 row142) — id, basis, version binding.

    Deliberately does NOT copy the embedded structured result: the report must be
    able to point at the authority without becoming a second result container.
    """
    if authority is None:
        return None
    missing = [name for name in _AUTHORITY_SOURCE_ATTRS if not hasattr(authority, name)]
    if missing:
        raise TypeError(
            "authority must expose an acceptance_snapshot shape; missing: "
            + ", ".join(missing))

    snapshot_id = authority.snapshot_id
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("authority must expose a non-empty snapshot_id")
    if authority.stage_package is None:
        raise ValueError("authority must expose a stage_package (pre-snapshot input)")
    embedded = authority.structured_strength_result
    # row142's "exactly one embedded structured result": embedded (a mapping), not
    # a list of results, and not a reference. Checked — never copied.
    if not isinstance(embedded, Mapping):
        raise ValueError("authority structured_strength_result must be exactly one "
                         "embedded mapping (not a sequence and not a reference)")
    basis = authority.authority_basis
    if not isinstance(basis, Mapping) or set(basis) != _AUTHORITY_BASIS_KEYS:
        raise ValueError("authority must expose an authority_basis mapping")
    version = authority.version_snapshot
    if not isinstance(version, Mapping) or set(version) != _VERSION_KEYS:
        raise ValueError("authority must expose a five-field version_snapshot")

    attribution = {
        "container_kind": _AUTHORITY_CONTAINER_KIND,
        "snapshot_id": snapshot_id,
        "minimum_meaning": _AUTHORITY_MINIMUM_MEANING,
        "stage_package_role": "pre_snapshot_input",
        "embedded_result_count": 1,
        "authority_basis": _strict_plain(dict(basis)),
        "version_binding": _strict_plain(dict(version)),
        "not_this_report": True,
        "not_a_second_container": True,
    }
    if set(attribution) != _AUTHORITY_ATTRIBUTION_KEYS:  # pragma: no cover - invariant
        raise AssertionError("authority attribution key set drifted")
    return attribution


def _render_roles(roles: Any) -> list[dict[str, Any]]:
    """Render role records (P-03 row146): category + named actor/target/scope.

    Mirrors the role ledger's own closed-domain rules so the user-facing artifact
    cannot misreport a receipt as something else (row146's unacceptable
    "receipt called review"): ``receipted`` requires a ``receipt_kind`` from
    ``RECEIPT_KINDS``, every other role must NOT carry one, and ``target`` must
    use an authorized kind prefix.
    """
    if roles is None:
        return []
    if isinstance(roles, (str, bytes, Mapping)):
        raise TypeError("roles must be an iterable of role records")
    rendered: list[dict[str, Any]] = []
    for record in roles:
        role = getattr(record, "role", _MISSING)
        if role not in ROLES:
            raise ValueError(f"invalid role: {role!r} (expected one of {', '.join(ROLES)})")
        entry: dict[str, Any] = {"role": role}
        for field in ("actor", "target", "scope"):
            value = getattr(record, field, _MISSING)
            if not isinstance(value, str) or not value:
                raise ValueError(f"role record must expose a named {field}"
                                 + (" (attribute missing)" if value is _MISSING else ""))
            entry[field] = value
        target = entry["target"]
        if not target.startswith(TARGET_PREFIXES):
            raise ValueError(
                "role record target must name an authorized kind prefix "
                + " / ".join(TARGET_PREFIXES))
        receipt_kind = getattr(record, "receipt_kind", _MISSING)
        if receipt_kind is _MISSING:
            receipt_kind = None
        if role == "receipted":
            if receipt_kind not in RECEIPT_KINDS:
                raise ValueError(
                    "a receipted record requires receipt_kind in "
                    + " / ".join(RECEIPT_KINDS)
                    + " (a receipt is administrative only, never substantive review)")
        elif receipt_kind is not None:
            raise ValueError("receipt_kind is only valid for role 'receipted'")
        entry["receipt_kind"] = receipt_kind
        if set(entry) != _ROLE_ENTRY_KEYS:  # pragma: no cover - invariant
            raise AssertionError("role entry key set drifted")
        rendered.append(entry)
    return rendered


def _role_category_summary(rendered: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose which of the four role categories are present (review-fix S1)."""
    recorded = {category: 0 for category in _ROLE_CATEGORIES}
    for entry in rendered:
        recorded[entry["role"]] += 1
    return {
        "recorded": [c for c in _ROLE_CATEGORIES if recorded[c]],
        "not_recorded": [c for c in _ROLE_CATEGORIES if not recorded[c]],
        "counts": recorded,
    }


def _current_account_of(authority: Any) -> str | None:
    """The current account REFERENCE, when the report was given an authority.

    Uses the same account-reference form as earlier accounts and change records
    (``acceptance_snapshot:<snapshot_id>``) so the three can be compared.
    """
    if authority is None:
        return None
    snapshot_id = getattr(authority, "snapshot_id", None)
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return None
    # Normalise: accept an id that is already an account reference.
    if snapshot_id.startswith(ACCOUNT_PREFIX):
        return snapshot_id
    return f"{ACCOUNT_PREFIX}{snapshot_id}"


def _render_history(history: Any, current_account: str | None) -> dict[str, Any]:
    """Render history/change meaning (P-02 §6.4/§6.5, P-03 row147).

    Prior material is marked historical-only (with its original scope and time
    when supplied) and can never be presented as current; a recorded change shows
    the proposed/current difference and names the authority that must decide
    whether it is acceptable; with no change context supplied the report says
    exactly that instead of pretending there is no history.
    """
    section: dict[str, Any] = {
        "format": 1,
        "statement": _HISTORY_STATEMENT,
        "current_account": current_account,
        "earlier_accounts": [],
        "proposed_change": None,
        "history_supplied": history is not None,
        "history_statement": _NO_HISTORY_STATEMENT,
        "earlier_scope_time_note": None,
        "other_changes_note": None,
    }
    if history is None:
        return section

    # Fail closed on material that is not history context at all: an object that
    # exposes neither of the two history facets must not be silently rendered as
    # "no history supplied" (that would let an unrelated carrier — e.g. an outer
    # replay binding — pass itself off as an answered history question).
    if not hasattr(history, "earlier_accounts") and not hasattr(history, "proposed_change"):
        raise TypeError(
            "history must be a history/change context (exposing earlier_accounts "
            "and/or proposed_change); pass None when there is no history to report")

    earlier = getattr(history, "earlier_accounts", None)
    if earlier is None:
        earlier = ()
    if isinstance(earlier, (str, bytes, Mapping)):
        raise TypeError("history.earlier_accounts must be an iterable of account references")
    rendered_earlier = []
    missing_scope_time = False
    for item in earlier:
        if isinstance(item, Mapping) or hasattr(item, "account_ref"):
            ref = item.get("account_ref") if isinstance(item, Mapping) \
                else getattr(item, "account_ref", None)
            scope = item.get("scope") if isinstance(item, Mapping) \
                else getattr(item, "scope", None)
            time = item.get("time") if isinstance(item, Mapping) \
                else getattr(item, "time", None)
        elif isinstance(item, str):
            ref, scope, time = item, None, None
        else:
            raise TypeError(
                "history.earlier_accounts entries must be account references or mappings")
        if not isinstance(ref, str) or not ref.startswith(ACCOUNT_PREFIX):
            raise ValueError("history.earlier_accounts must be account references")
        if current_account is not None and ref == current_account:
            raise ValueError("an earlier account cannot also be the current account")
        if scope is None or time is None:
            missing_scope_time = True
        rendered_earlier.append({
            "account_ref": ref, "meaning": "historical_only",
            "scope": scope if isinstance(scope, str) and scope else None,
            "time": time if isinstance(time, str) and time else None,
        })
    section["earlier_accounts"] = rendered_earlier
    if missing_scope_time:
        section["earlier_scope_time_note"] = (
            "Original scope and time were not provided for some earlier accounts, "
            "so their original scope and time meaning cannot be shown here.")

    proposed = getattr(history, "proposed_change", None)
    if proposed is not None:
        section["proposed_change"] = _render_proposed_change(proposed, current_account)
        section["history_statement"] = _HISTORY_CHANGE_STATEMENT
        section["other_changes_note"] = _OTHER_CHANGES_NOTE
    elif rendered_earlier:
        section["history_statement"] = _HISTORY_PRIOR_STATEMENT
    else:
        section["history_statement"] = _HISTORY_EMPTY_STATEMENT
    return section


def _render_proposed_change(proposed: Any, current_account: str | None) -> dict[str, Any]:
    """Render one recorded change relative to the CURRENT account.

    The change must be about the account this report is about: its two sides must
    include the current account. Which side is current determines the state:

    - ``awaiting_human_decision`` when adjudication or escalation is required
      (fail-closed: the change may not be treated as settled);
    - ``realized`` when the change's later side IS the current account;
    - ``proposed`` when the current account is still the earlier side.

    The side that is not current is rendered as ``prior_account`` with
    ``historical_only`` meaning, so prior material is never presented as current.
    """
    if current_account is None:
        raise ValueError(
            "a recorded change requires a known current account; pass the authority "
            "so the change can be related to it")

    from_account = getattr(proposed, "from_account", _MISSING)
    to_account = getattr(proposed, "to_account", _MISSING)
    for label, value in (("from_account", from_account), ("to_account", to_account)):
        if not isinstance(value, str) or not value.startswith(ACCOUNT_PREFIX):
            raise ValueError(f"proposed change {label} must be an account reference")
    if from_account == to_account:
        raise ValueError("proposed change accounts must differ")
    if current_account not in (from_account, to_account):
        raise ValueError(
            "proposed change accounts must include the current account "
            f"({current_account})")

    classes = getattr(proposed, "change_classes", _MISSING)
    if isinstance(classes, str) or classes is _MISSING:
        raise ValueError("proposed change must carry change_classes")
    classes = list(classes)
    if not classes or any(c not in CHANGE_CLASSES for c in classes):
        raise ValueError("proposed change classes must come from the closed vocabulary")

    differences = getattr(proposed, "differences", _MISSING)
    if isinstance(differences, (str, bytes)) or differences is _MISSING:
        raise ValueError("proposed change must carry differences")
    rendered_differences = []
    for entry in differences:
        if not isinstance(entry, Mapping) or set(entry) != _DIFFERENCE_KEYS:
            raise ValueError("proposed change difference has an invalid shape")
        if entry["change_class"] not in CHANGE_CLASSES:
            raise ValueError("proposed change difference class is unknown")
        rendered_differences.append(_strict_plain(dict(entry)))
    if not rendered_differences:
        raise ValueError("proposed change must carry at least one difference")

    for field in ("reason", "submitter"):
        value = getattr(proposed, field, _MISSING)
        if not isinstance(value, str) or not value:
            raise ValueError(f"proposed change must expose a named {field}")

    requires_adjudication = getattr(proposed, "requires_adjudication", _MISSING)
    if not isinstance(requires_adjudication, bool):
        raise ValueError("proposed change requires_adjudication must be a boolean")
    authority = getattr(proposed, "adjudication_authority", None)
    if requires_adjudication:
        if not isinstance(authority, str) or not authority:
            raise ValueError(
                "proposed change must name the adjudication authority when adjudication is needed")
    elif authority is not None:
        raise ValueError("proposed change authority is only valid when adjudication is needed")

    escalation_required = getattr(proposed, "escalation_required", False)
    if not isinstance(escalation_required, bool):
        raise ValueError("proposed change escalation_required must be a boolean")
    escalation_reason = getattr(proposed, "escalation_reason", None)
    if escalation_required and escalation_reason not in ESCALATION_REASONS:
        raise ValueError("proposed change escalation reason must come from the closed list")
    if not escalation_required and escalation_reason is not None:
        raise ValueError("proposed change escalation reason is only valid when escalation is required")

    # State is derived from the change's relation to the CURRENT account:
    # fail-closed first (an unadjudicated or escalating change is not settled),
    # then realized (the later side IS the current account) vs proposed (the
    # current account is still the earlier side). The side that is not current is
    # rendered as prior/historical-only — never as current.
    if requires_adjudication or escalation_required:
        state = "awaiting_human_decision"
    elif to_account == current_account:
        state = "realized"
    else:
        state = "proposed"
    prior_account = to_account if current_account == from_account else from_account

    entry = {
        "from_account": from_account,
        "to_account": to_account,
        "state": state,
        "state_scope": _CHANGE_STATE_SCOPE,
        "current_account": current_account,
        "prior_account": prior_account,
        "prior_meaning": "historical_only",
        "change_classes": classes,
        "differences": rendered_differences,
        "reason": getattr(proposed, "reason"),
        "submitter": getattr(proposed, "submitter"),
        "requires_adjudication": requires_adjudication,
        "adjudication_authority": authority,
        "escalation_required": escalation_required,
        "escalation_reason": escalation_reason,
        # Honesty marker: the adjudication/escalation declaration comes from the
        # submitter; the system does not decide materiality (Spec N2/N3).
        "declaration_basis": _DECLARATION_BASIS,
    }
    if set(entry) != _PROPOSED_CHANGE_KEYS:  # pragma: no cover - invariant
        raise AssertionError("proposed change key set drifted")
    return entry


def _recorded_at_iso(value: Any) -> str | None:
    """A decision's write time as UTC ISO-8601, or None when unavailable.

    Accepts the stored string (the record's own timestamp, which survives copying)
    or a numeric file timestamp (the shape used before the time became a stored
    field, kept so older callers keep working).
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone  # noqa: PLC0415
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(
            timespec="seconds")
    return None


def _render_lane(lane: Any) -> dict[str, Any] | None:
    """The lane/release section: how far the account may be used, and why not further.

    P-03 row144 asks for an *existing lane-limit observation*. Before this, the
    report could only say "this is not a release decision" — a denial with nothing
    observable behind it. Given lane decisions, the report states the ceiling, the
    reasons the facts support no more, the restrictions, and what evidence would
    change it. ``None`` means no lane decision exists, which the report says
    plainly rather than implying a default lane.

    Decisions also carry the write time of their ledger line and are grouped by
    their declared scope, so several decisions for one account read as one picture
    instead of a flat list.
    """
    if lane is None:
        return None
    records = lane if isinstance(lane, (list, tuple)) else [lane]
    rendered: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        to_dict = getattr(record, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("lane must expose to_dict() (a LaneDecisionRecord)")
        payload = to_dict()
        if not isinstance(payload, Mapping):
            raise TypeError("lane record to_dict() must return a mapping")
        if "acceptance_snapshot_ref" not in payload or "decided_lane" not in payload:
            raise TypeError("lane record must carry acceptance_snapshot_ref and "
                            "decided_lane")
        # The line's write time travels as a runtime attribute, never as a schema
        # field (see dp_lane.LaneDecisionRecord.recorded_at for that boundary).
        recorded_at = _recorded_at_iso(getattr(record, "recorded_at", None))
        scope = str(payload.get("scope") or "(no scope stated)")
        entry = {
            "record_id": payload.get("record_id"),
            "acceptance_snapshot_ref": payload.get("acceptance_snapshot_ref"),
            "requested_lane": payload.get("requested_lane"),
            "decided_lane": payload.get("decided_lane"),
            "decision_outcome": payload.get("decision_outcome"),
            "scope": scope,
            "lane_ceiling": payload.get("lane_ceiling"),
            "ceiling_reason": payload.get("ceiling_reason"),
            "version_currency": payload.get("version_currency"),
            # the second freshness gate (C-04 §6 item 5): support material past the
            # window its submitter declared cannot prop up a promotion
            "evidence_stale_support": bool(payload.get("evidence_stale_support")),
            "recorded_at": recorded_at,
            "prohibited_uses": list(payload.get("prohibited_uses") or []),
            "required_next_evidence_refs": list(
                payload.get("required_next_evidence_refs") or []),
            "lane_predicate_evidence_refs": list(
                payload.get("lane_predicate_evidence_refs") or []),
            # read, never asserted by this report: a record cannot claim authority
            "authorising": bool(payload.get("authorising")),
        }
        rendered.append(entry)
        group = groups.setdefault(scope, {
            "scope": scope, "decision_count": 0, "record_ids": [],
            "decided_lanes": [], "latest_recorded_at": None})
        group["decision_count"] += 1
        group["record_ids"].append(entry["record_id"])
        if entry["decided_lane"] not in group["decided_lanes"]:
            group["decided_lanes"].append(entry["decided_lane"])
        latest = group["latest_recorded_at"]
        if recorded_at and (latest is None or recorded_at > latest):
            group["latest_recorded_at"] = recorded_at
    return {
        "decisions": rendered,
        "decision_count": len(rendered),
        "decisions_by_scope": list(groups.values()),
        "scope_count": len(groups),
        "statement": (
            "A lane is a governed use/release ceiling for the named scope. It is "
            "not a scientific result, not a score, not a ranking, and not "
            "authority for any use beyond the stated scope and restrictions."),
        "unreachable_lanes_note": (
            "`validated` and `production` are not reachable for this account: they "
            "require an authorised cross-axis ordering, and this workline "
            "authorises no cross-axis ordering (P-02 §5.2, C-04 §6). Absence of "
            "those lanes is NOT a statement that the account is weak."),
        "recorded_at_note": (
            "The time shown against a decision is when its line was written to the "
            "ledger file. It is NOT a decision field: a copied or restored ledger "
            "carries the copy time, and the record itself states no decision time."),
        "not_release_note": (
            "Recording a lane decision is not a release, not a deployment, not a "
            "promotion, and not a Package A closure."),
    }


def render_dp_report(register: Mapping[str, Any], *, authority: Any = None,
                     roles: Any = None, history: Any = None,
                     lane: Any = None, now: Any = None) -> dict[str, Any]:
    """Render the five-point register into an ordered explanatory report.

    Args:
        register: the view returned by ``audit_dp_register`` — five point-name
            keys mapping to ``DP01FilteringResult`` objects, plus
            ``_register_meta``.

    Returns:
        An ordered ``dict`` whose top-level key order follows the P-02 §6.2
        meaning order: context, five_decision_register, per_point,
        findings_and_limitations, bounded_use, lane_and_release, review_and_admin,
        history_and_change, next_actions, final_user_instruction, plus
        ``_report_meta``.

    Raises:
        TypeError: if ``register`` is not a mapping, or a stored point value is
            not a proper result object (missing required fields).
        ValueError: if ``_register_meta`` is missing or the register does not
            contain exactly the five decision points.

    ``now`` (optional keyword) sets the instant freshness is judged against. It
    defaults to the render moment; a caller that needs a reproducible judgement
    (a test, or a replay that must not drift with the wall clock) passes it in.
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
        raise ValueError("register must contain exactly the five points ("
                         + "; ".join(detail) + ")")

    first_result = register[_POINT_ORDER[0]]
    if not hasattr(first_result, "analysis_context"):
        raise TypeError("register point results must expose analysis_context")
    analysis_context = first_result.analysis_context or {}

    five_register_points = []
    per_point_records = []
    findings_categories = set()
    accumulated_actions = []
    # one instant for the whole report: every piece of evidence is judged against
    # the same moment, so freshness cannot disagree item by item within one account
    decision_moment = now if now is not None else _decision_moment()

    for name in _POINT_ORDER:
        result = register[name]
        record = _point_to_record(name, result, now=decision_moment)
        per_point_records.append(record)

        five_register_points.append({
            "name": name,
            "purpose": record["purpose"],
            "judgment": record["judgment"],
        })

        judgment = record["judgment"]
        if judgment in _INSUFFICIENCY_JUDGMENTS:
            findings_categories.add("scientific_insufficiency")
        if judgment in _INTEGRITY_JUDGMENTS:
            findings_categories.add("integrity_failure")
        for a in record["next_actions"]:
            accumulated_actions.append({"point": name, **a})

    findings_list = [{"category": c, "consequence": _category_detail(c)}
                     for c in sorted(findings_categories)]

    context_out = {key: analysis_context.get(key) for key in
                   ("project_id", "analysis_id", "comparison", "n_samples",
                    "data_type", "audit_scope", "intended_use")}
    # DP-COHERENCE (OPEN-1 B1): the declared group sample sizes travel in
    # analysis_context. The register freezes structured context values
    # (dp01_models._freeze -> mappingproxy), and callers write this report to JSON
    # (the report.json artifact), so this ONE new key is projected back to a plain
    # JSON value. Every pre-existing context key is untouched.
    _declared_n_samples = context_out.get("n_samples")
    if isinstance(_declared_n_samples, Mapping):
        context_out["n_samples"] = _strict_plain(dict(_declared_n_samples))
    # P-01 §7.1: expose input/version context from the result's 5-field snapshot.
    version_snapshot = getattr(first_result, "version_snapshot", None)
    context_out["input_version"] = (dict(version_snapshot)
                                    if isinstance(version_snapshot, Mapping) else {})
    # P-01 §7.1 explicit exclusions: what the caller declared must be surfaced,
    # not silently dropped. `analysis_context.exclusions` is optional; when it is
    # present it is shown verbatim, and the declared audit scope boundary is
    # always shown alongside it. When it is absent the report says so honestly
    # (no fabrication). Shape of this sub-object is unchanged — one key added only
    # when a value was actually declared.
    declared_exclusions = analysis_context.get("exclusions")
    context_out["exclusions"] = {
        "note": ("Declared exclusions are shown verbatim."
                 if declared_exclusions
                 else "No explicit exclusions were declared for this audit."),
        "declared_boundary": analysis_context.get("audit_scope"),
    }
    if declared_exclusions:
        context_out["exclusions"]["declared_exclusions"] = declared_exclusions

    # P-02 §6.2 step 5: expose the separate four-axis structured result meaning.
    # Combined-account, view-only, explanatory — pass-through from derive_dp_strength.
    strength = derive_dp_strength(register)
    missing_axes = [key for key in _REPORT_AXES if key not in strength]
    if missing_axes:
        raise ValueError(f"derived strength is missing axes: {', '.join(missing_axes)}")
    result_axes = {key: strength[key] for key in _REPORT_AXES}
    result_axes["_provenance"] = {
        "combined_account": True,
        "view_only": True,
        "not_second_authority": True,
    }

    # DP-COHERENCE: derived from the register's own declarations + analysis
    # context (pure, deterministic). See dp_coherence.derive_cross_step_coherence.
    cross_step_coherence = derive_cross_step_coherence(register, analysis_context)

    # P-02 §6.2 item 8 (review and administrative boundary): the declaration keeps
    # its wording; authority attribution (P-03 row142) and the role records
    # (P-03 row146 / T-01 L622) are rendered alongside it.
    rendered_roles = _render_roles(roles)
    review_and_admin = {
        "format": 2,
        "statement": _REVIEW_STATEMENT,
        "authority_attribution": _render_authority_attribution(authority),
        "roles": rendered_roles,
        "roles_supplied": roles is not None,
        "roles_recorded": bool(rendered_roles),
        "role_categories": _role_category_summary(rendered_roles),
        "roles_statement": ("Role records were provided to this report for this account."
                            if rendered_roles else _NO_ROLES_SUPPLIED_STATEMENT),
    }

    report: dict[str, Any] = {
        "_report_meta": _report_meta(register_meta),
        "context": context_out,
        "state_meanings": _derive_state_meanings(
            rendered_roles=rendered_roles,
            authority_attribution=review_and_admin["authority_attribution"],
            history=history,
            per_point_records=per_point_records,
            findings_list=findings_list,
            context_of_register=analysis_context,
            not_yet_available_input=_NOT_YET_AVAILABLE),
        "five_decision_register": {
            "points": five_register_points,
            "first_bounded_set": bool(register_meta.get("first_bounded_set")),
            "note": register_meta.get("note"),
            # P-03 row148 / P-02: the reader must be able to see that this is one
            # bounded part of a larger product, and what is not here yet.
            "not_yet_available": [dict(entry) for entry in _NOT_YET_AVAILABLE],
            "not_yet_available_note": (
                "These product capabilities are not implemented here and are NOT "
                "being claimed; against each one the current account is unavailable. "
                "This list is a boundary statement, not a plan, a roadmap, or a "
                "release/lane authority."),
        },
        "per_point": {"points": per_point_records, "result_axes": result_axes},
        # DP-COHERENCE: cross-step scientific coherence is DERIVED here, never
        # stored, and it is not part of any per-point judgment: it judges the
        # joins between steps (and three global self-consistencies), adds nothing
        # to findings, and cannot trigger a stop condition. Its three tokens are
        # locked and must never be combined into a total.
        "cross_step_coherence": cross_step_coherence,
        "findings_and_limitations": findings_list,
        "bounded_use": (
            "This report explains the authoritative account within the named "
            "project and analysis only. It is not a release decision, not a lane "
            "authority, and not a deployment decision."
        ),
        # P-03 row144: the existing lane-limit observation sits with bounded use,
        # because C-04 §6.1 makes the ceiling an extension of the use statement.
        "lane_and_release": _render_lane(lane),
        "review_and_admin": review_and_admin,
        # P-02 §6.4/§6.5 / P-03 row147: history and change meaning, structured.
        "history_and_change": _render_history(history, _current_account_of(authority)),
        "next_actions": accumulated_actions,
        "final_user_instruction": (
            "What can be relied on: each point's judgment as established by the "
            "authoritative account (not this explanatory report). "
            "What cannot be claimed: this report is not a second result "
            "authority, and no total score, ranking, lane, or confidence can be "
            "inferred from it. "
            "What must stop: where any required identity, evidence, integrity, "
            "or authority fact is unverified, reliance on the affected claim "
            "must stop and the affected material is isolated. "
            "What evidence or review is required next: the per-point evidence "
            "gaps and next actions above identify what supporting evidence or "
            "review is required before the account can be depended on. "
            "Whether human escalation is required: where a conflict, integrity "
            "failure, or not-auditable condition persists, the affected decision "
            "routes to the named human authority for adjudication. "
            + _stop_authority_phrase([entry["category"] for entry in findings_list])
        ),
    }
    return report


# Product capabilities this MVP deliberately does not provide yet (P-03 row148:
# "can a user tell which P-00 capabilities the MVP demonstrates and which remain
# unavailable?"). Every entry is taken from this workline's own records of what is
# not built / not claimed (HANDOFF.md "未做 / 未宣称", DP-JOURNEY-2 GAP_LIST), so
# the list states a boundary rather than inventing a roadmap.
# ---------------------------------------------------------------------------
# The P-02 §5 vocabulary itself, transcribed from the specification
# ---------------------------------------------------------------------------
# Why this exists: the capability line for "the full state vocabulary" used to
# carry hand-written counts, and two rounds of repair proved they cannot be kept
# true by hand. A fresh-context verifier found the line claiming three meanings
# were not expressible while the same report attested them; the repair that
# followed introduced a count no artifact could check. Both defects survived
# because nothing compared the prose against a transcript of the source list.
# This constant is that transcript, so the prose and the assertions read the same
# data instead of each other's echoes.
#
# Rules honoured here (they are governance boundaries, not style choices):
# - The keys are the specification's own labels, verbatim. This does NOT create a
#   naming style: renaming a code state word is a governance decision, so the
#   spelling differences between these labels and the code identifiers stay open
#   questions rather than being resolved here. There are FIVE (an adversarial
#   review caught the fifth, which this comment had under-recorded):
#   ``scientifically_limited`` vs ``scientifically insufficient`` (:419),
#   ``conflicted`` vs ``conflicting`` (:423), ``integrity_failed`` vs
#   ``integrity-failed`` (:424), ``historical_only`` vs ``historical-only``
#   (:425), and ``not_auditable`` vs ``not auditable`` (:420). The remaining
#   eight labels are character-for-character the specification's.
# - "Expressible" is reported in the two distinct senses the code actually
#   implements: as a point judgement value in ``DP01_JUDGMENTS`` (plus
#   ``historical_only`` on the history side), or as an attestation this report
#   derives (the ``attested`` group). Conflating the two is what produced the
#   earlier drift.
# - All 13 labels are §5 table rows in P-02 (:416-430); ``limited-use`` is also
#   the name of this code's ``attested`` entry, so the label matches both sides.
# - ``pending_attempt`` is NOT in this list, because P-02 never names it: it is a
#   judgement value this workline's own model defines (``dp01_models``
#   ``DP01_JUDGMENTS``). The two lists are cross-checked below instead of mixed.
_P02_SECTION_5_MEANINGS: tuple[dict[str, str], ...] = (
    {"label": "auditable", "expressed_by": "point_judgement"},
    {"label": "scientifically_limited", "expressed_by": "point_judgement"},
    {"label": "conflicted", "expressed_by": "point_judgement"},
    {"label": "not_auditable", "expressed_by": "point_judgement"},
    {"label": "integrity_failed", "expressed_by": "point_judgement"},
    {"label": "historical_only", "expressed_by": "history_side_judgement"},
    {"label": "reviewed", "expressed_by": "attestation"},
    {"label": "blocked", "expressed_by": "attestation"},
    {"label": "limited-use", "expressed_by": "attestation"},
    {"label": "stale", "expressed_by": "attestation"},
    {"label": "scoped", "expressed_by": "attestation"},
    {"label": "planned", "expressed_by": "attestation"},
    {"label": "external", "expressed_by": "not_expressible"},
)

# Judgement values this code defines that P-02 does not name. Kept apart from the
# transcript so the disclosure never credits the specification with a word it does
# not contain, while a new code-side word still has to be declared HERE.
_CODE_ONLY_JUDGEMENTS: tuple[str, ...] = ("pending_attempt",)

# Derived views. The capability line and its verifier both read THESE, so a
# disagreement can only come from the data, never from two hand-written lists.
_P02_EXPRESSED_BY: dict[str, str] = {
    entry["label"]: entry["expressed_by"] for entry in _P02_SECTION_5_MEANINGS}
_P02_EXPRESSIBLE_LABELS: tuple[str, ...] = tuple(
    entry["label"] for entry in _P02_SECTION_5_MEANINGS
    if entry["expressed_by"] != "not_expressible")
_P02_UNEXPRESSIBLE_LABELS: tuple[str, ...] = tuple(
    entry["label"] for entry in _P02_SECTION_5_MEANINGS
    if entry["expressed_by"] == "not_expressible")

# Structural guards. They RAISE rather than ``assert`` on purpose: an adversarial
# review showed that ``python -O`` strips assertions, and with them stripped a
# duplicated vocabulary row sailed through and the disclosure listed the same
# meaning twice. A guard whose job is to keep a public statement honest must not
# be switchable off.
if len(set(_P02_EXPRESSED_BY)) != len(_P02_SECTION_5_MEANINGS):
    raise RuntimeError("P-02 §5 vocabulary transcript has a duplicate label")
if len(_P02_UNEXPRESSIBLE_LABELS) != 1:
    raise RuntimeError(
        "exactly one P-02 §5 meaning is expected to be unexpressible, found "
        f"{len(_P02_UNEXPRESSIBLE_LABELS)}")
_unknown_kinds = set(_P02_EXPRESSED_BY.values()) - {"point_judgement",
                                                   "history_side_judgement",
                                                   "attestation",
                                                   "not_expressible"}
if _unknown_kinds:
    raise RuntimeError(
        f"the vocabulary records an expression kind the report does not "
        f"implement: {sorted(_unknown_kinds)}")


def _point_judgement_labels() -> tuple[str, ...]:
    """The transcript's labels that this code expresses as point judgements."""
    return tuple(label for label in _P02_EXPRESSIBLE_LABELS
                 if _P02_EXPRESSED_BY[label] == "point_judgement")


# Third guard: the transcript's point-judgement group plus the declared code-only
# words must BE the model's judgement set. This is the guard whose absence let a
# word the specification never names ride inside the disclosure as if it were a
# P-02 §5 meaning.
if set(_point_judgement_labels()).union(_CODE_ONLY_JUDGEMENTS) != set(DP01_JUDGMENTS):
    raise RuntimeError(
        "the P-02 §5 transcript's point judgements plus _CODE_ONLY_JUDGEMENTS must "
        "equal DP01_JUDGMENTS exactly — a new judgement value has to be declared as "
        "either a transcribed P-02 §5 meaning or a code-only word")


def _vocabulary_group_line(labels: tuple[str, ...], clause: str) -> str:
    """A machine-checkable group listing: ``WORD, WORD and WORD <clause>``.

    The verifier splits on this shape, so the wording stays ordinary prose while
    the boundary of each group remains unambiguous.
    """
    if len(labels) == 1:
        return f"{labels[0]} {clause}"
    return f"{', '.join(labels[:-1])} and {labels[-1]} {clause}"


def _capability_status_for_state_vocabulary() -> str:
    """The honest status of "the full state vocabulary", derived from the data.

    Every count below is computed; none is typed by hand. The two expression
    senses are stated separately because they mean different things to a reader
    (a judgement value the point carries vs. a meaning this report attests), and
    the unexpressible group is named last with its reason.
    """
    point = tuple(label for label in _P02_EXPRESSIBLE_LABELS
                  if _P02_EXPRESSED_BY[label] == "point_judgement")
    history = tuple(label for label in _P02_EXPRESSIBLE_LABELS
                    if _P02_EXPRESSED_BY[label] == "history_side_judgement")
    attested = tuple(label for label in _P02_EXPRESSIBLE_LABELS
                     if _P02_EXPRESSED_BY[label] == "attestation")
    return (
        f"partially implemented: P-02 §5 lists "
        f"{len(_P02_SECTION_5_MEANINGS)} state and consequence meanings; "
        f"{_vocabulary_group_line(point, 'are expressible as point judgements')}, "
        f"{_vocabulary_group_line(history, 'on the history side')}, and "
        f"{_vocabulary_group_line(attested, 'are attested from facts this report holds')} "
        f"— {len(_P02_EXPRESSIBLE_LABELS)} of "
        f"{len(_P02_SECTION_5_MEANINGS)} in total, and "
        f"{_vocabulary_group_line(_P02_UNEXPRESSIBLE_LABELS, 'is not expressible')}, "
        f"because no input carries an outside-the-artifact physical observation; "
        f"see `state_meanings.unexpressed_states` for its reason and what would "
        f"have to change for it.")


_NOT_YET_AVAILABLE: tuple[dict[str, str], ...] = (
    {"capability": "no release machinery / no lane grant",
     "status": ("partially implemented: no lane is granted and no release authority "
                "exists, but a use/release ceiling can be recorded and rendered with "
                "its evidence, restrictions and required next evidence (see "
                "lane_and_release). This is not release machinery: no automatic "
                "promotion, transition, rollback or deployment; the decision caps use "
                "and never authorises it by itself")},
    {"capability": "deployment and Package A closure",
     "status": "not implemented and not claimed"},
    {"capability": "runtime proof / execution evidence",
     "status": "not implemented and not claimed"},
    {"capability": "independent reviewer approval",
     "status": "not performed: the system records roles, it does not conduct review"},
    {"capability": "additional decision points beyond the five",
     "status": "not implemented: five RNA-seq points only"},
    {"capability": "other analysis types beyond bulk RNA-seq",
     "status": "not implemented"},
    {"capability": "evidence provenance / freshness fields",
     "status": ("partially implemented: every evidence item now carries its "
                "observation time, the validity window its submitter declared, an "
                "optional provenance and subject, the claim it is offered for, and "
                "its role; the report derives and shows whether each item is still "
                "current. NOT implemented: per-item authority permission (who may "
                "rely on it), and any judgement of whether the declared provenance "
                "is genuine — the system records the declaration and does not "
                "adjudicate it")},
    {"capability": "the full state vocabulary",
     "status": _capability_status_for_state_vocabulary()},
    {"capability": "a formal report for showing to others",
     "status": ("not available: an inspector page for the accountable owner exists "
                "(ui/report.html) and renders this report for reading, but a "
                "user-facing product surface and any external-facing presentation "
                "remain undecided product decisions (P-03 §14)")},
)


# P-02 §5 state vocabulary that this system CANNOT express, with the mechanical
# reason and what would have to change. Recorded here (and surfaced in the report)
# instead of inventing state words: adding a public status/reason domain is a
# governance decision, and a word with no derivable trigger would be fabrication.
_UNEXPRESSED_STATES: tuple[dict[str, str], ...] = (
    {"state": "external",
     "reason": ("the current data model has no outside-the-artifact physical "
                "observation to describe: nothing in an audit carries a byte- or "
                "encoding-level observation made outside the artifact"),
     "what_would_change_it": ("an authorized input for external physical observations "
                              "(a new input contract, hence a governance decision)")},
)

# The P-02 §5 vocabulary transcript and the capability wording derived from it
# are defined ABOVE the ``_NOT_YET_AVAILABLE`` table (search for
# ``_P02_SECTION_5_MEANINGS``), because that table's state-vocabulary entry
# quotes the derived text at import time.


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a mapping or an attribute-bearing record."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _derive_state_meanings(*, rendered_roles: list[dict[str, Any]],
                           authority_attribution: dict[str, Any] | None,
                           history: Any, per_point_records: list[dict[str, Any]],
                           findings_list: list[dict[str, Any]],
                           context_of_register: Any = None,
                           not_yet_available_input: Any = None) -> dict[str, Any]:
    """The P-02 §5 state meanings this report can attest MECHANICALLY.

    Each attestation is either triggered by facts the report already holds, or
    explicitly absent. No word is emitted as a bare label: every present entry
    carries its reason and what it requires next (P-02 §5.1).

    Implemented here: ``reviewed``, ``blocked``, ``limited-use``, ``stale``,
    ``scoped`` and ``planned`` (the attested group of
    ``_P02_SECTION_5_MEANINGS``). See ``_UNEXPRESSED_STATES`` for the single
    meaning this system cannot express and why.
    """
    # Resolved once, up front: the ``planned`` attestation below reads this input,
    # so leaving the default resolved at the end would crash when a caller omits
    # it (the production caller always passes it).
    not_yet_available_input = not_yet_available_input or _NOT_YET_AVAILABLE
    attested: dict[str, dict[str, Any]] = {}

    # --- reviewed: a named reviewer record for a named target/scope ----------
    reviewers = [r for r in rendered_roles if r.get("role") == "reviewed"]
    if reviewers:
        attested["reviewed"] = {
            "state": "reviewed",
            "reason": "a named reviewer record is present for a named target and scope",
            "named": [{"actor": r.get("actor"), "target": r.get("target"),
                       "scope": r.get("scope")} for r in reviewers],
            "may_be_relied_on": "that review's bounded conclusion only",
            "must_not_be_inferred": "global approval, implementation proof, or closure",
            "next_requirement": "who reviewed what, and what the review excluded",
        }
    else:
        attested["reviewed"] = {
            "state": "reviewed",
            "reason": "NO reviewer record was supplied to this report",
            "may_be_relied_on": "nothing on this basis: nothing was reviewed here",
            "must_not_be_inferred": "that a review happened outside this report's view",
            "next_requirement": "supply a named reviewer record for a named target/scope",
        }

    # --- blocked: a required dependency/review/decision stops the claim ------
    blockers: list[dict[str, Any]] = []
    proposed = getattr(history, "proposed_change", None)
    if proposed is not None:
        if _field(proposed, "requires_adjudication") or _field(proposed, "escalation_required"):
            blockers.append({
                "kind": "change awaiting adjudication/escalation",
                "detail": f"change requires adjudication; authority: "
                          f"{_field(proposed, 'adjudication_authority') or 'not named'}",
                "released_by": _field(proposed, "adjudication_authority")
                               or "the named adjudication authority",
            })
    for record in per_point_records:
        for action in record.get("next_actions") or []:
            if action.get("action") in {"stop", "request_review"}:
                blockers.append({
                    "kind": f"{action.get('action')} on {record.get('name')}",
                    "detail": action.get("reason"),
                    "released_by": ("the named human authority "
                                    "(scientific review for scientific shortfalls, "
                                    "governance for unverifiable facts)"),
                })
    if blockers:
        attested["blocked"] = {
            "state": "blocked",
            "reason": "a required decision, review or fact prevents the affected claim "
                      "from proceeding",
            "blockers": blockers,
            "may_be_relied_on": "the stopping reason and the named next authority",
            "must_not_be_inferred": "silence as approval, or permission to substitute",
            "next_requirement": "what decision or evidence releases the block",
        }
    else:
        attested["blocked"] = {
            "state": "blocked",
            "reason": "no required decision, review or fact is currently pending",
            "may_be_relied_on": "this account's stated conclusions within their scope",
            "must_not_be_inferred": "that a future block cannot arise",
            "next_requirement": "nothing outstanding was observed",
        }

    # --- limited-use: the account's own basis for authorization is unconfirmed
    basis = (authority_attribution or {}).get("authority_basis") or {}
    if authority_attribution and not basis.get("confirmed"):
        unconfirmed = basis.get("unconfirmed_domains") or []
        attested["limited-use"] = {
            "state": "limited-use",
            "reason": ("the authority basis is NOT confirmed "
                       f"({len(unconfirmed)} of the integrity domains unconfirmed: "
                       + ", ".join(unconfirmed) + ")"),
            "ceiling": ("bounded review use only: the account may be read and explained, "
                        "but reliance is limited to the stated scope until the "
                        "unconfirmed domains are resolved"),
            "may_be_relied_on": "the stated scope and the fact that the basis is unconfirmed",
            "must_not_be_inferred": ("scientific truth, unrestricted use, deployment, or "
                                     "closure — and note this ceiling comes from the "
                                     "unconfirmed basis, NOT from a release/lane decision"),
            "next_requirement": "resolve the unconfirmed integrity domains",
        }
    elif authority_attribution:
        attested["limited-use"] = {
            "state": "limited-use",
            "reason": "no separate governed use/release ceiling is recorded here",
            "may_be_relied_on": "the account's stated scope only",
            "must_not_be_inferred": ("that a release or lane decision exists — this "
                                     "system grants none"),
            "next_requirement": "a governance decision, which this report cannot make",
        }
    else:
        attested["limited-use"] = {
            "state": "limited-use",
            "reason": "no authority container was supplied to this report",
            "may_be_relied_on": "nothing about release or use ceilings",
            "must_not_be_inferred": "any release or lane authority",
            "next_requirement": "supply the authority container the account belongs to",
        }

    # --- integrity-failed / not-auditable visibility (already judgement-level)
    if any(entry.get("category") == "integrity_failure" for entry in findings_list):
        attested["integrity-failed"] = {
            "state": "integrity-failed",
            "reason": "at least one point reports a fact that could not be verified",
            "may_be_relied_on": "the unavailability and the containment consequence",
            "must_not_be_inferred": "ordinary scientific weakness, or automatic downgrade",
            "next_requirement": "which integrity/authority fact failed, and what must stop",
        }

    # --- stale: a point was recorded under a different version binding --------
    # Mechanical: compare each point's recorded five-field version snapshot with
    # the CURRENT runtime version. This is what makes staleness OBSERVABLE without
    # weakening the ledger's fail-closed read-back (a mismatch is still rejected
    # there; here the report simply reports the fact instead of staying silent).
    current = dict(_current_version_snapshot())
    stale_points: list[dict[str, Any]] = []
    for record in per_point_records:
        recorded = record.get("version_snapshot")
        if not isinstance(recorded, Mapping):
            continue
        differing = {k: {"recorded": recorded.get(k), "current": current.get(k)}
                     for k in sorted(set(recorded) | set(current))
                     if recorded.get(k) != current.get(k)}
        if differing:
            stale_points.append({"point": record.get("name"), "differing": differing})
    if stale_points:
        attested["stale"] = {
            "state": "stale",
            "reason": ("at least one point was recorded under a different version "
                       "binding than the current runtime"),
            "points": stale_points,
            "may_be_relied_on": ("historical meaning within that point's FORMER scope "
                                 "and time only"),
            "must_not_be_inferred": "current validity",
            "next_requirement": "a current observation or decision under the current version",
        }
    else:
        attested["stale"] = {
            "state": "stale",
            "reason": "every point's version binding matches the current runtime",
            "may_be_relied_on": "the account within its stated scope",
            "must_not_be_inferred": "that a future runtime change cannot make it stale",
            "next_requirement": "nothing: re-checked against the current runtime",
        }

    # --- scoped: the account is bounded to a named boundary ------------------
    # A STATEMENT with consequences, deliberately distinct from the per-point
    # `scope` field (which is a position label): here the boundary is named and the
    # consequence is stated — conclusions hold inside it and are not global clearance.
    context = context_of_register if isinstance(context_of_register, Mapping) else {}
    named_boundary = {k: context.get(k) for k in
                      ("project_id", "analysis_id", "comparison", "audit_scope",
                       "intended_use") if context.get(k)}
    if named_boundary:
        attested["scoped"] = {
            "state": "scoped",
            "reason": ("this account is bounded to a named project/analysis/comparison/"
                       "scope/use boundary"),
            "named_boundary": named_boundary,
            "may_be_relied_on": "the conclusion within exactly that boundary",
            "must_not_be_inferred": ("global product, phase or package clearance; or that "
                                     "the same conclusion holds for another object"),
            "next_requirement": ("what boundary is named, and whether expansion is "
                                 "separately authorized"),
        }

    # --- planned: report-level intent, never an account state ----------------
    planned_entries = [entry for entry in not_yet_available_input if entry.get("capability")]
    if planned_entries:
        attested["planned"] = {
            "state": "planned",
            "reason": ("the report lists future capabilities that are described but not "
                       "built — REPORT-LEVEL intent, not a state of this account"),
            "planned_capabilities": [dict(entry) for entry in planned_entries],
            "may_be_relied_on": "awareness of future intent or need",
            "must_not_be_inferred": ("implementation, evidence, proof, deployment or "
                                     "closure; and never as this account's outcome"),
            "next_requirement": "is this only a plan, or is separate evidence present?",
        }
    else:
        attested["planned"] = {
            "state": "planned",
            "reason": "no future capability is described in this artifact",
            "may_be_relied_on": "nothing about future intent",
            "must_not_be_inferred": "that no future work is planned elsewhere",
            "next_requirement": "nothing described here",
        }

    not_yet_available_input = not_yet_available_input or _NOT_YET_AVAILABLE
    return {
        "format": 1,
        "statement": ("These are the P-02 §5 state meanings this report can attest from "
                      "facts it holds. Each entry states its reason and what it "
                      "requires; `unexpressed_states` lists the meanings this system "
                      "cannot express and what would have to change."),
        "attested": attested,
        "unexpressed_states": [dict(entry) for entry in _UNEXPRESSED_STATES],
        "not_yet_available": [dict(entry) for entry in not_yet_available_input],
    }


def _category_detail(category: str) -> str:
    if category == "scientific_insufficiency":
        return ("Point is supportable only as scientifically limited or conflicting; "
                "the evidence available does not support the named claim. (This is "
                "NOT the same as a fact that could not be verified — see "
                "integrity_failure.)")
    if category == "integrity_failure":
        return ("A required fact could not be verified — identity, source, version, "
                "binding, snapshot, permission, authority, or the critical context "
                "and declaration provenance a point needs to be auditable at all; "
                "the affected material is isolated.")
    return ""


# Which human authority a stop routes to, by finding category (the ONLY two the
# report can derive today). Naming the category is required for P-03 row150's
# "that names what must stop, why, the consequence, the next evidence, and WHO
# OWNS IT" — a generic "the named human authority" satisfies the first four and
# not the fifth.
_STOP_AUTHORITY_BY_CATEGORY = {
    "integrity_failure": "governance authority",
    "scientific_insufficiency": "scientific review authority",
}


def _stop_authority_phrase(finding_categories: list[str]) -> str:
    """Name the authority category the affected decisions route to (or say none)."""
    owners = [_STOP_AUTHORITY_BY_CATEGORY[category]
              for category in finding_categories
              if category in _STOP_AUTHORITY_BY_CATEGORY]
    if not owners:
        return ("No stop or escalation is currently required for this account, so "
                "no authority category needs to be named.")
    listed = " and ".join(owners)
    return (f"Where the affected decision must stop, it routes to the {listed} for "
            "adjudication; the accountable role category is named here rather than "
            "left unspecified.")


__all__ = ["render_dp_report"]

