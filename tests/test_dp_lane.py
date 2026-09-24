"""DP-LANE — release/lane decision record tests (C-04 narrow first slice).

Covers the closed lane domain, the derived ceiling, fail-closed behaviour, the
static consistency rules C-04 §8 states, the durable record shape, and the
guarantee that reading a ledger can never yield an authorising lane the facts do
not support.

KEY SHAPE (after the adversarial review, findings F1/F5): a stored record carries
FACTS, never conclusions. ``lane_ceiling``, ``ceiling_reason``, ``version_currency``
and ``authorising`` are DERIVED on read from those facts, so neither a hand-written
ledger line nor a future code path can make the report display a lane the evidence
does not support. The write time is recorded per line rather than inferred from the
file, so two decisions are distinguishable in time and survive copying.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

import pytest

from bioaudit.dp_lane import (
    DP_LANE_OUTCOMES,
    DP_LANES,
    DPLaneStore,
    lane_ceiling,
    record_lane_decision,
)

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return DPLaneStore(tmp_path / "lane.jsonl")


def _record(store, **overrides):
    kwargs = dict(
        record_id="L1",
        acceptance_snapshot_ref="snap-A",
        requested_lane="candidate",
        decision_outcome="accepted",
        scope="DP-01 filtering / research use",
        axes_auditable=True,
        lane_predicate_evidence_refs=("ev-1",),
    )
    kwargs.update(overrides)
    return record_lane_decision(store, **kwargs)


def _register(tmp_path):
    """A real five-point register, built with the report suite's own fixtures."""
    from test_dp_report import _CONTEXT, _FIVE_AUDITABLE

    from bioaudit.api import audit_dp_register
    from bioaudit.dp01_store import DP01JSONLStore

    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    return audit_dp_register({"audit_id": "lane-report",
                              "analysis_context": dict(_CONTEXT),
                              "integrity_metadata": _INTEGRITY,
                              "decision_points": dict(_FIVE_AUDITABLE)}, ledger)


def _render_report_with(store):
    from bioaudit.api import render_dp_report

    return render_dp_report(_register(pathlib.Path(store.path).parent),
                           lane=store.all())


def _write_line(store, payload):
    store.path.write_text(json.dumps(payload, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")


def _forged(store, **overrides):
    """A valid hand-written ledger line: honest facts, chosen claims.

    The version binding is the runtime one, so the line is schema-valid and the
    only thing left to test is whether its CLAIMS (lane, outcome, any stored derived
    value) survive the read-time derivation.
    """
    payload = {
        "record_id": "forged",
        "acceptance_snapshot_ref": "snap-A",
        "requested_lane": "candidate",
        "decided_lane": "candidate",
        "decision_outcome": "accepted",
        "scope": "scope-A",
        "version_snapshot": dict(store._current_version()),
        "axes_auditable": True,
        "residual_unordered": False,
        "has_restrictions": False,
        "evidence_stale_support": False,
        "recorded_at": "2026-01-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# closed domain (C-04 §4: never renamed or expanded)
# ---------------------------------------------------------------------------


def test_closed_lane_domain_is_the_inherited_four():
    assert DP_LANES == ("research-draft", "candidate", "validated", "production")
    assert DP_LANE_OUTCOMES == ("accepted", "rejected", "return_for_evidence",
                                "withdrawn")


def test_lane_outside_the_closed_domain_is_refused(store):
    """C-04 §11 forbids inventing product-facing lane labels."""
    for bad in ("draft", "release-ready", "Production", "shipped", ""):
        with pytest.raises(ValueError):
            _record(store, requested_lane=bad)


def test_unknown_outcome_is_refused(store):
    with pytest.raises(ValueError):
        _record(store, decision_outcome="approved")


def test_result_authority_can_only_be_consumed_by_reference(store):
    """C-04 §3: the only route to result authority is acceptance_snapshot_ref."""
    with pytest.raises(ValueError):
        _record(store, acceptance_snapshot_ref="")


# ---------------------------------------------------------------------------
# derived ceiling (C-04 §6) — the heart of this slice
# ---------------------------------------------------------------------------


def test_auditable_vector_without_ordering_is_capped_at_candidate(store):
    """C-04 §6 row 3: completeness does not authorise a higher lane."""
    record = _record(store)
    assert record.decided_lane == "candidate"
    assert record.lane_ceiling == "candidate"
    assert "no authorised cross-axis ordering" in record.ceiling_reason


def test_validated_and_production_are_unreachable_in_this_build():
    for kwargs in (dict(integrity_ok=True, version_currency="current",
                        axes_auditable=True, residual_unordered=False,
                        has_restrictions=False),
                   dict(integrity_ok=True, version_currency="current",
                        axes_auditable=True, residual_unordered=True,
                        has_restrictions=True)):
        ceiling, reason = lane_ceiling(**kwargs)
        assert ceiling in ("candidate", "research-draft")
        assert reason


def test_integrity_failure_admits_no_valid_conclusion():
    """C-04 §6 row 1 + §9."""
    ceiling, reason = lane_ceiling(integrity_ok=False, version_currency="current",
                                   axes_auditable=True, residual_unordered=False,
                                   has_restrictions=False)
    assert ceiling == "research-draft"
    assert "integrity" in reason


def test_unverifiable_axis_is_integrity_class_not_scientific_insufficiency():
    """C-04 §6 row 2: it must not be reinterpreted as `insufficient`."""
    ceiling, reason = lane_ceiling(integrity_ok=True, version_currency="current",
                                   axes_auditable=False, residual_unordered=False,
                                   has_restrictions=False)
    assert ceiling == "research-draft"
    assert "insufficiency" in reason


def test_residual_unordered_axes_restrict_the_candidate():
    ceiling, reason = lane_ceiling(integrity_ok=True, version_currency="current",
                                   axes_auditable=True, residual_unordered=True,
                                   has_restrictions=False)
    assert ceiling == "candidate"
    assert "residual unordered" in reason


def test_restrictions_restrict_the_candidate():
    ceiling, reason = lane_ceiling(integrity_ok=True, version_currency="current",
                                   axes_auditable=True, residual_unordered=False,
                                   has_restrictions=True)
    assert ceiling == "candidate"
    assert "limitations or prohibited uses" in reason


# ---------------------------------------------------------------------------
# the request never compels promotion (C-04 §8.5, §8.10)
# ---------------------------------------------------------------------------


def test_request_above_the_ceiling_lands_below_it_not_up(store):
    """The decision lands at or below the evidenced ceiling, and the record keeps
    BOTH values so the gap is visible rather than silent."""
    record = _record(store, requested_lane="validated")
    assert record.requested_lane == "validated"
    assert record.decided_lane == "candidate"
    assert record.lane_ceiling == "candidate"
    assert record.authorising is True  # candidate is authorised; `validated` was not


def test_no_valid_conclusion_means_no_authorising_outcome(store):
    """C-04 §6 rows 1-2 + §9: `accepted` may not assert a lane the facts deny."""
    with pytest.raises(ValueError) as excinfo:
        _record(store, axes_auditable=False)
    message = str(excinfo.value)
    assert "no valid release/lane conclusion" in message
    assert "insufficiency" in message
    assert store.all() == []  # nothing was recorded
    refused = _record(store, axes_auditable=False, decision_outcome="rejected")
    assert refused.decided_lane == "research-draft"
    assert refused.authorising is False


def test_requesting_research_draft_is_allowed_and_not_authorising(store):
    record = _record(store, requested_lane="research-draft")
    assert record.decided_lane == "research-draft"
    assert record.authorising is False


# ---------------------------------------------------------------------------
# outcome-specific static meanings (C-04 §8.8-§8.10, §10)
# ---------------------------------------------------------------------------


def test_return_for_evidence_fixes_the_return_state_and_needs_next_evidence(store):
    with pytest.raises(ValueError):
        _record(store, decision_outcome="return_for_evidence")
    with pytest.raises(ValueError):  # evidence named but no resubmission identity
        _record(store, decision_outcome="return_for_evidence",
                required_next_evidence_refs=("ev-next",))
    record = _record(store, decision_outcome="return_for_evidence",
                     required_next_evidence_refs=("ev-next",),
                     resubmission_id="R-2")
    assert record.decided_lane == "research-draft"
    assert record.required_next_evidence_refs == ("ev-next",)
    assert record.authorising is False


def test_rejected_and_withdrawn_never_carry_an_active_lane(store):
    for outcome in ("rejected", "withdrawn"):
        record = _record(store, record_id=f"L-{outcome}", decision_outcome=outcome)
        assert record.decided_lane == "research-draft"
        assert record.authorising is False


def test_accepted_candidate_is_the_only_authorising_shape(store):
    record = _record(store)
    assert record.authorising is True
    assert record.decision_outcome == "accepted"


# ---------------------------------------------------------------------------
# the review blocker (F1): a stored line cannot assert a conclusion
# ---------------------------------------------------------------------------


def test_stored_records_carry_facts_and_no_derived_conclusion(store):
    _record(store)
    payload = json.loads(store.path.read_text(encoding="utf-8").strip())
    for derived in ("lane_ceiling", "ceiling_reason", "version_currency",
                    "authorising"):
        assert derived not in payload, f"{derived} must never be stored"
    for fact in ("axes_auditable", "residual_unordered", "has_restrictions",
                 "version_snapshot", "decided_lane", "recorded_at"):
        assert fact in payload, f"{fact} must be stored"


def test_a_hand_written_ceiling_cannot_exceed_the_derived_one(store):
    """The forged line claims `production`; the read recomputes and refuses it."""
    _write_line(store, _forged(store, decided_lane="production",
                               requested_lane="production",
                               lane_ceiling="production",
                               ceiling_reason="forged ceiling",
                               version_currency="current",
                               authorising=True))
    with pytest.raises(ValueError) as excinfo:
        store.all()
    assert "ceiling" in str(excinfo.value)


def test_a_rejected_outcome_never_reads_as_authorising(store):
    """`authorising` is derived on read: a rejected record can never carry it.

    (A line that tries to store an `authorising` key at all is rejected as an
    unknown field — the conclusion has no place in the stored record.)
    """
    _write_line(store, _forged(store, decision_outcome="rejected"))
    assert store.all()[0].authorising is False
    _write_line(store, _forged(store, decision_outcome="rejected",
                               authorising=True))
    with pytest.raises(ValueError, match="unknown"):
        store.all()


def test_a_hand_written_record_without_facts_is_refused(store):
    _write_line(store, _forged(store, axes_auditable=None))
    with pytest.raises(ValueError):
        store.all()


def test_the_report_refuses_a_forged_top_lane(store):
    """End-to-end statement of F1: the report never renders it."""
    _write_line(store, _forged(store, decided_lane="production",
                               requested_lane="production",
                               lane_ceiling="production"))
    with pytest.raises(ValueError):
        _render_report_with(store)


# ---------------------------------------------------------------------------
# freshness: reuse the version binding, mark stale, never discard (C-04 §9)
# ---------------------------------------------------------------------------


def test_stale_version_binding_caps_the_lane_and_is_marked(store):
    """A decision taken under another runtime stays an honest record, is marked
    stale, and cannot be promoted."""
    _record(store, requested_lane="candidate", decision_outcome="rejected")
    payload = json.loads(store.path.read_text(encoding="utf-8").strip())
    payload["version_snapshot"] = {key: "0.0.0-older"
                                  for key in payload["version_snapshot"]}
    _write_line(store, payload)
    record = store.get("L1")
    assert record.version_currency == "stale"
    assert record.lane_ceiling == "research-draft"
    assert "no longer matches the current runtime" in record.ceiling_reason
    assert record.authorising is False


def test_a_stale_predicate_cannot_authorise_even_a_candidate(store):
    _record(store)
    payload = json.loads(store.path.read_text(encoding="utf-8").strip())
    payload["version_snapshot"] = {key: "0.0.0-older"
                                  for key in payload["version_snapshot"]}
    with pytest.raises(ValueError):
        _write_line(store, payload)
        store.all()


# ---------------------------------------------------------------------------
# durability and schema (same discipline as the other ledgers)
# ---------------------------------------------------------------------------


def test_records_are_immutable_and_round_trip(store):
    from dataclasses import FrozenInstanceError

    record = _record(store)
    with pytest.raises(FrozenInstanceError):
        record.decided_lane = "validated"  # frozen
    read_back = store.get("L1")
    assert read_back == record
    assert read_back.version_snapshot == record.version_snapshot


def test_duplicate_record_id_is_refused(store):
    _record(store)
    with pytest.raises(ValueError):
        _record(store)


def test_unknown_key_in_a_record_is_refused(store):
    payload = _forged(store)
    payload["score"] = 0.9
    _write_line(store, payload)
    with pytest.raises(ValueError):
        store.all()


def test_scalar_or_ranking_field_cannot_enter_a_record(store):
    """C-04 §11: a lane is never a score or a ranking."""
    record = _record(store)
    assert not any(key in record.to_dict() for key in
                   ("score", "total_score", "ranking", "winner", "confidence"))


def test_for_snapshot_filters_by_the_authority_reference(store):
    _record(store, record_id="L1", acceptance_snapshot_ref="snap-A")
    _record(store, record_id="L2", acceptance_snapshot_ref="snap-B")
    assert [r.record_id for r in store.for_snapshot("snap-A")] == ["L1"]
    assert [r.record_id for r in store.for_snapshot("snap-Z")] == []


def test_a_restriction_must_be_carried_not_merely_asserted(store):
    """C-04 §8.12 / §9 (review F7): the boolean and the list must agree."""
    with pytest.raises(ValueError, match="inconsistent"):
        _record(store, has_restrictions=True, prohibited_uses=())
    with pytest.raises(ValueError, match="inconsistent"):
        _record(store, has_restrictions=False, prohibited_uses=("no clinical use",))
    assert store.all() == []
    carried = _record(store, has_restrictions=True,
                      prohibited_uses=("no clinical use",))
    assert "limitations or prohibited uses" in carried.ceiling_reason
    assert carried.prohibited_uses == ("no clinical use",)


def test_append_requires_a_lane_decision_record(store):
    with pytest.raises(TypeError):
        store.append({"record_id": "L1"})


# ---------------------------------------------------------------------------
# the write time (review F5): recorded per line, not inferred from the file
# ---------------------------------------------------------------------------


def test_each_record_carries_its_own_write_time(store):
    first = _record(store, record_id="L1")
    time.sleep(1.1)
    second = _record(store, record_id="L2")
    assert first.recorded_at and second.recorded_at
    assert first.recorded_at != second.recorded_at, (
        "two decisions must be distinguishable in time")
    assert second.recorded_at > first.recorded_at
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00",
                        first.recorded_at)


def test_a_copied_ledger_keeps_each_recorded_time(store):
    """Unlike a file mtime, a stored timestamp survives copying and moving."""
    first = _record(store, record_id="L1")
    time.sleep(1.1)
    second = _record(store, record_id="L2")
    copy = store.path.with_name("copy.jsonl")
    copy.write_bytes(store.path.read_bytes())
    past = time.time() - 86400 * 30
    os.utime(copy, (past, past))  # the COPY's file time is now old
    copied = DPLaneStore(copy)
    assert copied.get("L1").recorded_at == first.recorded_at
    assert copied.get("L2").recorded_at == second.recorded_at


def test_no_reader_returns_a_record_without_a_time(store):
    """Every public read path must carry the time (review F9)."""
    _record(store)
    assert store.get("L1").recorded_at
    assert store.all()[0].recorded_at
    assert store.for_snapshot("snap-A")[0].recorded_at


# ---------------------------------------------------------------------------
# report integration (P-03 row144: the lane limit must be observable)
# ---------------------------------------------------------------------------


def test_report_without_a_lane_decision_says_nothing_rather_than_implying_one(tmp_path):
    from bioaudit.api import render_dp_report

    report = render_dp_report(_register(tmp_path))
    assert report["lane_and_release"] is None
    assert "not a lane authority" in report["bounded_use"]


def test_report_renders_the_lane_ceiling_reasons_and_restrictions(store):
    _record(store, has_restrictions=True, prohibited_uses=("no clinical use",))
    section = _render_report_with(store)["lane_and_release"]
    decision = section["decisions"][0]
    assert section["decision_count"] == 1
    assert decision["decided_lane"] == "candidate"
    assert decision["acceptance_snapshot_ref"] == "snap-A"
    assert decision["ceiling_reason"]
    assert decision["prohibited_uses"] == ["no clinical use"]
    assert decision["authorising"] is True
    assert decision["recorded_at"]
    assert "not a scientific result" in section["statement"]
    assert "validated" in section["unreachable_lanes_note"]
    assert "not a release" in section["not_release_note"]


def test_report_accepts_a_list_of_lane_decisions(store):
    _record(store, record_id="L1")
    _record(store, record_id="L2", decision_outcome="withdrawn")
    assert _render_report_with(store)["lane_and_release"]["decision_count"] == 2


def test_report_refuses_a_lane_object_that_cannot_be_read(tmp_path):
    from bioaudit.api import render_dp_report

    with pytest.raises(TypeError):
        render_dp_report(_register(tmp_path), lane=object())


# ---------------------------------------------------------------------------
# grouping by declared scope
# ---------------------------------------------------------------------------


def test_decisions_are_grouped_by_their_declared_scope(store):
    """C-04 §5 binds every decision to a declared scope."""
    _record(store, record_id="L1", scope="scope-A")
    _record(store, record_id="L2", scope="scope-A", decision_outcome="withdrawn")
    _record(store, record_id="L3", scope="scope-B", decision_outcome="rejected")
    section = _render_report_with(store)["lane_and_release"]
    assert section["decision_count"] == 3
    assert section["scope_count"] == 2
    by_scope = {g["scope"]: g for g in section["decisions_by_scope"]}
    assert by_scope["scope-A"]["decision_count"] == 2
    assert sorted(by_scope["scope-A"]["record_ids"]) == ["L1", "L2"]
    assert by_scope["scope-B"]["decision_count"] == 1
    assert sum(g["decision_count"] for g in section["decisions_by_scope"]) == 3


def test_scope_grouping_lists_the_lanes_decided_within_it(store):
    _record(store, record_id="L1", scope="scope-A")
    _record(store, record_id="L2", scope="scope-A", requested_lane="research-draft")
    by_scope = _render_report_with(store)["lane_and_release"]["decisions_by_scope"]
    assert by_scope[0]["decided_lanes"] == ["candidate", "research-draft"]


def test_the_group_latest_time_is_the_newest_decision(store):
    """F5's second half: the group's newest time must be the LAST decision."""
    _record(store, record_id="L1", scope="scope-A")
    time.sleep(1.1)
    later = _record(store, record_id="L2", scope="scope-A")
    by_scope = _render_report_with(store)["lane_and_release"]["decisions_by_scope"]
    assert by_scope[0]["latest_recorded_at"] == later.recorded_at


# ---------------------------------------------------------------------------
# evidence freshness gates the ceiling (C-04 §6 item 5)
# ---------------------------------------------------------------------------


def _evidence_item(*, role: str = "support", days_ago: float = 1,
                   valid_for_seconds: int = 30 * 86400,
                   now: str = "2026-09-12T00:00:00+00:00"):
    """One qualified EvidenceItem whose freshness is easy to tune."""
    from datetime import datetime, timedelta, timezone

    from bioaudit.dp01_models import EvidenceItem

    moment = datetime.fromisoformat(now) - timedelta(days=days_ago)
    return EvidenceItem(source_type="observed", verification="confirmed", value="v",
                        observed_at=moment, valid_for_seconds=valid_for_seconds,
                        evidence_role=role)


def test_stale_supporting_evidence_blocks_promotion(store):
    """An expired SUPPORT item cannot prop up a decision any more."""
    item = _evidence_item(role="support", days_ago=90)
    record = _record(store, decision_outcome="rejected", evidence=(item,),
                     now="2026-09-12T00:00:00+00:00")
    assert record.evidence_stale_support is True
    assert record.lane_ceiling == "research-draft"
    assert "past the validity window" in record.ceiling_reason
    assert record.authorising is False


def test_current_supporting_evidence_does_not_block(store):
    item = _evidence_item(role="support", days_ago=1)
    record = _record(store, evidence=(item,), now="2026-09-12T00:00:00+00:00")
    assert record.evidence_stale_support is False
    assert record.lane_ceiling == "candidate"
    assert record.authorising is True


def test_a_stale_limitation_does_not_gate_the_ceiling(store):
    """Human ruling: only the SUPPORT role gates. An expired caveat is not support."""
    stale_caveat = _evidence_item(role="limitation", days_ago=90)
    record = _record(store, evidence=(stale_caveat,),
                     now="2026-09-12T00:00:00+00:00")
    assert record.evidence_stale_support is False
    assert record.lane_ceiling == "candidate"


def test_a_stale_conflict_or_history_also_does_not_gate(store):
    for role in ("conflict", "historical_context", "planned_future_requirement"):
        record = _record(store, record_id=f"L-{role}",
                         evidence=(_evidence_item(role=role, days_ago=90),),
                         now="2026-09-12T00:00:00+00:00")
        assert record.evidence_stale_support is False, role


def test_accepted_above_the_blocked_ceiling_is_refused(store):
    """With stale support the ceiling is the return state, so `accepted` raises."""
    with pytest.raises(ValueError, match="no valid release/lane conclusion"):
        _record(store, evidence=(_evidence_item(days_ago=90),),
                now="2026-09-12T00:00:00+00:00")
    assert store.all() == []


def test_the_evidence_freshness_fact_is_stored_and_shown(store):
    _record(store, decision_outcome="rejected",
            evidence=(_evidence_item(days_ago=90),),
            now="2026-09-12T00:00:00+00:00")
    payload = json.loads(store.path.read_text(encoding="utf-8").strip())
    assert payload["evidence_stale_support"] is True
    # the conclusions are still derived, never stored
    assert "version_currency" not in payload
    assert "lane_ceiling" not in payload
    decision = _render_report_with(store)["lane_and_release"]["decisions"][0]
    assert decision["evidence_stale_support"] is True
    assert decision["lane_ceiling"] == "research-draft"
