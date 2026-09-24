"""DP-CHANGE — history/change records (P-02 §6.4 / §6.5, P-03 row147).

A later audited account is a NEW bounded judgment related to a prior account, not
an invisible replacement: prior material stays historical-only, the
proposed/current difference is visible, and the authority that must decide whether
the change is acceptable is named. Sources that cannot be established fail closed.

Runs via the project pytest `pythonpath=["src"]`.
"""

import json

import pytest

from bioaudit.api import dp_change_store, list_dp_changes, record_dp_change

_FROM = "acceptance_snapshot:snapshot:account-1"
_TO = "acceptance_snapshot:snapshot:account-2"

_DIFferences = [{"change_class": "decision_interpretation",
                 "path": "result.validation.completeness",
                 "before": "complete", "after": "partial"}]


def _record(store, **kw):
    params = {
        "change_id": "c1",
        "from_account": _FROM,
        "to_account": _TO,
        "change_classes": ["decision_interpretation"],
        "differences": [dict(_DIFferences[0])],
        "reason": "the threshold evidence was requalified",
        "submitter": "synthetic:analyst-1",
        "requires_adjudication": True,
        "adjudication_authority": "scientific-reviewer",
        "escalation_required": False,
    }
    params.update(kw)
    params = {k: v for k, v in params.items() if v is not None}
    return record_dp_change(store, **params)


@pytest.fixture
def change_ledger(tmp_path):
    return dp_change_store(tmp_path / "changes.jsonl")


# 1. the seven change classes are a closed vocabulary
def test_all_seven_classes_accepted(change_ledger):
    classes = ["project_or_analysis_context", "input_or_version",
               "evidence_or_provenance", "decision_interpretation",
               "limitation_or_conflict", "intended_use",
               "review_or_authority_boundary"]
    record = _record(change_ledger, change_classes=classes,
                     differences=[{"change_class": c, "path": f"p.{c}",
                                   "before": "a", "after": "b"} for c in classes])
    assert set(record.change_classes) == set(classes)
    assert change_ledger.get("c1") is not None


def test_unknown_change_class_rejected(change_ledger):
    with pytest.raises(ValueError, match="invalid change class"):
        _record(change_ledger, change_classes=["not_a_class"])


def test_empty_change_classes_rejected(change_ledger):
    with pytest.raises(ValueError):
        _record(change_ledger, change_classes=[])


# 2. differences must be non-empty and use the closed vocabulary
def test_empty_differences_rejected(change_ledger):
    with pytest.raises(ValueError, match="differences"):
        _record(change_ledger, differences=[])


def test_difference_class_must_be_known(change_ledger):
    with pytest.raises(ValueError, match="invalid change class"):
        _record(change_ledger,
                differences=[{"change_class": "nope", "path": "p",
                              "before": 1, "after": 2}])


def test_repeated_change_class_rejected(change_ledger):
    with pytest.raises(ValueError, match="must not repeat"):
        _record(change_ledger, change_classes=["intended_use", "intended_use"])


def test_difference_path_must_be_a_named_string(change_ledger):
    with pytest.raises(ValueError, match="difference path"):
        _record(change_ledger,
                differences=[{"change_class": "intended_use", "path": 7,
                              "before": "a", "after": "b"}])


def test_flags_must_be_booleans(change_ledger):
    with pytest.raises(ValueError, match="requires_adjudication"):
        _record(change_ledger, requires_adjudication="yes",
                adjudication_authority="scientific-reviewer")
    with pytest.raises(ValueError, match="escalation_required"):
        _record(change_ledger, escalation_required="yes")


def test_stored_record_keeps_explicit_none_for_optional_fields(change_ledger):
    """'absent' must not be confused with 'explicitly none' after a round trip."""
    _record(change_ledger, requires_adjudication=False,
            adjudication_authority=None, escalation_required=False,
            escalation_reason=None)
    payload = change_ledger._payloads()[0]        # noqa: SLF001 - contract probe
    assert "adjudication_authority" in payload and payload["adjudication_authority"] is None
    assert "escalation_reason" in payload and payload["escalation_reason"] is None
    record = change_ledger.get("c1")
    assert record.adjudication_authority is None
    assert record.escalation_reason is None


def test_difference_shape_is_closed(change_ledger):
    with pytest.raises(ValueError):
        _record(change_ledger,
                differences=[{"change_class": "intended_use", "path": "p",
                              "before": 1}])


# 3. named fields, version binding, ledger discipline
def test_named_fields_required(change_ledger):
    for field in ("reason", "submitter"):
        with pytest.raises(ValueError, match=field):
            _record(change_ledger, **{field: ""})


def test_duplicate_change_id_rejected(change_ledger):
    _record(change_ledger)
    with pytest.raises(ValueError, match="duplicate"):
        _record(change_ledger)


def test_cross_line_duplicate_rejected(change_ledger):
    record = _record(change_ledger)
    with open(change_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        change_ledger.get("c1")


def test_bad_json_line_reports_line_number(change_ledger):
    _record(change_ledger)
    with open(change_ledger.path, "a", encoding="utf-8") as fh:
        fh.write("{not-json}\n")
    with pytest.raises(ValueError, match="line"):
        change_ledger.get("c1")


def test_version_mismatch_rejected(change_ledger):
    record = _record(change_ledger)
    payload = record.to_dict()
    payload["change_id"] = "c-ver"
    payload["version_snapshot"] = {**payload["version_snapshot"],
                                   "engine_version": "0.0.1"}
    with open(change_ledger.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        change_ledger.get("c-ver")


def test_append_lock_present(change_ledger):
    assert hasattr(change_ledger, "_append_lock")


# 4. both sides must be ACCOUNT references, and they must differ
def test_accounts_must_be_account_references(change_ledger):
    with pytest.raises(ValueError, match="account reference"):
        _record(change_ledger, from_account="report:some-report")
    with pytest.raises(ValueError, match="account reference"):
        _record(change_ledger, to_account="point:analysis-1:dp01")


def test_same_account_rejected(change_ledger):
    with pytest.raises(ValueError, match="must differ"):
        _record(change_ledger, to_account=_FROM)


# 5. adjudication and escalation coupling
def test_adjudication_authority_required_when_adjudication_needed(change_ledger):
    params = {"change_id": "c2", "from_account": _FROM, "to_account": _TO,
              "change_classes": ["intended_use"],
              "differences": [{"change_class": "intended_use", "path": "p",
                               "before": "a", "after": "b"}],
              "reason": "r", "submitter": "synthetic:analyst-1",
              "requires_adjudication": True, "escalation_required": False}
    with pytest.raises(ValueError, match="adjudication_authority"):
        record_dp_change(change_ledger, **params)


def test_adjudication_authority_forbidden_without_adjudication(change_ledger):
    with pytest.raises(ValueError, match="adjudication_authority"):
        _record(change_ledger, requires_adjudication=False,
                adjudication_authority="scientific-reviewer")


def test_escalation_reason_must_come_from_the_closed_list(change_ledger):
    with pytest.raises(ValueError, match="escalation_reason"):
        _record(change_ledger, escalation_required=True,
                escalation_reason="because I said so")
    record = _record(change_ledger, change_id="c3", escalation_required=True,
                     escalation_reason="creates_second_result_authority_path")
    assert record.escalation_reason == "creates_second_result_authority_path"


def test_escalation_reason_forbidden_without_escalation(change_ledger):
    with pytest.raises(ValueError, match="escalation_reason"):
        _record(change_ledger, escalation_required=False,
                escalation_reason="promotes_material_beyond_its_role")


# lists all records
def test_list_changes(change_ledger):
    _record(change_ledger)
    _record(change_ledger, change_id="c4", from_account=_TO,
            to_account="acceptance_snapshot:snapshot:account-3")
    assert [r.change_id for r in list_dp_changes(change_ledger)] == ["c1", "c4"]
