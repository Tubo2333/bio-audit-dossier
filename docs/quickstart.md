# 快速开始

> Bio-Audit Dossier：可随稿件交付的决策级审计记录（五个 RNA-seq 方法学决策点）。
> 完整项目说明见 [README](https://github.com/Tubo2333/bio-audit-dossier#readme)；[English](quickstart.en.html)。
> 所属体系：[bio-audit](https://github.com/Tubo2333/bio-audit)（审计引擎与评测层）。
> Python >= 3.10（本地实测 3.12；CI 双矩阵 3.10/3.12）。

## 1. 安装

```bash
git clone git@github.com:Tubo2333/bio-audit-dossier.git
cd bio-audit-dossier
pip install -e ".[dev,ui]"        # 核心 + 测试 + UI 薄壳
# 演示（深色审计台四页）另装 demo extra：
pip install -e ".[demo]"          # streamlit>=1.33,<2
# 或按 lockfile 锁定依赖（B6 纪律）：
pip install -r requirements.lock -r requirements-dev.lock && pip install -e .
```

## 2. 一分钟上手

```bash
# 审计一条 v2 轨迹（评分只消费 decisions；version/provenance 是元数据）
bio-audit run src/bioaudit/data/trajectories/v2/deg_correct.json

# golden 回归：20 轨迹 137 决策 vs 冻结基线，必须 0 差异
bio-audit golden

# 单决策审计（--act 必填，deg_method 同名异构消歧）
bio-audit audit-decision decision.json --act scrna
```

## 3. 常用命令

| 命令 | 用途 |
|---|---|
| `bio-audit run <轨迹.json>` | 审计一条轨迹（报告含三元组快照 engine/ruleset/ontology） |
| `bio-audit golden` | golden 回归（20 轨迹 137 决策，0 差异，失败 exit 1） |
| `bio-audit audit-decision <json> --act <范式>` | 单决策审计（B3 契约） |
| `bio-audit validate-ontology` | 本体校验器三职责（覆盖/语义边界/冲突） |
| `bio-audit ruleset-validate` | 规则治理三闸（清单 + D2 冲突 + golden）——规则变更必跑 |
| `bio-audit benchmark-validate` | benchmark 四闸（清单 + 污染 + 覆盖 + golden） |
| `bio-audit benchmark-run` | 批量评测运行器 + 功效报告 |
| `bio-audit reward-validate` | reward 五闸（映射/确定性/spike-in/消融/golden） |
| `bio-audit reward <轨迹.json>` | reward 训练信号（只消费 final verdict） |
| `bio-audit parse-notebook <nb>` / `cross-validate <nb>` | M3 解析 / 交叉验证（M1×M3 对齐） |
| `python -m mcp.server` | 启动 MCP server（Agent 接入）；`--selfcheck` 自检 |
| `bio-audit migrate-trajectories` / `trajectory-validate` | 轨迹 v1→v2 迁移（只读）/ v2 schema 校验 |

## 4. 接入方式

- **Python API**：`run_audit` / `audit_decision` / `match_details` 三入口
  （pydantic 校验 + 错误码 + paradigm 必填），见 [API 契约](api-contract.html)；
- **MCP**：stdio JSON-RPC，工具 = `audit_decision` / `audit_trajectory` / `report`，
  见 [MCP 契约](mcp-contract.html)；
- **演示（推荐）**：`streamlit run demo/app.py`（深色审计台四页：审计工坊 / 采集演示 /
  评测与奖励 / 关于；冷启动 ≤10s 到可交互，演示前先启动预热一次；界面静态预览见
  [demo-preview.html](demo-preview.html)）；
- **Streamlit 薄壳**：`streamlit run ui/app.py`（只调 bioaudit.api，轻量 API 演示；
  与 `demo/` 并存，去留后续决策）。

## 5. 规则与任务集贡献

规则即代码：改规则/加任务集走 PR + 三闸/四闸门禁（流程见
[CONTRIBUTING.md](https://github.com/Tubo2333/bio-audit-dossier/blob/main/CONTRIBUTING.md)）；
评分路径改动必须 golden 0 差异（漂移必须逐条解释，C4）。

## 6. 测试与回归

```bash
pytest                              # 全量（本地/CI 双矩阵；含 demo 冒烟）
bio-audit golden --json             # 0 差异；diff≠0 即红=人工确认门槛
python scripts/generate_scrna_r0.py --output /tmp/r0.json   # R0 确定性锚定（与包内逐字节一致）
```

## 7. 更多

- [文档中心](index.html) · [设计文档](specs/index.html) ·
  [Release](https://github.com/Tubo2333/bio-audit-dossier/releases) · 许可 Apache-2.0
