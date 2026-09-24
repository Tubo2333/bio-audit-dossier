# 文档中心

Bio-Audit 文档站目录（站点规范见 [site-design](site-design.html)）。按分类浏览：

## 入门

| 文档 | 说明 |
|---|---|
| [快速开始（中文）](quickstart.html) | 安装、CLI 审计、golden 回归、三闸/四闸/五闸、API/MCP 接入、演示启动 |
| [Quick Start (English)](quickstart.en.html) | English version of the quick start |
| [Demo 静态预览](demo-preview.html) | 新 demo 四屏关键界面静态预览（image-to-code 复刻，含真实数据；完整交互见运行应用，启动方式 `streamlit run demo/app.py`） |
| [README](https://github.com/Tubo2333/bio-audit-dossier#readme) | 项目总览（GitHub 渲染；[英文版](https://github.com/Tubo2333/bio-audit-dossier/blob/main/README.en.md)） |
| [规则贡献指南](https://github.com/Tubo2333/bio-audit-dossier/blob/main/CONTRIBUTING.md) | 规则/任务集变更流程（三闸/四闸）、代码规范 |

## 契约与宪法（一级文档）

| 文档 | 说明 |
|---|---|
| [API 契约](api-contract.html) | 三入口（run_audit / audit_decision / match_details）请求/响应 schema + 错误码 + reward 入口 |
| [MCP 契约](mcp-contract.html) | MCP server 工具说明、请求/响应示例、错误码映射、握手 |
| [reward 映射宪法](reward-mapping.html) | level→reward 非线性映射定稿、-1 mask、γ=0.30 硬惩罚、PRM 预留接口 |
| [reward 协议](reward-protocol.html) | 配方定义、mask 语义、预注册统计量、锚点阈值、实测表 |

## 协议与设计

| 文档 | 说明 |
|---|---|
| [benchmark 协议](https://github.com/Tubo2333/bio-audit/blob/main/docs/protocols/benchmark-protocol.md) | 任务集生成/标注/难度/split/gap/功效/黑盒/覆盖/评审（父仓文档） |
| [Agent 评测协议（宪法）](https://github.com/Tubo2333/bio-audit/blob/main/docs/protocols/agent-eval-protocol.md) | 真实 Agent 评测宪法（含 §4.1 declared 修订）（父仓文档） |
| [设计文档](specs/index.html) | 站点规范与设计定稿索引 |

## 评测口径登记

- 窗口级完成报告与真实评测报告**不随本仓库发布**（属内部治理记录）；
  下文「数字口径速查」仅登记口径与出处，不给站点链接。

## 数字口径速查（教训 #2，禁止混写）

| 口径 | 数值 |
|---|---|
| demo 轨迹（D5 修复后引擎重跑；窗口 M 词表补齐后） | 29 分 · blocked · L0×3 / L1×5 / L-1×0（S7 PCA_arbitrary → L1、S11 no_trajectory → L0；见 M1 报告） |
| G-2 真实运行重评（GSE115978，declared + 规则放宽后） | 30.0 needs_correction · L0=0 / L1×7 / L3×1 / L-1×12 |
| **K1 重评（2026-08-16，immune scRNA 规则落地后，ruleset 1.5.0）** | **30.0 needs_correction（不变）** · L0=0 / L1×19 / L3×1 / L-1×0（12 条 immune 从"无法评估"变为"有风险·细胞级伪重复"；分数不变因 data_handling 维仍主导，见 K1 报告） |
| **10X 黄金对照 A 版（2026-08-16，窗口 L，GSE132465 CRC 10X，确定性脚本非 LLM，ruleset 1.6.0）** | **80.0 pass** · L0=0 / L1=0 / L-1=0 · 11 决策（doublet_detection scDblFinder → L3，D1.1 首次真实执行验证） |
| **10X 变体 B 版采集链路闭环（窗口 M，2026-08-16，expected_types 强制预期决策点，ruleset 1.7.0）** | **63.7 blocked** · doublet_detection 补入（provenance=expected）→ D1.1 L0 直接出自采集链路（原"引擎级补验"路径已取代，见 M1 报告）） |
| **L-b 真实短评测（2026-08-16，GSE115978 聚焦短分析，真实 LLM，¥0.43）** | **30.0 needs_correction** · L2×1 / L1×4 / L-1×0（5 决策全可评分；高分不保证如实兑现） |

完整口径与出处见 L1 报告 与 [site-design §6.2](site-design.html#62-数字口径纪律教训-2-单一事实源)。
