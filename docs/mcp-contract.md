# Bio-Audit MCP 契约 v1（窗口 C / C4）

> **日期**：2026-08-15
> **依据**：refactor-plan-v1.1（B1/B2/B3/B4）+ trajectory-capture-design-v1（§八）
> **实现**：`mcp/server.py`（最小 stdio JSON-RPC 2.0，MCP 协议 **2024-11-05**，零额外依赖）
> **启动**：仓库根目录 `python -m mcp.server`；自检 `python -m mcp.server --selfcheck`

---

## 一、部署形态

| 项 | 说明 |
|----|------|
| 传输 | stdio（JSON-RPC 2.0 逐行），MCP 协议版本 `2024-11-05` |
| 启动 | `python -m mcp.server`（mcp/ 目录独立启动；`bioaudit` 需已安装） |
| 自检 | `python -m mcp.server --selfcheck`（进程内协议自检，CI 冒烟用，exit 0/1） |
| 错误码 | 复用 `bioaudit.errors`（`bad-request` / `validation-error` / `paradigm-not-found` / `rule-not-found` / `internal-error`），映射为 JSON-RPC `-32602`（参数类）/ `-32603`（内部） |
| 会话存储 | `BIOAUDIT_LOG_DIR`（引擎事件）/ `BIOAUDIT_VERDICT_DIR`（verdict）/ `BIOAUDIT_WAL_DIR`（WAL），默认 `~/.bioaudit/` |

## 二、工具契约

### 1. `audit_decision` — 单决策审计（M1 主动上报入口）

```jsonc
// 请求
{
  "decision": {                       // Decision schema（A15：extra=forbid）
    "step_id": "s3",                  // 必填
    "decision_type": "deg_method",    // 必填
    "choice": "DESeq2",               // 必填
    "rationale": "...",               // 可选
    "context": {"data_category": "raw_counts", ...},  // 可选
    "tool_call": null, "code_snippet": null            // 可选
  },
  "paradigm": "deg",                  // 必填（B2：deg/pan/scrna 同名异构消歧）
  "session_id": "cv_abc123"           // 可选；提供时走 M1 通道：
}                                     //   白名单校验 + 幂等键 + WAL + verdict provisional
```

```jsonc
// 响应（无 session_id）：{"score": DecisionScore}
// 响应（有 session_id）：
{
  "score": { "step_id": "s3", "level": 3, "numeric_score": 0.85,
             "matched_rules": ["M1.1-DEG-001"], "explanation": "...",
             "evidence_citations": [...], "alternatives": [...],
             "reward_signal": ... },
  "verdict_id": "d5b1c468-30d",
  "status": "provisional",            // B4：provisional → final/revoked
  "idempotency_key": "..."
}
```

错误示例（paradigm 未知 → 错误码复用）：

```jsonc
{
  "jsonrpc": "2.0", "id": 3, "error": {
    "code": -32602,
    "message": "未知范式 'banana'（合法: ['deg', 'pan', 'scrna']）",
    "data": { "audit_code": "paradigm-not-found", "details": {...}, "http_status": 404 }
  }
}
```

### 2. `audit_trajectory` — 轨迹审计（run_audit 全量）

```jsonc
{
  "trajectory": [ /* 决策数组 */ ] | { "version": 2, "decisions": [...], ... },
  "act": "deg",                       // 可选；None = 全量规则（38 唯一）
  "session_id": "audit_abc"           // 可选；引擎事件落盘（C5 trace 可查）
}
```

响应：`trajectory_score / verdict / dimension_scores / n_decisions / critical_issues / report`（report 含三元组快照 engine+ruleset+ontology，C1/P2）。

### 3. `report` — 会话审计报告（**只消费 final**，B4）

```jsonc
{ "session_id": "cv_abc123", "act": "scrna" }   // act 可选
```

响应：

```jsonc
{
  "session_id": "cv_abc123",
  "verdicts": {
    "final":       [ /* VerdictRecord：报告与 reward 只消费 final */ ],
    "revoked":     [ /* 虚报被推翻，分数不得进入报告 */ ],
    "provisional": [ /* 待交叉验证定案 */ ]
  },
  "n_final": 8, "n_revoked": 1, "n_provisional": 2,
  "engine_events": [ {"event_type": "decision_scored", "node": "evaluate", ...} ],
  "wal_recovery": { "n_entries": 12, "completed": [...], "interrupted": [...] }
}
```

## 三、协议握手

```jsonc
// client → server（initialize）
{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{...}}}
// server → client
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2024-11-05",
  "capabilities":{"tools":{"listChanged":false}},
  "serverInfo":{"name":"bio-audit-mcp","version":"1.0.0"}}}
// client → server（通知，无响应）
{"jsonrpc":"2.0","method":"notifications/initialized"}
```

`tools/list` 返回三个工具（`audit_decision` / `audit_trajectory` / `report`），
inputSchema 见 `mcp/server.py:list_tools`（paradigm 枚举 deg/pan/scrna）。

## 四、端到端示例（测试 = 契约守护）

`tests/test_mcp.py`：
- initialize 握手 / tools/list（三工具 + paradigm 必填）/ tools/call
- `audit_decision` → 引擎 → DecisionScore（level 3 + 证据 + 替代方案）
- 错误码：缺 paradigm → `bad-request`；未知 paradigm → `paradigm-not-found`；
  字段非法 → `validation-error`（全部映射 JSON-RPC `-32602`）
- `audit_trajectory` → 轨迹分 + 三元组快照；非法 act → `paradigm-not-found`
- `report` → final-only 视图（revoked 不进 final）
- `python -m mcp.server --selfcheck` 独立启动自检（CI 步骤同款）
