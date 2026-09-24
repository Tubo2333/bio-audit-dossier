"""Shared append-only JSONL ledger skeleton (review-fix: kill the drift tax).

``dp01_store``, ``dp_authority`` and ``dp_roles`` had each grown their own copy
of the same durability discipline (advisory append lock, flush + fsync, read-back
validation with the offending line pinned, cross-line duplicate rejection). The
lock copies were byte-identical.

This module holds that skeleton ONCE. Schema validation stays in each owning
module: subclasses supply ``_validate_payload`` (and their own record builders),
because the closed key set belongs to the ledger's meaning, not to the skeleton.

``dp01_store.py`` deliberately stays untouched (it is frozen historical code);
only ``dp_authority`` and ``dp_roles`` build on this base.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class JSONLLedger:
    """Append-only JSONL ledger base: locking, durability, and read-back checks.

    Subclasses must provide:
    - ``_validate_payload(payload)``: closed-schema validation (raises ValueError)
    - ``_record_id(payload)``: the identity field used for duplicate detection
    """

    #: Human-readable ledger name used in error messages.
    _label = "ledger"
    #: Payload key holding the record identity.
    _id_field = "record_id"

    def __init__(self, path):
        if not path:
            raise ValueError(f"{self._label} path is required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- hooks ---------------------------------------------------------------
    def _validate_payload(self, payload: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _record_id(self, payload: Mapping[str, Any]) -> str:
        return payload[self._id_field]

    def _check_additional(self, payload: Mapping[str, Any],
                          existing: list[dict[str, Any]]) -> None:
        """Optional per-ledger invariant, evaluated INSIDE the append lock.

        Default is a no-op. Subclasses override this for ledger-specific rules
        (e.g. cardinality limits) so the check runs under the same lock as the
        duplicate check — no second lock acquisition, no TOCTOU window.
        """
        return None

    # -- locking -------------------------------------------------------------
    @contextmanager
    def _append_lock(self):
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                lock.write("0")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # -- read ---------------------------------------------------------------
    def _payloads(self) -> list[dict[str, Any]]:
        """Read and validate every line, rejecting malformed or duplicated records."""
        if not self.path.exists():
            return []
        payloads: list[dict[str, Any]] = []
        seen: set[str] = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid {self._label} line {lineno}: {exc}") from exc
                self._validate_payload(payload)
                record_id = self._record_id(payload)
                if record_id in seen:
                    raise ValueError(
                        f"duplicate {self._label} id at line {lineno}: {record_id}")
                seen.add(record_id)
                payloads.append(payload)
        return payloads

    # -- write --------------------------------------------------------------
    def _append_payload(self, payload: Mapping[str, Any]) -> None:
        """Validate, then append under the lock with flush + fsync."""
        self._validate_payload(payload)
        with self._append_lock():
            # Read the ledger ONCE: the duplicate check and any subclass
            # invariant share the same snapshot of existing records.
            existing = self._payloads()
            for other in existing:
                if self._record_id(other) == self._record_id(payload):
                    raise ValueError(
                        f"duplicate {self._label} id: {self._record_id(payload)}")
            self._check_additional(payload, existing)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())


__all__ = ["JSONLLedger"]
