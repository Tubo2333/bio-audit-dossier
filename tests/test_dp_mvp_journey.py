"""DP-JOURNEY — single-journey end-to-end walkthrough harness (observation window).

This harness is an OBSERVATION INSTRUMENT, not an acceptance judgement. It runs
ONE named journey over the existing public seams
(``audit_dp_register`` -> ``render_dp_report`` -> ``derive_dp_strength``) so the
observable product meaning can be inspected against the P-03 observation
categories.

Run with ``-s`` to print the journey transcript used to write the observation
record. It modifies no source code and declares no acceptance.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bioaudit.api import (
    audit_dp_register,
    build_dp_acceptance_snapshot,
    derive_dp_strength,
    dp_acceptance_snapshot_ref,
    dp_authority_store,
    dp_change_store,
    dp_role_store,
    record_dp_change,
    record_dp_role,
    render_dp_report,
)
from bioaudit.dp01_store import DP01JSONLStore

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

# P-02 §3.1: the named journey context (confirmed before any decision reading).
_CONTEXT = {
    "project_id": "project-journey-1",
    "analysis_id": "analysis-journey-1",
    "comparison": "case_vs_control",
    "data_type": "bulk_rnaseq",
    "audit_scope": "five-point-mvp",
    "intended_use": "scientific_analysis",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
}


def _decl(method):
    return {"method": method, "threshold": 10,
            "sample_rule": "at_least_two_samples", "unit": "gene",
            "source": "declared"}


# ---------------------------------------------------------------------------
# One declared instant for the whole harness.
#
# DP-EVIDENCE-QUALIFICATION made evidence carry WHEN it was observed and HOW
# LONG it holds, and made report freshness depend on it. An evidence item that
# does not declare ``observed_at`` falls back to the moment the record was
# WRITTEN - a fact about that run, not about this journey. The same journey run
# twice would then differ by that fact alone, so ``test_journey_reproducible``
# would pass or fail on whether the two runs happened to land in the same
# second.
#
# This harness declares the observation moment instead of leaning on that
# fallback: every observation is a fact about the journey, so the journey is
# reproducible across runs. The render instant is pinned for the same reason -
# freshness is judged against it. The pattern is the one already used by
# ``tests/test_dp_evidence_qualification.py`` (a module-level NOW plus an
# explicit ``now=``). An unqualified item still defaults to write time; that
# default is exercised there, deliberately, and is not what this harness tests.
# ---------------------------------------------------------------------------
FIXED_TIME = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def _obs(value, verification="confirmed"):
    return {"source_type": "observed", "verification": verification, "value": value,
            "observed_at": FIXED_TIME.isoformat(timespec="seconds")}


# The single named journey: 4 points carry qualified evidence, 1 (correction)
# carries only a label -> exercises §4.6/§4.7 mixed preservation and the
# §5.2 insufficiency-vs-integrity distinction.
_JOURNEY_POINTS = {
    "filtering": {"decision_declaration": _decl("low_count_filter"),
                  "evidence_observations": [_obs("low_count_filter")]},
    "normalization": {"decision_declaration": _decl("tmm"),
                      "evidence_observations": [_obs("tmm")]},
    "differential_method": {"decision_declaration": _decl("deseq2"),
                            "evidence_observations": [_obs("deseq2")]},
    "multiple_testing_correction": {
        "decision_declaration": _decl("BH"),
        # label only: declared/unverified, no qualified observed evidence.
        # It still declares the harness's one observation moment: an item without
        # observed_at falls back to the moment the record is WRITTEN (a fact about
        # that run), which is exactly the wall-clock that test_journey_reproducible
        # must not depend on (see the FIXED_TIME note above).
        "evidence_observations": [{"source_type": "declared",
                                   "verification": "unverified", "value": "BH",
                                   "observed_at": FIXED_TIME.isoformat(timespec="seconds")}],
    },
    "significance_threshold": {"decision_declaration": _decl("padj_0.05"),
                               "evidence_observations": [_obs("padj_0.05")]},
}

_POINT_IDS = {"filtering": "dp01", "normalization": "dp02",
              "differential_method": "dp03",
              "multiple_testing_correction": "dp04",
              "significance_threshold": "dp05"}


def run_journey(ledger, audit_id="journey-1"):
    """Run the single named journey end-to-end over the public seams (pure)."""
    register = audit_dp_register(_request(audit_id), ledger)
    report = render_dp_report(register)
    strength = derive_dp_strength(register)
    return {"register": register, "report": report, "strength": strength,
            "ledger": ledger, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# DP-JOURNEY-2: the same journey, now including the authority container and
# role records. Role actors are SYNTHETIC by construction: recording a role
# demonstrates the mechanism and is NOT evidence that a review occurred
# (T-01 §11.2 L675). Real use must involve real named humans.
# ---------------------------------------------------------------------------

SYNTHETIC = "synthetic:"


def _request(audit_id):
    return {"audit_id": audit_id, "analysis_context": _CONTEXT,
            "integrity_metadata": _INTEGRITY, "decision_points": _JOURNEY_POINTS}


def run_journey_with_authority(tmp_path, audit_id="journey-2"):
    """Extended journey in the NATURAL order:

    register → report → strength → snapshot(register=) → ref → roles.

    ``build_acceptance_snapshot`` now accepts the already-built register, so the
    two public entry points compose (the DP-JOURNEY-2 integration defect was
    fixed); no read-back workaround is needed any more.
    """
    ledger = DP01JSONLStore(tmp_path / "journey.jsonl")
    authority = dp_authority_store(tmp_path / "authority.jsonl")
    roles = dp_role_store(tmp_path / "roles.jsonl")
    request = _request(audit_id)

    register = audit_dp_register(request, ledger)
    strength = derive_dp_strength(register)
    snapshot = build_dp_acceptance_snapshot(request, ledger, authority,
                                            register=register)
    ref = dp_acceptance_snapshot_ref(snapshot)

    record_dp_role(roles, record_id="j-authored", role="authored",
                   actor=f"{SYNTHETIC}author-1", target=f"report:{audit_id}",
                   scope="the five decision declarations and their evidence")
    record_dp_role(roles, record_id="j-reviewed", role="reviewed",
                   actor=f"{SYNTHETIC}reviewer-1",
                   target=f"acceptance_snapshot:{snapshot.snapshot_id}",
                   scope="DP-01..DP-05 declarations, evidence and limitations")
    record_dp_role(roles, record_id="j-receipted", role="receipted",
                   actor=f"{SYNTHETIC}coordinator-1",
                   target=f"acceptance_snapshot:{snapshot.snapshot_id}",
                   scope="administrative receipt of the snapshot",
                   receipt_kind="administrative")

    # Report integration (DP-REPORT-AUTHORITY / DP-CHANGE): the report is rendered
    # WITH authority attribution, role records and change context, so the
    # user-facing artifact can answer "which result is authoritative", "who
    # authored/reviewed/receipted" and "what changed relative to an earlier account".
    changes = dp_change_store(tmp_path / "changes.jsonl")
    record_dp_change(changes, change_id="j-change",
                     from_account="acceptance_snapshot:snapshot:journey-1",
                     to_account=f"acceptance_snapshot:{snapshot.snapshot_id}",
                     change_classes=["evidence_or_provenance"],
                     differences=[{"change_class": "evidence_or_provenance",
                                   "path": "evidence.observations",
                                   "before": "unqualified", "after": "confirmed"}],
                     reason="evidence was requalified for this account",
                     submitter=f"{SYNTHETIC}analyst-1",
                     requires_adjudication=True,
                     adjudication_authority="scientific-reviewer",
                     escalation_required=False)
    change_record = changes.get("j-change")
    history = SimpleNamespace(
        earlier_accounts=["acceptance_snapshot:snapshot:journey-1"],
        proposed_change=change_record)
    report = render_dp_report(register, authority=snapshot, roles=roles.all(),
                              history=history, now=FIXED_TIME)

    return {"register": register, "report": report, "strength": strength,
            "ledger": ledger, "authority": authority, "roles": roles,
            "snapshot": snapshot, "ref": ref, "audit_id": audit_id,
            "request": request}


@pytest.fixture
def journey(tmp_path):
    return run_journey(DP01JSONLStore(tmp_path / "journey.jsonl"))


@pytest.fixture
def journey2(tmp_path):
    return run_journey_with_authority(tmp_path)


# 1. the journey runs; five points each persist one shared-ledger record
def test_journey_runs_and_persists_five_records(journey):
    reg = journey["register"]
    assert {"filtering", "normalization", "differential_method",
            "multiple_testing_correction", "significance_threshold"} <= set(reg)
    assert reg["_register_meta"]["first_bounded_set"] is True
    ledger = journey["ledger"]
    for name, suffix in _POINT_IDS.items():
        assert ledger.get(f"journey-1:{suffix}") is not None, name


# 2. mixed meaning preserved (4 auditable + 1 scientifically limited), no scalar
def test_mixed_meaning_preserved_no_scalar(journey):
    points = {p["name"]: p["judgment"] for p in journey["report"]["per_point"]["points"]}
    assert points["multiple_testing_correction"] == "scientifically_limited"
    assert sum(1 for v in points.values() if v == "auditable") == 4

    banned = {"total_score", "winner", "summary_judgment", "overall_score",
              "confidence", "ranking", "quality", "lane"}

    def walk(node):
        if isinstance(node, dict):
            for k in node:
                assert k not in banned, k
                walk(node[k])
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(journey["report"])


# 3. the report carries the P-02 §6.2 ten-item order
def test_report_has_meaning_order(journey):
    assert list(journey["report"].keys()) == [
        "_report_meta", "context", "state_meanings", "five_decision_register",
        "per_point", "cross_step_coherence", "findings_and_limitations",
        "bounded_use", "lane_and_release", "review_and_admin",
        "history_and_change", "next_actions", "final_user_instruction"]


# 4. P-02 §6.2 item 5: four axes exposed separately with provenance
def test_report_exposes_four_axes(journey):
    axes = journey["report"]["per_point"]["result_axes"]
    assert {"validation", "scope", "inference_type", "explanation_depth"} <= set(axes)
    assert axes["_provenance"]["combined_account"] is True
    assert axes["_provenance"]["not_second_authority"] is True


# 5. §7.3 evidence/findings, §7.1 input_version, §7.9 five-item instruction
def test_report_content_completions_present(journey):
    report = journey["report"]
    point = report["per_point"]["points"][0]
    assert "evidence" in point and "findings" in point
    assert "ruleset_version" in report["context"]["input_version"]
    text = report["final_user_instruction"].lower()
    for marker in ("reli", "claim", "must stop", "human"):
        assert marker in text, marker


# 6. explanatory, not authority / not acceptance
def test_report_is_explanatory_not_authority(journey):
    report = journey["report"]
    assert report["_report_meta"]["not_authority"] is True
    assert report["_report_meta"]["not_acceptance"] is True
    assert journey["strength"]["_strength_meta"]["view_only"] is True

    def walk(node):
        if isinstance(node, dict):
            for k in node:
                assert k not in {"acceptance", "release", "lane", "deployment",
                                 "package_a"}, k
                walk(node[k])
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(report)


# 7. reproducibility: same journey twice -> equivalent report structure
def test_journey_reproducible(tmp_path):
    first = run_journey(DP01JSONLStore(tmp_path / "a.jsonl"))
    second = run_journey(DP01JSONLStore(tmp_path / "b.jsonl"))
    assert repr(first["report"]) == repr(second["report"])


# transcript for the observation record (run with -s)
def test_journey_transcript(journey):
    report = journey["report"]
    print("\n=== JOURNEY TRANSCRIPT (observation material) ===")
    print("context:", report["context"])
    for p in report["per_point"]["points"]:
        print(f"  point {p['name']}: judgment={p['judgment']} "
              f"declared={p['declared_method']} evidence={len(p['evidence'])} "
              f"findings={len(p['findings'])} gaps={len(p['evidence_gaps'])} "
              f"actions={[a['action'] for a in p['next_actions']]}")
    print("findings_and_limitations:", report["findings_and_limitations"])
    print("result_axes.validation:", report["per_point"]["result_axes"]["validation"])
    print("result_axes.inference_type:", report["per_point"]["result_axes"]["inference_type"])
    print("result_axes.scope:", report["per_point"]["result_axes"]["scope"])
    print("result_axes.explanation_depth:", report["per_point"]["result_axes"]["explanation_depth"])
    print("final_user_instruction:", report["final_user_instruction"])
    print("=== END TRANSCRIPT ===")


# ---------------------------------------------------------------------------
# DP-JOURNEY-2 tests: authority container + synthetic role records
# ---------------------------------------------------------------------------

# 10. extended journey runs: 5 point records, 1 snapshot, 3 role records
def test_extended_journey_persists_all_three_ledgers(journey2):
    for suffix in ("dp01", "dp02", "dp03", "dp04", "dp05"):
        assert journey2["ledger"].get(f"journey-2:{suffix}") is not None
    assert journey2["authority"].get(journey2["snapshot"].snapshot_id) is not None
    assert {r.role for r in journey2["roles"].all()} == {
        "authored", "reviewed", "receipted"}


# 11. the embedded result is the authority (fully-confirmed integrity basis)
def test_extended_journey_snapshot_is_authoritative(journey2):
    prov = journey2["snapshot"].structured_strength_result["_provenance"]
    assert prov["authoritative"] is True
    assert prov["sole_container"] is True
    assert journey2["snapshot"].authority_basis["confirmed"] is True


# 12. the one-way ref exposes no result content
def test_extended_journey_ref_is_one_way(journey2):
    inner = journey2["ref"]["acceptance_snapshot_ref"]
    assert inner["snapshot_id"] == journey2["snapshot"].snapshot_id
    for axis in ("validation", "scope", "inference_type", "explanation_depth"):
        assert axis not in inner


# 13. INTEGRITY GUARD: every role actor in this harness is synthetic
def test_harness_role_actors_are_synthetic(journey2):
    for record in journey2["roles"].all():
        assert record.actor.startswith(SYNTHETIC), record.actor


# 14. receipted carries an administrative receipt_kind (never substantive)
def test_receipted_role_is_administrative(journey2):
    receipted = journey2["roles"].get("j-receipted")
    assert receipted.receipt_kind == "administrative"
    assert journey2["roles"].get("j-reviewed").receipt_kind is None


# 15. CONSISTENCY GUARD with an INDEPENDENT contrast: recompute the four axes
#     from the LEDGER's own read-back results and compare BOTH the report and the
#     snapshot against that recomputation. (Comparing the report to the snapshot
#     alone would be self-consistent once the register is shared.)
def test_axes_match_ledger_recomputed_axes(journey2):
    from_ledger = {name: journey2["ledger"].get(f"journey-2:{suffix}").result
                   for name, suffix in _POINT_IDS.items()}
    from_ledger["_register_meta"] = dict(journey2["register"]["_register_meta"])
    recomputed = derive_dp_strength(from_ledger)
    report_axes = journey2["report"]["per_point"]["result_axes"]
    embedded = journey2["snapshot"].structured_strength_result
    for axis in ("validation", "scope", "inference_type", "explanation_depth"):
        assert report_axes[axis] == recomputed[axis], axis
        assert embedded[axis] == recomputed[axis], axis


# 16. Report integration (DP-REPORT-AUTHORITY). The earlier observation here
#     ("the report does not yet point at the authority container, nor surface the
#     role records") has been CLOSED: the assertions below are the reversed,
#     positive form. They read the report's own structured fields rather than
#     substring-matching a repr.
def _report_values_only(node):
    """Yield only the VALUES of the report (never key names) for containment checks."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _report_values_only(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _report_values_only(value)


def test_report_now_surfaces_authority_and_roles(journey2):
    """The report now points at the authority container and names the roles."""
    section = journey2["report"]["review_and_admin"]
    attribution = section["authority_attribution"]
    assert attribution["snapshot_id"] == journey2["snapshot"].snapshot_id
    assert attribution["container_kind"] == "acceptance_snapshot"
    assert attribution["embedded_result_count"] == 1
    assert attribution["not_a_second_container"] is True
    # values-only containment: the role actors/targets appear as report VALUES
    values = list(_report_values_only(journey2["report"]))
    assert any(journey2["snapshot"].snapshot_id == v for v in values)
    assert section["roles_recorded"] is True
    for record in journey2["roles"].all():
        assert any(record.actor == v for v in values), record.actor
        assert any(record.target == v for v in values), record.target


# 17. history/change meaning is surfaced (DP-CHANGE, P-03 row147)
def test_report_surfaces_history_and_change(journey2):
    section = journey2["report"]["history_and_change"]
    assert section["history_supplied"] is True
    assert section["earlier_accounts"] == [
        {"account_ref": "acceptance_snapshot:snapshot:journey-1",
         "meaning": "historical_only", "scope": None, "time": None}]
    change = section["proposed_change"]
    current = f"acceptance_snapshot:{journey2['snapshot'].snapshot_id}"
    assert change["from_account"] == "acceptance_snapshot:snapshot:journey-1"
    assert change["to_account"] == current
    # the recorded change is about THIS report's current account, and requires
    # adjudication, so it is not settled (fail-closed)
    assert change["current_account"] == current
    assert change["to_account"] == change["current_account"]
    assert change["prior_account"] == "acceptance_snapshot:snapshot:journey-1"
    assert change["prior_meaning"] == "historical_only"
    assert change["state"] == "awaiting_human_decision"
    assert change["adjudication_authority"] == "scientific-reviewer"
    assert change["declaration_basis"] == "declared_by_submitter"
    assert change["submitter"].startswith(SYNTHETIC)


# 17. DEFECT CLOSED (was: test_natural_order_register_then_snapshot_conflicts).
#     The two public entry points now compose in the natural order: passing the
#     already-built register means the point registration is not re-run, so the
#     duplicate-audit-id guard is not hit. Human adjudication (2026) classified
#     the old conflict as an implementation defect; this test now asserts the fix.
def test_natural_order_register_then_snapshot_works(tmp_path):
    ledger = DP01JSONLStore(tmp_path / "natural.jsonl")
    authority = dp_authority_store(tmp_path / "natural-auth.jsonl")
    request = _request("natural-1")
    register = audit_dp_register(request, ledger)
    snapshot = build_dp_acceptance_snapshot(request, ledger, authority,
                                            register=register)
    assert snapshot.snapshot_id == "snapshot:natural-1"
    assert authority.get(snapshot.snapshot_id) is not None


# 18. transcript for the extended journey (run with -s)
def test_extended_journey_transcript(journey2):
    snap = journey2["snapshot"]
    print("\n=== JOURNEY-2 TRANSCRIPT (observation material) ===")
    print(f"snapshot_id={snap.snapshot_id}")
    print(f"authority_basis={snap.authority_basis}")
    print(f"embedded provenance={snap.structured_strength_result['_provenance']}")
    print(f"one-way ref={journey2['ref']}")
    for record in journey2["roles"].all():
        print(f"  role={record.role} actor={record.actor} target={record.target} "
              f"scope={record.scope} receipt_kind={record.receipt_kind}")
    print("report keys:", list(journey2["report"].keys()))
    print("report.review_and_admin:", journey2["report"]["review_and_admin"])
    print("report.history_and_change:", journey2["report"]["history_and_change"])
    print("=== END JOURNEY-2 TRANSCRIPT ===")
