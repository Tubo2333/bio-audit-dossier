"""C5 可观测性测试（窗口 C 验收项 15/16）。

- 15. 事件写入失败显式告警：EventStore.append 失败 → EventWriteError（不再静默
      丢写，F11）；run_audit 显式告警进 state/report；health() 可见
- 16. 引擎 trace：session 级审计过程日志（审计者也可审计）——run_audit 全程
      事件 + audit_decision(session_id=...) 单决策事件 + trace 重放/摘要
- 补充：H12 日志轮转（容量上限 + 分片 + 重放不丢事件）
"""

import json

import pytest

from bioaudit.api.audit import audit_decision, run_audit
from bioaudit.capture.engine_trace import session_summary, trace_session
from bioaudit.paths import TRAJECTORIES_LEGACY_DIR
from bioaudit.storage.event_store import (
    AuditEvent,
    EventStore,
    EventWriteError,
)

DEG_CORRECT = json.loads(
    (TRAJECTORIES_LEGACY_DIR / "deg_correct.json").read_text(encoding="utf-8")
)


def _store(tmp_path, monkeypatch, session="sess-e"):
    monkeypatch.setenv("BIOAUDIT_LOG_DIR", str(tmp_path))
    return EventStore(), session


# ── 15. 写失败显式告警（F11）──


def test_append_failure_raises_and_counts(tmp_path, monkeypatch):
    store, session = _store(tmp_path, monkeypatch)
    store.start_session(session)
    real_open = open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith(f"{session}.jsonl"):
            raise OSError("disk full (simulated)")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    with pytest.raises(EventWriteError) as ei:
        store.append(AuditEvent(event_type="decision_scored", node="evaluate"))
    assert "disk full" in str(ei.value)
    assert store.write_failures == 1
    assert "disk full" in store.last_write_error
    assert store.health()["ok"] is False


def test_run_audit_event_write_failure_is_explicit_warning(tmp_path, monkeypatch):
    """管道不崩但告警显式可见：state.event_store_warnings + report。"""
    store, session = _store(tmp_path, monkeypatch)
    store.start_session(session)
    real_open = open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith(f"{session}.jsonl"):
            raise OSError("simulated disk full")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    result = run_audit(DEG_CORRECT, act="deg", session_id=session)
    assert result["error"] is None  # 管道主流程不受影响
    assert result["trajectory_score"] == 85.0  # 评分照常
    assert result["event_store_warnings"], "必须显式告警（不再静默丢写）"
    assert "事件写入失败" in result["event_store_warnings"][0]
    assert result["report"]["event_store_warnings"]


def test_audit_decision_event_write_failure_flagged_in_explanation(tmp_path, monkeypatch):
    store, session = _store(tmp_path, monkeypatch)
    store.start_session(session)
    real_open = open

    def failing_open(file, *args, **kwargs):
        if str(file).endswith(f"{session}.jsonl"):
            raise OSError("simulated disk full")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    score = audit_decision(
        {"step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                     "design": "simple_two_group", "n_replicates": 6}},
        paradigm="deg", session_id=session,
    )
    assert score["level"] == 3  # 评分不受影响
    assert "event_store_warning" in score["explanation"]  # 显式告警


# ── 16. 引擎 trace（审计者也可审计）──


def test_run_audit_engine_trace_session(tmp_path, monkeypatch):
    _, session = _store(tmp_path, monkeypatch)
    run_audit(DEG_CORRECT, act="deg", session_id=session)
    events = trace_session(session)
    event_types = {e.event_type for e in events}
    # 7 步管道事件齐全（审计者也可审计）
    assert {
        "parse_complete", "rule_matched", "decision_scored",
        "aggregation_complete", "propagation_traced", "report_generated",
    } <= event_types
    summary = session_summary(events)
    assert summary["n_events"] == len(events)
    assert summary["nodes"]["evaluate"] >= 1
    assert summary["window"]["first"] and summary["window"]["last"]


def test_audit_decision_trace_with_session(tmp_path, monkeypatch):
    _, session = _store(tmp_path, monkeypatch)
    audit_decision(
        {"step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                     "design": "simple_two_group", "n_replicates": 6}},
        paradigm="deg", session_id=session,
    )
    events = trace_session(session)
    scored = [e for e in events if e.event_type == "decision_scored"]
    assert len(scored) == 1
    assert scored[0].payload["paradigm"] == "deg"
    assert scored[0].payload["level"] == 3


def test_audit_decision_without_session_no_events(tmp_path, monkeypatch):
    """不传 session_id → 不落盘（保持无副作用）。"""
    _, session = _store(tmp_path, monkeypatch)
    audit_decision(
        {"step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                     "design": "simple_two_group", "n_replicates": 6}},
        paradigm="deg",
    )
    assert trace_session(session) == []


def test_trace_empty_session(tmp_path, monkeypatch):
    _, session = _store(tmp_path, monkeypatch)
    assert trace_session(session) == []
    assert session_summary([])["n_events"] == 0


# ── H12 日志轮转 ──


def test_event_log_rotation_within_retention_no_loss(tmp_path, monkeypatch):
    """轮转分片保留窗口内事件不丢、顺序保持（H12）。"""
    monkeypatch.setenv("BIOAUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("BIOAUDIT_EVENT_ROTATE_BYTES", "1000")  # 每 ~5 事件轮转一次
    store = EventStore()
    store.start_session("rot-sess")
    n = 12
    for i in range(n):
        store.append(AuditEvent(event_type="decision_scored", node="evaluate",
                                payload={"i": i}))
    parts = sorted(tmp_path.glob("rot-sess*.jsonl"))
    assert len(parts) >= 2, "超过容量应轮转分片"
    replayed = store.replay("rot-sess")
    assert len(replayed) == n, "保留窗口内重放不丢事件"
    assert [e.payload["i"] for e in replayed] == list(range(n))  # 顺序保持（最老→最新）
    assert store.health()["ok"] is True


def test_event_log_rotation_retention_cap(tmp_path, monkeypatch):
    """容量上限：超保留上限的最老分片被裁剪，保留窗口内事件完整且有序。"""
    monkeypatch.setenv("BIOAUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("BIOAUDIT_EVENT_ROTATE_BYTES", "100")  # 每次追加即轮转
    monkeypatch.setenv("BIOAUDIT_EVENT_MAX_FILES", "3")
    store = EventStore()
    store.start_session("rot-cap")
    n = 30
    for i in range(n):
        store.append(AuditEvent(event_type="decision_scored", node="evaluate",
                                payload={"i": i}))
    replayed = store.replay("rot-cap")
    assert replayed, "保留窗口内事件必须可重放"
    payloads = [e.payload["i"] for e in replayed]
    assert payloads == sorted(payloads), "重放顺序必须单调（最老→最新）"
    assert payloads[-1] == n - 1, "最新事件不丢"
    # 裁剪后总量 ≤ 保留上限 × 分片数（3 片 + 当前片）
    assert len(replayed) <= 4 * 2  # 每片至多 2 事件（100B 阈值）的宽松上界
