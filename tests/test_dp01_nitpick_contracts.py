"""DP-01 nitpick N1-N6 contract tests (DP01-NITPICKS).

Only N6 changes observable behavior (fail-closed on re_audit+attempt
combination); everything else is behavior-neutral tidying guarded by positive
controls. All calls go through the public seam.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore

VERSION_KEYS = {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"}


def _make_request(audit_id="audit-1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p", "analysis_id": "a", "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {"method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"], "pending_targets": ["p"],
        },
    }


def _audit_chain(store):
    audit_dp01_filtering(_make_request("r1"), store)
    child = {**_make_request("ch-1"), "attempt": {"parent_audit_id": "r1", "change_kind": "evidence_supplement", "change_reason": "x"}}
    audit_dp01_filtering(child, store)


def _reaudit_request(audit_id="ra-1"):
    # no integrity_metadata: exercises the omitted-integrity inheritance path
    return {"audit_id": audit_id, "re_audit": {"operation": "re_audit", "target_audit_id": "ch-1"}}


class _CountingStore:
    """Duck-typed store that records every get() audit_id."""

    def __init__(self, store):
        self._s = store
        self.calls = []

    def get(self, audit_id):
        self.calls.append(audit_id)
        return self._s.get(audit_id)

    def append(self, record):
        return self._s.append(record)

    @property
    def path(self):
        return self._s.path


# N6: fail-closed on re_audit + attempt combination

def test_combining_re_audit_and_attempt_fails_closed(tmp_path):
    path = tmp_path / "both.jsonl"
    store = DP01JSONLStore(path)
    _audit_chain(store)
    request = {
        **_make_request("ra-both"),
        "re_audit": {"operation": "re_audit", "target_audit_id": "ch-1"},
        "attempt": {"parent_audit_id": "r1", "change_kind": "evidence_supplement", "change_reason": "x"},
    }
    with pytest.raises(ValueError, match="combine"):
        audit_dp01_filtering(request, store)


# N2: re-audit fetches the target record exactly once

def test_reaudit_fetches_target_once(tmp_path):
    store = DP01JSONLStore(tmp_path / "single-get.jsonl")
    _audit_chain(store)
    counter = _CountingStore(store)
    audit_dp01_filtering(_reaudit_request(), counter)
    assert counter.calls == ["ch-1"]


# N6/N4 positive control: a normal re-audit (no attempt) still succeeds and the
# omitted-integrity inheritance path (single-pass, no recursion) still works

def test_normal_reaudit_still_succeeds_with_inherited_integrity(tmp_path):
    store = DP01JSONLStore(tmp_path / "normal-reaudit.jsonl")
    _audit_chain(store)
    result = audit_dp01_filtering(_reaudit_request(), store)
    assert result.judgment in ("auditable", "scientifically_limited")
    record = store.get("ra-1")
    assert record.operation == "re_audit"
    assert record.parent_audit_id == "ch-1"
    assert record.source_version_snapshot is not None


# N5: the re-audit change kind/reason stay fixed by the contract

def test_reaudit_change_kind_and_reason_semantics(tmp_path):
    store = DP01JSONLStore(tmp_path / "kind.jsonl")
    _audit_chain(store)
    audit_dp01_filtering(_reaudit_request(), store)
    record = store.get("ra-1")
    assert record.change.kind == "decision_correction"
    assert record.change.reason == "explicit re-audit"


# N1/N3 guards: bound public result still carries the five-field version
# snapshot (module-level import keeps behavior), replay docstring stays precise

def test_result_version_snapshot_has_five_keys(tmp_path):
    store = DP01JSONLStore(tmp_path / "versnap.jsonl")
    result = audit_dp01_filtering(_make_request(), store)
    assert set(result.version_snapshot) == VERSION_KEYS


def test_replay_docstring_mentions_child_attempt_skip():
    doc = DP01JSONLStore.replay.__doc__ or ""
    assert "child-attempt" in doc.lower()
    assert "comparison" in doc