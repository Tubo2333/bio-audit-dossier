# 设计文档（仓库内规范与设计定稿）

仓库内 `docs/` 的设计/规范/契约索引。**分类规则见 [site-design §4](../site-design.html#4-目录组织h23)**。

## 站点规范

| 文档 | 说明 |
|---|---|
| [站点规范（site-design）](../site-design.html) | 导航结构 / 目录组织 / 双语策略 / Release 衔接 / 链接与数字口径纪律 |

## 契约与宪法（根级一级文档，位置固定原因见 site-design §4）

| 文档 | 说明 |
|---|---|
| [API 契约](../api-contract.html) | 三入口 schema + 错误码 + 示例（代码 docstring 锚定） |
| [MCP 契约](../mcp-contract.html) | MCP server 工具说明（test_mcp 断言路径） |
| [reward 映射宪法](../reward-mapping.html) | level→reward 映射定稿决策记录 |
| [reward 协议](../reward-protocol.html) | 配方 / 校准 / 锚点协议 |

## 宪法协议（docs/protocols/）

| 文档 | 说明 |
|---|---|
| [benchmark 协议](../protocols/benchmark-protocol.html) | 任务集生成/标注/难度/split/gap/功效/黑盒/覆盖/评审 |
| [Agent 评测协议](../protocols/agent-eval-protocol.html) | 真实评测宪法（含 §4.1 declared 修订） |

## 与外部审计内存的区别

- 仓库内 `docs/specs/` = 本仓库自有的规范/设计定稿（进入 Pages）；
- **外部** `D:\C-file\docs\specs\`（refactor-plan / ontology-design / trajectory-capture-design /
  execution-plan / 审计报告等）= 审计中枢共享内存，**不进 Pages、不动路径**（site-design §1.2）。
