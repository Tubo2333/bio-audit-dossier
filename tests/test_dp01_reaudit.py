"""DP-01 W5B-2 explicit child re-audit contract tests."""
from pathlib import Path
import json
import sys
import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore

# 证据的观测时点必须显式声明。
#
# 它是"这条证据什么时候观测到的"这一事实。不声明时，系统按策略落到**写入时刻**，
# 于是同一份请求跑两次会得到**不同的记录**——而下面的确定性判据
# （test_reaudit_is_append_only_independent_and_bounded_diff_deterministic 里
# `serialize(record) == serialize(record2)`）拿两个独立 store 各跑一次，就会因为
# **跨过一次秒边界**而失败。实测约 12 轮 1 次，单跑常过，所以长期没被发现。
#
# 这与 tests/test_dp_mvp_journey.py 那个偶发失败同一病根（那里也是给 harness 钉住
# 观测时点与渲染时刻）。写法沿用 tests/test_dp_evidence_qualification.py 的模块级常量。
_OBSERVED_AT = "2026-09-12T12:00:00+00:00"

BASE = {
    "audit_id": "parent-1",
    "analysis_context": {"project_id":"p","analysis_id":"a","comparison":"case_vs_control","data_type":"bulk_rnaseq","audit_scope":"filtering","intended_use":"scientific_analysis", "exclusions": "none — no explicit exclusions declared"},
    "decision_declaration": {"method":"low_count_filter","threshold":10,"sample_rule":"at_least_two_samples","unit":"gene","source":"declared"},
    "integrity_metadata": {**{domain: {"state": "confirmed"} for domain in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")}, "material_ids": ["material-1"], "pending_targets": ["filtering-decision"]},
    "evidence_observations": [{"source_type":"declared","verification":"unverified","value":"low_count_filter","observed_at":_OBSERVED_AT},{"source_type":"observed","verification":"confirmed","value":"low_count_filter","observed_at":_OBSERVED_AT}],
}

def child_request():
    return {**BASE, "audit_id":"child-1", "attempt":{"parent_audit_id":"parent-1","change_kind":"decision_correction","change_reason":"correct threshold"}, "decision_declaration":{**BASE["decision_declaration"],"threshold":5}}

def test_explicit_reaudit_targets_child_and_reuses_audit_decision(monkeypatch, tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(BASE, store); audit_dp01_filtering(child_request(), store)
    called = []
    import bioaudit.dp01_adapter as adapter
    original = adapter.audit_decision
    monkeypatch.setattr(adapter, "audit_decision", lambda *a, **k: (called.append((a,k)) or original(*a, **k)))
    req = {"audit_id":"reaudit-1", "re_audit":{"operation":"re_audit","target_audit_id":"child-1"}, "decision_declaration":{**BASE["decision_declaration"],"threshold":20}, "evidence_observations":BASE["evidence_observations"] + [{"source_type":"observed","verification":"confirmed","value":"log-ref"}]}
    result = audit_dp01_filtering(req, store)
    record = store.get("reaudit-1")
    assert called
    assert record.parent_audit_id == "child-1"
    assert record.operation == "re_audit"
    assert record.input_snapshot["decision_declaration"]["threshold"] == 20
    assert record.input_snapshot["analysis_context"] == store.get("child-1").input_snapshot["analysis_context"]
    assert record.result == result
    assert record.audit_id not in {"parent-1", "child-1"}


def test_reaudit_records_source_version_snapshot(tmp_path):
    import json as _json
    store = DP01JSONLStore(tmp_path / "m3.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-m3", "attempt": {"parent_audit_id": "parent-1", "change_kind": "decision_correction", "change_reason": "x"}}
    audit_dp01_filtering(child, store)
    source = store.get("child-m3").version_snapshot
    request = {"audit_id": "reaudit-m3", "re_audit": {"operation": "re_audit", "target_audit_id": "child-m3"}}
    audit_dp01_filtering(request, store)
    payload = _json.loads(store.serialize(store.get("reaudit-m3")))
    assert "source_version_snapshot" in payload
    assert dict(payload["source_version_snapshot"]) == dict(source)
    assert dict(store.get("reaudit-m3").source_version_snapshot) == dict(source)


def test_child_and_reaudit_record_submitter_and_affected_evidence(tmp_path):
    import json as _json
    store = DP01JSONLStore(tmp_path / "l2.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-l2", "attempt": {"parent_audit_id": "parent-1", "change_kind": "evidence_supplement", "change_reason": "add data", "submitter": "alice", "affected_evidence": ["obs-1", "obs-2"]}}
    audit_dp01_filtering(child, store)
    child_payload = _json.loads(store.serialize(store.get("child-l2")))
    assert child_payload["change"]["submitter"] == "alice"
    assert child_payload["change"]["affected_evidence"] == ["obs-1", "obs-2"]
    req = {"audit_id": "reaudit-l2", "re_audit": {"operation": "re_audit", "target_audit_id": "child-l2", "submitter": "bob", "affected_evidence": ["obs-2"]}}
    audit_dp01_filtering(req, store)
    reaudit_payload = _json.loads(store.serialize(store.get("reaudit-l2")))
    assert reaudit_payload["change"]["submitter"] == "bob"
    assert reaudit_payload["change"]["affected_evidence"] == ["obs-2"]


def test_reaudit_is_append_only_independent_and_bounded_diff_deterministic(tmp_path):
    path = tmp_path / "a.jsonl"; store = DP01JSONLStore(path)
    audit_dp01_filtering(BASE, store); audit_dp01_filtering(child_request(), store)
    before_parent = store.serialize(store.get("parent-1")); before_child = store.serialize(store.get("child-1"))
    req = {"audit_id":"reaudit-1", "re_audit":{"operation":"re_audit","target_audit_id":"child-1"}, "decision_declaration":{**BASE["decision_declaration"],"threshold":20}, "evidence_observations":BASE["evidence_observations"]}
    audit_dp01_filtering(req, store)
    assert path.read_text(encoding="utf-8").splitlines()[0] + "\n" == before_parent
    assert path.read_text(encoding="utf-8").splitlines()[1] + "\n" == before_child
    record = store.get("reaudit-1")
    assert record.input_snapshot is not store.get("child-1").input_snapshot
    assert record.change_summary.changes
    assert [c.path for c in record.change_summary.changes] == sorted(c.path for c in record.change_summary.changes)
    # Independent determinism oracle: rebuild the same re-audit request against an
    # independently reconstructed store and compare canonical change-summary bytes.
    store2 = DP01JSONLStore(tmp_path / "b.jsonl")
    audit_dp01_filtering(BASE, store2); audit_dp01_filtering(child_request(), store2)
    audit_dp01_filtering(req, store2)
    record2 = store2.get("reaudit-1")
    assert json.dumps(record.change_summary.to_dict(), sort_keys=True, separators=(",", ":")) == json.dumps(record2.change_summary.to_dict(), sort_keys=True, separators=(",", ":"))
    assert store.serialize(record) == store2.serialize(record2)
    assert not hasattr(record.result, "score") and not hasattr(record.result, "authority")
    assert not any(any(word in c.path for word in ("acceptance","release","lane","authority","score")) for c in record.change_summary.changes)


def test_reaudit_merge_is_deep_copied_and_validated(tmp_path, monkeypatch):
    import bioaudit.dp01_adapter as adapter
    store = DP01JSONLStore(tmp_path / "merge.jsonl")
    audit_dp01_filtering(BASE, store)
    child = {**BASE, "audit_id": "child-merge", "attempt": {"parent_audit_id": "parent-1", "change_kind": "decision_correction", "change_reason": "x"}}
    audit_dp01_filtering(child, store)
    evidence = [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}]
    req = {"audit_id": "reaudit-merge", "re_audit": {"operation": "re_audit", "target_audit_id": "child-merge"}, "evidence_observations": evidence, "integrity_metadata": BASE["integrity_metadata"]}
    original = adapter._reaudit
    seen = []

    def wrapped(request, store):
        seen.append(adapter._merge_snapshot(store.get(request["re_audit"]["target_audit_id"]).input_snapshot, request))
        return original(request, store)

    monkeypatch.setattr(adapter, "_reaudit", wrapped)
    audit_dp01_filtering(req, store)
    assert len(seen) == 1
    merged = seen[0]
    assert merged["evidence_observations"][0] == {"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}
    evidence[0]["value"] = "mutated"
    assert merged["evidence_observations"][0]["value"] == "low_count_filter"


def test_empty_summary_and_fail_closed_targets(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(BASE, store); audit_dp01_filtering(child_request(), store)
    same = {"audit_id":"r1", "re_audit":{"operation":"re_audit","target_audit_id":"child-1"}}
    audit_dp01_filtering(same, store)
    assert store.get("r1").change_summary.changes == ()
    with pytest.raises(KeyError, match="target"):
        audit_dp01_filtering({"audit_id":"missing-target","re_audit":{"operation":"re_audit","target_audit_id":"nope"}}, store)
    with pytest.raises(ValueError, match="duplicate"):
        audit_dp01_filtering(same | {"audit_id":"r1"}, store)
    with pytest.raises(ValueError, match="re.audit|operation"):
        audit_dp01_filtering({"audit_id":"bad","re_audit":{"operation":"wrong","target_audit_id":"child-1"}}, store)


def test_reaudit_requires_explicit_operation_and_child_target(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(BASE, store)
    with pytest.raises(ValueError, match="re.audit"):
        audit_dp01_filtering({"audit_id":"implicit","target_audit_id":"parent-1"}, store)
    with pytest.raises(ValueError, match="child"):
        audit_dp01_filtering({"audit_id":"parent-target","re_audit":{"operation":"re_audit","target_audit_id":"parent-1"}}, store)


def test_serialization_has_no_approval_semantics(tmp_path):
    store = DP01JSONLStore(tmp_path / "a.jsonl")
    audit_dp01_filtering(BASE, store); audit_dp01_filtering(child_request(), store)
    audit_dp01_filtering({"audit_id":"r","re_audit":{"operation":"re_audit","target_audit_id":"child-1"}}, store)
    raw = store.serialize(store.get("r"))
    payload = json.loads(raw)
    for forbidden in ("acceptance", "release", "lane", "authority", "score", "aggregate"):
        assert forbidden not in payload["result"]
        assert forbidden not in payload.get("change_summary", {})
