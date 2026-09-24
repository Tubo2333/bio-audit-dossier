"""Immutable DP-01 filtering declaration models."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
import math


# Authorization §7 version binding: DP-01 binds input format and semantic-adapter
# versions in addition to the report ruleset/ontology/engine triple.
DP01_INPUT_FORMAT_VERSION = "1.0"
DP01_ADAPTER_VERSION = "1.0"

# Shared structured-output vocabularies (used by adapter construction and store read-back validation).
DP01_JUDGMENTS = frozenset({"auditable", "conflicted", "not_auditable", "pending_attempt", "scientifically_limited", "integrity_failed"})
DP01_ACTIONS = frozenset({"request_evidence", "submit_correction", "request_review", "stop"})
DP01_FINDING_KINDS = frozenset({"method", "integrity_failure"})
DP01_EVIDENCE_STATES = frozenset({"confirmed", "unverified", "conflicted", "not_applicable"})
DP01_FINDING_STATES = frozenset({"confirmed", "failed", "unverified", "conflicted", "not_applicable"})
DP01_LIMITATION_KINDS = frozenset({"filtering_support"})
DP01_GAP_KINDS = frozenset({"missing_critical_context", "absent_execution_observation", "declared_observed_conflict", "integrity_failure"})
DP01_CONTRIBUTION_STATUSES = frozenset({"established", "pending", "unresolved"})
DP01_EVIDENCE_SOURCE_TYPES = frozenset({"declared", "observed", "referenced", "derived"})
# P-02 §4.1: what an item is FOR — as distinct from how it was obtained
# (`source_type`). Closed set; adding a role is a governance decision.
DP01_EVIDENCE_ROLES = frozenset({
    "support", "limitation", "conflict", "historical_context",
    "external_observation", "planned_future_requirement"})



def _freeze(value: Any, _active: set[int] | None = None) -> Any:
    """Freeze the explicitly supported JSON-like structured value domain."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is unsupported in DP-01 structured values")
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise TypeError(f"unsupported DP-01 structured value: {type(value).__name__}")
    active = set() if _active is None else _active
    marker = id(value)
    if marker in active:
        raise ValueError("cyclic DP-01 structured value is unsupported")
    active.add(marker)
    try:
        if isinstance(value, Mapping):
            items = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("DP-01 structured mapping keys must be strings")
                items[key] = _freeze(item, active)
            return MappingProxyType(items)
        return tuple(_freeze(item, active) for item in value)
    finally:
        active.remove(marker)

@dataclass(frozen=True)
class FilteringDeclaration:
    method: str; threshold: Any; sample_rule: Any; unit: str; source: str
    def __post_init__(self):
        object.__setattr__(self, "threshold", _freeze(self.threshold)); object.__setattr__(self, "sample_rule", _freeze(self.sample_rule))
    def to_dict(self): return {"method":self.method,"threshold":_plain(self.threshold),"sample_rule":_plain(self.sample_rule),"unit":self.unit,"source":self.source}

@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence, qualified as P-02 §4.1 requires.

    Beyond the original three facts (``source_type`` / ``verification`` / ``value``),
    every item now carries the qualification the surface has to show:

    - ``observed_at`` / ``valid_for_seconds``: WHEN it was observed and HOW LONG it
      stays current. Both are caller-declared inputs; whether the item is still
      current is NOT stored — it is derived at render time against the deciding
      moment, the same way the lane ceiling is derived rather than trusted.
    - ``provenance``: where the material came from and which named subject it
      concerns (P-02 §4.2: similarity, filename or method name may never stand in
      for this).
    - ``supports``: the exact claim this item is offered for — "evidence present"
      must never be read as "claim supported" (P-02 §4.1).
    - ``evidence_role``: what the item is *for* (support / limitation / conflict /
      historical context / external observation / planned future requirement), as
      P-02 §4.1 lists.

    ``source_type`` and ``evidence_role`` are kept apart on purpose: the first says
    how the material was obtained, the second says what weight it may carry here.
    """

    source_type: str
    verification: str
    value: Any
    observed_at: datetime
    valid_for_seconds: int
    provenance: str | None = None
    provenance_subject: str | None = None
    supports: str | None = None
    evidence_role: str = "support"

    def __post_init__(self):
        object.__setattr__(self, "value", _freeze(self.value))
        if not isinstance(self.observed_at, datetime):
            raise ValueError("evidence observed_at must be a datetime")
        if self.observed_at.tzinfo is None:
            raise ValueError("evidence observed_at must carry a timezone")
        # One-second precision, decided HERE rather than at serialisation time: the
        # ledger stores seconds, so a record built with microseconds would not
        # round-trip equal to itself. Normalising at construction keeps the in-memory
        # record and the durable record the same thing in meaning.
        object.__setattr__(
            self, "observed_at",
            self.observed_at.astimezone(timezone.utc).replace(microsecond=0))
        if not isinstance(self.valid_for_seconds, int) or self.valid_for_seconds <= 0:
            raise ValueError("evidence valid_for_seconds must be a positive integer")

    def is_current(self, *, now: datetime) -> bool:
        """Whether this item is still within its declared validity window.

        Derived, never stored: the record keeps the observation time and the window,
        so the same ledger read at two different moments yields two honest answers
        instead of one frozen claim.
        """
        if now.tzinfo is None:
            raise ValueError("now must carry a timezone")
        return (now - self.observed_at).total_seconds() <= self.valid_for_seconds

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "verification": self.verification,
            "value": _plain(self.value),
            "observed_at": self.observed_at.isoformat(timespec="seconds"),
            "valid_for_seconds": self.valid_for_seconds,
            "provenance": self.provenance,
            "provenance_subject": self.provenance_subject,
            "supports": self.supports,
            "evidence_role": self.evidence_role,
        }

@dataclass(frozen=True)
class DP01Finding:
    kind: str; source: str; state: str; value: Any
    def __post_init__(self): object.__setattr__(self, "value", _freeze(self.value))
    def to_dict(self): return {"kind":self.kind,"source":self.source,"state":self.state,"value":_plain(self.value)}

@dataclass(frozen=True)
class DP01Limitation:
    kind: str; state: str; detail: str
    def to_dict(self): return {"kind":self.kind,"state":self.state,"detail":self.detail}

@dataclass(frozen=True)
class DP01EvidenceGap:
    kind: str; source: str; state: str; target: str
    def to_dict(self): return {"kind":self.kind,"source":self.source,"state":self.state,"target":self.target}

@dataclass(frozen=True)
class DP01LocalContribution:
    status: str; scope: str; detail: str
    def to_dict(self): return {"status":self.status,"scope":self.scope,"detail":self.detail}

def _parse_affected_evidence(value):
    """Strictly normalize the affected-evidence field.

    Accepts a list/tuple of non-empty strings; rejects bare strings (which would
    otherwise be char-split by tuple()) and non-iterables with ValueError so
    callers fail closed instead of silently canonicalizing or leaking TypeError.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("affected evidence must be a sequence of non-empty strings")
    if not all(isinstance(x, str) and x for x in value):
        raise ValueError("affected evidence must be a sequence of non-empty strings")
    return tuple(value)


@dataclass(frozen=True)
class DP01AttemptChange:
    kind: str; reason: str; submitter: str | None = None; affected_evidence: tuple[str, ...] = ()
    def __post_init__(self):
        if self.kind not in {"evidence_supplement", "decision_correction"}: raise ValueError("invalid DP-01 change kind")
        if not isinstance(self.reason, str) or not self.reason: raise ValueError("change reason is required")
        if self.submitter is not None and (not isinstance(self.submitter, str) or not self.submitter): raise ValueError("change submitter is invalid")
        object.__setattr__(self, "affected_evidence", _parse_affected_evidence(self.affected_evidence))
    def to_dict(self):
        out = {"change_kind":self.kind,"change_reason":self.reason}
        if self.submitter is not None: out["submitter"] = self.submitter
        if self.affected_evidence: out["affected_evidence"] = list(self.affected_evidence)
        return out

@dataclass(frozen=True)
class DP01Change:
    path: str; before: Any; after: Any
    def __post_init__(self):
        object.__setattr__(self, "before", _freeze(self.before))
        object.__setattr__(self, "after", _freeze(self.after))

@dataclass(frozen=True)
class DP01ChangeSummary:
    changes: tuple[DP01Change, ...] = ()
    def __post_init__(self): object.__setattr__(self,"changes",tuple(self.changes))
    def to_dict(self): return {"changes":[{"path":x.path,"before":_plain(x.before),"after":_plain(x.after)} for x in self.changes]}

@dataclass(frozen=True)
class ControlledNextAction:
    action: str; reason: str; target: str; next_state: str
    def to_dict(self): return {"action":self.action,"reason":self.reason,"target":self.target,"next_state":self.next_state}

@dataclass(frozen=True)
class DP01FilteringResult:
    scope: str; analysis_context: Mapping[str, Any]; declaration: FilteringDeclaration | None; evidence: tuple[EvidenceItem, ...]
    diagnostic_explanation: str; matched_rule_ids: tuple[str, ...]; judgment: str; next_actions: tuple[ControlledNextAction, ...]
    findings: tuple[DP01Finding, ...] = (); limitations: tuple[DP01Limitation, ...] = (); evidence_gaps: tuple[DP01EvidenceGap, ...] = ()
    local_contribution: DP01LocalContribution = DP01LocalContribution("unresolved", "filtering", "No filtering contribution established")
    audit_id: str | None = None
    version_snapshot: Mapping[str, Any] | None = None
    def __post_init__(self):
        object.__setattr__(self,"analysis_context",_freeze(self.analysis_context)); object.__setattr__(self,"evidence",tuple(self.evidence)); object.__setattr__(self,"matched_rule_ids",tuple(self.matched_rule_ids)); object.__setattr__(self,"next_actions",tuple(self.next_actions)); object.__setattr__(self,"findings",tuple(self.findings)); object.__setattr__(self,"limitations",tuple(self.limitations)); object.__setattr__(self,"evidence_gaps",tuple(self.evidence_gaps))
        if self.version_snapshot is not None:
            object.__setattr__(self,"version_snapshot",_freeze(dict(self.version_snapshot)))

def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite float is unsupported in DP-01 serialization")
        return value
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    raise TypeError(f"unsupported value for DP-01 serialization: {type(value).__name__}")

__all__=["DP01_INPUT_FORMAT_VERSION","DP01_ADAPTER_VERSION","DP01_JUDGMENTS","DP01_ACTIONS","DP01_FINDING_KINDS","DP01_EVIDENCE_STATES","DP01_FINDING_STATES","DP01_LIMITATION_KINDS","DP01_GAP_KINDS","DP01_CONTRIBUTION_STATUSES","DP01_EVIDENCE_SOURCE_TYPES","DP01_EVIDENCE_ROLES","ControlledNextAction","DP01Change","DP01ChangeSummary","DP01AttemptChange","DP01EvidenceGap","DP01Finding","DP01Limitation","DP01LocalContribution","DP01FilteringResult","EvidenceItem","FilteringDeclaration"]
