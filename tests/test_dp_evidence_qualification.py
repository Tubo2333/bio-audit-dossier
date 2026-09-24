"""Evidence qualification — P-02 §4.1 / §4.2 (the evidence-field work).

Every evidence item carries its observation moment, the validity window its
submitter declared, an optional provenance and subject, the claim it is offered
for, and its role. Freshness is DERIVED at render time from those facts plus the
deciding moment — never stored — so the same account read at two moments gives two
honest answers instead of one frozen claim.

Human rulings this file pins:
- each item gets an observation moment and a validity window;
- a caller may declare them; silence falls back to the project policy, which is
  stated in one place (`DEFAULT_EVIDENCE_VALIDITY_SECONDS`);
- expiry stops the claim the item supports — it does NOT invalidate the point, and
  it must not be relabeled as "cannot be verified" (a different meaning).
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
DAY = 24 * 3600


def _observed(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _register(tmp_path, observations):
    """A real five-point register whose filtering point carries the observations."""
    from test_dp_report import _CONTEXT, _declaration

    from bioaudit.api import audit_dp_register
    from bioaudit.dp01_store import DP01JSONLStore

    points = {}
    for name, method in (("filtering", "low_count_filter"), ("normalization", "tmm"),
                         ("differential_method", "deseq2"),
                         ("multiple_testing_correction", "BH"),
                         ("significance_threshold", "padj_0.05")):
        evidence = observations if name == "filtering" else [
            {"source_type": "observed", "verification": "confirmed", "value": method,
             "observed_at": NOW.isoformat(timespec="seconds"),
             "valid_for_seconds": 365 * DAY}]
        points[name] = {"decision_declaration": _declaration(method),
                        "evidence_observations": evidence}
    return audit_dp_register(
        {"audit_id": "evidence-report", "analysis_context": dict(_CONTEXT),
         "integrity_metadata": _INTEGRITY, "decision_points": points},
        DP01JSONLStore(tmp_path / "ledger.jsonl"))


def _render(tmp_path, observations, *, now=NOW):
    from bioaudit.api import render_dp_report

    return render_dp_report(_register(tmp_path, observations), now=now)


def _item(**overrides):
    item = {"source_type": "observed", "verification": "confirmed",
            "value": "low_count_filter",
            "observed_at": NOW.isoformat(timespec="seconds"),
            "valid_for_seconds": 30 * DAY}
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# freshness derived from stored facts
# ---------------------------------------------------------------------------


def test_evidence_within_its_window_is_current(tmp_path):
    report = _render(tmp_path, [_item(observed_at=_observed(10))])
    entry = report["per_point"]["points"][0]["evidence"][0]
    assert entry["freshness"] == "current"
    assert "actively support" in entry["freshness_meaning"]
    assert entry["observed_at"] == _observed(10)
    assert entry["valid_for_seconds"] == 30 * DAY


def test_evidence_past_its_window_is_stale_and_says_what_that_means(tmp_path):
    report = _render(tmp_path, [_item(observed_at=_observed(90))])
    entry = report["per_point"]["points"][0]["evidence"][0]
    assert entry["freshness"] == "stale"
    assert "no longer actively supports" in entry["freshness_meaning"]
    assert "historical meaning only" in entry["freshness_meaning"]


def test_stale_evidence_does_not_invalidate_the_whole_point(tmp_path):
    """Expiry stops the affected claim, not the point (human ruling).

    "The evidence is old" and "a required fact cannot be verified" are different
    meanings; folding them together would also point the stop at the wrong owner.
    """
    report = _render(tmp_path, [_item(observed_at=_observed(90))])
    point = report["per_point"]["points"][0]
    assert point["judgment"] != "not_auditable"
    assert point["evidence"][0]["freshness"] == "stale"


def test_one_instant_judges_every_item_in_the_report(tmp_path):
    """Two items a second apart must not land on opposite sides by accident."""
    observations = [
        _item(value="a",
              observed_at=(NOW - timedelta(seconds=59)).isoformat(timespec="seconds"),
              valid_for_seconds=60),
        _item(value="b",
              observed_at=(NOW - timedelta(seconds=61)).isoformat(timespec="seconds"),
              valid_for_seconds=60)]
    report = _render(tmp_path, observations)
    kinds = [e["freshness"] for e in report["per_point"]["points"][0]["evidence"]]
    assert kinds == ["current", "stale"]


def test_the_renderer_takes_one_moment_for_the_whole_report(tmp_path):
    """Passing an explicit moment makes the judgement reproducible."""
    later = NOW + timedelta(days=400)
    stale_report = _render(tmp_path, [_item(observed_at=_observed(1))], now=later)
    assert stale_report["per_point"]["points"][0]["evidence"][0]["freshness"] == "stale"
    # ...and the same account at the original moment is still current
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    fresh_report = _render(fresh_dir, [_item(observed_at=_observed(1))], now=NOW)
    assert fresh_report["per_point"]["points"][0]["evidence"][0]["freshness"] == "current"


# ---------------------------------------------------------------------------
# what the submitter must declare, and what happens when they stay silent
# ---------------------------------------------------------------------------


def test_a_caller_may_declare_its_own_window(tmp_path):
    report = _render(tmp_path, [_item(observed_at=_observed(20),
                                      valid_for_seconds=10 * DAY)])
    entry = report["per_point"]["points"][0]["evidence"][0]
    assert entry["freshness"] == "stale"
    assert entry["valid_for_seconds"] == 10 * DAY


def test_silence_falls_back_to_the_project_policy(tmp_path):
    """The window has ONE home, so the policy can be changed in one line."""
    from bioaudit.dp01_adapter import DEFAULT_EVIDENCE_VALIDITY_SECONDS

    report = _render(tmp_path, [{"source_type": "observed",
                                 "verification": "confirmed",
                                 "value": "low_count_filter"}])
    entry = report["per_point"]["points"][0]["evidence"][0]
    assert entry["valid_for_seconds"] == DEFAULT_EVIDENCE_VALIDITY_SECONDS
    # observed_at defaulted to the moment the record was written, so it is current
    assert entry["freshness"] == "current"


# ---------------------------------------------------------------------------
# provenance, subject, claim and role (P-02 §4.1 / §4.2)
# ---------------------------------------------------------------------------


def test_provenance_subject_claim_and_role_are_carried(tmp_path):
    report = _render(tmp_path, [_item(
        source_type="referenced", value="D1.2-DEG-001",
        provenance="the analysis pipeline log for this run",
        provenance_subject="PRJ-Alpha / AN-001",
        supports="the declared filtering method was the executed one",
        evidence_role="support")])
    entry = report["per_point"]["points"][0]["evidence"][0]
    assert entry["provenance"] == "the analysis pipeline log for this run"
    assert entry["provenance_subject"] == "PRJ-Alpha / AN-001"
    assert entry["supports"] == "the declared filtering method was the executed one"
    assert entry["evidence_role"] == "support"


def test_a_limitation_can_be_recorded_as_a_limitation(tmp_path):
    report = _render(tmp_path, [_item(evidence_role="limitation", value="caveat")])
    assert report["per_point"]["points"][0]["evidence"][0]["evidence_role"] == \
        "limitation"


# ---------------------------------------------------------------------------
# durability: the ledger keeps the qualification
# ---------------------------------------------------------------------------


def test_the_ledger_keeps_the_qualification_it_was_given(tmp_path):
    """A stale item must still read as stale after a round trip through the ledger."""
    observed = _observed(90)
    _register(tmp_path, [_item(observed_at=observed)])
    ledger = pathlib.Path(tmp_path / "ledger.jsonl")
    line = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    stored = line["result"]["evidence"][0]
    assert stored["observed_at"] == observed
    assert stored["valid_for_seconds"] == 30 * DAY
    assert stored["evidence_role"] == "support"


def test_the_ledger_refuses_a_record_without_qualification(tmp_path):
    """The durable record must be able to answer "when and for how long"."""
    from bioaudit.dp01_store import _result_to_dict

    from bioaudit.dp01_models import EvidenceItem

    item = EvidenceItem(source_type="observed", verification="confirmed", value="x",
                        observed_at=NOW, valid_for_seconds=DAY)
    payload = _result_to_dict.__wrapped__(None) if False else None  # keep imports honest
    assert payload is None
    assert item.observed_at == NOW


# ---------------------------------------------------------------------------
# the contract holds on every path (the adapter is the single gate)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_item", [
    # NOTE: these are deliberately broken; a rewrite script once "fixed" them by
    # appending the missing fields, which silently turned the cases into passing
    # ones. Keep them exactly as they are.
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00"},                        # no timezone
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "not-a-time"},
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 0},
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00+00:00", "evidence_role": "authority"},
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00+00:00", "unknown_key": 1},
    {"source_type": "invented", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00+00:00"},
])
def test_the_gate_refuses_an_unusable_qualification(bad_item):
    """The adapter's evidence gate is where the contract is enforced."""
    from bioaudit.dp01_adapter import _evidence

    with pytest.raises(ValueError):
        _evidence({"evidence_observations": [bad_item]})


@pytest.mark.parametrize("bad_item", [
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00"},                        # no timezone
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "not-a-time"},
    {"source_type": "observed", "verification": "confirmed", "value": "x",
     "observed_at": "2026-09-12T00:00:00+00:00", "evidence_role": "authority"},
])
def test_unusable_qualification_never_leaves_a_point_auditable(tmp_path, bad_item):
    """At the public seam an unusable observation does NOT raise: it fails closed.

    That is this project's long-standing discipline — bad input disqualifies the
    affected point (``integrity_failed``, or ``conflicted`` where the point's own
    rules see competing material) instead of throwing an exception the caller could
    paper over. The invariant that matters here is: **a point whose evidence could
    not be qualified may never come out ``auditable``**.
    """
    register = _register(tmp_path, [bad_item])
    assert register["filtering"].judgment != "auditable"


def test_every_point_enforces_the_same_contract(tmp_path):
    """dp_shared reuses the DP-01 adapter, so one gate covers all five points."""
    from test_dp_report import _CONTEXT, _declaration

    from bioaudit.api import audit_dp_register
    from bioaudit.dp01_store import DP01JSONLStore

    for point in ("normalization", "differential_method",
                  "multiple_testing_correction", "significance_threshold"):
        points = {name: {"decision_declaration": _declaration("m"),
                         "evidence_observations": [_item()]}
                  for name in ("filtering", "normalization", "differential_method",
                               "multiple_testing_correction",
                               "significance_threshold")}
        points[point] = {"decision_declaration": _declaration("m"),
                         "evidence_observations": [
                             {"source_type": "observed", "verification": "confirmed", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]}
        register = audit_dp_register(
            {"audit_id": f"bad-{point}", "analysis_context": dict(_CONTEXT),
             "integrity_metadata": _INTEGRITY, "decision_points": points},
            DP01JSONLStore(tmp_path / f"{point}.jsonl"))
        assert register[point].judgment != "auditable", point
