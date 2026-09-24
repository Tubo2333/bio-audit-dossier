"""Replay real-caller slice: exercise the public `bioaudit.api.replay_dp01_filtering`
seam end to end.

The point of this slice (DP01-REPLAY-CALLER) is that an upper-layer caller exists:
every scenario imports the replay seam ONLY through `bioaudit.api`, never reaching
for the private adapter `_audit_dp01_filtering` directly.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from bioaudit.api import audit_dp01_filtering, replay_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore


def _make_request(audit_id="v1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {
            "project_id": "p",
            "analysis_id": "a",
            "comparison": "case_vs_control",
            "data_type": "bulk_rnaseq",
            "audit_scope": "filtering",
            "intended_use": "scientific_analysis",
            "exclusions": "none — no explicit exclusions declared",
            "exclusions": "none — no explicit exclusions declared",
            "exclusions": "none — no explicit exclusions declared",
        },
        "decision_declaration": {
            "method": "low_count_filter",
            "threshold": 10,
            "sample_rule": "at_least_two_samples",
            "unit": "gene",
            "source": "declared",
        },
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
            "material_ids": ["m"],
            "pending_targets": ["p"],
        },
    }


def test_api_replay_root_matches_recorded_result(tmp_path):
    store = DP01JSONLStore(tmp_path / "root.jsonl")
    audit_dp01_filtering(_make_request(), store)
    replayed = replay_dp01_filtering(store, "v1")
    assert replayed.audit_id == "v1"
    assert replayed.result == store.get("v1").result


def test_api_replay_child_skips_comparison(tmp_path):
    store = DP01JSONLStore(tmp_path / "child.jsonl")
    audit_dp01_filtering(_make_request(), store)
    child = {
        **_make_request("child"),
        "attempt": {"parent_audit_id": "v1", "change_kind": "evidence_supplement", "change_reason": "x"},
    }
    audit_dp01_filtering(child, store)
    replayed = replay_dp01_filtering(store, "child")
    assert replayed.audit_id == "child"
    assert replayed.parent_audit_id == "v1"


def test_api_replay_missing_id_raises_key_error(tmp_path):
    store = DP01JSONLStore(tmp_path / "empty.jsonl")
    with pytest.raises(KeyError, match="v1"):
        replay_dp01_filtering(store, "v1")


def test_api_replay_version_mismatch_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "mismatch.jsonl")
    audit_dp01_filtering(_make_request(), store)
    import json as _json

    line = store.path.read_text(encoding="utf-8").splitlines()[0]
    payload = _json.loads(line)
    payload["version_snapshot"]["engine_version"] = "old-engine"
    store.path.write_text(_json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        replay_dp01_filtering(store, "v1")


def test_api_replay_divergence_fails_closed(tmp_path):
    store = DP01JSONLStore(tmp_path / "tampered.jsonl")
    audit_dp01_filtering(_make_request(), store)
    import json as _json

    line = store.path.read_text(encoding="utf-8").splitlines()[0]
    payload = _json.loads(line)
    payload["result"]["judgment"] = "not_auditable"
    store.path.write_text(_json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="replay mismatch"):
        replay_dp01_filtering(store, "v1")