"""C4 MCP server 测试（窗口 C 验收项 12/13/14）。

- 12. 工具 = audit_decision / audit_trajectory / report（paradigm 必填 +
      错误码复用 errors.py）
- 13. mcp/ 目录可独立启动（python -m mcp.server + --selfcheck）；契约文档
      docs/mcp-contract.md（单独检查文件存在）
- 14. 端到端：MCP 调 audit_decision → 引擎 → DecisionScore（含证据/替代方案）
"""

import json
import subprocess
import sys
from pathlib import Path

# mcp 版本兼容（窗口 G 实测：mcp>=1.x 将常量移至 mcp.types 并更名；
# 旧版从 mcp.server 导出）——优先新路径，回退旧路径
try:
    from mcp.types import INVALID_PARAMS as JSONRPC_INVALID_PARAMS  # mcp >= 1.x
except ImportError:  # pragma: no cover - 旧版 mcp
    from mcp.server import (  # type: ignore[no-redef]
        JSONRPC_INVALID_PARAMS,
    )

from mcp.server import (
    JSONRPC_METHOD_NOT_FOUND,
    MCP_PROTOCOL_VERSION,
    McpServer,
    list_tools,
    selfcheck,
)

VALID_DECISION = {
    "step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
    "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                "design": "simple_two_group", "n_replicates": 6},
}


def _call(server, name, arguments, request_id=1):
    return json.loads(server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })))


# ── 协议基础 ──


def test_initialize_handshake():
    server = McpServer()
    resp = json.loads(server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": MCP_PROTOCOL_VERSION},
    })))
    assert resp["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "bio-audit-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_notifications_no_response():
    server = McpServer()
    assert server.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })) is None


def test_unknown_method():
    server = McpServer()
    resp = json.loads(server.handle_line(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "frobnicate",
    })))
    assert resp["error"]["code"] == JSONRPC_METHOD_NOT_FOUND


def test_parse_error_and_invalid_request():
    server = McpServer()
    assert json.loads(server.handle_line("not json"))["error"]["code"] == -32700
    assert json.loads(server.handle_line(json.dumps({
        "id": 1, "method": "ping", "params": {}})))["error"]["code"] == -32600


# ── 12. 工具清单：paradigm 必填 + 三工具 ──


def test_tools_list_three_tools_paradigm_required():
    tools = {t["name"]: t for t in list_tools()}
    assert set(tools) == {"audit_decision", "audit_trajectory", "report"}
    ad = tools["audit_decision"]["inputSchema"]
    assert ad["required"] == ["decision", "paradigm"]  # B2：paradigm 必填
    assert ad["properties"]["paradigm"]["enum"] == ["deg", "pan", "scrna"]
    assert tools["report"]["inputSchema"]["required"] == ["session_id"]


# ── 14. 端到端：audit_decision → 引擎 → DecisionScore ──


def test_mcp_audit_decision_end_to_end():
    server = McpServer()
    resp = _call(server, "audit_decision", {
        "decision": VALID_DECISION, "paradigm": "deg",
    })
    assert "error" not in resp, resp.get("error")
    payload = json.loads(resp["result"]["content"][0]["text"])
    score = payload["score"]
    assert score["level"] == 3  # 引擎真实评分
    assert "M1.1-DEG-001" in score["matched_rules"]
    assert score["evidence_citations"]  # 含证据
    assert "alternatives" in score  # 含替代方案


def test_mcp_audit_decision_paradigm_required_and_error_codes():
    server = McpServer()
    # 缺 paradigm → bad-request 映射到 invalid params
    resp = _call(server, "audit_decision", {"decision": VALID_DECISION})
    assert resp["error"]["code"] == JSONRPC_INVALID_PARAMS
    assert resp["error"]["data"]["audit_code"] == "bad-request"
    # 未知范式 → paradigm-not-found（错误码复用）
    resp = _call(server, "audit_decision", {
        "decision": VALID_DECISION, "paradigm": "banana",
    })
    assert resp["error"]["code"] == JSONRPC_INVALID_PARAMS
    assert resp["error"]["data"]["audit_code"] == "paradigm-not-found"
    # 决策字段非法 → validation-error
    resp = _call(server, "audit_decision", {
        "decision": {"choice": "DESeq2"}, "paradigm": "deg",
    })
    assert resp["error"]["data"]["audit_code"] == "validation-error"


def test_mcp_audit_decision_with_session_m1_channel(tmp_path, monkeypatch):
    """带 session_id → M1 通道：白名单拒绝显式报错；通过则 provisional verdict。"""
    monkeypatch.setenv("BIOAUDIT_VERDICT_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("BIOAUDIT_WAL_DIR", str(tmp_path / "w"))
    server = McpServer()
    resp = _call(server, "audit_decision", {
        "decision": VALID_DECISION, "paradigm": "deg", "session_id": "mcp-sess",
    })
    assert resp["error"]["data"]["audit_code"] == "validation-error"  # 白名单拒绝
    # 白名单放行后通过（env 白名单）
    monkeypatch.setenv("BIOAUDIT_SESSION_WHITELIST", "mcp-sess")
    resp = _call(server, "audit_decision", {
        "decision": VALID_DECISION, "paradigm": "deg", "session_id": "mcp-sess",
    })
    assert "error" not in resp, resp.get("error")
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "provisional"
    assert payload["verdict_id"]


# ── audit_trajectory / report 工具 ──


def test_mcp_audit_trajectory():
    server = McpServer()
    resp = _call(server, "audit_trajectory", {
        "trajectory": [VALID_DECISION], "act": "deg",
    })
    assert "error" not in resp, resp.get("error")
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["trajectory_score"] == 85.0
    assert payload["verdict"] == "pass"
    assert payload["report"]["ruleset_version"]  # 三元组快照
    # 非法 act → paradigm-not-found
    resp = _call(server, "audit_trajectory", {
        "trajectory": [VALID_DECISION], "act": "nope",
    })
    assert resp["error"]["data"]["audit_code"] == "paradigm-not-found"


def test_mcp_report_final_only(tmp_path, monkeypatch):
    """report 工具：只消费 final verdict（B4）+ 事件 + WAL 恢复。"""
    monkeypatch.setenv("BIOAUDIT_VERDICT_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("BIOAUDIT_WAL_DIR", str(tmp_path / "w"))
    monkeypatch.setenv("BIOAUDIT_LOG_DIR", str(tmp_path / "events"))
    from bioaudit.capture.verdict import VerdictStatus, VerdictStore

    store = VerdictStore(tmp_path / "v")
    store.create("rep-sess", "s1", "qc_filtering", "hard_threshold", "scrna",
                 "M1声明", score_snapshot={"level": 1},
                 status=VerdictStatus.FINAL)
    store.create("rep-sess", "s2", "dim_reduction", "PCA_elbow_selection", "scrna",
                 "M1声明", status=VerdictStatus.REVOKED)

    server = McpServer()
    resp = _call(server, "report", {"session_id": "rep-sess"})
    assert "error" not in resp, resp.get("error")
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["n_final"] == 1
    assert payload["n_revoked"] == 1
    assert [v["decision_type"] for v in payload["verdicts"]["final"]] == ["qc_filtering"]
    # 缺 session_id → bad-request
    resp = _call(server, "report", {})
    assert resp["error"]["data"]["audit_code"] == "bad-request"


# ── 13. 独立启动 + 契约文档 ──


def test_mcp_selfcheck():
    assert selfcheck() == 0


def test_mcp_module_runs_selfcheck_via_python_m():
    """mcp/ 目录可独立启动：python -m mcp.server --selfcheck。"""
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "mcp.server", "--selfcheck"],
        capture_output=True, text=True, cwd=repo_root, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PASS" in proc.stdout


def test_mcp_contract_document_exists():
    doc = Path(__file__).resolve().parent.parent / "docs" / "mcp-contract.md"
    assert doc.exists(), "C4 验收项 13：MCP 契约文档 docs/mcp-contract.md"
    text = doc.read_text(encoding="utf-8")
    for tool in ("audit_decision", "audit_trajectory", "report"):
        assert tool in text
    assert "paradigm" in text
