from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.dp01_store import DP01JSONLStore
from bioaudit.dp01_models import DP01_INPUT_FORMAT_VERSION, DP01_ADAPTER_VERSION
from bioaudit.report import SnapshotTriple, assert_snapshot_compatible

VERSION_KEYS = {"ruleset_version", "ontology_version", "engine_version", "input_format_version", "adapter_version"}


def _replay_request(audit_id="v1"):
    return {
        "audit_id": audit_id,
        "analysis_context": {"project_id": "p", "analysis_id": "a", "comparison": "case_vs_control", "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared"},
        "decision_declaration": {"method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared"},
        "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
        "integrity_metadata": {**{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")}, "material_ids": ["m"], "pending_targets": ["p"]},
    }


def test_version_constants_are_defined():
    assert isinstance(DP01_INPUT_FORMAT_VERSION, str) and DP01_INPUT_FORMAT_VERSION
    assert isinstance(DP01_ADAPTER_VERSION, str) and DP01_ADAPTER_VERSION


def test_current_snapshot_accepts_five_field_shape():
    current = SnapshotTriple("rules", "ontology", "engine", extra={"input_format_version": DP01_INPUT_FORMAT_VERSION, "adapter_version": DP01_ADAPTER_VERSION})
    recorded = {"ruleset_version": "rules", "ontology_version": "ontology", "engine_version": "engine", "input_format_version": DP01_INPUT_FORMAT_VERSION, "adapter_version": DP01_ADAPTER_VERSION}
    assert_snapshot_compatible(recorded, current)


def test_any_version_mismatch_fails_closed():
    current = SnapshotTriple("rules", "ontology", "engine", extra={"input_format_version": DP01_INPUT_FORMAT_VERSION, "adapter_version": DP01_ADAPTER_VERSION})
    for key in VERSION_KEYS:
        recorded = {"ruleset_version": "rules", "ontology_version": "ontology", "engine_version": "engine", "input_format_version": DP01_INPUT_FORMAT_VERSION, "adapter_version": DP01_ADAPTER_VERSION}
        recorded[key] = "old"
        with pytest.raises(ValueError, match="mismatch"):
            assert_snapshot_compatible(recorded, current)


def test_dp01_record_persists_five_field_snapshot(tmp_path):
    from bioaudit.api import audit_dp01_filtering
    store = DP01JSONLStore(tmp_path / "v.jsonl")
    audit_dp01_filtering(_replay_request(), store)
    payload = store.get("v1")
    assert payload.audit_id == "v1"
    raw = __import__("json").loads(store.path.read_text(encoding="utf-8"))
    assert set(raw["version_snapshot"]) == VERSION_KEYS
    assert raw["version_snapshot"]["input_format_version"] == DP01_INPUT_FORMAT_VERSION
    assert raw["version_snapshot"]["adapter_version"] == DP01_ADAPTER_VERSION


def test_replay_matches_recorded_result(tmp_path):
    from bioaudit.api import audit_dp01_filtering
    from bioaudit.dp01_adapter import _audit_dp01_filtering
    store = DP01JSONLStore(tmp_path / "replay.jsonl")
    audit_dp01_filtering(_replay_request(), store)
    replayed = store.replay("v1", _audit_dp01_filtering)
    assert replayed.audit_id == "v1"
    assert replayed.result == store.get("v1").result


def test_replay_skips_comparison_for_child_attempt(tmp_path):
    from bioaudit.api import audit_dp01_filtering
    from bioaudit.dp01_adapter import _audit_dp01_filtering
    base = _replay_request()
    store = DP01JSONLStore(tmp_path / "child-replay.jsonl")
    audit_dp01_filtering(base, store)
    child = {**base, "audit_id": "child", "attempt": {"parent_audit_id": "v1", "change_kind": "evidence_supplement", "change_reason": "x"}}
    audit_dp01_filtering(child, store)
    # Child-attempt records store a pending submission form; replay must not
    # compare it against a fresh final judgment, which would always mismatch.
    replayed = store.replay("child", _audit_dp01_filtering)
    assert replayed.audit_id == "child"
    assert replayed.parent_audit_id == "v1"


def test_replay_missing_audit_id_raises_key_error(tmp_path):
    store = DP01JSONLStore(tmp_path / "empty.jsonl")
    from bioaudit.dp01_adapter import _audit_dp01_filtering
    with pytest.raises(KeyError, match="v1"):
        store.replay("v1", _audit_dp01_filtering)


def test_replay_version_mismatch_fails_closed(tmp_path):
    from bioaudit.api import audit_dp01_filtering
    from bioaudit.dp01_adapter import _audit_dp01_filtering
    store = DP01JSONLStore(tmp_path / "mismatch.jsonl")
    audit_dp01_filtering(_replay_request(), store)
    path = store.path
    line = path.read_text(encoding="utf-8").splitlines()[0]
    import json as _json
    payload = _json.loads(line)
    payload["version_snapshot"]["engine_version"] = "old-engine"
    path.write_text(_json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        store.replay("v1", _audit_dp01_filtering)


def test_legacy_record_without_snapshot_is_rejected(tmp_path):
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"audit_id":"legacy","input_snapshot":{},"result":{}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        DP01JSONLStore(path).get("legacy")


def test_snapshot_shape_is_strict():
    with pytest.raises(ValueError, match="shape"):
        assert_snapshot_compatible({"engine_version": "engine"}, SnapshotTriple("rules", "ontology", "engine"))