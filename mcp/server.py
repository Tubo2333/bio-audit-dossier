"""MCP server（窗口 C / C4；refactor-plan-v1.1 B1/B3；设计 §八）。

实现：最小 **stdio JSON-RPC 2.0** MCP server（MCP 协议 2024-11-05），
零额外依赖（仅 pydantic/pyyaml 核心依赖）。

工具契约（C4 验收项 12，paradigm 必填 + 错误码复用 errors.py）：
- ``audit_decision``：单决策审计（decision + **paradigm 必填**）；带 session_id
  时走 M1 上报通道（白名单 + verdict provisional + 幂等）；
- ``audit_trajectory``：轨迹审计（run_audit 全量结果，含 report 快照三元组）；
- ``report``：会话审计报告——**只消费 final verdict**（B4）+ 引擎事件 +
  WAL 崩溃恢复摘要。

启动：仓库根目录 ``python -m mcp.server``（mcp/ 独立目录，零 cwd 依赖）；
自检：``python -m mcp.server --selfcheck``（CI 冒烟，不阻塞 stdin）。
契约文档：docs/mcp-contract.md。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional

from bioaudit.errors import ERROR_HTTP_STATUS, BioAuditError, ErrorCode
from bioaudit.models.trajectory import VALID_PARADIGMS

logger = logging.getLogger(__name__)

#: MCP 协议版本（2024-11-05 稳定版）
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "bio-audit-mcp"
SERVER_VERSION = "1.0.0"

#: JSON-RPC 错误码（MCP 约定）
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

#: audit 错误码 → JSON-RPC 错误码
AUDIT_TO_JSONRPC: dict[str, int] = {
    ErrorCode.BAD_REQUEST: JSONRPC_INVALID_PARAMS,
    ErrorCode.VALIDATION_ERROR: JSONRPC_INVALID_PARAMS,
    ErrorCode.PARADIGM_NOT_FOUND: JSONRPC_INVALID_PARAMS,
    ErrorCode.RULE_NOT_FOUND: JSONRPC_INVALID_PARAMS,
    ErrorCode.INTERNAL_ERROR: JSONRPC_INTERNAL_ERROR,
}


def _audit_error_to_jsonrpc(exc: BioAuditError) -> dict:
    """BioAuditError → JSON-RPC error 对象（错误码复用，data 携带 audit 细节）。"""
    return {
        "code": AUDIT_TO_JSONRPC.get(exc.code, JSONRPC_INTERNAL_ERROR),
        "message": exc.message,
        "data": {
            "audit_code": exc.code,
            "details": exc.details,
            "http_status": ERROR_HTTP_STATUS.get(exc.code, 500),
        },
    }


def _rpc_error(request_id: Any, code: int, message: str, data: Optional[dict] = None) -> str:
    return json.dumps({
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": code, "message": message, **({"data": data} if data else {})},
    }, ensure_ascii=False)


def _rpc_result(request_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result},
                      ensure_ascii=False)


class McpServer:
    """MCP server（stdio JSON-RPC 2.0；可独立启动 / 可注入测试）。"""

    def __init__(self, out: Optional[Any] = None):
        #: 输出流（None → sys.stdout；测试注入 StringIO）
        self.out = out

    # ── 协议分发 ──

    def handle_line(self, line: str) -> Optional[str]:
        """处理一行 JSON-RPC 请求，返回响应行（通知无响应 → None）。"""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return _rpc_error(None, JSONRPC_PARSE_ERROR, "Parse error")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc_error(msg.get("id") if isinstance(msg, dict) else None,
                              JSONRPC_INVALID_REQUEST, "Invalid Request")
        method = msg.get("method", "")
        request_id = msg.get("id")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                return self._handle_initialize(request_id, params)
            if method == "notifications/initialized":
                return None
            if method == "ping":
                return _rpc_result(request_id, {})
            if method == "tools/list":
                return _rpc_result(request_id, {"tools": list_tools()})
            if method == "tools/call":
                return self._handle_tools_call(request_id, params)
            return _rpc_error(request_id, JSONRPC_METHOD_NOT_FOUND,
                              f"Method not found: {method}")
        except BioAuditError as exc:
            return _rpc_error(request_id, AUDIT_TO_JSONRPC.get(exc.code, JSONRPC_INTERNAL_ERROR),
                              exc.message, {
                                  "audit_code": exc.code, "details": exc.details,
                              })
        except Exception as exc:  # 不裸抛（B1）
            logger.exception("MCP 内部错误")
            return _rpc_error(request_id, JSONRPC_INTERNAL_ERROR,
                              f"Internal error: {exc}", {"error_type": type(exc).__name__})

    def _handle_initialize(self, request_id: Any, params: dict) -> str:
        client_protocol = (params or {}).get("protocolVersion", "")
        return _rpc_result(request_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "client_protocol_requested": client_protocol,
        })

    def _handle_tools_call(self, request_id: Any, params: dict) -> str:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise BioAuditError(
                ErrorCode.BAD_REQUEST, "tools/call arguments 必须是对象"
            )
        if name == "audit_decision":
            result = call_audit_decision(arguments)
        elif name == "audit_trajectory":
            result = call_audit_trajectory(arguments)
        elif name == "report":
            result = call_report(arguments)
        else:
            raise BioAuditError(
                ErrorCode.BAD_REQUEST,
                f"未知工具 {name!r}（合法: audit_decision/audit_trajectory/report）",
            )
        return _rpc_result(request_id, {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": False,
        })

    # ── stdio 主循环 ──

    def serve_stdio(self) -> int:
        """阻塞读 stdin 逐行处理（MCP stdio transport）。"""
        stream = self.out if self.out is not None else sys.stdout
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            if response is not None:
                stream.write(response + "\n")
                stream.flush()
        return 0


# ── 工具实现（C4 验收项 12/14：paradigm 必填 + 错误码复用 + 端到端）──

def _decision_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "step_id": {"type": "string"},
            "decision_type": {"type": "string"},
            "choice": {"type": "string"},
            "rationale": {"type": "string"},
            "context": {"type": "object"},
            "tool_call": {"type": ["string", "null"]},
            "code_snippet": {"type": ["string", "null"]},
        },
        "required": ["step_id", "decision_type", "choice"],
        "additionalProperties": False,
    }


def list_tools() -> list[dict]:
    """工具清单（tools/list）。"""
    return [
        {
            "name": "audit_decision",
            "description": (
                "单决策审计（M1 主动上报入口）。decision + 必填 paradigm（deg/pan/scrna，"
                "同名异构消歧 B2）；带 session_id 时走 M1 通道"
                "（白名单 + verdict provisional + 幂等）。"
                "返回 DecisionScore（含证据/替代方案）与 verdict 状态位。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "decision": _decision_schema(),
                    "paradigm": {"type": "string", "enum": sorted(VALID_PARADIGMS)},
                    "session_id": {"type": "string", "description": "M1 会话（可选；白名单校验）"},
                },
                "required": ["decision", "paradigm"],
            },
        },
        {
            "name": "audit_trajectory",
            "description": (
                "轨迹审计（run_audit 全量结果：轨迹分数/verdict/维度分/report 快照三元组）。"
                "act 可选（None = 全量规则）；session_id 可选（事件落盘）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "trajectory": {
                        "description": "决策数组或轨迹 v2 对象",
                        "type": ["array", "object"],
                    },
                    "act": {"type": ["string", "null"], "enum": sorted(VALID_PARADIGMS)},
                    "session_id": {"type": "string"},
                },
                "required": ["trajectory"],
            },
        },
        {
            "name": "report",
            "description": (
                "会话审计报告：**只消费 final verdict**（B4）+ 引擎事件 trace + WAL 崩溃恢复。"
                "session_id 必填（须为已采集会话）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "act": {"type": ["string", "null"], "enum": sorted(VALID_PARADIGMS)},
                },
                "required": ["session_id"],
            },
        },
    ]


def call_audit_decision(arguments: dict) -> dict:
    """audit_decision 工具：引擎端到端（验收项 14）+ M1 通道（session 可选）。"""
    if "decision" not in arguments or "paradigm" not in arguments:
        raise BioAuditError(
            ErrorCode.BAD_REQUEST,
            "audit_decision 缺必填参数 decision/paradigm（B2：paradigm 必填）",
            details={"missing": [k for k in ("decision", "paradigm") if k not in arguments]},
        )
    paradigm = arguments["paradigm"]
    session_id = arguments.get("session_id")
    if session_id is not None:
        # M1 通道：白名单 + 幂等 + verdict provisional（B3/B4）
        from bioaudit.capture.m1_reporter import M1Reporter
        from bioaudit.capture.session import SessionWhitelist

        whitelist = SessionWhitelist()
        reporter = M1Reporter(session_id, paradigm, whitelist=whitelist)
        reporter.start()
        report = reporter.report(arguments["decision"])
        if not report.get("ok"):
            # 白名单拒绝 → 显式 validation-error（错误码复用）
            raise BioAuditError(
                ErrorCode.VALIDATION_ERROR,
                str(report.get("error", "M1 上报被拒绝")),
                details={"session_id": session_id},
            )
        return {
            "score": report["score"],
            "verdict_id": report.get("verdict_id"),
            "status": report.get("status"),
            "idempotency_key": report.get("idempotency_key"),
        }
    from bioaudit.api.audit import audit_decision

    score = audit_decision(arguments["decision"], paradigm=paradigm)
    return {"score": score}


def call_audit_trajectory(arguments: dict) -> dict:
    """audit_trajectory 工具：run_audit 全量结果（含 report 三元组快照）。"""
    if "trajectory" not in arguments:
        raise BioAuditError(ErrorCode.BAD_REQUEST, "audit_trajectory 缺必填参数 trajectory")
    from bioaudit.api.audit import run_audit

    result = run_audit(
        arguments["trajectory"],
        act=arguments.get("act"),
        session_id=arguments.get("session_id"),
    )
    if result.get("error"):
        code = result.get("error_code") or ErrorCode.INTERNAL_ERROR
        raise BioAuditError(code, result["error"])
    return {
        "trajectory_score": result["trajectory_score"],
        "verdict": result["eval_verdict"],
        "dimension_scores": result["dimension_scores"],
        "n_decisions": len(result.get("step_scores", [])),
        "critical_issues": result.get("critical_issues", []),
        "report": result.get("report"),
    }


def call_report(arguments: dict) -> dict:
    """report 工具：会话审计报告（final-only verdict + 事件 + WAL 恢复）。"""
    session_id = arguments.get("session_id")
    if not session_id:
        raise BioAuditError(ErrorCode.BAD_REQUEST, "report 缺必填参数 session_id")
    from bioaudit.capture.verdict import VerdictStatus, VerdictStore
    from bioaudit.capture.wal import WAL
    from bioaudit.storage.event_store import EventStore

    store = VerdictStore()
    records = store.get(session_id)
    final = [r.as_dict() for r in records if r.status == VerdictStatus.FINAL]
    revoked = [r.as_dict() for r in records if r.status == VerdictStatus.REVOKED]
    provisional = [r.as_dict() for r in records if r.status == VerdictStatus.PROVISIONAL]

    events = EventStore().replay(session_id)
    wal = WAL()
    try:
        recovery = wal.recovery(session_id)
    except Exception:
        recovery = {}

    return {
        "session_id": session_id,
        "act": arguments.get("act"),
        "verdicts": {
            # B4：报告与 reward 只消费 final；revoked/provisional 单独列出供审计
            "final": final,
            "revoked": revoked,
            "provisional": provisional,
        },
        "n_final": len(final),
        "n_revoked": len(revoked),
        "n_provisional": len(provisional),
        "engine_events": [
            {"event_type": e.event_type, "node": e.node, "timestamp": e.timestamp}
            for e in events
        ],
        "wal_recovery": recovery,
    }


# ── 入口 ──

def main(argv: Optional[list[str]] = None) -> int:
    """``python -m mcp.server`` 独立启动；``--selfcheck`` 自检（CI 冒烟）。"""
    args = list(argv if argv is not None else sys.argv[1:])
    if "--selfcheck" in args:
        return selfcheck()
    server = McpServer()
    return server.serve_stdio()


def selfcheck() -> int:
    """进程内协议自检（CI 冒烟，不阻塞 stdin）。"""
    from io import StringIO

    buf = StringIO()
    server = McpServer(out=buf)
    ok = True
    for line in [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": MCP_PROTOCOL_VERSION}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "audit_decision", "arguments": {
                        "decision": {
                            "step_id": "s1", "decision_type": "deg_method",
                            "choice": "DESeq2",
                            "context": {"data_category": "raw_counts",
                                        "sequencing": "bulk_RNA_seq",
                                        "design": "simple_two_group",
                                        "n_replicates": 6},
                        },
                        "paradigm": "deg",
                    }}}),
    ]:
        response = server.handle_line(line)
        if response is None:
            ok = False
            continue
        parsed = json.loads(response)
        if "error" in parsed:
            ok = False
    # 非法输入 → 错误码（paradigm-not-found 走 JSON-RPC invalid params）
    bad = server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "audit_decision", "arguments": {
            "decision": {"step_id": "s1", "decision_type": "deg_method", "choice": "x"},
            "paradigm": "banana",
        }},
    }))
    err = json.loads(bad)["error"]
    if err["code"] != JSONRPC_INVALID_PARAMS or err["data"]["audit_code"] != "paradigm-not-found":
        ok = False
    print(f"MCP selfcheck: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
