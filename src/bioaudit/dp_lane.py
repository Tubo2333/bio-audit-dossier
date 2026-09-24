"""DP-LANE — release/lane decision record (C-04 semantics, narrow first slice).

WHAT THIS IS
------------
C-04 defines a governed *use/release ceiling* — a lane — that caps what a result
may be used or released for, and it fixes the closed lane domain, the decision
concepts a decision record carries, a static ceiling table, and fail-closed
consequences. It explicitly does NOT define an evaluator, release machinery,
transitions, replay, or persistence.

This module implements the narrow first slice of that: one append-only, immutable
decision record per ``record_id``, whose lane is *derived from facts the audit
already holds* and which fails closed whenever the facts do not support the
requested lane.

WHAT THIS IS NOT
----------------
- Not release machinery: nothing here promotes, transitions, deploys or releases.
- Not a scientific score: a lane caps use; it never states truth or strength.
- Not a new lane vocabulary: the four values are the existing closed domain.
- Not a new authority path: it consumes the acceptance snapshot by *identifier*
  only (``acceptance_snapshot_ref``), never a result carrier.

STORED FACTS vs DERIVED CONCLUSIONS
-----------------------------------
An adversarial review found that the first version stored the *conclusion* it had
derived (``lane_ceiling``, ``ceiling_reason``, ``version_currency``, ``authorising``)
and trusted it on read, so one hand-written ledger line could render an authorising
``production`` decision while the same report said that lane was unreachable. The
record now stores **facts only**:

- the five-field version binding (the freshness input),
- ``axes_auditable`` / ``residual_unordered`` / ``has_restrictions`` (the four-axis
  facts this build can observe),
- ``decided_lane`` / ``decision_outcome`` / scope / references,
- ``recorded_at``: when the line was written.

Everything else is a **property derived on read** from those facts plus the current
runtime, and a stored lane above what the facts support makes the read fail closed.

WHY THE CEILING IS DERIVED, NOT DECLARED
----------------------------------------
C-04's ceiling table is stated in terms of the result's kind (``axis_vector``,
``conditional_ordered_result``, relation ``incomparable``). This workline's data
model has no result-kind field, and adding one would be a new public field, which is
a governance decision. So the ceiling is *computed* from the four-axis result the
report already carries. ``validated`` and ``production`` are therefore unreachable
in this build, because they require an authorised cross-axis ordering that does not
exist here — recorded as an explicit reason rather than silently implied.

FRESHNESS
---------
C-04 requires lane predicates to cover freshness facts. This module reuses the
project's single existing freshness notion — the frozen five-field version binding
that every ledger record already carries — instead of inventing a second,
calendar-based one. Unlike the other ledgers, a version mismatch here does NOT
invalidate the record: it is recorded as ``version_currency: stale`` and caps the
lane, because a decision taken under an older runtime remains an honest record of
that decision. The mismatch is therefore an *observed predicate*, not a durability
failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from bioaudit.dp01_store import _current_version_snapshot, _plain
from bioaudit.dp_ledger import JSONLLedger

# ---------------------------------------------------------------------------
# Closed domains (C-04 §4, §5, §10) — inherited verbatim, never extended here
# ---------------------------------------------------------------------------

#: The existing closed lane domain. C-04 forbids renaming, supplementing,
#: subdividing or replacing it with product-side terms.
DP_LANES: tuple[str, ...] = ("research-draft", "candidate", "validated", "production")

#: C-04 §5 decision outcomes.
DP_LANE_OUTCOMES: tuple[str, ...] = ("accepted", "rejected", "return_for_evidence",
                                    "withdrawn")

#: Outcome that fixes the decided lane to a non-authorising return state (C-04 §8.8).
_RETURN_FIXED_LANE = "research-draft"

#: C-04 §11: labels that must never be treated as lane authority or scientific truth.
_PROHIBITED_KEYS = ("score", "total_score", "overall_score", "confidence", "ranking",
                    "winner", "quality", "strength", "scientific_truth")

#: Stored decision content.
_REQUIRED_DECISION = ("record_id", "acceptance_snapshot_ref", "requested_lane",
                      "decided_lane", "decision_outcome", "scope", "recorded_at")
#: Stored facts the decision carries (the input side of the derivation).
_REQUIRED_FACTS = ("version_snapshot", "axes_auditable", "residual_unordered",
                   "has_restrictions", "evidence_stale_support")
_REQUIRED = _REQUIRED_DECISION + _REQUIRED_FACTS
_ALLOWED = _REQUIRED + ("lane_predicate_evidence_refs", "prohibited_uses",
                        "required_next_evidence_refs", "resubmission_id", "note")


def _lane_rank(lane: str) -> int:
    return DP_LANES.index(lane)


def lane_ceiling(*, integrity_ok: bool, version_currency: str,
                 axes_auditable: bool, residual_unordered: bool,
                 has_restrictions: bool, evidence_stale_support: bool = False
                 ) -> tuple[str, str]:
    """The highest lane the evidenced facts support, plus the reason.

    Reads like C-04 §6's ceiling table, narrowed to the result shapes this build can
    produce. Every branch states a ceiling and why; none of them guesses, repairs or
    fills a gap (C-04 §9).

    The ceiling has TWO independent hard gates, because C-04 §6 requires the lane
    predicates to cover freshness facts and there are two different freshness facts:

    - ``version_currency``: the runtime the decision was taken under (did the rules,
      engine, ontology or input format move on?);
    - ``evidence_stale_support``: whether the material offered to SUPPORT this
      decision is still inside the validity window its submitter declared. An
      expired limitation, conflict or historical item says nothing about support, so
      it does not gate the ceiling — the report names it instead.
    """
    if not integrity_ok:
        return ("research-draft",
                "required integrity/authority facts are not auditable, so no valid "
                "release or lane conclusion exists (C-04 §6 row 1)")
    if version_currency != "current":
        return ("research-draft",
                "the lane predicate rests on a version binding that no longer "
                "matches the current runtime, so no promotion is supportable "
                "(C-04 §9, stale dependency)")
    if evidence_stale_support:
        return ("research-draft",
                "the evidence offered to support this decision is past the validity "
                "window its submitter declared, so nothing here actively supports a "
                "promotion; supply current supporting evidence, or record the honest "
                "outcome (C-04 §6 item 5: freshness facts are required "
                "dependencies)")
    if not axes_auditable:
        # a required axis fact is missing: an integrity-class failure, which C-04 §6
        # forbids reinterpreting as scientific insufficiency
        return ("research-draft",
                "a required axis fact is not auditable; an integrity-class gap must "
                "not be reinterpreted as scientific insufficiency (C-04 §6 row 2)")
    # From here the structured result is auditable. Without an authorised cross-axis
    # ordering the ceiling is `candidate` — completeness alone never authorises more.
    reason = ("the auditable structured result is a cross-axis vector with no "
              "authorised cross-axis ordering, so at most `candidate` is supported "
              "(C-04 §6 row 3)")
    if residual_unordered:
        reason = ("the auditable structured result retains residual unordered axes, "
                  "so at most a restricted `candidate` is supported (C-04 §6 row 5)")
    if has_restrictions:
        reason = ("the auditable structured result carries explicit limitations or "
                  "prohibited uses, so at most a restricted `candidate` is supported "
                  "(C-04 §6 row 4)")
    return ("candidate", reason)


def _version_currency(recorded: Mapping[str, Any],
                      current: Mapping[str, Any] | None = None) -> str:
    """Whether a recorded version binding still matches the current runtime.

    ``current`` is injectable so a caller (or test) can pin the runtime instead of
    the process-wide snapshot; it is never stored.
    """
    current = dict(current if current is not None else _current_version_snapshot())
    if set(recorded) != set(current):
        return "stale"
    return "current" if all(recorded[k] == current[k] for k in current) else "stale"


def _derive_from_facts(payload: Mapping[str, Any],
                       *, current_runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Recompute every conclusion from the stored facts, or fail closed.

    The single place a lane conclusion is produced — used both when a decision is
    written and when a ledger line is read. A line recording a lane above what its
    facts support cannot be read at all.
    """
    version = payload.get("version_snapshot")
    if not isinstance(version, Mapping) or not version:
        raise ValueError("invalid lane record: version_snapshot must be the runtime "
                         "binding the decision was taken under")
    currency = _version_currency(version, current_runtime)
    ceiling, reason = lane_ceiling(
        integrity_ok=True,
        version_currency=currency,
        axes_auditable=bool(payload["axes_auditable"]),
        residual_unordered=bool(payload["residual_unordered"]),
        has_restrictions=bool(payload["has_restrictions"]),
        evidence_stale_support=bool(payload["evidence_stale_support"]))
    decided = str(payload["decided_lane"])
    outcome = str(payload["decision_outcome"])
    if _lane_rank(decided) > _lane_rank(ceiling):
        raise ValueError(
            f"lane record is not auditable against its own facts: it records "
            f"decided_lane={decided!r} while its stored facts support at most "
            f"{ceiling!r} ({reason}) — the condition C-04 §6 rows 1-2 forbid from "
            f"being recorded as an authorising lane")
    if outcome == "accepted" and ceiling == _RETURN_FIXED_LANE:
        raise ValueError(
            "no valid release/lane conclusion exists for this record's facts, so no "
            "outcome may record an authorising lane; use decision_outcome="
            "'rejected' or 'return_for_evidence' (C-04 §6 rows 1-2, §9)")
    return {
        "lane_ceiling": ceiling,
        "ceiling_reason": reason,
        "version_currency": currency,
        "authorising": bool(outcome == "accepted"
                            and decided != _RETURN_FIXED_LANE
                            and currency == "current"
                            and not payload["evidence_stale_support"]
                            and _lane_rank(decided) <= _lane_rank(ceiling)),
    }


@dataclass(frozen=True)
class LaneDecisionRecord:
    """One immutable lane decision: stored facts, conclusions derived on read.

    ``authorising`` is never true by declaration: it holds only when the outcome is
    ``accepted``, the decided lane is above the return state, the runtime binding is
    current, and the decided lane is within the ceiling recomputed from the stored
    facts. A record can therefore never *claim* to authorise; it can only be read as
    authorising.
    """

    record_id: str
    acceptance_snapshot_ref: str
    requested_lane: str
    decided_lane: str
    decision_outcome: str
    scope: str
    version_snapshot: Mapping[str, Any]
    axes_auditable: bool
    residual_unordered: bool
    has_restrictions: bool
    #: Whether the material offered to SUPPORT this decision is past the validity
    #: window its submitter declared. Stored as a fact — computed by the write path
    #: from the evidence it was handed — and never derived on read, because the
    #: evidence itself lives in the account ledger, not in this file.
    evidence_stale_support: bool
    #: When the line was written (UTC ISO-8601). Stored, so it survives copying —
    #: unlike a file mtime, which becomes the copy time.
    recorded_at: str
    lane_predicate_evidence_refs: tuple[str, ...] = ()
    prohibited_uses: tuple[str, ...] = ()
    required_next_evidence_refs: tuple[str, ...] = ()
    resubmission_id: str | None = None
    note: str | None = None
    _prohibited: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_snapshot",
                           MappingProxyType(dict(self.version_snapshot)))
        for name in ("lane_predicate_evidence_refs", "prohibited_uses",
                     "required_next_evidence_refs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "_prohibited", tuple(_PROHIBITED_KEYS))

    # -- derived on read, never stored ---------------------------------------

    def _derived(self) -> dict[str, Any]:
        return _derive_from_facts({
            "version_snapshot": dict(self.version_snapshot),
            "axes_auditable": self.axes_auditable,
            "residual_unordered": self.residual_unordered,
            "has_restrictions": self.has_restrictions,
            "evidence_stale_support": self.evidence_stale_support,
            "decided_lane": self.decided_lane,
            "decision_outcome": self.decision_outcome,
        })

    @property
    def lane_ceiling(self) -> str:
        return self._derived()["lane_ceiling"]

    @property
    def ceiling_reason(self) -> str:
        return self._derived()["ceiling_reason"]

    @property
    def version_currency(self) -> str:
        return self._derived()["version_currency"]

    @property
    def authorising(self) -> bool:
        return self._derived()["authorising"]

    def to_stored(self) -> dict[str, Any]:
        """Exactly what goes on disk: facts and decision content, no conclusions."""
        return {
            "record_id": self.record_id,
            "acceptance_snapshot_ref": self.acceptance_snapshot_ref,
            "requested_lane": self.requested_lane,
            "decided_lane": self.decided_lane,
            "decision_outcome": self.decision_outcome,
            "scope": self.scope,
            "version_snapshot": _plain(dict(self.version_snapshot)),
            "axes_auditable": bool(self.axes_auditable),
            "residual_unordered": bool(self.residual_unordered),
            "has_restrictions": bool(self.has_restrictions),
            "evidence_stale_support": bool(self.evidence_stale_support),
            "recorded_at": self.recorded_at,
            "lane_predicate_evidence_refs": list(self.lane_predicate_evidence_refs),
            "prohibited_uses": list(self.prohibited_uses),
            "required_next_evidence_refs": list(self.required_next_evidence_refs),
            "resubmission_id": self.resubmission_id,
            "note": self.note,
        }

    def to_dict(self) -> dict[str, Any]:
        """Stored facts plus the derived conclusions, for reporting only."""
        payload = self.to_stored()
        payload.update(self._derived())
        return payload


def _validate_payload(payload: Any) -> None:
    """Closed-schema validation for one lane-decision record (read-back enforced).

    Besides the schema it recomputes the ceiling from the stored facts: a line whose
    lane exceeds them is rejected here, which is what stops the read path serving a
    forged top lane.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("invalid lane record: not a mapping")
    missing = [key for key in _REQUIRED if key not in payload]
    if missing:
        raise ValueError(f"invalid lane record: missing {sorted(missing)}")
    unknown = sorted(set(payload) - set(_ALLOWED))
    if unknown:
        raise ValueError(f"invalid lane record: unknown {unknown}")
    for key in ("requested_lane", "decided_lane"):
        if payload[key] not in DP_LANES:
            raise ValueError(f"invalid lane record: {key} outside the closed lane "
                             f"domain: {payload[key]!r}")
    if payload["decision_outcome"] not in DP_LANE_OUTCOMES:
        raise ValueError("invalid lane record: unknown decision outcome "
                         f"{payload['decision_outcome']!r}")
    for key in _REQUIRED_FACTS[1:]:
        if not isinstance(payload[key], bool):
            raise ValueError(f"invalid lane record: {key} must be a boolean fact")
    for key in ("lane_predicate_evidence_refs", "prohibited_uses",
                "required_next_evidence_refs"):
        value = payload.get(key, [])
        if not isinstance(value, (list, tuple)) or not all(isinstance(x, str)
                                                           for x in value):
            raise ValueError(f"invalid lane record: {key} must be strings")
    if payload["decision_outcome"] == "return_for_evidence":
        if not payload.get("required_next_evidence_refs"):
            raise ValueError("invalid lane record: return_for_evidence requires "
                             "non-empty required_next_evidence_refs (C-04 §8.8)")
        if payload["decided_lane"] != _RETURN_FIXED_LANE:
            raise ValueError("invalid lane record: return_for_evidence fixes "
                             "decided_lane=research-draft (C-04 §8.8)")
        if not payload.get("resubmission_id"):
            raise ValueError("invalid lane record: return_for_evidence requires a "
                             "new resubmission_id (C-04 §8.8)")
    stamp = payload["recorded_at"]
    if not isinstance(stamp, str) or not stamp:
        raise ValueError("invalid lane record: recorded_at must be a timestamp string")
    # the derivation: raises when the stored lane exceeds what the facts support
    _derive_from_facts(payload)


class DPLaneStore(JSONLLedger):
    """Append-only JSONL ledger of lane decisions (own file, closed schema)."""

    _label = "lane decision ledger"
    _id_field = "record_id"
    _validate_payload = staticmethod(_validate_payload)

    @staticmethod
    def _current_version() -> Mapping[str, Any]:
        """The runtime binding (exposed so callers and tests can pin it)."""
        return _current_version_snapshot()

    @staticmethod
    def _to_record(payload: Mapping[str, Any]) -> LaneDecisionRecord:
        # Conclusions are NOT taken from the payload; the record derives them.
        return LaneDecisionRecord(
            record_id=payload["record_id"],
            acceptance_snapshot_ref=payload["acceptance_snapshot_ref"],
            requested_lane=payload["requested_lane"],
            decided_lane=payload["decided_lane"],
            decision_outcome=payload["decision_outcome"],
            scope=payload["scope"],
            version_snapshot=dict(payload["version_snapshot"]),
            axes_auditable=payload["axes_auditable"],
            residual_unordered=payload["residual_unordered"],
            has_restrictions=payload["has_restrictions"],
            evidence_stale_support=payload["evidence_stale_support"],
            recorded_at=payload["recorded_at"],
            lane_predicate_evidence_refs=tuple(
                payload.get("lane_predicate_evidence_refs") or ()),
            prohibited_uses=tuple(payload.get("prohibited_uses") or ()),
            required_next_evidence_refs=tuple(
                payload.get("required_next_evidence_refs") or ()),
            resubmission_id=payload.get("resubmission_id"),
            note=payload.get("note"),
        )

    def append(self, record: LaneDecisionRecord) -> None:
        if not isinstance(record, LaneDecisionRecord):
            raise TypeError("append requires a LaneDecisionRecord")
        self._append_payload(record.to_stored())

    def get(self, record_id: str) -> LaneDecisionRecord:
        for payload in self._payloads():
            if payload["record_id"] == record_id:
                return self._to_record(payload)
        raise KeyError(f"missing lane decision record: {record_id}")

    def all(self) -> list[LaneDecisionRecord]:
        return [self._to_record(p) for p in self._payloads()]

    def for_snapshot(self, acceptance_snapshot_ref: str) -> list[LaneDecisionRecord]:
        return [self._to_record(p) for p in self._payloads()
                if p["acceptance_snapshot_ref"] == acceptance_snapshot_ref]

    def all_with_recorded_at(self) -> list[LaneDecisionRecord]:
        """Every record with its own stored write time.

        Kept for callers written before the time became a stored field; it is now
        the same as :meth:`all`, because no record is ever missing its time.
        """
        return self.all()

    def recorded_at(self, record_id: str) -> str | None:
        """The stored write time of ``record_id``, or None when it is absent."""
        for payload in self._payloads():
            if payload["record_id"] == record_id:
                return payload["recorded_at"]
        return None


def _stale_supporting_evidence(evidence: tuple[Any, ...], *,
                               now: str | None = None) -> bool:
    """Whether any SUPPORTING item is past the window its submitter declared.

    Only ``evidence_role == "support"`` counts: an expired limitation, conflict or
    historical item carries no support to lose, so it must not gate the ceiling.
    Items are judged against the decision moment (``now``), the same instant the
    record itself is stamped with, so a decision and its freshness verdict agree.
    """
    if not evidence:
        return False
    moment = _as_moment(now)
    for item in evidence:
        role = getattr(item, "evidence_role", None)
        if role is None and isinstance(item, Mapping):
            role = item.get("evidence_role", "support")
        if role != "support":
            continue
        try:
            if not item.is_current(now=moment):
                return True
        except (AttributeError, TypeError, ValueError) as exc:
            # An item that cannot be judged as current cannot support a promotion:
            # fail closed rather than assume it is fine.
            raise ValueError(
                f"lane decision received an evidence item that cannot be judged for "
                f"freshness: {exc}") from exc
    return False


def _as_moment(now: str | None) -> Any:
    """The decision moment as an aware UTC datetime (defaults to right now)."""
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        parsed = now
    else:
        parsed = datetime.fromisoformat(str(now))
    if parsed.tzinfo is None:
        raise ValueError("now must carry a timezone offset")
    return parsed.astimezone(timezone.utc)


def record_lane_decision(
    lane_store: DPLaneStore,
    *,
    record_id: str,
    acceptance_snapshot_ref: str,
    requested_lane: str,
    decision_outcome: str,
    scope: str,
    axes_auditable: bool,
    residual_unordered: bool = False,
    has_restrictions: bool = False,
    evidence: tuple[Any, ...] = (),
    evidence_stale_support: bool | None = None,
    lane_predicate_evidence_refs: tuple[str, ...] = (),
    prohibited_uses: tuple[str, ...] = (),
    required_next_evidence_refs: tuple[str, ...] = (),
    resubmission_id: str | None = None,
    note: str | None = None,
    now: str | None = None,
    integrity_ok: bool = True,
    current_runtime: Mapping[str, Any] | None = None,
) -> LaneDecisionRecord:
    """Record one lane decision, failing closed on anything the facts do not support.

    The decided lane is never promoted to meet the request (C-04 §8.10): the ceiling
    is computed from the facts and the decision lands at or below it. ``accepted``
    where the facts establish no conclusion raises instead — the caller must state
    the honest outcome (``rejected`` or ``return_for_evidence``).

    ``evidence`` is the account's evidence for this decision. Any item whose role is
    ``support`` and whose declared validity window has passed blocks promotion, and
    that fact is stored so a later reader sees why (C-04 §6 item 5 requires the lane
    predicates to cover freshness facts). Items in other roles — limitation,
    conflict, historical context, external observation, planned — do not gate the
    ceiling; expiry there is a matter for the report to show, not a bar to use.

    ``integrity_ok=False`` asserts that the required integrity and authority facts
    are NOT auditable (C-04 §6 row 1). This build cannot store that as a positive
    fact — the record describes an auditable account — so the honest representation
    is the refusal: no authorising outcome is available, and the caller records
    ``rejected``/``return_for_evidence`` (those store a return-state lane, which the
    read path still validates against the runtime binding).
    """
    if not acceptance_snapshot_ref:
        raise ValueError("lane decision requires an acceptance_snapshot_ref: result "
                         "authority may only be consumed through it (C-04 §3)")
    if requested_lane not in DP_LANES:
        raise ValueError(f"unknown lane: {requested_lane!r} (closed domain "
                         f"{list(DP_LANES)})")
    if decision_outcome not in DP_LANE_OUTCOMES:
        raise ValueError(f"unknown decision outcome: {decision_outcome!r}")

    # C-04 §8.12 / §9: restrictions must be consistent with what the record carries,
    # and an empty restriction set must not be inferred when restrictions are
    # required. The derived reason says "carries explicit limitations or prohibited
    # uses", so a record claiming that must actually carry some — and vice versa.
    if bool(has_restrictions) != bool(prohibited_uses):
        raise ValueError(
            "lane decision restrictions are inconsistent: has_restrictions="
            f"{bool(has_restrictions)} while prohibited_uses="
            f"{list(prohibited_uses or [])} — a record may not assert a restriction "
            "it does not carry, nor carry one it does not assert (C-04 §8.12, §9)")

    recorded_version = dict(current_runtime if current_runtime is not None
                            else _current_version_snapshot())
    # C-04 §6 item 5: freshness is a required dependency of the lane predicate, and
    # the two freshness facts are judged here, at the moment of the decision:
    #   - the runtime binding (did the rules/engine/format move on?), and
    #   - whether the material offered to SUPPORT this decision is still in its
    #     declared window. Roles other than `support` are not support: an expired
    #     limitation or historical item does not bar use, it just stops being news.
    stale_support = (_stale_supporting_evidence(evidence, now=now)
                     if evidence_stale_support is None
                     else bool(evidence_stale_support))
    ceiling, reason = lane_ceiling(
        integrity_ok=bool(integrity_ok),
        version_currency="current",
        axes_auditable=bool(axes_auditable),
        residual_unordered=bool(residual_unordered),
        has_restrictions=bool(has_restrictions),
        evidence_stale_support=stale_support)

    if decision_outcome == "return_for_evidence":
        if not required_next_evidence_refs:
            raise ValueError("return_for_evidence requires non-empty "
                             "required_next_evidence_refs (C-04 §8.8)")
        if not resubmission_id:
            raise ValueError("return_for_evidence requires a new resubmission_id "
                             "(C-04 §8.8)")
        decided = _RETURN_FIXED_LANE
    elif decision_outcome == "accepted":
        # C-04 §8.10: a request above the supportable ceiling does NOT force the
        # decided lane up — it lands at or below the evidenced ceiling, and the
        # record keeps BOTH values so the gap is visible rather than silent.
        decided = requested_lane if _lane_rank(requested_lane) <= _lane_rank(ceiling) \
            else ceiling
    else:  # rejected / withdrawn
        decided = _RETURN_FIXED_LANE

    if decision_outcome == "accepted" and (
            not integrity_ok or ceiling == _RETURN_FIXED_LANE):
        raise ValueError(
            "no valid release/lane conclusion exists for this account, so no "
            "outcome may record an authorising lane; use decision_outcome="
            "'rejected' or 'return_for_evidence' (C-04 §6 rows 1-2, §9). "
            f"Ceiling reason: {reason}")

    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = LaneDecisionRecord(
        record_id=record_id,
        acceptance_snapshot_ref=acceptance_snapshot_ref,
        requested_lane=requested_lane,
        decided_lane=decided,
        decision_outcome=decision_outcome,
        scope=scope,
        version_snapshot=recorded_version,
        axes_auditable=bool(axes_auditable),
        residual_unordered=bool(residual_unordered),
        has_restrictions=bool(has_restrictions),
        evidence_stale_support=stale_support,
        recorded_at=stamp,
        lane_predicate_evidence_refs=tuple(lane_predicate_evidence_refs),
        prohibited_uses=tuple(prohibited_uses),
        required_next_evidence_refs=tuple(required_next_evidence_refs),
        resubmission_id=resubmission_id,
        note=note,
    )
    lane_store.append(record)
    return record
