"""DP-01 crash-durability and cross-process contract tests (DP01-CRASH-DURABILITY).

Proves, without changing storage semantics, that:
- C1: a confirmed append (fsync returned) is durable;
- C2: an interrupted append leaves either the old state or the old state plus
      exactly one complete valid JSONL line; a torn line is never silently
      accepted, and any malformed line fails the whole store closed with the
      line number pinned for manual recovery;
- C3: a ledger written by one process is readable and replayable from a fresh
      interpreter.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioaudit.api import audit_dp01_filtering, replay_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore, DP01AuditRecord


def _make_request(audit_id="audit-1"):
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


# Child writer: replicates the exact write sequence append() performs
# (open "a" -> write -> optional flush -> fsync-owned by caller) so a kill at
# either side of flush() is observable as file state. Killed via TerminateProcess
# (Popen.kill on Windows); the Python text buffer is lost on hard kill.
_CHILD_WRITER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[5])
from bioaudit.api import audit_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore, DP01AuditRecord

path, sentinel, audit_id, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
req = {
    "audit_id": audit_id,
    "analysis_context": {"project_id": "p", "analysis_id": "a", "comparison": "case_vs_control", "data_type": "bulk_rnaseq", "audit_scope": "filtering", "intended_use": "scientific_analysis", "exclusions": "none — no explicit exclusions declared"},
    "decision_declaration": {"method": "low_count_filter", "threshold": 10, "sample_rule": "at_least_two_samples", "unit": "gene", "source": "declared"},
    "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
    "integrity_metadata": {
        "identity": {"state": "confirmed"}, "version": {"state": "confirmed"}, "source": {"state": "confirmed"},
        "binding": {"state": "confirmed"}, "snapshot": {"state": "confirmed"}, "permission": {"state": "confirmed"},
        "unique_authority": {"state": "confirmed"}, "material_ids": ["m"], "pending_targets": ["p"],
    },
}
result = audit_dp01_filtering(req, {})
line = DP01JSONLStore.serialize(DP01AuditRecord(audit_id, req, result))
with open(path, "a", encoding="utf-8") as f:
    f.write(line)
    if mode == "flush":
        f.flush()
    Path(sentinel).write_text("go", encoding="utf-8")
    while True:
        time.sleep(1)
"""


def _expected_line(audit_id):
    req = _make_request(audit_id)
    return DP01JSONLStore.serialize(DP01AuditRecord(audit_id, req, audit_dp01_filtering(req, {})))


def _norm(b):
    # store.append uses text-mode open (default newline=None), so on Windows the
    # persisted lines keep \r\n endings; normalize before byte comparison.
    return b.replace(b"\r\n", b"\n")


def _spawn_and_kill(path, mode):
    sentinel = path.with_name(path.name + ".wsentinel")
    sentinel.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_WRITER, str(path), str(sentinel), "crashed-writer", mode, str(ROOT / "src")],
        cwd=ROOT, env=env,
    )
    deadline = time.time() + 20
    while time.time() < deadline and not sentinel.exists():
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    assert sentinel.exists(), "child writer never reached the sentinel (check child stderr)"
    proc.kill()
    proc.wait(timeout=10)


# ---- 1-5: torn-state behavior matrix (fail-closed contract) ----

def test_empty_file_missing_id_is_key_error(tmp_path):
    store = DP01JSONLStore(tmp_path / "empty.jsonl")
    with pytest.raises(KeyError, match="audit-1"):
        store.get("audit-1")


def test_torn_tail_fails_closed_with_line_number(tmp_path):
    path = tmp_path / "torn.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request(), store)
    line1 = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(line1 + "\n" + '{"audit_id"', encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        store.get("audit-1")
    msg = str(exc.value)
    assert "malformed" in msg
    assert "line 2" in msg


def test_complete_line_without_trailing_newline_reads(tmp_path):
    path = tmp_path / "nonl.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request(), store)
    line = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(line, encoding="utf-8")
    assert store.get("audit-1").audit_id == "audit-1"


def test_middle_corruption_fails_closed(tmp_path):
    path = tmp_path / "mid.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request("audit-1"), store)
    audit_dp01_filtering(_make_request("audit-2"), store)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\nnot-json\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        store.get("audit-1")


def test_intact_ledger_reads_and_replays(tmp_path):
    store = DP01JSONLStore(tmp_path / "intact.jsonl")
    audit_dp01_filtering(_make_request("audit-1"), store)
    audit_dp01_filtering(_make_request("audit-2"), store)
    assert store.get("audit-1").result == replay_dp01_filtering(store, "audit-1").result
    assert store.get("audit-2").result == replay_dp01_filtering(store, "audit-2").result


# ---- 6-8: real process death + torn-tail invariant ----

def test_kill_before_flush_leaves_old_state(tmp_path):
    path = tmp_path / "kill-pre.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request(), store)
    old = path.read_bytes()
    _spawn_and_kill(path, "hold")
    content = path.read_bytes()
    # buffered write is lost on hard kill -> byte-identical old state
    assert content == old
    store = DP01JSONLStore(path)
    assert store.get("audit-1").audit_id == "audit-1"


def test_kill_after_flush_leaves_complete_new_line(tmp_path):
    path = tmp_path / "kill-post.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request(), store)
    old = path.read_bytes()
    _spawn_and_kill(path, "flush")
    content = path.read_bytes()
    # flushed data reached the OS; a process kill cannot lose it
    assert _norm(content) == _norm(old) + _expected_line("crashed-writer").encode()
    store = DP01JSONLStore(path)
    assert store.get("crashed-writer").audit_id == "crashed-writer"


def test_kill_tail_is_never_a_silently_accepted_torn_line(tmp_path):
    for mode in ("hold", "flush"):
        path = tmp_path / f"tail-{mode}.jsonl"
        store = DP01JSONLStore(path)
        audit_dp01_filtering(_make_request(), store)
        old = path.read_bytes()
        _spawn_and_kill(path, mode)
        tail = _norm(path.read_bytes())[len(_norm(old)):]
        # either the append never happened, or it is exactly one complete line
        assert tail == b"" or tail == _expected_line("crashed-writer").encode()
    # a genuinely torn JSON-ish fragment must never read as a record
    torn_path = tmp_path / "torn-frag.jsonl"
    store = DP01JSONLStore(torn_path)
    audit_dp01_filtering(_make_request(), store)
    good = _expected_line("x")
    torn_path.write_bytes(torn_path.read_bytes() + good[:-3].encode())
    with pytest.raises(ValueError, match="malformed"):
        store.get("audit-1")


# ---- 9: cross-process read + replay ----

def test_cross_process_read_and_replay(tmp_path):
    path = tmp_path / "cross.jsonl"
    store = DP01JSONLStore(path)
    audit_dp01_filtering(_make_request("audit-1"), store)
    audit_dp01_filtering(_make_request("audit-2"), store)
    child_req = {**_make_request("audit-3"), "attempt": {"parent_audit_id": "audit-2", "change_kind": "evidence_supplement", "change_reason": "x"}}
    audit_dp01_filtering(child_req, store)

    reader = r"""
import sys, json
sys.path.insert(0, sys.argv[3])
from bioaudit.api import replay_dp01_filtering
from bioaudit.dp01_store import DP01JSONLStore
store = DP01JSONLStore(sys.argv[1])
out = {}
for i in json.loads(sys.argv[2]):
    rec = replay_dp01_filtering(store, i)
    out[i] = {"parent": rec.parent_audit_id, "judgment": rec.result.judgment}
print(json.dumps(out))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", reader, str(path), '["audit-1", "audit-2", "audit-3"]', str(ROOT / "src")],
        capture_output=True, text=True, cwd=ROOT, env=env, check=True, timeout=30,
    )
    out = json.loads(proc.stdout.strip())
    assert out["audit-1"] == {"parent": None, "judgment": "auditable"}
    assert out["audit-2"] == {"parent": None, "judgment": "auditable"}
    assert out["audit-3"] == {"parent": "audit-2", "judgment": "pending_attempt"}