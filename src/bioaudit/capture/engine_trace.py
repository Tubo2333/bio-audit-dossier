"""引擎 trace（窗口 C / C5：审计者也可审计）。

- :func:`trace_session`：重放某 session 的引擎审计过程事件（EventStore 事件流：
  parse_complete → rule_matched → decision_scored → aggregation_complete →
  propagation_traced → report_generated；C5 验收项 16）；
- :func:`session_summary`：结构化摘要（阶段/事件数/时间窗/告警）。

配套：``run_audit`` 全程写事件；``audit_decision(session_id=...)`` 写单决策事件；
``bio-audit trace <session_id>`` 输出审计过程日志。
"""

from __future__ import annotations

from typing import Optional

from bioaudit.storage.event_store import AuditEvent, EventStore

#: 事件 → 管道阶段（trace 视图分组）
STAGE_ORDER: list[tuple[str, str]] = [
    ("input_validation", "输入校验"),
    ("parse", "解析"),
    ("match", "规则匹配"),
    ("evaluate", "评分"),
    ("detect_conflicts", "冲突检测"),
    ("aggregate", "聚合"),
    ("trace", "错误传播"),
    ("report", "报告"),
]


def trace_session(
    session_id: str, log_dir: Optional[str] = None
) -> list[AuditEvent]:
    """重放 session 的引擎审计过程事件（审计者也可审计）。"""
    return EventStore(log_dir).replay(session_id)


def session_summary(events: list[AuditEvent]) -> dict:
    """结构化 trace 摘要：阶段事件计数 / 时间窗 / 事件类型分布。"""
    by_node: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for e in events:
        by_node[e.node] = by_node.get(e.node, 0) + 1
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
    timestamps = sorted(e.timestamp for e in events)
    return {
        "n_events": len(events),
        "nodes": by_node,
        "event_types": by_type,
        "window": {
            "first": timestamps[0] if timestamps else None,
            "last": timestamps[-1] if timestamps else None,
        },
        "stages": [
            {"node": node, "label": label, "n_events": by_node.get(node, 0)}
            for node, label in STAGE_ORDER
        ],
    }


def render_trace(session_id: str, events: list[AuditEvent]) -> str:
    """人类可读的审计过程日志（CLI ``bio-audit trace`` 输出）。"""
    lines = [f"引擎审计过程 trace — session {session_id}（{len(events)} 事件）"]
    for e in events:
        payload = ", ".join(
            f"{k}={v}" for k, v in (e.payload or {}).items()
        )[:120]
        lines.append(
            f"  [{e.timestamp}] {e.node:>16} {e.event_type:<24} {payload}"
        )
    summary = session_summary(events)
    lines.append(
        f"摘要: {summary['n_events']} 事件 / {len(summary['nodes'])} 节点 / "
        f"窗 {summary['window']['first']} → {summary['window']['last']}"
    )
    return "\n".join(lines)


__all__ = ["trace_session", "session_summary", "render_trace", "STAGE_ORDER"]
