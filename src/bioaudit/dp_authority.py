"""Sole result-authority container (inherited C-03 route).

Implements the INHERITED authority route only — it does not invent a new one:

    stage_package  (pre-snapshot input; not final result authority)
      → acceptance_snapshot   (sole result-authority container)
          containing exactly one embedded structured_strength_result
      → acceptance_snapshot_ref   (one-way; the only way a later release
                                   decision may reach result authority)

Forbidden shortcuts fail closed (T-01 L55/L253/L580; P-03 row142): a second
snapshot, second result, copied result, alternative carrier, independent result
reference, `structured_strength_result_ref`, or snapshot back-reference.

Authority basis (review-fix, Spec F1 per human decision (b)+(c)): the container
still persists when integrity domains are not confirmed, but it does NOT
unconditionally assert authority — it records the authority basis
(``authority_basis``) and the embedded result's ``_provenance.authoritative`` is
BOUND to that basis. Domain names/states reuse the existing vocabulary
(``dp01_adapter`` integrity domains); no new public semantic is introduced.

Persistence uses its own ledger with a closed schema, an append lock and fsync.
``dp01_store.py`` is untouched.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bioaudit.dp01_store import _current_version_snapshot, _result_to_dict
from bioaudit.dp_assembly import _POINT_SEAMS, audit_dp_register
from bioaudit.dp_ledger import JSONLLedger
from bioaudit.dp_strength import derive_dp_strength

_AXES = ("validation", "scope", "inference_type", "explanation_depth")
_SUFFIXES = ("dp01", "dp02", "dp03", "dp04", "dp05")

# Keys that would create a second result / carrier / authority path.
_FORBIDDEN_KEYS = frozenset({
    "structured_strength_result_ref",
    "second_snapshot",
    "second_result",
    "alternative_carrier",
    "back_reference",
    "copied_result",
    "independent_result_reference",
    "results",
})

_ALLOWED_PAYLOAD_KEYS = frozenset({
    "snapshot_id", "stage_package", "structured_strength_result",
    "version_snapshot", "integrity_metadata", "audit_ids", "authority_basis",
})
_ALLOWED_STAGE_PACKAGE_KEYS = frozenset({"input_snapshot"})
_ALLOWED_BASIS_KEYS = frozenset({"confirmed", "unconfirmed_domains", "basis"})

_VERSION_KEYS = frozenset({
    "ruleset_version", "ontology_version", "engine_version",
    "input_format_version", "adapter_version",
})
_INTEGRITY_DOMAINS = ("identity", "version", "source", "binding", "snapshot",
                      "permission", "unique_authority")
_INTEGRITY_STATES = frozenset({"confirmed", "failed", "unverified", "conflicted"})


def _plain(value: Any) -> Any:
    """JSON-plain projection of the supported structured value domain.

    Non-string mapping keys are rejected with ``TypeError`` (parity with
    ``dp01_store._plain``) rather than silently coerced to strings.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("authority mapping keys must be strings")
            out[key] = _plain(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise TypeError(f"unsupported authority value: {type(value).__name__}")


def _authority_basis(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Record whether the authority basis (integrity domains) is confirmed."""
    unconfirmed = sorted(
        domain for domain in _INTEGRITY_DOMAINS
        if not (isinstance(metadata.get(domain), Mapping)
                and metadata[domain].get("state") == "confirmed")
    )
    confirmed = not unconfirmed
    return {
        "confirmed": confirmed,
        "unconfirmed_domains": unconfirmed,
        "basis": ("integrity domains confirmed" if confirmed
                  else "integrity domains not confirmed"),
    }


@dataclass(frozen=True)
class StagePackage:
    """Pre-snapshot input (T-01: 'stage_package is pre-snapshot input')."""

    input_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"input_snapshot": _plain(self.input_snapshot)}


@dataclass(frozen=True)
class AcceptanceSnapshot:
    """Sole result-authority container holding exactly one embedded result."""

    snapshot_id: str
    stage_package: StagePackage
    structured_strength_result: Mapping[str, Any]
    version_snapshot: Mapping[str, Any]
    integrity_metadata: Mapping[str, Any]
    audit_ids: tuple[str, ...]
    authority_basis: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "stage_package": self.stage_package.to_dict(),
            "structured_strength_result": _plain(self.structured_strength_result),
            "version_snapshot": _plain(self.version_snapshot),
            "integrity_metadata": _plain(self.integrity_metadata),
            "audit_ids": list(self.audit_ids),
            "authority_basis": _plain(self.authority_basis),
        }


def _validate_audit_ids(audit_ids: Any) -> None:
    """The five audit ids must bind to ONE audit prefix and the ordered suffixes."""
    if not isinstance(audit_ids, list) or len(audit_ids) != 5 \
            or not all(isinstance(x, str) and x and ":" in x for x in audit_ids):
        raise ValueError("invalid audit_ids (expected five '<audit>:<suffix>' ids)")
    prefixes = {x.rsplit(":", 1)[0] for x in audit_ids}
    suffixes = [x.rsplit(":", 1)[1] for x in audit_ids]
    if len(prefixes) != 1 or not next(iter(prefixes)) or suffixes != list(_SUFFIXES):
        raise ValueError(
            "invalid audit_ids: must bind to one audit with ordered dp01..dp05 suffixes")


def _validate_integrity_metadata(metadata: Any) -> None:
    if not isinstance(metadata, Mapping):
        raise ValueError("invalid integrity metadata")
    if set(metadata) != set(_INTEGRITY_DOMAINS) | {"material_ids", "pending_targets"}:
        raise ValueError("invalid integrity metadata shape")
    for domain in _INTEGRITY_DOMAINS:
        entry = metadata[domain]
        if not isinstance(entry, Mapping) or set(entry) != {"state"} \
                or entry["state"] not in _INTEGRITY_STATES:
            raise ValueError("invalid integrity domain state")
    for key in ("material_ids", "pending_targets"):
        if not isinstance(metadata[key], list) or not all(
                isinstance(x, str) and x for x in metadata[key]):
            raise ValueError("invalid integrity targets")


def _validate_payload(p: Any) -> None:
    """Closed-schema validation for one persisted snapshot payload (fail-closed)."""
    if not isinstance(p, Mapping):
        raise ValueError("invalid authority snapshot: payload must be a mapping")
    keys = set(p)
    # Forbidden-path check FIRST so the rejection is reachable and specific
    # (review-fix: previously dead code behind the closed-key-set check).
    if keys & _FORBIDDEN_KEYS:
        raise ValueError(
            "invalid authority snapshot: forbidden shortcut key present: "
            + ", ".join(sorted(keys & _FORBIDDEN_KEYS)))
    if keys - _ALLOWED_PAYLOAD_KEYS or _ALLOWED_PAYLOAD_KEYS - keys:
        raise ValueError("invalid authority snapshot: closed key set violated")

    snapshot_id = p["snapshot_id"]
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("invalid authority snapshot id")

    stage = p["stage_package"]
    if not isinstance(stage, Mapping) or set(stage) != _ALLOWED_STAGE_PACKAGE_KEYS:
        raise ValueError("invalid stage_package shape")
    if not isinstance(stage["input_snapshot"], Mapping):
        raise ValueError("invalid stage_package input_snapshot")

    result = p["structured_strength_result"]
    if not isinstance(result, Mapping):
        raise ValueError(
            "structured_strength_result must be exactly one embedded mapping")
    if set(result) & _FORBIDDEN_KEYS:
        raise ValueError("structured_strength_result must be embedded, not referenced")
    if not set(_AXES) <= set(result):
        raise ValueError("structured_strength_result is missing required axes")

    basis = p["authority_basis"]
    if not isinstance(basis, Mapping) or set(basis) != _ALLOWED_BASIS_KEYS:
        raise ValueError("invalid authority_basis shape")
    if not isinstance(basis["confirmed"], bool):
        raise ValueError("invalid authority_basis confirmed flag")
    if not isinstance(basis["unconfirmed_domains"], list) or not all(
            isinstance(x, str) and x in _INTEGRITY_DOMAINS
            for x in basis["unconfirmed_domains"]):
        raise ValueError("invalid authority_basis unconfirmed_domains")

    provenance = result.get("_provenance")
    if not isinstance(provenance, Mapping) or provenance.get("sole_container") is not True:
        raise ValueError("structured_strength_result provenance must set sole_container")
    # The authority assertion must be BOUND to the recorded basis (Spec F1 b+c):
    # never assert `authoritative` while the basis is unconfirmed.
    if provenance.get("authoritative") is not basis["confirmed"]:
        raise ValueError(
            "structured_strength_result provenance.authoritative must match authority_basis")

    version = p["version_snapshot"]
    if not isinstance(version, Mapping) or set(version) != _VERSION_KEYS:
        raise ValueError("invalid authority version snapshot shape")
    if not all(isinstance(v, str) for v in version.values()):
        raise ValueError("invalid authority version snapshot values")
    current = _current_version_snapshot()
    mismatches = {k: (version[k], current[k]) for k in _VERSION_KEYS
                  if version[k] != current[k]}
    if mismatches:
        raise ValueError(f"runtime snapshot mismatch: {mismatches}")

    _validate_integrity_metadata(p["integrity_metadata"])
    _validate_audit_ids(p["audit_ids"])


class DPAuthorityStore(JSONLLedger):
    """Append-only JSONL ledger for acceptance snapshots (own file, closed schema).

    Durability comes from ``JSONLLedger`` (append lock, flush + fsync, read-back
    validation pinning the offending line, cross-line duplicate rejection) so the
    same discipline is not copied per ledger. The closed key set is authority
    specific and stays here.
    """

    _label = "authority ledger"
    _id_field = "snapshot_id"
    _validate_payload = staticmethod(_validate_payload)

    def append(self, snapshot: AcceptanceSnapshot) -> None:
        if not isinstance(snapshot, AcceptanceSnapshot):
            raise TypeError("append requires an AcceptanceSnapshot")
        self._append_payload(snapshot.to_dict())

    def _check_additional(self, payload: Mapping[str, Any],
                          existing: list[dict[str, Any]]) -> None:
        """One acceptance snapshot per governed account, checked inside the lock.

        T-01 L338 / P-03 row142 / C-03: ``stage_package -> acceptance_snapshot`` is
        a ONE-way, one-to-one binding — there is exactly one conceptual acceptance
        binding per governed account. The ledger already rejects a duplicate
        ``snapshot_id``; that alone did not stop a second container for the same
        account (the same five governed point records plus the same pre-snapshot
        input) under a different id, which would make two containers claim the same
        account.

        The account identity used here is the pair (the five governed point record
        ids, the registered input snapshot): one such account may produce exactly
        one acceptance snapshot. A different input snapshot (a genuinely new
        bounded judgment) is a different account and may have its own snapshot.

        Reliance note: the direct indexing below is safe only because ``_payloads``
        already validated every existing line against the closed schema before this
        hook runs; this is not an independent defense line.
        """
        for other in existing:
            if (list(other["audit_ids"]) == list(payload["audit_ids"])
                    and other["stage_package"]["input_snapshot"]
                    == payload["stage_package"]["input_snapshot"]):
                raise ValueError(
                    "an account already has an acceptance snapshot; a second "
                    "snapshot for the same governed point records and input "
                    f"snapshot is not permitted ({other['snapshot_id']} exists)")

    def get(self, snapshot_id: str) -> AcceptanceSnapshot:
        for payload in self._payloads():
            if payload["snapshot_id"] == snapshot_id:
                # Deep copies: the read path must not hand out internal references.
                return AcceptanceSnapshot(
                    snapshot_id=payload["snapshot_id"],
                    stage_package=StagePackage(
                        copy.deepcopy(payload["stage_package"]["input_snapshot"])),
                    structured_strength_result=copy.deepcopy(
                        payload["structured_strength_result"]),
                    version_snapshot=copy.deepcopy(payload["version_snapshot"]),
                    integrity_metadata=copy.deepcopy(payload["integrity_metadata"]),
                    audit_ids=tuple(payload["audit_ids"]),
                    authority_basis=copy.deepcopy(payload["authority_basis"]),
                )
        raise KeyError(f"missing authority snapshot: {snapshot_id}")


def _canonical_result(result: Any, label: str) -> Any:
    """Canonical, comparison-safe projection of a point result (fail-closed)."""
    try:
        return _result_to_dict(result)
    except AttributeError as exc:
        raise TypeError(f"{label} does not look like a point result: {exc}") from exc


def _point_records(audit_id: str, point_store: Any) -> dict[str, tuple[str, Any, Any]]:
    """Fetch the five persisted point records (single fetch, shared by both paths).

    Returns ``{point_name: (record_id, result, input_snapshot)}``.

    Raises
    ------
    ValueError
        A required point record is missing from the ledger.
    TypeError
        The store returns something that does not expose ``result`` and
        ``input_snapshot`` (duck-typed store mismatch) — never a bare
        ``AttributeError``.
    """
    records: dict[str, tuple[str, Any, Any]] = {}
    for name, (_seam, suffix) in _POINT_SEAMS.items():
        record_id = f"{audit_id}:{suffix}"
        try:
            record = point_store.get(record_id)
        except KeyError as exc:
            raise ValueError(
                f"authority snapshot references a missing point record: {record_id}") from exc
        result = getattr(record, "result", None)
        input_snapshot = getattr(record, "input_snapshot", None)
        if result is None or input_snapshot is None:
            raise TypeError(
                f"point store record {record_id} must expose 'result' and 'input_snapshot'")
        records[name] = (record_id, result, input_snapshot)
    return records


def _expected_point_snapshot(assembly_request: Mapping[str, Any], name: str,
                            record_id: str) -> dict[str, Any]:
    """Rebuild the per-point request snapshot a registration would have stored.

    ``audit_dp_register`` unpacks one assembly request into five per-point
    requests shaped ``{audit_id: '<audit>:<suffix>', analysis_context,
    integrity_metadata, **decision_points[point]}``; that per-point request is
    what the ledger actually persists as ``input_snapshot``. Comparing the
    assembly request to it directly would be a category error, so the expected
    per-point snapshot is rebuilt here and compared to the stored one.

    Raises ValueError when the assembly request is not shaped as expected.
    """
    points = assembly_request.get("decision_points")
    if not isinstance(points, Mapping) or set(points) != set(_POINT_SEAMS):
        raise ValueError("assembly request must carry exactly the five decision_points")
    point_value = points[name]
    if not isinstance(point_value, Mapping):
        raise ValueError(f"decision_points[{name}] must be a mapping")
    context = assembly_request.get("analysis_context")
    integrity = assembly_request.get("integrity_metadata")
    if not isinstance(context, Mapping) or not isinstance(integrity, Mapping):
        raise ValueError("assembly request must carry analysis_context and integrity_metadata")

    expected = {
        "audit_id": record_id,
        "analysis_context": _plain(context),
        "integrity_metadata": _plain(integrity),
    }
    for key, value in point_value.items():
        expected[key] = _plain(value)
    return expected


def _verify_prebuilt_register(register: Any, assembly_request: Mapping[str, Any],
                             records: Mapping[str, tuple[str, Any, Any]]) -> None:
    """Strictly verify a caller-supplied register against the persisted records.

    The register is already a product concept (returned by ``audit_dp_register``);
    accepting one here removes the double-registration conflict. A snapshot must
    never embed a result — or a ``stage_package`` — that disagrees with the
    ledger it references, so two bindings are enforced per point:

    1. the per-point request rebuilt from the caller's ``assembly_request`` must
       equal the ``input_snapshot`` the ledger actually registered (otherwise the
       container's pre-snapshot input and its embedded result would come from two
       different inputs); and
    2. the register's point result must equal the persisted record's result
       (compared on the ledger's own canonical serialization).

    Raises
    ------
    TypeError
        ``register`` is not a mapping, or a point value does not look like a
        result object.
    ValueError
        ``_register_meta`` is missing, the point set is not exactly the five, the
        request disagrees with the persisted input snapshot, or a point result
        disagrees with the persisted record.
    """
    if not isinstance(register, Mapping):
        raise TypeError("register must be a mapping")
    if not isinstance(register.get("_register_meta"), Mapping):
        raise ValueError("register must include a _register_meta mapping")

    point_names = {name for name in register if name != "_register_meta"}
    expected = set(_POINT_SEAMS)
    if point_names != expected:
        detail = []
        missing = expected - point_names
        extra = point_names - expected
        if missing:
            detail.append("missing: " + ", ".join(sorted(missing)))
        if extra:
            detail.append("unknown: " + ", ".join(sorted(extra)))
        raise ValueError(
            "register must contain exactly the five points (" + "; ".join(detail) + ")")

    for name, (record_id, result, input_snapshot) in records.items():
        if _plain(input_snapshot) != _expected_point_snapshot(assembly_request, name, record_id):
            raise ValueError(
                "assembly request does not match the persisted input snapshot of "
                f"{record_id}")
        if _canonical_result(register[name], f"register point '{name}'") != \
                _canonical_result(result, f"point record {record_id}"):
            raise ValueError(
                f"register point '{name}' does not match the persisted record {record_id}")


def build_acceptance_snapshot(assembly_request: Mapping[str, Any], point_store: Any,
                              authority_store: DPAuthorityStore, *,
                              snapshot_id: str | None = None,
                              register: Mapping[str, Any] | None = None) -> AcceptanceSnapshot:
    """Build and persist the sole-authority snapshot for one assembly request.

    Reuses the existing point seams (``audit_dp_register``) and the reviewed
    four-axis meaning (``derive_dp_strength``). The embedded result is THE
    authority inside this container, but its ``authoritative`` flag is bound to
    the recorded authority basis rather than asserted unconditionally.

    ``register`` (optional): an already-built register for the same audit. When
    supplied, the point registration is NOT re-run — this is what lets the two
    public entry points compose in the natural order. The supplied register is
    verified per point against the persisted records, and the request is verified
    against the persisted input snapshot, and both are rejected on mismatch.
    """
    if not isinstance(assembly_request, Mapping):
        raise TypeError("assembly request must be a mapping")
    if set(assembly_request) & _FORBIDDEN_KEYS:
        raise ValueError("assembly request must not carry a forbidden result path")

    audit_id = assembly_request.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("audit_id is required for the authority snapshot")

    if register is None:
        # Point records live in the point ledger; this persists and validates shape.
        register = audit_dp_register(assembly_request, point_store)
        # Single existence cross-check for this path (the prebuilt path checks
        # the same records inside _verify_prebuilt_register).
        _point_records(audit_id, point_store)
    else:
        _verify_prebuilt_register(register, assembly_request,
                                  _point_records(audit_id, point_store))

    metadata = assembly_request.get("integrity_metadata")
    basis = _authority_basis(metadata if isinstance(metadata, Mapping) else {})

    audit_ids = tuple(f"{audit_id}:{suffix}" for suffix in _SUFFIXES)

    axes = derive_dp_strength(register)
    embedded = {axis: axes[axis] for axis in _AXES}
    embedded["_provenance"] = {"authoritative": basis["confirmed"],
                               "sole_container": True}

    snapshot = AcceptanceSnapshot(
        snapshot_id=snapshot_id or f"snapshot:{audit_id}",
        stage_package=StagePackage(copy.deepcopy(dict(assembly_request))),
        structured_strength_result=embedded,
        version_snapshot=dict(_current_version_snapshot()),
        integrity_metadata=copy.deepcopy(dict(metadata)),
        audit_ids=audit_ids,
        authority_basis=basis,
    )
    authority_store.append(snapshot)
    return snapshot


def acceptance_snapshot_ref(snapshot: AcceptanceSnapshot) -> dict[str, Any]:
    """Return the one-way reference a later release decision may consume.

    Deliberately exposes no result content and no second container path: the ref
    names the snapshot and its version binding only.
    """
    if not isinstance(snapshot, AcceptanceSnapshot):
        raise TypeError("snapshot must be an AcceptanceSnapshot")
    return {
        "acceptance_snapshot_ref": {
            "snapshot_id": snapshot.snapshot_id,
            "version_snapshot": _plain(snapshot.version_snapshot),
        }
    }


__all__ = ["AcceptanceSnapshot", "DPAuthorityStore", "StagePackage",
           "acceptance_snapshot_ref", "build_acceptance_snapshot"]
