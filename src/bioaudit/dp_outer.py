"""Outer replay binding (C-03): non-public, one-way, verification-only.

T-01 §6.4: ``OuterReplayBindingRecord`` **must remain outside the public result
meaning and outside result authority**. Its only permitted conceptual role is
one-way verification or constraint of an already governed relationship. It must
not create a result, create a snapshot, carry a result as an alternative, become
a relation category, become a release or lane authority, reverse the direction of
the stage-package/snapshot relationship, or be treated as runtime or replay proof
merely because it is present.

T-01 §8.4: the direction is conceptual — from pre-snapshot stage material toward
the acceptance snapshot; it cannot be used as reverse evidence that creates a new
result or snapshot.

T-01 L338: exactly ONE conceptual acceptance binding (enforced here as
"at most one binding per account"). T-01 L821: this boundary is realized
**without selecting a trust root or any cryptographic mechanism** — the schema
below therefore carries no proof/hash/signature material, and the module exposes
no route that turns a binding into an authority, a snapshot or a result.

RELIANCE BOUNDARY (stated honestly, for the later formal gate): this record
**asserts** a relationship; it does not **establish** one. It does not check that
``bound_account`` / ``stage_material_ref`` exist, and it carries no integrity or
freshness facts and no separate pre-snapshot/snapshot hash boundaries — the
later C-03 §7 conditions under which an outer binding "may be relied upon"
(there: "subject to ... its own verifiable integrity and freshness facts"; B4 §5
declares separate hash boundaries). Realizing those is a separate technical
authority decision, not something this window may infer. Until then the binding
must not be presented as verifiable material on which reliance is founded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bioaudit.dp01_store import _current_version_snapshot
from bioaudit.dp_change import ACCOUNT_PREFIX
from bioaudit.dp_ledger import JSONLLedger
from bioaudit.dp_roles import _VERSION_KEYS

# The conceptual direction is fixed: stage material -> acceptance snapshot.
DIRECTION = "stage_material_to_acceptance_snapshot"
STAGE_PREFIX = "stage_package:"

# One-way is a STRUCTURAL guarantee, not a checked condition: the two authorized
# prefixes are disjoint, so a stage reference can never also be the bound account
# (an equality branch between the two would be unreachable). Asserted here as
# executable code so a future prefix change fails loudly at import rather than
# silently discarding the guarantee (Standards finding 8).
assert not STAGE_PREFIX.startswith(ACCOUNT_PREFIX), (
    "STAGE_PREFIX and ACCOUNT_PREFIX must be disjoint (one-way direction)")
assert not ACCOUNT_PREFIX.startswith(STAGE_PREFIX), (
    "STAGE_PREFIX and ACCOUNT_PREFIX must be disjoint (one-way direction)")

# Plain-language non-claim (T-01 §6.4 / P-03 row143): presence is not proof.
NON_CLAIM = (
    "An outer replay binding is verification-only constraint material: its presence "
    "is not runtime or replay proof, it creates no result or snapshot, and it cannot "
    "be reversed into authority.")


# The verification statement is bounded prose: it must not become a carrier for
# result payloads ("carry a result as an alternative", T-01 §6.4 must-not 3), so
# it is length-capped and rejects structural payload markers.
STATEMENT_MAX = 500
_PAYLOAD_MARKERS = ("{", "}", "[", "]", "\n", "\r")

_ALLOWED_KEYS = frozenset({
    "binding_id", "bound_account", "stage_material_ref", "verification_statement",
    "verifier", "direction", "verification_only", "version_snapshot",
})

# Keys that would let the record create or carry authority, become a relation or
# release/lane authority, or pass itself off as proof (T-01 §6.4 must-not list).
_FORBIDDEN_KEYS = frozenset({
    "proof", "proofs", "hash", "hashes", "digest", "signature", "signatures",
    "replay_result", "runtime_proof", "trust_root", "cryptographic_evidence",
    "result", "results", "structured_strength_result",
    "structured_strength_result_ref", "acceptance_snapshot",
    "acceptance_snapshot_ref", "snapshot", "relation", "lane", "release",
    "release_decision", "score", "scientific_score", "candidate_authority",
    "authority",
})


@dataclass(frozen=True)
class OuterReplayBindingRecord:
    """One-way verification-only constraint between stage material and an account.

    ``version_snapshot`` is frozen to an immutable mapping in ``__post_init__``,
    so a returned record cannot be tampered with through its nested content
    (``frozen=True`` alone only locks attribute rebinding) — parity with
    ``dp_roles.RoleRecord``.
    """

    binding_id: str
    bound_account: str
    stage_material_ref: str
    verification_statement: str
    verifier: str
    direction: str = DIRECTION
    verification_only: bool = True
    version_snapshot: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_snapshot",
                           MappingProxyType(dict(self.version_snapshot or {})))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "bound_account": self.bound_account,
            "stage_material_ref": self.stage_material_ref,
            "verification_statement": self.verification_statement,
            "verifier": self.verifier,
            "direction": self.direction,
            "verification_only": self.verification_only,
            # Every field is schema-validated on append, so no projection helper is
            # needed here (a hand-built record without a version fails closed).
            "version_snapshot": dict(self.version_snapshot or {}),
        }


def _validate_payload(p: Any) -> None:
    """Closed-schema validation for one persisted binding (fail-closed)."""
    if not isinstance(p, Mapping):
        raise ValueError("invalid outer binding: payload must be a mapping")
    keys = set(p)
    # Forbidden (promotion/proof) keys first so the rejection is specific.
    if keys & _FORBIDDEN_KEYS:
        raise ValueError(
            "invalid outer binding: forbidden key present: "
            + ", ".join(sorted(keys & _FORBIDDEN_KEYS)))
    if keys - _ALLOWED_KEYS or _ALLOWED_KEYS - keys:
        raise ValueError("invalid outer binding: closed key set violated")

    binding_id = p["binding_id"]
    if not isinstance(binding_id, str) or not binding_id:
        raise ValueError("invalid outer binding id")

    bound_account = p["bound_account"]
    if not isinstance(bound_account, str) or not bound_account.startswith(ACCOUNT_PREFIX):
        raise ValueError("invalid outer binding: bound_account must be an account reference")

    stage = p["stage_material_ref"]
    if not isinstance(stage, str) or not stage.startswith(STAGE_PREFIX):
        raise ValueError(
            "invalid outer binding: stage_material_ref must be pre-snapshot stage "
            f"material (prefix {STAGE_PREFIX})")
    # The direction cannot be reversed structurally: STAGE_PREFIX and
    # ACCOUNT_PREFIX are disjoint (asserted at import time above), so a stage
    # reference can never also be the bound account. (No separate equality check:
    # it would be unreachable.)

    for field in ("verification_statement", "verifier"):
        value = p[field]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"invalid outer binding: {field} must be a named non-empty string")

    statement = p["verification_statement"]
    if len(statement) > STATEMENT_MAX:
        raise ValueError(
            "invalid outer binding: "
            f"verification_statement must be at most {STATEMENT_MAX} characters")
    if any(marker in statement for marker in _PAYLOAD_MARKERS):
        raise ValueError(
            "invalid outer binding: verification_statement must be bounded prose; "
            "it must not carry a structured payload (braces, brackets or line breaks)")

    if p["direction"] != DIRECTION:
        raise ValueError(
            f"invalid outer binding: direction must be {DIRECTION} (it cannot be reversed)")
    if p["verification_only"] is not True:
        raise ValueError(
            "invalid outer binding: verification_only must be true (the binding is not proof)")

    version = p["version_snapshot"]
    if not isinstance(version, Mapping) or set(version) != _VERSION_KEYS:
        raise ValueError("invalid outer binding version snapshot shape")
    if not all(isinstance(v, str) for v in version.values()):
        raise ValueError("invalid outer binding version snapshot values")
    current = _current_version_snapshot()
    mismatches = {k: (version[k], current[k]) for k in _VERSION_KEYS
                  if version[k] != current[k]}
    if mismatches:
        raise ValueError(f"runtime snapshot mismatch: {mismatches}")


class DPOuterBindingStore(JSONLLedger):
    """Append-only JSONL ledger for outer bindings (own file, closed schema).

    Non-public by construction: nothing in the product surface reads this ledger,
    and the module exposes no route from a binding to authority.
    """

    _label = "outer binding ledger"
    _id_field = "binding_id"
    _validate_payload = staticmethod(_validate_payload)

    def _check_additional(self, payload: Mapping[str, Any],
                          existing: list[dict[str, Any]]) -> None:
        """Cardinality invariant, evaluated inside the append lock (T-01 L338).

        Scope (stated honestly): this is **at most one binding per account**. The
        stage-material side is deliberately NOT constrained — one stage package may
        be referenced by bindings for different accounts (T-01 L365: these are
        conceptual product-preservation boundaries, not database cardinalities).

        Reliance note: the direct ``other["bound_account"]`` index below is safe
        only because ``_payloads`` already validated every existing line against the
        closed schema before this hook runs (``dp_ledger._append_payload`` calls
        ``_validate_payload`` per line during read-back, then this hook). Do not
        treat this hook as an independent defense line: if that read-back validation
        is ever relaxed, this index becomes a ``KeyError`` path.
        """
        for other in existing:
            if other["bound_account"] == payload["bound_account"]:
                raise ValueError(
                    "at most one outer binding is permitted per account "
                    f"({payload['bound_account']} already has {other['binding_id']})")

    @staticmethod
    def _to_record(payload: Mapping[str, Any]) -> OuterReplayBindingRecord:
        return OuterReplayBindingRecord(
            binding_id=payload["binding_id"],
            bound_account=payload["bound_account"],
            stage_material_ref=payload["stage_material_ref"],
            verification_statement=payload["verification_statement"],
            verifier=payload["verifier"],
            direction=payload["direction"],
            verification_only=payload["verification_only"],
            version_snapshot=dict(payload["version_snapshot"]),
        )

    def append(self, record: OuterReplayBindingRecord) -> None:
        if not isinstance(record, OuterReplayBindingRecord):
            raise TypeError("append requires an OuterReplayBindingRecord")
        # No silent version fill: the record must carry its own version binding
        # (a hand-built record without one fails closed in _validate_payload).
        self._append_payload(record.to_dict())

    def get(self, binding_id: str) -> OuterReplayBindingRecord:
        for payload in self._payloads():
            if payload["binding_id"] == binding_id:
                return self._to_record(payload)
        raise KeyError(f"missing outer binding: {binding_id}")

    def all(self) -> list[OuterReplayBindingRecord]:
        return [self._to_record(p) for p in self._payloads()]


def record_outer_binding(store: DPOuterBindingStore, *, binding_id: str,
                         bound_account: str, stage_material_ref: str,
                         verification_statement: str, verifier: str,
                         direction: str = DIRECTION,
                         verification_only: bool = True) -> OuterReplayBindingRecord:
    """Record one non-public, one-way, verification-only outer binding."""
    record = OuterReplayBindingRecord(
        binding_id=binding_id,
        bound_account=bound_account,
        stage_material_ref=stage_material_ref,
        verification_statement=verification_statement,
        verifier=verifier,
        direction=direction,
        verification_only=verification_only,
        version_snapshot=dict(_current_version_snapshot()),
    )
    store.append(record)
    return record


def list_outer_bindings(store: DPOuterBindingStore) -> list[OuterReplayBindingRecord]:
    return store.all()


__all__ = ["DIRECTION", "DPOuterBindingStore", "NON_CLAIM", "OuterReplayBindingRecord",
           "STAGE_PREFIX", "list_outer_bindings", "record_outer_binding"]
