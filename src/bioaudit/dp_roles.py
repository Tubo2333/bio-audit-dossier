"""Role and scope records — authored / reviewed / receipted / adjudicated.

Makes the P-01 §8.1 / P-02 §6.3 distinction REAL: who authored material, who
independently reviewed it (with a named scope), who administratively receipted
it, and who adjudicated it — each as a traceable record with a named actor,
a named target and a named scope.

Hard boundaries (inherited product meaning, not new semantics):
- The system does NOT conduct review and does NOT adjudicate. It records that a
  role was exercised, by whom, over what target and scope.
- These records are explanatory/constraining material and must NEVER become
  result authority (T-01 L532). The closed schema has no result/carrier key
  (``approved``/``lane``/``release``/result keys are rejected), a ``RoleRecord``
  is rejected by the authority seam, and no API path feeds role records into the
  report or the strength derivation.
- No generated output is "reviewed" merely because it exists; no receipt is
  substantive review; no scoped review or receipt is global approval
  (T-01 §11.2 L675). "Receipt is administrative only" is an EXPLICIT constraint:
  a ``receipted`` record must carry a ``receipt_kind`` from a closed domain
  (administrative / point_in_time), and no other role may carry one — so a
  receipt can never be dressed up as substantive review.
- Targets use a closed kind vocabulary (``acceptance_snapshot:`` / ``report:`` /
  ``point:``). That binds the *kind*, not the identity: existence of the target
  is not claimed (a review may examine an artifact with no ledger id, T-01 L704).

Persistence is its own ledger (separate from the point ledger and the authority
ledger); the durability skeleton is shared via ``JSONLLedger``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bioaudit.dp01_store import _current_version_snapshot
from bioaudit.dp_ledger import JSONLLedger

# The four roles are INHERITED product meaning (P-01 §8.1 / P-02 §6.3 /
# T-01 §4.8 L290): authored, independently reviewed, approved-adjudicated,
# administratively receipted. Not new vocabulary.
ROLES = ("authored", "reviewed", "receipted", "adjudicated")

# Closed vocabulary of authorized target kinds (prefix contract only).
TARGET_PREFIXES = ("acceptance_snapshot:", "report:", "point:")

# Closed domain for administrative receipt (T-01 L705: administrative
# observation only; never substantive review or approval).
RECEIPT_KINDS = ("administrative", "point_in_time")

_ALLOWED_KEYS = frozenset({
    "record_id", "role", "actor", "target", "scope", "version_snapshot",
    "note", "receipt_kind",
})
_REQUIRED_KEYS = _ALLOWED_KEYS - {"note", "receipt_kind"}

# Keys that would turn an explanatory record into a second result/carrier or
# smuggle in approval / lane / release semantics.
_FORBIDDEN_KEYS = frozenset({
    "approved", "approval", "global_approval", "lane", "release",
    "release_decision", "result", "results", "score", "scientific_score",
    "confidence", "ranking", "structured_strength_result",
    "structured_strength_result_ref", "acceptance_snapshot",
    "acceptance_snapshot_ref", "second_snapshot", "alternative_carrier",
    "back_reference", "copied_result",
})

_VERSION_KEYS = frozenset({
    "ruleset_version", "ontology_version", "engine_version",
    "input_format_version", "adapter_version",
})


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("role mapping keys must be strings")
            out[key] = _plain(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise TypeError(f"unsupported role value: {type(value).__name__}")


@dataclass(frozen=True)
class RoleRecord:
    """One named role exercised over a named target within a named scope.

    ``version_snapshot`` is frozen to an immutable mapping in ``__post_init__``,
    so a returned record cannot be tampered with through its nested content
    (Standards S2: ``frozen=True`` alone only locks attribute rebinding).
    """

    record_id: str
    role: str
    actor: str
    target: str
    scope: str
    version_snapshot: Mapping[str, Any]
    note: str | None = None
    receipt_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_snapshot",
                           MappingProxyType(dict(self.version_snapshot)))

    def to_dict(self) -> dict[str, Any]:
        out = {
            "record_id": self.record_id,
            "role": self.role,
            "actor": self.actor,
            "target": self.target,
            "scope": self.scope,
            "version_snapshot": _plain(self.version_snapshot),
        }
        if self.note is not None:
            out["note"] = self.note
        if self.receipt_kind is not None:
            out["receipt_kind"] = self.receipt_kind
        return out


def _validate_payload(p: Any) -> None:
    """Closed-schema validation for one persisted role record (fail-closed)."""
    if not isinstance(p, Mapping):
        raise ValueError("invalid role record: payload must be a mapping")
    keys = set(p)
    # Forbidden path first so the rejection is reachable and specific.
    if keys & _FORBIDDEN_KEYS:
        raise ValueError(
            "invalid role record: forbidden key present: "
            + ", ".join(sorted(keys & _FORBIDDEN_KEYS)))
    if keys - _ALLOWED_KEYS or _REQUIRED_KEYS - keys:
        raise ValueError("invalid role record: closed key set violated")

    record_id = p["record_id"]
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("invalid role record id")

    role = p["role"]
    if role not in ROLES:
        raise ValueError(f"invalid role: {role!r} (expected one of {', '.join(ROLES)})")

    for field in ("actor", "target", "scope"):
        value = p[field]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"invalid role record: {field} must be a named non-empty string")

    target = p["target"]
    if not target.startswith(TARGET_PREFIXES):
        raise ValueError(
            "invalid role record: target must name an authorized kind prefix "
            + " / ".join(TARGET_PREFIXES))

    receipt_kind = p.get("receipt_kind")
    if role == "receipted":
        if receipt_kind not in RECEIPT_KINDS:
            raise ValueError(
                "invalid role record: a receipted record requires receipt_kind in "
                + " / ".join(RECEIPT_KINDS)
                + " (a receipt is administrative only, never substantive review)")
    elif receipt_kind is not None:
        raise ValueError(
            "invalid role record: receipt_kind is only valid for role 'receipted'")

    note = p.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("invalid role record: note must be a string")

    version = p["version_snapshot"]
    if not isinstance(version, Mapping) or set(version) != _VERSION_KEYS:
        raise ValueError("invalid role version snapshot shape")
    if not all(isinstance(v, str) for v in version.values()):
        raise ValueError("invalid role version snapshot values")
    current = _current_version_snapshot()
    mismatches = {k: (version[k], current[k]) for k in _VERSION_KEYS
                  if version[k] != current[k]}
    if mismatches:
        raise ValueError(f"runtime snapshot mismatch: {mismatches}")


class DPRoleStore(JSONLLedger):
    """Append-only JSONL ledger for role+scope records (own file, closed schema)."""

    _label = "role ledger"
    _id_field = "record_id"
    _validate_payload = staticmethod(_validate_payload)

    @staticmethod
    def _to_record(payload: Mapping[str, Any]) -> RoleRecord:
        return RoleRecord(
            record_id=payload["record_id"],
            role=payload["role"],
            actor=payload["actor"],
            target=payload["target"],
            scope=payload["scope"],
            version_snapshot=dict(payload["version_snapshot"]),
            note=payload.get("note"),
            receipt_kind=payload.get("receipt_kind"),
        )

    def append(self, record: RoleRecord) -> None:
        if not isinstance(record, RoleRecord):
            raise TypeError("append requires a RoleRecord")
        self._append_payload(record.to_dict())

    def get(self, record_id: str) -> RoleRecord:
        for payload in self._payloads():
            if payload["record_id"] == record_id:
                return self._to_record(payload)
        raise KeyError(f"missing role record: {record_id}")

    def for_target(self, target: str) -> list[RoleRecord]:
        return [self._to_record(p) for p in self._payloads() if p["target"] == target]

    def all(self) -> list[RoleRecord]:
        return [self._to_record(p) for p in self._payloads()]


def record_role(role_store: DPRoleStore, *, record_id: str, role: str, actor: str,
                target: str, scope: str, note: str | None = None,
                receipt_kind: str | None = None) -> RoleRecord:
    """Record that a role was exercised (role + actor + target + scope).

    A ``receipted`` record must pass ``receipt_kind`` from the closed
    administrative domain; other roles must not.
    """
    record = RoleRecord(
        record_id=record_id,
        role=role,
        actor=actor,
        target=target,
        scope=scope,
        version_snapshot=dict(_current_version_snapshot()),
        note=note,
        receipt_kind=receipt_kind,
    )
    role_store.append(record)
    return record


def list_roles(role_store: DPRoleStore, target: str | None = None) -> list[RoleRecord]:
    """List role records, optionally filtered to one named target."""
    return role_store.all() if target is None else role_store.for_target(target)


__all__ = ["DPRoleStore", "RECEIPT_KINDS", "ROLES", "TARGET_PREFIXES", "RoleRecord",
           "list_roles", "record_role"]
