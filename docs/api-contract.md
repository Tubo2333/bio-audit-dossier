# Bio-Audit API 契约（B3，阶段 1 接口）

> **日期**：2026-08-14
> **依据**：refactor-plan-v1.1 B1（pydantic schema + 错误码）/ B2（audit_decision 必填 paradigm）；
> audit-report A5（context 类型不安全）/ A7（human override 无校验）/ A15（Decision 无字段校验）
> **代码事实源**：`src/bioaudit/errors.py`（错误码）、`src/bioaudit/api/contract.py`（请求 schema 与校验）、
> `src/bioaudit/api/audit.py`（三入口实现）；本契约文档与代码**双向锁定**（tests/test_api_contract.py 有守卫）。

---

## 一、总则

1. **输入全部 pydantic schema 校验**：三入口的非法输入一律**显式报错**（携带错误码），
   绝不静默降级（例如：旧实现 act 未知时静默回退全量规则 → 现为 `paradigm-not-found`）。
2. **异常不裸抛**：API 层捕获一切内部异常，统一包装为
   `BioAuditError（code / message / details / status）`；`run_audit` 的管道内部失败
   写入 `state["error"]` + `state["error_code"]`（返回结构内错误，不抛裸异常）。
3. **错误负载统一结构**：`{"error": {"code": ..., "message": ..., "details": {...}}}`。

## 二、错误码体系

| 错误码 | HTTP 状态 | 触发场景 |
|--------|-----------|----------|
| `bad-request` | 400 | 请求负载结构性非法：trajectory 非数组/非对象、对象缺 `decisions` 键、human_overrides 非对象 |
| `validation-error` | 422 | 字段级校验失败（pydantic）：Decision 缺必填字段/未知字段（A15）、human_overrides 越界（A7）、轨迹 v2 缺必填字段 |
| `paradigm-not-found` | 404 | act/paradigm 不在 `{deg, pan, scrna}`（B1：不再静默回退全量规则） |
| `rule-not-found` | 404 | matched_rules 引用的规则 ID 在注册表不存在（不再静默丢弃） |
| `internal-error` | 500 | 未预期内部异常（包装后抛出/返回） |

常量事实源：`bioaudit.errors.ERROR_CODES`（frozenset）；HTTP 映射：`ERROR_HTTP_STATUS`。

## 三、入口 1：run_audit

```python
run_audit(
    trajectory: list[dict] | dict,
    act: str | None = None,          # "deg" | "pan" | "scrna"；None = 全量规则（38 唯一）；非法 → paradigm-not-found
    profile_id: str = "default",
    session_id: str | None = None,
    human_overrides: dict | None = None,  # {step_id: level}；level 必须为 int 且 -1..4（A7）
) -> dict
```

### 请求 schema（trajectory）

- v1（兼容）：决策数组 `list[dict]`；
- v2（B4 轨迹）：含 `decisions` 键的对象（`version`/`provenance` 等元数据被忽略，评分只消费 `decisions`）；
- 每条决策（`Decision` schema，A15——未知字段一律报错，拼错字段不再静默吞掉）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| step_id | str | ✅ | 步骤标识（human_overrides 的键） |
| decision_type | str | ✅ | 决策类型（如 deg_method / normalization） |
| choice | str | ✅ | Agent 选择的方法 |
| rationale | str | — | Agent 陈述的理由 |
| context | dict | — | 决策上下文（规则匹配用） |
| tool_call / code_snippet | str | — | 溯源字段 |

### 响应 schema

```jsonc
{
  "session_id": "audit_xxxxxxxx",
  "profile_id": "default",
  "act": "deg",
  "error": null,               // 管道内部失败时非空
  "error_code": null,          // 管道内部失败时的错误码（bad-request/…/internal-error）
  "parsed_steps": [/* ParsedStep */],
  "matched_rules": {"s1": ["M1.1-DEG-001"], "...": []},
  "step_scores": [/* DecisionScore */],
  "conflicts": [/* conflict */],
  "dimension_scores": {"data_handling": 0.85, "...": 0.85},
  "trajectory_score": 85.0,
  "eval_verdict": "pass",      // pass | needs_correction | blocked
  "critical_issues": [],
  "error_chains": [],
  "report": {
    "audit_id": "audit_xxxxxxxx",
    "profile": "default",
    "act": "deg",
    /* B5：C1 三元组快照（可复现性底线，P2）——ruleset_version 读自
       src/bioaudit/rules/ruleset.json（内容哈希 + semver），ontology_version
       读自本体 paradigms.yaml，engine_version = 包版本 */
    "engine_version": "0.2.1",
    "ruleset_version": "1.4.0",
    "ontology_version": "0.1.1",
    "snapshot": { "ruleset_version": "1.4.0", "ontology_version": "0.1.1", "engine_version": "0.2.1" },
    "n_decisions": 8,
    "n_rules_matched": 8,
    "trajectory_score": 85.0,
    "verdict": "pass",
    "critical_issues": [],
    "dimension_scores": {},
    "error_chains": [],
    "conflicts_needing_review": []
  }
}
```

### 示例

```python
from bioaudit.api import run_audit
from bioaudit.errors import BioAuditError

try:
    state = run_audit(
        [{"step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
          "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                      "design": "simple_two_group", "n_replicates": 6}}],
        act="deg",
        human_overrides={"s1": 4},
    )
except BioAuditError as exc:          # 输入非法：exc.code ∈ 错误码表
    print(exc.to_dict())
```

## 四、入口 2：audit_decision（B2：paradigm 必填）

```python
audit_decision(
    decision: dict,              # Decision schema（同上）
    paradigm: str,               # ★必填（无默认）："deg" | "pan" | "scrna"
    mappings_dir=None,           # 兼容参数（默认本体 input_synonyms）
) -> dict                        # DecisionScore（matched_rules/level/numeric_score/explanation/…）
```

- **paradigm 必填**（B2）：`deg_method` 等决策类型在 bulk-DEG 与 scRNA 下同名异构——
  同一 choice 必须按范式规则集评分。缺参 → `TypeError`（签名强制）；
  非法值 → `paradigm-not-found`。
- 示例（**paradigm 消歧**：同 `deg_method`/`DESeq2` 不同范式不同评分）：

```python
from bioaudit.api import audit_decision

bulk = {"step_id": "s3", "decision_type": "deg_method", "choice": "DESeq2",
        "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                    "design": "simple_two_group", "n_replicates": 6}}
scrna = {"step_id": "s3", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"sequencing": "10X_scRNA_seq", "n_patients": 5}}

audit_decision(bulk, paradigm="deg")    # → level 3（M1.1-DEG-001，bulk 正确方法）
audit_decision(scrna, paradigm="scrna") # → level 0（scRNA 规则要求 pseudobulk 形式）
```

## 五、入口 3：match_details（透明匹配明细）

```python
match_details(
    decision_type: str,          # 非空字符串；非法 → validation-error
    context: dict,               # 必须为对象；非法 → validation-error
    act: str | None = None,      # 范式；非法 → paradigm-not-found
) -> list[dict]                  # [{rule_id, title, checks: [{type, expr, expected, actual, pass}], matched}]
```

- 用于 UI 展示"引擎检查了什么"；无规则命中返回空列表（合法结果，非错误）。

## 六、human_overrides 校验（A7）

- 输入：`dict[str, int]`；每个值必须为 **int 且 -1 ≤ level ≤ 4**
  （-1 无法评估 / 0 危险 / 1 有风险 / 2 可接受 / 3 正确 / 4 示范级）；
- `bool`、float、str、越界 int 均**拒绝**：抛 `validation-error`
  （details 含 step_id / value / valid_range）；
- **拒绝的同时记录事件**：EventStore 追加 `invalid_override_rejected`
  （payload: step_id / value / reason），保证审计者自身可审计（C5）；
- 校验通过的 override 在 Step 3 应用，事件 `human_overrode` 照常记录。

## 七、轨迹 v2 schema（B4 联动，摘要）

```jsonc
{
  "version": 2,                    // ★必填；≠ 2 → validation-error
  "trajectory_id": "deg_correct",  // ★必填
  "act": "deg",                    // 可选（deg/pan/scrna）
  "provenance": {"source": "legacy", "migrated_from": "deg_correct.json", "migrator": "...", "note": "..."},
  "decisions": [/* Decision schema，同 §三 */]   // ★必填，非空
}
```

- 元数据（version/provenance/act）**不参与评分**（B4 不变量：迁移后 golden 仍 0 差异）；
- 校验器：`bioaudit.models.trajectory.validate_trajectory`；CLI：`bio-audit trajectory-validate`；
- 缺必填字段 → `validation-error`（A15）。

## 九、入口 4：reward（窗口 E / E1.1，阶段 4）

```python
reward(
    trajectory: list[dict] | dict,   # v1 决策数组 / v2 轨迹 / benchmark 任务
    act: str | None = None,          # "deg" | "pan" | "scrna"；None → 从轨迹 act 键推断
    recipe: str = "B",               # "A" 纯规则分 | "B" +L0 硬惩罚（默认）| "C" PRM 预留
    session_id: str | None = None,   # 采集会话：只消费 final verdict（B4）
    verdicts: list[dict] | None = None,  # VerdictRecord.as_dict() 列表（离线/测试）
    prm_weights: dict[str, float] | None = None,  # 配方 C 权重（PRM 预留接口）
    snapshot: SnapshotTriple | None = None,       # 三元组快照（默认 current_snapshot()）
) -> dict
```

### 响应 schema

```jsonc
{
  "step_rewards": [
    {"step_id": "S1", "decision_type": "api_data_integrity", "order": 0,
     "source": "declared",           // declared | backfilled（M3 补漏，阶段末尾聚合）
     "level": 3, "reward": 0.85,
     "masked": false, "mask_reason": null}   // mask_reason: level_minus_one | revoked | provisional_not_final | no_verdict_record
  ],
  "trajectory_reward": 0.85,          // 全 mask → null（不可评估，不给 0 虚假信号）
  "meta": {
    "reward_schema": "reward.v1",
    "status": "experimental_uncalibrated",   // E4.10：C3 语义不变，禁止当校准信号
    "recipe": "B",
    "verdict_mode": "all_final" | "final_only",
    "aggregation": "mean" | "weighted_mean",
    "saturation": "ceiling_0.85_no_micro_adjustment",
    "n_decisions": 12, "n_unmasked": 12, "n_masked": 0,
    "mask_reasons": {}, "n_l0": 0, "n_l1": 0,
    "has_l0_penalty_applied": false,
    "ceiling_reward": 0.85, "evidence_adjustment_enabled": false,
    "snapshot": {"ruleset_version": "1.4.0", "ontology_version": "0.1.1", "engine_version": "0.2.1"}
  }
}
```

### 契约要点（E1-E4 纪律）

1. **外围输出层**：只消费 run_audit 的 step_scores，不触碰评分路径
   （golden 0 差异硬验收，E4.11）；report 新增 `reward` 块（experimental 标注），
   既有字段不变；
2. **-1 必须 mask**（F1）：不参与分子与分母；全 mask → `trajectory_reward: null`；
3. **只消费 final**（B4）：传 session_id/verdicts 时 revoked/provisional/无记录
   → mask；不传 = all_final 模式（legacy/benchmark）；
4. **F4**：交叉验证四类判定（虚报/漏报/未验证）不进 reward（只进报告）；
5. **快照三元组**（C1/P2）：meta.snapshot 绑定 ruleset/ontology/engine 版本；
6. 非法输入复用 B3 错误码（bad-request / validation-error / paradigm-not-found），
   reward 管道失败 → 内部异常包装后抛出（不裸抛）。

### 映射与配方（定稿，数值论证见 docs/reward-mapping.md）

| level | 0 | 1 | 2 | 3 | 4 | -1 |
|-------|---|---|---|---|---|-----|
| reward | 0.00 | 0.30 | 0.60 | 0.85 | 1.00 | **mask** |

配方 B（默认）：`mean(未 mask 步骤) × 0.30`（当且仅当存在未 mask L0，二元惩罚）。

### 相关 CLI

```
bio-audit reward <trajectory> [--act] [--recipe A|B|C] [--session <id>] [--prm-weights <json>]
bio-audit reward-calibrate [--seed] [--n-boot]      # E3：30 任务校准报告
bio-audit reward-validate                            # E4：五闸（映射/确定性/spike-in/消融/golden）
```

## 十、行为变更清单（相对 B3 前）

| 旧行为 | 新行为 |
|--------|--------|
| act 未知 → 静默回退全量规则 | `paradigm-not-found` |
| Decision 未知字段（如 decisionType）→ 静默忽略/降级 | `validation-error`（extra=forbid） |
| human_overrides 任意值写入 | 校验 int 且 -1..4；非法拒绝 + 记录事件 |
| matched 规则缺失 → 静默丢弃 | `rule-not-found` |
| 内部异常裸抛/裸字符串 | `BioAuditError` 或 `state["error_code"]` |
| audit_decision act 可选 | paradigm **必填**（签名强制） |
