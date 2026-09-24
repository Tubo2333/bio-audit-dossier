"""History / change records (P-02 §6.4 / §6.5, P-01 §8.2 / §8.3, P-03 row147).

A later audited account is a NEW bounded judgment related to a prior account, not
an invisible replacement. This module records that relation so the user surface
can show:

- which account is current and which material is **historical-only** (prior
  material keeps its original scope and time meaning; history must never revive
  stale authority or silently make an old observation current);
- the **proposed/current difference** across the seven §6.4 change classes; and
- the **authority that must decide whether the change is acceptable** (§6.5).

Boundaries:
- ``P-02 §6.4`` states it defines the USER MEANING only, not storage, persistence,
  migration, comparison mechanics or revision implementation. The ledger here is
  therefore an implementation choice, not a spec mandate; no rollback/migration
  mechanism is implemented.
- Both sides of a change must be ACCOUNT references (``acceptance_snapshot:``), so
  a change can never promote report/point material beyond its role (§6.5 bullet 4).
- Escalation reasons are a CLOSED list mirroring the six §6.5 stop-and-escalate
  triggers; nothing is invented.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bioaudit.dp01_store import _current_version_snapshot
from bioaudit.dp_ledger import JSONLLedger
from bioaudit.dp_roles import _VERSION_KEYS


def _plain(value: Any) -> Any:
    """Strict JSON-plain projection (raises on unsupported values).

    Local to this module so error messages name change records, not roles.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("change mapping keys must be strings")
            out[key] = _plain(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise TypeError(f"unsupported change value: {type(value).__name__}")

# P-02 §6.4 (verbatim vocabulary): the meanings a change must be intelligible in.
CHANGE_CLASSES = (
    "project_or_analysis_context",
    "input_or_version",
    "evidence_or_provenance",
    "decision_interpretation",
    "limitation_or_conflict",
    "intended_use",
    "review_or_authority_boundary",
)

# P-02 §6.5 stop-and-escalate triggers, as a closed vocabulary.
ESCALATION_REASONS = (
    "alters_frozen_or_admitted_rule",
    "adds_public_object_field_status_relation_lane_score_or_carrier",
    "creates_second_result_authority_path",
    "promotes_material_beyond_its_role",
    "turns_bounded_use_into_release_authority",
    # P-02 §6.5 bullet 6 wording: "cross into technical design, implementation,
    # testing, runtime proof, deployment, evidence collection, or closure".
    "crosses_into_technical_design_or_closure",
)

# A change relates two ACCOUNT references; report/point material cannot be an end.
ACCOUNT_PREFIX = "acceptance_snapshot:"

_ALLOWED_KEYS = frozenset({
    "change_id", "from_account", "to_account", "change_classes", "differences",
    "reason", "submitter", "requires_adjudication", "adjudication_authority",
    "escalation_required", "escalation_reason", "version_snapshot",
})
_REQUIRED_KEYS = frozenset({
    "change_id", "from_account", "to_account", "change_classes", "differences",
    "reason", "submitter", "requires_adjudication", "escalation_required",
    "version_snapshot",
})
_DIFFERENCE_KEYS = frozenset({"change_class", "path", "before", "after"})


@dataclass(frozen=True)
class ChangeRecord:
    """One bounded change relation between a prior and a later account."""

    change_id: str
    from_account: str
    to_account: str
    change_classes: tuple[str, ...]
    differences: tuple[Mapping[str, Any], ...]
    reason: str
    submitter: str
    requires_adjudication: bool
    version_snapshot: Mapping[str, Any]
    adjudication_authority: str | None = None
    escalation_required: bool = False
    escalation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "change_id": self.change_id,
            "from_account": self.from_account,
            "to_account": self.to_account,
            "change_classes": list(self.change_classes),
            "differences": [_plain(dict(d)) for d in self.differences],
            "reason": self.reason,
            "submitter": self.submitter,
            "requires_adjudication": self.requires_adjudication,
            "escalation_required": self.escalation_required,
            "version_snapshot": _plain(dict(self.version_snapshot)),
            # Optional fields are always present (as None when unused) so a stored
            # record never conflates "absent" with "explicitly none".
            "adjudication_authority": self.adjudication_authority,
            "escalation_reason": self.escalation_reason,
        }
        return out


def _validate_payload(p: Any) -> None:
    """Closed-schema validation for one persisted change record (fail-closed)."""
    if not isinstance(p, Mapping):
        raise ValueError("invalid change record: payload must be a mapping")
    keys = set(p)
    if keys - _ALLOWED_KEYS or _REQUIRED_KEYS - keys:
        raise ValueError("invalid change record: closed key set violated")

    change_id = p["change_id"]
    if not isinstance(change_id, str) or not change_id:
        raise ValueError("invalid change id")

    for field in ("from_account", "to_account"):
        value = p[field]
        if not isinstance(value, str) or not value.startswith(ACCOUNT_PREFIX):
            raise ValueError(f"{field} must be an account reference "
                             f"(prefix {ACCOUNT_PREFIX})")
    if p["from_account"] == p["to_account"]:
        raise ValueError("from_account and to_account must differ")

    classes = p["change_classes"]
    if not isinstance(classes, list) or not classes:
        raise ValueError("change_classes must be a non-empty list")
    if len(set(classes)) != len(classes):
        raise ValueError("change_classes must not repeat")
    for value in classes:
        if value not in CHANGE_CLASSES:
            raise ValueError(f"invalid change class: {value!r}")

    differences = p["differences"]
    if not isinstance(differences, list) or not differences:
        raise ValueError("differences must be a non-empty list")
    for entry in differences:
        if not isinstance(entry, Mapping) or set(entry) != _DIFFERENCE_KEYS:
            raise ValueError("invalid difference shape")
        if entry["change_class"] not in CHANGE_CLASSES:
            raise ValueError(f"invalid change class: {entry['change_class']!r}")
        if not isinstance(entry["path"], str) or not entry["path"]:
            raise ValueError("difference path must be a named string")

    for field in ("reason", "submitter"):
        value = p[field]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a named non-empty string")

    requires_adjudication = p["requires_adjudication"]
    if not isinstance(requires_adjudication, bool):
        raise ValueError("requires_adjudication must be a boolean")
    authority = p.get("adjudication_authority")
    if requires_adjudication:
        if not isinstance(authority, str) or not authority:
            raise ValueError(
                "adjudication_authority is required when adjudication is needed")
    elif authority is not None:
        raise ValueError(
            "adjudication_authority is only valid when adjudication is needed")

    escalation_required = p["escalation_required"]
    if not isinstance(escalation_required, bool):
        raise ValueError("escalation_required must be a boolean")
    escalation_reason = p.get("escalation_reason")
    if escalation_required:
        if escalation_reason not in ESCALATION_REASONS:
            raise ValueError(
                "escalation_reason is required and must be one of "
                + " / ".join(ESCALATION_REASONS))
    elif escalation_reason is not None:
        raise ValueError("escalation_reason is only valid when escalation is required")

    version = p["version_snapshot"]
    if not isinstance(version, Mapping) or set(version) != _VERSION_KEYS:
        raise ValueError("invalid change version snapshot shape")
    if not all(isinstance(v, str) for v in version.values()):
        raise ValueError("invalid change version snapshot values")
    current = _current_version_snapshot()
    mismatches = {k: (version[k], current[k]) for k in _VERSION_KEYS
                  if version[k] != current[k]}
    if mismatches:
        raise ValueError(f"runtime snapshot mismatch: {mismatches}")


class DPChangeStore(JSONLLedger):
    """Append-only JSONL ledger for change records (own file, closed schema)."""

    _label = "change ledger"
    _id_field = "change_id"
    _validate_payload = staticmethod(_validate_payload)

    @staticmethod
    def _to_record(payload: Mapping[str, Any]) -> ChangeRecord:
        return ChangeRecord(
            change_id=payload["change_id"],
            from_account=payload["from_account"],
            to_account=payload["to_account"],
            change_classes=tuple(payload["change_classes"]),
            differences=tuple(dict(d) for d in payload["differences"]),
            reason=payload["reason"],
            submitter=payload["submitter"],
            requires_adjudication=payload["requires_adjudication"],
            version_snapshot=dict(payload["version_snapshot"]),
            adjudication_authority=payload.get("adjudication_authority"),
            escalation_required=payload["escalation_required"],
            escalation_reason=payload.get("escalation_reason"),
        )

    def append(self, record: ChangeRecord) -> None:
        if not isinstance(record, ChangeRecord):
            raise TypeError("append requires a ChangeRecord")
        self._append_payload(record.to_dict())

    def get(self, change_id: str) -> ChangeRecord:
        for payload in self._payloads():
            if payload["change_id"] == change_id:
                return self._to_record(payload)
        raise KeyError(f"missing change record: {change_id}")

    def all(self) -> list[ChangeRecord]:
        return [self._to_record(p) for p in self._payloads()]


def record_change(change_store: DPChangeStore, *, change_id: str, from_account: str,
                  to_account: str, change_classes, differences, reason: str,
                  submitter: str, requires_adjudication: bool,
                  adjudication_authority: str | None = None,
                  escalation_required: bool = False,
                  escalation_reason: str | None = None) -> ChangeRecord:
    """Record one bounded change relation between two accounts."""
    record = ChangeRecord(
        change_id=change_id,
        from_account=from_account,
        to_account=to_account,
        change_classes=tuple(change_classes),
        differences=tuple(dict(d) for d in differences),
        reason=reason,
        submitter=submitter,
        requires_adjudication=requires_adjudication,
        version_snapshot=dict(_current_version_snapshot()),
        adjudication_authority=adjudication_authority,
        escalation_required=escalation_required,
        escalation_reason=escalation_reason,
    )
    change_store.append(record)
    return record


def list_changes(change_store: DPChangeStore) -> list[ChangeRecord]:
    return change_store.all()


__all__ = ["ACCOUNT_PREFIX", "CHANGE_CLASSES", "DPChangeStore", "ESCALATION_REASONS",
           "ChangeRecord", "list_changes", "record_change"]
