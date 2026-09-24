"""MCP server（阶段 2 落地，窗口 C；refactor-plan-v1.1 B1/B3；设计 §八）。

工具契约（C4 验收项 12，paradigm 必填 + 错误码复用 errors.py）：
- ``audit_decision``：单决策审计（decision + **paradigm 必填**，B2 消歧）；
  带 session_id 时走 M1 上报通道（白名单 + verdict provisional + 幂等，B3/B4）；
- ``audit_trajectory``：轨迹审计（run_audit 全量结果，含 report 快照三元组）；
- ``report``：会话审计报告——**只消费 final verdict**（B4）+ 引擎事件 + WAL。

实现：mcp/server.py（最小 stdio JSON-RPC 2.0，MCP 协议 2024-11-05，零额外依赖）。
启动：仓库根目录 ``python -m mcp.server``；自检：``python -m mcp.server --selfcheck``。
契约文档：docs/mcp-contract.md。
"""

MCP_VERSION = "1.0.0"

__all__ = ["MCP_VERSION"]
