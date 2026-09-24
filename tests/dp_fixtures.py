"""Shared test fixtures for the DP workline.

The evidence contract became stricter with the evidence-qualification work: every
observation must declare WHEN it was observed and HOW LONG it stays current
(P-02 §4.1). Rather than repeat that in every test, the helper below produces a
qualified observation that tests can override field by field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: A fixed observation moment, so a test's freshness outcome is deterministic
#: instead of drifting with the wall clock.
OBSERVED_AT = "2026-09-12T00:00:00+00:00"

#: A year-long validity window: current for every test that does not deliberately
#: age its evidence.
ONE_YEAR = 365 * 24 * 60 * 60


def evidence(source_type: str = "observed", verification: str = "confirmed",
             value: object = "low_count_filter", *, observed_at: str = OBSERVED_AT,
             valid_for_seconds: int = ONE_YEAR, provenance: str | None = None,
             provenance_subject: str | None = None, supports: str | None = None,
             evidence_role: str = "support") -> dict:
    """One evidence observation carrying the fields the contract now requires."""
    item: dict = {
        "source_type": source_type,
        "verification": verification,
        "value": value,
        "observed_at": observed_at,
        "valid_for_seconds": valid_for_seconds,
    }
    if provenance is not None:
        item["provenance"] = provenance
    if provenance_subject is not None:
        item["provenance_subject"] = provenance_subject
    if supports is not None:
        item["supports"] = supports
    if evidence_role != "support":
        item["evidence_role"] = evidence_role
    return item


def aged(days: int, *, at: str = OBSERVED_AT) -> str:
    """An observation moment ``days`` before the fixed one (for staleness tests)."""
    moment = datetime.fromisoformat(at) - timedelta(days=days)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
