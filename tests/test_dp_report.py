"""DP-REPORT — five-point register report layer tests (authorized seam).

Exercises ``bioaudit.api.render_dp_report``: a pure function that turns the
register view (from ``audit_dp_register``) into an ordered, explanatory report
structure following the P-01 §7.1–§7.9 / P-02 §6.2 ten-item meaning order.
The report is explanatory, NOT a second result-authority container, NOT
acceptance/release; it contains no total score / winner / summary judgment.

Runs via the project pytest `pythonpath=["src"]`.
"""

import pathlib

import pytest

from bioaudit.api import audit_dp_register, render_dp_report, derive_dp_strength
from bioaudit.dp01_store import DP01JSONLStore
from bioaudit.dp01_models import DP01_JUDGMENTS
from bioaudit.dp_report import (  # noqa: PLC2701
    _CODE_ONLY_JUDGEMENTS,
    _P02_EXPRESSED_BY,
    _P02_EXPRESSIBLE_LABELS,
    _P02_SECTION_5_MEANINGS,
    _P02_UNEXPRESSIBLE_LABELS,
    _capability_status_for_state_vocabulary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_INTEGRITY = {**{domain: {"state": "confirmed"} for domain in
                 ("identity", "version", "source", "binding", "snapshot",
                  "permission", "unique_authority")},
              "material_ids": ["material-1"], "pending_targets": ["decision"]}

_CONTEXT = {
    "project_id": "project-1",
    "analysis_id": "analysis-1",
    "comparison": "case_vs_control",
    "data_type": "bulk_rnaseq",
    "audit_scope": "five-point",
    "intended_use": "scientific_analysis",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
    "exclusions": "none — no explicit exclusions declared",
}


def _declaration(method, threshold=10, sample_rule="at_least_two_samples"):
    return {"method": method, "threshold": threshold,
            "sample_rule": sample_rule, "unit": "gene", "source": "declared"}


def _obs(value, verification="confirmed", source_type="observed"):
    return {"source_type": source_type, "verification": verification, "value": value}


def _point(declaration, evidence):
    return {"decision_declaration": declaration, "evidence_observations": evidence}


_FIVE_AUDITABLE = {
    "filtering": _point(_declaration("low_count_filter"), [_obs("low_count_filter")]),
    "normalization": _point(_declaration("tmm"), [_obs("tmm")]),
    "differential_method": _point(_declaration("deseq2"), [_obs("deseq2")]),
    "multiple_testing_correction": _point(_declaration("BH"), [_obs("BH")]),
    "significance_threshold": _point(_declaration("padj_0.05"), [_obs("padj_0.05")]),
}

_MIXED = {
    "filtering": _point(_declaration("low_count_filter"), [_obs("low_count_filter")]),
    "normalization": _point(_declaration("tmm"),
                            [_obs("tmm", verification="unverified")]),       # scientifically_limited
    "differential_method": _point(_declaration("deseq2"),
                                  [_obs("deseq2"), _obs("edgeR")]),          # conflicted
    "multiple_testing_correction": _point({"method": "BH", "threshold": 5,
                                           "unit": "gene", "source": "declared"},
                                          [_obs("BH")]),                     # integrity_failed
    "significance_threshold": _point(_declaration("padj_0.05"), [_obs("padj_0.05")]),
}


@pytest.fixture
def ledger(tmp_path):
    return DP01JSONLStore(tmp_path / "ledger.jsonl")


@pytest.fixture
def five_auditable_register(ledger):
    return audit_dp_register(
        {"audit_id": "rp-1", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)


@pytest.fixture
def mixed_register(ledger):
    return audit_dp_register(
        {"audit_id": "rp-m", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _MIXED},
        ledger)


# ---------------------------------------------------------------------------
# 1. Report top-level keys follow the P-02 §6.2 meaning order
# ---------------------------------------------------------------------------

def test_report_keys_in_meaning_order(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    # ``lane_and_release`` was added with the C-04 lane slice: the ceiling belongs
    # with the bounded-use statement (C-04 §6.1), before review/admin, history/change
    # and next action.
    expected = ["_report_meta", "context", "state_meanings", "five_decision_register",
                "per_point", "cross_step_coherence", "findings_and_limitations",
                "bounded_use", "lane_and_release", "review_and_admin",
                "history_and_change", "next_actions", "final_user_instruction"]
    # ``cross_step_coherence`` was added with the DP-COHERENCE slice (2026-09-15,
    # human-approved OPEN-2 / checklist M3): the chain-level derived view sits with
    # the per-point section it is derived from, before findings/limitations.
    assert list(report.keys()) == expected
    # with no lane decision supplied, the section says so instead of implying a lane
    assert report["lane_and_release"] is None


# ---------------------------------------------------------------------------
# 2. Five decision-point register lists the five points + first-bounded-set
# ---------------------------------------------------------------------------

def test_five_decision_register_lists_points_and_bounded_set(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    reg = report["five_decision_register"]
    names = [p["name"] for p in reg["points"]]
    assert names == ["filtering", "normalization", "differential_method",
                     "multiple_testing_correction", "significance_threshold"]
    assert reg["first_bounded_set"] is True


# ---------------------------------------------------------------------------
# 3. Mixed judgments preserved; no total score / winner / summary in report
# ---------------------------------------------------------------------------

def test_mixed_register_preserved_no_scalar(mixed_register):
    report = render_dp_report(mixed_register)
    judgments = {p["judgment"] for p in report["per_point"]["points"]}
    assert "auditable" in judgments
    assert "scientifically_limited" in judgments
    assert "conflicted" in judgments
    assert "integrity_failed" in judgments

    # no scalar / winner anywhere in the whole report structure
    def walk(node):
        if isinstance(node, dict):
            keys = set(node)
            assert not (keys & {"total_score", "winner", "summary_judgment",
                                "overall_score", "combined_judgment"}), keys
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(report)


# ---------------------------------------------------------------------------
# 4. Report is explanatory, not authority / acceptance
# ---------------------------------------------------------------------------

def test_report_explanatory_not_authority(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    meta = report["_report_meta"]
    assert meta["explanatory"] is True
    assert meta["not_authority"] is True
    assert meta["not_acceptance"] is True

    def walk(node):
        if isinstance(node, dict):
            for k in node:
                assert k not in {"acceptance", "release", "lane", "deployment",
                                 "package_a"}, k
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(report)


# ---------------------------------------------------------------------------
# 5. Scientific insufficiency kept separate from integrity/authority failure
# ---------------------------------------------------------------------------

def test_scientific_insufficiency_separate_from_integrity(mixed_register):
    report = render_dp_report(mixed_register)
    by_name = {p["name"]: p for p in report["per_point"]["points"]}
    # normalization: scientifically_limited; correction: integrity_failed
    assert by_name["normalization"]["judgment"] == "scientifically_limited"
    assert by_name["multiple_testing_correction"]["judgment"] == "integrity_failed"
    # findings section must not collapse them under one bucket
    findings = report["findings_and_limitations"]
    categories = {f["category"] for f in findings}
    assert "scientific_insufficiency" in categories
    assert "integrity_failure" in categories


# ---------------------------------------------------------------------------
# 6. Context carried from the register
# ---------------------------------------------------------------------------

def test_context_propagates(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    ctx = report["context"]
    assert ctx["project_id"] == "project-1"
    assert ctx["analysis_id"] == "analysis-1"
    assert ctx["comparison"] == "case_vs_control"
    assert ctx["intended_use"] == "scientific_analysis"


# ---------------------------------------------------------------------------
# 7. A point's next-action surfaces in the report
# ---------------------------------------------------------------------------

def test_stop_and_request_evidence_surface(mixed_register):
    report = render_dp_report(mixed_register)
    actions = set()
    for p in report["per_point"]["points"]:
        for a in p["next_actions"]:
            actions.add(a["action"])
    assert "request_evidence" in actions
    assert "submit_correction" in actions


# ---------------------------------------------------------------------------
# 8. first-bounded-set note passes through
# ---------------------------------------------------------------------------

def test_first_bounded_set_note_pass_through(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    assert report["five_decision_register"]["note"] == \
        "First bounded set of five decision points, not the complete product decision set."


# ---------------------------------------------------------------------------
# 9. Fail-closed on malformed input
# ---------------------------------------------------------------------------

def test_invalid_register_fail_closed(ledger):
    full = audit_dp_register(
        {"audit_id": "rp-x", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)
    with pytest.raises((ValueError, TypeError)):
        render_dp_report("not-a-mapping")
    missing_meta = {k: v for k, v in full.items() if k != "_register_meta"}
    with pytest.raises((ValueError, TypeError)):
        render_dp_report(missing_meta)


# ---------------------------------------------------------------------------
# DP-REPORT-AXES: four-axis structured result embedded in per_point
# ---------------------------------------------------------------------------

def test_report_embeds_four_axes(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    axes = report["per_point"]["result_axes"]
    assert {"validation", "scope", "inference_type", "explanation_depth"} <= set(axes)


def test_report_axes_match_derived_strength(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    strength = derive_dp_strength(five_auditable_register)
    axes = report["per_point"]["result_axes"]
    for key in ("validation", "scope", "inference_type", "explanation_depth"):
        assert axes[key] == strength[key], key


def test_report_axes_have_no_scalar_keys(mixed_register):
    report = render_dp_report(mixed_register)
    axes = report["per_point"]["result_axes"]
    banned = {"total_score", "confidence", "ranking", "quality", "lane",
              "winner", "summary", "overall_score"}

    def walk(node):
        if isinstance(node, dict):
            for k in node:
                assert k not in banned, k
                walk(node[k])
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(axes)


def test_report_axes_carry_combined_account_provenance(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    provenance = report["per_point"]["result_axes"]["_provenance"]
    assert provenance["combined_account"] is True
    assert provenance["view_only"] is True
    assert provenance["not_second_authority"] is True


# ---------------------------------------------------------------------------
# DP-REPORT-2: §7.3 per-point evidence/findings, §7.1 input/version, §7.9 five-item
# ---------------------------------------------------------------------------

def test_per_point_renders_evidence_and_findings(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    by_name = {p["name"]: p for p in report["per_point"]["points"]}
    filtering = by_name["filtering"]
    # evidence from the register point result (observed, confirmed, low_count_filter)
    assert any(e["source_type"] == "observed" and e["verification"] == "confirmed"
               and e["value"] == "low_count_filter" for e in filtering["evidence"])
    # findings present for an auditable point (method/declared/confirmed)
    assert any(f["kind"] == "method" and f["state"] == "confirmed" for f in filtering["findings"])


def test_context_renders_input_version_and_exclusions(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    ctx = report["context"]
    assert "ruleset_version" in ctx["input_version"]
    assert "engine_version" in ctx["input_version"]
    assert "adapter_version" in ctx["input_version"]
    # exclusions are REQUIRED (P-03 row133) and shown verbatim when declared
    assert ctx["exclusions"]["declared_boundary"] == _CONTEXT["audit_scope"]
    assert ctx["exclusions"]["declared_exclusions"] == _CONTEXT["exclusions"]
    assert "verbatim" in ctx["exclusions"]["note"].lower()


def test_missing_or_blank_exclusions_fails_closed_not_silently_accepted(tmp_path):
    """P-03 row133: silence about what is NOT included is not acceptable.

    Either declare exclusions, or declare explicitly that there are none — the key
    must be present and non-blank. Missing/blank makes each point not_auditable
    (a required fact could not be verified) rather than quietly proceeding.
    """
    for label, context in (("missing", {k: v for k, v in _CONTEXT.items()
                                       if k != "exclusions"}),
                           ("blank", {**_CONTEXT, "exclusions": "   "})):
        ledger = DP01JSONLStore(tmp_path / f"ledger-{label}.jsonl")
        register = audit_dp_register(
            {"audit_id": f"rp-{label}", "analysis_context": context,
             "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
            ledger)
        report = render_dp_report(register)
        assert all(p["judgment"] == "not_auditable"
                   for p in report["per_point"]["points"]), label
        text = report["final_user_instruction"].lower()
        assert "governance authority" in text, label


def test_declared_exclusions_are_surfaced_not_dropped(tmp_path):
    """P-01 §7.1 / P-03 row133: a declared exclusion must appear in the report.

    Previously the caller's `analysis_context.exclusions` was accepted and then
    silently discarded, leaving only a fixed "not carried" note.
    """
    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    context = {**_CONTEXT, "exclusions": "exclude batch 3 (instrument drift)"}
    register = audit_dp_register(
        {"audit_id": "rp-excl", "analysis_context": context,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)
    section = render_dp_report(register)["context"]["exclusions"]
    assert section["declared_exclusions"] == "exclude batch 3 (instrument drift)"
    assert section["declared_boundary"] == _CONTEXT["audit_scope"]
    assert "verbatim" in section["note"].lower()


def test_final_instruction_has_five_items(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    text = report["final_user_instruction"]
    # P-01 §7.9 five-item semantics present (robust markers, not brittle substrings)
    assert "reli" in text.lower()          # What can be relied on
    assert "claim" in text.lower()         # What cannot be claimed
    assert "must stop" in text.lower()     # What must stop
    assert ("evidence" in text.lower() or "review" in text.lower())  # next evidence/review
    assert "human" in text.lower()         # whether human escalation is required


# ---------------------------------------------------------------------------
# P-03 row150: when something must stop, the report names WHO OWNS it
# ---------------------------------------------------------------------------

def test_stop_section_names_the_governance_authority(mixed_register):
    """Row150 fifth element: 'who owns it'. An integrity failure must name the
    governance authority category, not just 'the named human authority'."""
    text = render_dp_report(mixed_register)["final_user_instruction"].lower()
    assert "governance authority" in text


def test_stop_section_names_scientific_review_for_insufficiency(tmp_path):
    """A purely scientific shortfall routes to scientific review, not governance."""
    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    register = audit_dp_register(
        {"audit_id": "rp-soft", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY,
         "decision_points": {
             name: _point(_declaration(method), [_obs(method, verification="unverified")])
             for name, method in (
                 ("filtering", "low_count_filter"), ("normalization", "tmm"),
                 ("differential_method", "deseq2"),
                 ("multiple_testing_correction", "BH"),
                 ("significance_threshold", "padj_0.05"))}},
        ledger)
    text = render_dp_report(register)["final_user_instruction"].lower()
    assert "scientific review authority" in text
    assert "governance authority" not in text


# ---------------------------------------------------------------------------
# P-03 row148: the reader must see that this is one bounded part of the product
# ---------------------------------------------------------------------------

def test_report_lists_what_is_not_available_yet(five_auditable_register):
    section = render_dp_report(five_auditable_register)["five_decision_register"]
    listed = {entry["capability"] for entry in section["not_yet_available"]}
    assert section["first_bounded_set"] is True
    assert len(listed) >= 5
    # each entry must name something concrete and carry its own status
    for entry in section["not_yet_available"]:
        assert entry["capability"] and entry["status"], entry
    assert any("release" in c and "lane" in c for c in listed), listed
    assert any("closure" in c for c in listed), listed
    assert any("reviewer" in c for c in listed), listed
    note = section["not_yet_available_note"].lower()
    assert "not" in note and "claimed" in note
    assert "roadmap" in note  # it must disclaim being a plan


def test_not_available_list_is_not_a_new_authority_or_score(five_auditable_register):
    """The boundary list must stay a statement: no score, no lane, no release."""
    report = render_dp_report(five_auditable_register)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {"score", "total_score", "lane", "winner",
                                   "release", "ranking"}, key
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(report["five_decision_register"])


# ---------------------------------------------------------------------------
# P-03 row140: "scientific insufficiency" and "could not be verified" must stay
# two distinct meanings — including for the not_auditable state
# ---------------------------------------------------------------------------

def test_not_auditable_is_not_folded_into_scientific_insufficiency(tmp_path):
    """A point whose REQUIRED FACT could not be verified (not_auditable) must not
    be reported as ordinary scientific insufficiency, and must not be routed to
    the scientific review authority.

    Reachability note: the public five-point seam turns missing critical context
    into integrity_failed before this state appears, so the combination is pinned
    here through the report's own point contract (the same `register` shape the
    other report tests use) rather than by driving the adapter.
    """
    from types import SimpleNamespace

    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    register = audit_dp_register(
        {"audit_id": "rp-notaud", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE},
        ledger)
    # replace one point's judgment with not_auditable, keeping every required field
    patched = dict(register)
    original = register["filtering"]
    patched["filtering"] = SimpleNamespace(**{
        field: ("not_auditable" if field == "judgment" else getattr(original, field))
        for field in ("judgment", "scope", "diagnostic_explanation", "declaration",
                      "limitations", "evidence_gaps", "next_actions",
                      "analysis_context", "evidence", "findings")})

    report = render_dp_report(patched, authority=_authority())
    categories = {f["category"] for f in report["findings_and_limitations"]}
    assert "integrity_failure" in categories
    assert "scientific_insufficiency" not in categories
    text = report["final_user_instruction"].lower()
    assert "governance authority" in text
    assert "scientific review authority" not in text


def test_scientific_insufficiency_is_distinct_from_integrity_failure(tmp_path):
    """The other direction: ordinary insufficiency must NOT be reported as an
    unverifiable fact (dp_strength already guards the reverse relabeling)."""
    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    soft = {name: _point(_declaration(method),
                         [_obs(method, verification="unverified")])
            for name, method in (("filtering", "low_count_filter"),
                                 ("normalization", "tmm"),
                                 ("differential_method", "deseq2"),
                                 ("multiple_testing_correction", "BH"),
                                 ("significance_threshold", "padj_0.05"))}
    register = audit_dp_register(
        {"audit_id": "rp-soft2", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": soft}, ledger)
    report = render_dp_report(register)
    categories = {f["category"] for f in report["findings_and_limitations"]}
    assert categories == {"scientific_insufficiency"}
    text = report["final_user_instruction"].lower()
    assert "scientific review authority" in text
    assert "governance authority" not in text


def test_p02_section_5_transcript_partitions_and_matches_the_spec_list():
    """The vocabulary transcript must stay a faithful, complete partition.

    P-02 §5 lists the meanings a surface must keep distinct (13 table rows at
    ``:416-430``, repeated across §8.1 ``:608`` and ``:615``). This test pins the
    labels and the expression kind of each, so a code change that adds or
    renames a state word has to face the transcript — and through it the
    capability disclosure and the artifact checker, which read this same data.
    """
    expected = {
        "auditable": "point_judgement",
        "scientifically_limited": "point_judgement",
        "conflicted": "point_judgement",
        "not_auditable": "point_judgement",
        "integrity_failed": "point_judgement",
        "historical_only": "history_side_judgement",
        "reviewed": "attestation",
        "blocked": "attestation",
        "limited-use": "attestation",
        "stale": "attestation",
        "scoped": "attestation",
        "planned": "attestation",
        "external": "not_expressible",
    }
    assert _P02_EXPRESSED_BY == expected
    assert len(_P02_SECTION_5_MEANINGS) == len(expected) == 13
    # `pending_attempt` is a judgement value this code defines; P-02 never names
    # it, so it must NOT be credited to the specification's vocabulary. The two
    # lists together must still be exactly the model's judgement set.
    assert "pending_attempt" not in _P02_EXPRESSED_BY
    assert set(_CODE_ONLY_JUDGEMENTS) == {"pending_attempt"}
    assert (set(label for label, kind in _P02_EXPRESSED_BY.items()
                if kind == "point_judgement")
            | set(_CODE_ONLY_JUDGEMENTS)) == set(DP01_JUDGMENTS)
    # the expressible side is exactly the three expressed-by groups, in order,
    # and the unexpressible side is its complement — i.e. a partition
    expressed = [label for label, kind in _P02_EXPRESSED_BY.items()
                 if kind != "not_expressible"]
    unexpressed = [label for label, kind in _P02_EXPRESSED_BY.items()
                   if kind == "not_expressible"]
    assert list(_P02_EXPRESSIBLE_LABELS) == expressed
    assert list(_P02_UNEXPRESSIBLE_LABELS) == unexpressed
    assert set(expressed) | set(unexpressed) == set(expected)
    assert not set(expressed) & set(unexpressed)
    # the disclosure states the transcript's own arithmetic
    status = _capability_status_for_state_vocabulary()
    assert f"P-02 §5 lists {len(expected)} state and consequence meanings" in status
    assert f"{len(expressed)} of {len(expected)} in total" in status
    assert "external is not expressible" in status


# ---------------------------------------------------------------------------
# P-02 §5 state meanings: mechanically attested, or explicitly unexpressed
# ---------------------------------------------------------------------------

def test_state_meanings_attests_only_derivable_words(five_auditable_register):
    section = render_dp_report(five_auditable_register)["state_meanings"]
    attested = section["attested"]
    # every word this report can derive, each with a reason + requirement
    for word in ("reviewed", "blocked", "limited-use", "stale", "scoped", "planned"):
        assert word in attested, word
        assert attested[word]["reason"], word
        assert attested[word]["next_requirement"], word
    # nothing was reviewed and nothing is pending in this fixture
    assert "NO reviewer record" in attested["reviewed"]["reason"]
    assert "no required decision" in attested["blocked"]["reason"]
    # this fixture is recorded under the current runtime, so it is not stale
    assert "matches the current runtime" in attested["stale"]["reason"]
    # the one meaning the system still cannot express is listed WITH its reason
    unexpressed = {e["state"]: e for e in section["unexpressed_states"]}
    assert set(unexpressed) == {"external"}
    for state, entry in unexpressed.items():
        assert entry["reason"], state
        assert entry["what_would_change_it"], state
    # and the disclosure accounts for the WHOLE P-02 §5 vocabulary, with every
    # count derived from the transcript rather than typed by hand.
    assert set(attested) | set(unexpressed) == {
        label for label, kind in _P02_EXPRESSED_BY.items()
        if kind in ("attestation", "not_expressible")}, (
            "the report's attestations plus its unexpressed list must cover the "
            "transcript's attestation and unexpressible groups exactly")
    assert set(attested) == {label for label, kind in _P02_EXPRESSED_BY.items()
                             if kind == "attestation"}
    expressible = len(_P02_EXPRESSIBLE_LABELS)
    status = section["not_yet_available"][-2]["status"]
    assert "the full state vocabulary" == section["not_yet_available"][-2]["capability"]
    assert f"{len(_P02_SECTION_5_MEANINGS)} state and consequence meanings" in status
    assert f"{expressible} of {len(_P02_SECTION_5_MEANINGS)} in total" in status
    for label in _P02_EXPRESSED_BY:
        assert label in status, f"the disclosure omits {label}"
    # the sentence a verifier's repair got wrong: the stated total must be the
    # transcript's own length, never a hand-written number
    assert status == _capability_status_for_state_vocabulary()


def test_stale_is_attested_when_a_point_carries_another_version(tmp_path):
    """P-02: 'a required current observation is no longer adequate'.

    Mechanically derived by comparing the point's recorded version binding with
    the current runtime — no human declaration involved.
    """
    from types import SimpleNamespace

    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    register = audit_dp_register(
        {"audit_id": "rp-stale", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE}, ledger)
    original = register["filtering"]
    recorded = dict(original.version_snapshot)
    recorded["engine_version"] = "0.0.0-older"
    patched = dict(register)
    patched["filtering"] = SimpleNamespace(**{
        **{f: getattr(original, f) for f in
           ("judgment", "scope", "diagnostic_explanation", "declaration", "limitations",
            "evidence_gaps", "next_actions", "analysis_context", "evidence", "findings")},
        "version_snapshot": recorded})

    stale = render_dp_report(patched)["state_meanings"]["attested"]["stale"]
    assert "different version binding" in stale["reason"]
    assert stale["points"][0]["point"] == "filtering"
    assert stale["points"][0]["differing"]["engine_version"]["recorded"] == "0.0.0-older"
    assert "current validity" in stale["must_not_be_inferred"]


def test_vocabulary_disclosure_states_its_counts_verbatim_and_guards_raise():
    """Two guarantees an adversarial review showed were only half-held.

    1. The counts must be STATED, not merely satisfiable by another number in the
       same sentence. A hand-typed total ("14 state and consequence meanings")
       used to pass a substring check because "13" still occurred inside
       "12 of 13 in total".
    2. The structural guards must not be switchable off. They used to be plain
       ``assert``s, which ``python -O`` strips — and with them stripped, a
       duplicated vocabulary row reached the public disclosure (the sentence then
       listed `auditable` twice and claimed 14 meanings).

    The guard firing itself is covered end-to-end by the adversarial suite's
    ``python -O`` case; here the shape of the guard is pinned so a later edit
    cannot quietly turn it back into an assert.
    """
    status = _capability_status_for_state_vocabulary()
    total = len(_P02_SECTION_5_MEANINGS)
    expressible = len(_P02_EXPRESSIBLE_LABELS)
    assert f"P-02 §5 lists {total} state and consequence meanings" in status
    assert f"{expressible} of {total} in total" in status
    # the numbers really are derived from the transcript, not literals
    assert total == len(_P02_EXPRESSED_BY)
    assert expressible == sum(1 for kind in _P02_EXPRESSED_BY.values()
                              if kind != "not_expressible")

    import bioaudit.dp_report as report_module  # noqa: PLC0415
    source = pathlib.Path(report_module.__file__).read_text(encoding="utf-8")
    for guard in ("P-02 §5 vocabulary transcript has a duplicate label",
                  "exactly one P-02 §5 meaning is expected to be unexpressible",
                  "a new judgement value has to be declared as"):
        assert guard in source, guard
    assert "assert len(set(_P02_EXPRESSED_BY))" not in source, (
        "the duplicate-label guard must raise, not assert (python -O strips asserts)")


def test_scoped_and_planned_are_attested_with_their_boundaries(five_auditable_register):
    attested = render_dp_report(five_auditable_register)["state_meanings"]["attested"]
    # scoped: the boundary is NAMED and the consequence stated (not just a label)
    assert attested["scoped"]["named_boundary"]["project_id"] == _CONTEXT["project_id"]
    assert "not be inferred" in attested["scoped"]["must_not_be_inferred"] or \
           "clearance" in attested["scoped"]["must_not_be_inferred"]
    # planned: report-level intent, explicitly NOT an account state
    assert "REPORT-LEVEL" in attested["planned"]["reason"]
    planned = attested["planned"]["planned_capabilities"]
    assert planned and all(e["status"] for e in planned), planned
    assert "never as this account's outcome" in attested["planned"]["must_not_be_inferred"]


def test_blocked_is_attested_when_a_change_awaits_adjudication(tmp_path):
    from types import SimpleNamespace

    from bioaudit.dp_change import DPChangeStore, record_change

    ledger = DP01JSONLStore(tmp_path / "ledger.jsonl")
    register = audit_dp_register(
        {"audit_id": "rp-blocked", "analysis_context": _CONTEXT,
         "integrity_metadata": _INTEGRITY, "decision_points": _FIVE_AUDITABLE}, ledger)
    authority = _authority()
    change = record_change(
        DPChangeStore(tmp_path / "changes.jsonl"), change_id="c-block",
        from_account=f"acceptance_snapshot:{authority.snapshot_id}",
        to_account="acceptance_snapshot:snapshot:later",
        change_classes=["intended_use"],
        differences=[{"change_class": "intended_use", "path": "intended_use",
                      "before": "a", "after": "b"}],
        reason="needs a decision", submitter="synthetic:analyst-1",
        requires_adjudication=True, adjudication_authority="governance authority")
    report = render_dp_report(register, authority=authority,
                              history=SimpleNamespace(proposed_change=change,
                                                      earlier_accounts=()))
    blocked = report["state_meanings"]["attested"]["blocked"]
    assert blocked["blockers"], "the pending adjudication must be reported as a block"
    assert "adjudication" in blocked["blockers"][0]["kind"]
    assert blocked["blockers"][0]["released_by"] == "governance authority"


def test_limited_use_is_attested_from_the_unconfirmed_basis(tmp_path):
    """The ceiling comes from the container's OWN basis being unconfirmed.

    Built through the real authority path (seven integrity domains left
    unverified) rather than a hand-made stub, so the attestation is exercised on
    an authority the product can actually produce.
    """
    from bioaudit.api import build_dp_acceptance_snapshot, dp_authority_store

    soft = {**{d: {"state": "unverified"} for d in
               ("identity", "version", "source", "binding", "snapshot",
                "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["t"]}
    request = {"audit_id": "rp-limited", "analysis_context": _CONTEXT,
               "integrity_metadata": soft, "decision_points": _FIVE_AUDITABLE}
    point_store = DP01JSONLStore(tmp_path / "points.jsonl")
    register = audit_dp_register(request, point_store)
    snapshot = build_dp_acceptance_snapshot(request, point_store,
                                            dp_authority_store(tmp_path / "a.jsonl"),
                                            register=register)
    assert snapshot.authority_basis["confirmed"] is False

    report = render_dp_report(register, authority=snapshot)
    limited = report["state_meanings"]["attested"]["limited-use"]
    assert "NOT confirmed" in limited["reason"]
    assert "identity" in limited["reason"]
    # it must not pass itself off as a release/lane decision
    assert "NOT from a release/lane decision" in limited["must_not_be_inferred"]


def test_state_meanings_introduces_no_new_point_state_words(five_auditable_register):
    """No silent vocabulary growth: every state word shown must be a P-02 §5 word."""
    allowed = {"auditable", "scientifically_limited", "conflicted", "not_auditable",
               "pending_attempt", "integrity_failed", "historical_only",
               "stale", "blocked", "scoped", "external", "planned", "reviewed",
               "limited-use", "integrity-failed"}
    report = render_dp_report(five_auditable_register)
    for point in report["per_point"]["points"]:
        assert point["judgment"] in allowed, point["judgment"]
    for word in report["state_meanings"]["attested"]:
        assert word in allowed, word
    for entry in report["state_meanings"]["unexpressed_states"]:
        assert entry["state"] in allowed, entry["state"]


# ---------------------------------------------------------------------------
# DP-REPORT-AUTHORITY: authority attribution + role records in the report
# ---------------------------------------------------------------------------

_STATEMENT = ("This report has not itself performed independent review and does "
              "not constitute a receipt, approval, or adjudication.")

_VERSION = {"ruleset_version": "1.7.0", "ontology_version": "0.1.3",
            "engine_version": "0.3.0", "input_format_version": "1.0",
            "adapter_version": "1.0"}


def _authority(snapshot_id="snapshot:rp-1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        stage_package=SimpleNamespace(input_snapshot={"audit_id": "rp-1"}),
        structured_strength_result={"validation": {"completeness": "partial"}},
        authority_basis={"confirmed": True, "unconfirmed_domains": [],
                         "basis": "integrity domains confirmed"},
        version_snapshot=dict(_VERSION),
    )


def _role(role="authored", actor="synthetic:author-1", target="report:rp-1",
          scope="the five decision declarations", receipt_kind=None):
    from types import SimpleNamespace
    return SimpleNamespace(role=role, actor=actor, target=target, scope=scope,
                           receipt_kind=receipt_kind)


def test_report_renders_authority_attribution(five_auditable_register):
    report = render_dp_report(five_auditable_register, authority=_authority())
    attribution = report["review_and_admin"]["authority_attribution"]
    # P-03 row142 minimum meaning: the container kind, the pre-snapshot input
    # role, and "exactly one embedded result" are all stated.
    assert attribution["container_kind"] == "acceptance_snapshot"
    assert attribution["stage_package_role"] == "pre_snapshot_input"
    assert attribution["embedded_result_count"] == 1
    assert "sole result-authority container" in attribution["minimum_meaning"]
    assert attribution["snapshot_id"] == "snapshot:rp-1"
    assert attribution["authority_basis"]["confirmed"] is True
    assert set(attribution["version_binding"]) == set(_VERSION)
    # ...and the reader must see that this report is neither the container nor a
    # second one (P-03 row142 unacceptable behaviour list).
    assert attribution["not_this_report"] is True
    assert attribution["not_a_second_container"] is True


def test_authority_attribution_key_set_is_closed(five_auditable_register):
    """No extra key (e.g. a back-reference or a copied result) may appear."""
    report = render_dp_report(five_auditable_register, authority=_authority())
    attribution = report["review_and_admin"]["authority_attribution"]
    assert set(attribution) == {
        "container_kind", "snapshot_id", "minimum_meaning", "stage_package_role",
        "embedded_result_count", "authority_basis", "version_binding",
        "not_this_report", "not_a_second_container",
    }


def test_authority_attribution_does_not_copy_the_result(five_auditable_register):
    report = render_dp_report(five_auditable_register, authority=_authority())
    attribution = report["review_and_admin"]["authority_attribution"]

    def walk(node):
        if isinstance(node, dict):
            for key in node:
                assert key not in {"validation", "scope", "inference_type",
                                   "explanation_depth"}, key
                walk(node[key])
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(attribution)


def test_report_renders_role_records(five_auditable_register):
    roles = [_role(),
             _role(role="adjudicated", actor="synthetic:adjudicator-1",
                   target="acceptance_snapshot:snapshot:rp-1",
                   scope="scientific adjudication of the disputed method"),
             _role(role="receipted", actor="synthetic:coordinator-1",
                   target="acceptance_snapshot:snapshot:rp-1",
                   scope="administrative receipt", receipt_kind="administrative")]
    report = render_dp_report(five_auditable_register, roles=roles)
    section = report["review_and_admin"]
    assert section["roles_recorded"] is True
    assert section["roles_supplied"] is True
    assert [r["role"] for r in section["roles"]] == ["authored", "adjudicated", "receipted"]
    for rendered, source in zip(section["roles"], roles):
        # exact key set: nothing extra (e.g. a score) can silently appear
        assert set(rendered) == {"role", "actor", "target", "scope", "receipt_kind"}
        assert rendered["actor"] == source.actor
        assert rendered["target"] == source.target
        assert rendered["scope"] == source.scope
    assert section["roles"][2]["receipt_kind"] == "administrative"
    # S1: the report exposes which of the four categories are present/absent
    assert section["role_categories"]["recorded"] == ["authored", "receipted", "adjudicated"]
    assert section["role_categories"]["not_recorded"] == ["reviewed"]


def test_report_states_when_no_roles_supplied(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    section = report["review_and_admin"]
    assert section["roles"] == []
    assert section["roles_supplied"] is False
    assert section["roles_recorded"] is False
    assert section["role_categories"]["recorded"] == []
    assert section["role_categories"]["not_recorded"] == [
        "authored", "reviewed", "receipted", "adjudicated"]
    # the wording is bounded by the input (it reports what the report was given)
    text = section["roles_statement"].lower()
    assert "no role records were provided" in text
    assert "cannot say" in text


def test_report_distinguishes_supplied_empty_from_not_supplied(five_auditable_register):
    report = render_dp_report(five_auditable_register, roles=[])
    section = report["review_and_admin"]
    assert section["roles_supplied"] is True
    assert section["roles_recorded"] is False


def test_review_and_admin_keeps_original_statement(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    assert report["review_and_admin"]["statement"] == _STATEMENT


def test_report_without_optional_inputs_has_no_attribution(five_auditable_register):
    report = render_dp_report(five_auditable_register)
    assert report["review_and_admin"]["authority_attribution"] is None


def test_report_rejects_malformed_authority(five_auditable_register):
    from types import SimpleNamespace

    def authority(**overrides):
        base = dict(snapshot_id="snapshot:rp-1",
                    stage_package=SimpleNamespace(input_snapshot={}),
                    structured_strength_result={"validation": {}},
                    authority_basis={"confirmed": True, "unconfirmed_domains": [],
                                     "basis": "integrity domains confirmed"},
                    version_snapshot=dict(_VERSION))
        base.update(overrides)
        return SimpleNamespace(**base)

    # each breakage is isolated so the failing check is identifiable
    with pytest.raises(ValueError, match="non-empty snapshot_id"):
        render_dp_report(five_auditable_register, authority=authority(snapshot_id=""))
    with pytest.raises(ValueError, match="stage_package"):
        render_dp_report(five_auditable_register, authority=authority(stage_package=None))
    with pytest.raises(ValueError, match="exactly one embedded mapping"):
        render_dp_report(five_auditable_register,
                         authority=authority(structured_strength_result=[{}, {}]))
    with pytest.raises(ValueError, match="authority_basis"):
        render_dp_report(five_auditable_register, authority=authority(authority_basis={}))
    with pytest.raises(ValueError, match="authority_basis"):
        render_dp_report(five_auditable_register,
                         authority=authority(authority_basis={"confirmed": "yes"}))
    without_field = dict(_VERSION)
    del without_field["adapter_version"]
    with pytest.raises(ValueError, match="version_snapshot"):
        render_dp_report(five_auditable_register,
                         authority=authority(version_snapshot=without_field))
    # a shape that is not an acceptance snapshot at all
    with pytest.raises(TypeError, match="missing"):
        render_dp_report(five_auditable_register, authority=SimpleNamespace(snapshot_id="x"))


def test_report_rejects_malformed_roles(five_auditable_register):
    # unknown role
    with pytest.raises(ValueError, match="invalid role"):
        render_dp_report(five_auditable_register, roles=[_role(role="approved")])
    # missing / empty named fields
    with pytest.raises(ValueError, match="named scope"):
        render_dp_report(five_auditable_register, roles=[_role(scope="")])
    with pytest.raises(ValueError, match="named actor"):
        render_dp_report(five_auditable_register, roles=[_role(actor=None)])
    # target must use an authorized kind prefix
    with pytest.raises(ValueError, match="authorized kind prefix"):
        render_dp_report(five_auditable_register,
                         roles=[_role(target="somewhere-else:whatever")])
    # B2: the report mirrors the role ledger's receipt rules
    with pytest.raises(ValueError, match="requires receipt_kind"):
        render_dp_report(five_auditable_register,
                         roles=[_role(role="receipted", target="report:rp-1")])
    with pytest.raises(ValueError, match="requires receipt_kind"):
        render_dp_report(five_auditable_register,
                         roles=[_role(role="receipted", target="report:rp-1",
                                      receipt_kind="SUBSTANTIVE_APPROVAL")])
    with pytest.raises(ValueError, match="only valid for role 'receipted'"):
        render_dp_report(five_auditable_register,
                         roles=[_role(role="reviewed", receipt_kind="administrative")])
    # roles container itself must be an iterable of records
    with pytest.raises(TypeError, match="iterable"):
        render_dp_report(five_auditable_register, roles="authored")
    with pytest.raises(TypeError, match="iterable"):
        render_dp_report(five_auditable_register, roles={"role": "authored"})
    with pytest.raises(TypeError):
        render_dp_report(five_auditable_register, roles=42)


def test_report_with_authority_and_roles_stays_non_authority(five_auditable_register):
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              roles=[_role()])
    assert report["_report_meta"]["not_authority"] is True
    assert report["_report_meta"]["not_acceptance"] is True

    def walk(node):
        if isinstance(node, dict):
            for key in node:
                assert key not in {"acceptance", "release", "lane", "deployment",
                                   "package_a"}, key
                walk(node[key])
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(report)


def test_api_exposes_report_optional_parameters():
    import inspect

    from bioaudit import api

    params = inspect.signature(api.render_dp_report).parameters
    assert "authority" in params
    assert "roles" in params
    assert "history" in params


# ---------------------------------------------------------------------------
# DP-CHANGE: history/change meaning in the report (P-02 §6.4/§6.5, P-03 row147)
# ---------------------------------------------------------------------------

_ACCOUNT = "acceptance_snapshot:snapshot:rp-1"
_EARLIER = "acceptance_snapshot:snapshot:rp-0"


def _proposed(requires_adjudication=True, escalation_required=False):
    from types import SimpleNamespace
    kwargs = dict(
        from_account=_EARLIER, to_account=_ACCOUNT,
        change_classes=["decision_interpretation"],
        differences=[{"change_class": "decision_interpretation",
                      "path": "result.validation.completeness",
                      "before": "complete", "after": "partial"}],
        reason="the threshold evidence was requalified",
        submitter="synthetic:analyst-1",
        requires_adjudication=requires_adjudication,
        escalation_required=escalation_required,
    )
    if requires_adjudication:
        kwargs["adjudication_authority"] = "scientific-reviewer"
    if escalation_required:
        kwargs["escalation_reason"] = "creates_second_result_authority_path"
    return SimpleNamespace(**kwargs)


def test_history_absent_is_stated_explicitly(five_auditable_register):
    report = render_dp_report(five_auditable_register, authority=_authority())
    section = report["history_and_change"]
    assert section["history_supplied"] is False
    assert section["earlier_accounts"] == []
    assert section["proposed_change"] is None
    assert section["current_account"] == "acceptance_snapshot:snapshot:rp-1"
    text = section["history_statement"].lower()
    assert "no change context was provided" in text


def test_history_renders_earlier_accounts_as_historical_only(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[_EARLIER], proposed_change=None)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    section = report["history_and_change"]
    assert section["earlier_accounts"] == [{
        "account_ref": _EARLIER, "meaning": "historical_only",
        "scope": None, "time": None}]
    # an earlier account is never presented as current authority
    assert all(e["meaning"] != "current" for e in section["earlier_accounts"])


def test_proposed_change_shows_difference_and_adjudicating_authority(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[_EARLIER],
                              proposed_change=_proposed())
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    change = report["history_and_change"]["proposed_change"]
    assert change["from_account"] == _EARLIER
    assert change["to_account"] == _ACCOUNT
    # the current account is the report's own authority; the change is relative to it
    assert change["current_account"] == _ACCOUNT
    assert change["prior_account"] == _EARLIER
    assert change["prior_meaning"] == "historical_only"
    # requires adjudication -> the change may not be treated as settled
    assert change["state"] == "awaiting_human_decision"
    assert change["adjudication_authority"] == "scientific-reviewer"
    assert change["declaration_basis"] == "declared_by_submitter"
    assert change["differences"][0]["before"] == "complete"
    assert change["differences"][0]["after"] == "partial"
    assert set(change) == {
        "from_account", "to_account", "state", "state_scope", "current_account",
        "prior_account", "prior_meaning", "change_classes", "differences", "reason",
        "submitter", "requires_adjudication", "adjudication_authority",
        "escalation_required", "escalation_reason", "declaration_basis",
    }


def test_realized_change_is_not_reported_as_proposed(five_auditable_register):
    """When the change's later side IS the current account, the change has been
    realized — the report must not claim the prior account is still current."""
    from types import SimpleNamespace
    proposed = _proposed(requires_adjudication=False)
    history = SimpleNamespace(earlier_accounts=[_EARLIER], proposed_change=proposed)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    change = report["history_and_change"]["proposed_change"]
    assert change["state"] == "realized"
    assert change["current_account"] == _ACCOUNT
    assert change["prior_account"] == _EARLIER
    assert change["current_account"] != change["prior_account"]


def test_unrealized_change_keeps_prior_side_current(five_auditable_register):
    """A change whose later side is NOT the current account is a proposal: the
    current account stays the from side."""
    from types import SimpleNamespace
    proposed = _proposed(requires_adjudication=False)
    proposed.from_account = _ACCOUNT          # current
    proposed.to_account = "acceptance_snapshot:snapshot:rp-2"   # not yet current
    history = SimpleNamespace(earlier_accounts=[_EARLIER], proposed_change=proposed)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    change = report["history_and_change"]["proposed_change"]
    assert change["state"] == "proposed"
    assert change["current_account"] == _ACCOUNT
    assert change["prior_account"] == "acceptance_snapshot:snapshot:rp-2"


def test_change_unrelated_to_current_account_is_rejected(five_auditable_register):
    """The report cannot relate a change to an account it is not about."""
    from types import SimpleNamespace
    proposed = _proposed(requires_adjudication=False)
    proposed.from_account = "acceptance_snapshot:snapshot:rp-7"
    proposed.to_account = "acceptance_snapshot:snapshot:rp-8"
    with pytest.raises(ValueError, match="must include the current account"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=SimpleNamespace(earlier_accounts=[],
                                                 proposed_change=proposed))


def test_proposed_change_requires_a_known_current_account(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[], proposed_change=_proposed())
    with pytest.raises(ValueError, match="current account"):
        render_dp_report(five_auditable_register, history=history)


def test_escalating_change_is_blocked(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[], proposed_change=_proposed(
        requires_adjudication=False, escalation_required=True))
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    change = report["history_and_change"]["proposed_change"]
    assert change["state"] == "awaiting_human_decision"
    assert change["escalation_reason"] == "creates_second_result_authority_path"


def test_earlier_accounts_carry_scope_and_time_when_provided(five_auditable_register):
    """P-02 §6.4 L508: prior material keeps its original SCOPE and TIME meaning."""
    from types import SimpleNamespace
    history = SimpleNamespace(
        earlier_accounts=[{"account_ref": _EARLIER,
                           "scope": "project-1 / analysis-1",
                           "time": "2026-09-01"}],
        proposed_change=None)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    section = report["history_and_change"]
    assert section["earlier_accounts"] == [{
        "account_ref": _EARLIER, "meaning": "historical_only",
        "scope": "project-1 / analysis-1", "time": "2026-09-01"}]
    assert section["earlier_scope_time_note"] is None


def test_missing_earlier_scope_time_is_stated_not_implied(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[_EARLIER], proposed_change=None)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    section = report["history_and_change"]
    assert section["earlier_accounts"][0]["scope"] is None
    assert "scope and time were not provided" in section["earlier_scope_time_note"]


def test_history_supplied_but_empty_does_not_claim_it_was_absent(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[], proposed_change=None)
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    section = report["history_and_change"]
    assert section["history_supplied"] is True
    # the wording must not contradict the input
    assert "no change context was provided" not in section["history_statement"].lower()
    assert "no earlier accounts and no proposed change" in section["history_statement"].lower()


def test_history_and_change_key_set_is_closed(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(earlier_accounts=[_EARLIER], proposed_change=_proposed())
    report = render_dp_report(five_auditable_register, authority=_authority(),
                              history=history)
    assert set(report["history_and_change"]) == {
        "format", "statement", "current_account", "earlier_accounts",
        "proposed_change", "history_supplied", "history_statement",
        "earlier_scope_time_note", "other_changes_note",
    }


def test_history_rejects_earlier_account_equal_to_current(five_auditable_register):
    from types import SimpleNamespace
    history = SimpleNamespace(
        earlier_accounts=["acceptance_snapshot:snapshot:rp-1"],
        proposed_change=None)
    with pytest.raises(ValueError, match="cannot also be the current account"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=history)


def test_history_rejects_malformed_inputs(five_auditable_register):
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="account references"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=SimpleNamespace(earlier_accounts=["report:x"],
                                                 proposed_change=None))
    with pytest.raises(TypeError, match="iterable"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=SimpleNamespace(earlier_accounts="report:x",
                                                 proposed_change=None))
    # a proposed change missing the adjudication authority
    bad = _proposed()
    bad.adjudication_authority = None
    with pytest.raises(ValueError, match="adjudication authority"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=SimpleNamespace(earlier_accounts=[],
                                                 proposed_change=bad))
    # an unknown change class
    bad2 = _proposed()
    bad2.change_classes = ["not_a_class"]
    with pytest.raises(ValueError, match="closed vocabulary"):
        render_dp_report(five_auditable_register, authority=_authority(),
                         history=SimpleNamespace(earlier_accounts=[],
                                                 proposed_change=bad2))


def test_history_rejects_material_that_is_not_history_context(five_auditable_register,
                                                              tmp_path):
    """An unrelated carrier must not be silently rendered as "no history".

    Passing something that exposes neither history facet is a caller error: it
    would otherwise let an ungoverned object answer the history question by
    accident (and, for the outer replay binding, let that record insinuate itself
    into a report input).
    """
    from types import SimpleNamespace

    from bioaudit.api import dp_outer_binding_store, record_dp_outer_binding

    binding = record_dp_outer_binding(
        dp_outer_binding_store(tmp_path / "outer.jsonl"),
        binding_id="b-not-history",
        bound_account="acceptance_snapshot:snapshot:rp-1",
        stage_material_ref="stage_package:stage:rp-1",
        verification_statement="NOT-HISTORY-CONTEXT",
        verifier="synthetic:verifier-1")

    for wrong in (binding, SimpleNamespace(), "acceptance_snapshot:snapshot:rp-1"):
        with pytest.raises(TypeError, match="history/change context"):
            render_dp_report(five_auditable_register, authority=_authority(),
                             history=wrong)


# ---------------------------------------------------------------------------
# 9. NON-PUBLIC (DP-OUTER scenario 9): the report never contains binding material
#    (the end-to-end guard also lives in test_dp_outer.py, where it was delivered;
#    this copy keeps the constraint visible next to the report's own tests)
# ---------------------------------------------------------------------------

def test_report_never_contains_outer_binding_material(five_auditable_register, tmp_path):
    from bioaudit.api import dp_outer_binding_store, record_dp_outer_binding

    binding = record_dp_outer_binding(
        dp_outer_binding_store(tmp_path / "outer.jsonl"),
        binding_id="b-report-guard",
        bound_account="acceptance_snapshot:snapshot:rp-1",
        stage_material_ref="stage_package:stage:rp-1",
        verification_statement="BOUND-PROSE-MARKER",
        verifier="synthetic:verifier-outer-1")

    report = render_dp_report(five_auditable_register, authority=_authority())

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                yield from strings(value)

    values = list(strings(report))
    assert not any(binding.binding_id in v for v in values)
    assert not any(binding.verifier in v for v in values)
    assert not any(binding.stage_material_ref in v for v in values)
    assert not any(binding.verification_statement in v for v in values)


