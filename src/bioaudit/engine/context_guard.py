"""missing 三档运行时强制（窗口 M / M2.4；refactor-plan-v1.1 A1-A5；ontology-design §四）。

**裁决（M2.7，2026-08-16 经项目负责人在线确认，Option B 规则引用驱动）**：
触发范围 = 规则引用驱动——``context_schema`` 中缺失键**且被该类型候选规则引用**
（``required_context`` ∪ ``context_constraints`` 键）才按档位处理；无规则引用的
schema 键缺失不影响评分正确性，不凭空降级（实测：golden 0 漂移、任务 13 决策）。

档位语义（先最严档位定决策状态，再规则求值，A5）：
- **fail-closed**：缺失且被候选规则引用 → 决策 **未验证（level=-2）**，跳过规则求值
  （含 override）——评估前提不成立，语义与 -1（无法评估/未知方法）区分；
- **skip**：缺失且被候选规则引用 → **跳过依赖该键的规则**（不匹配；
  ``matched_rules_skipped`` 溯源——A1 交互规则：最严规则被跳过时等级可能抬高，
  以 skipped 列表如实呈现，不静默）；
- **fail-open**：视为满足（仅无害键）；
- **类型强制（A5/A3）**：context 值按 schema type 校验；非法/枚举外值 →
  该键标 unverified → 按档位处理（A3 裁决）；
- **A2 运行时断言**：罚分规则（level_0/1 方法命中）引用的键禁止 fail-open
  （静态校验器已有，运行时再断言防未来规则改动绕过）。
"""

from __future__ import annotations

import re
from typing import Optional

from bioaudit.models.decision import ParsedStep
from bioaudit.models.rule import Rule
from bioaudit.models.score import DecisionScore
from bioaudit.ontology.loader import Ontology, get_ontology

#: 决策状态
STATE_OK = "ok"                    # 可正常求值
STATE_UNVERIFIED = "unverified"    # 关键上下文缺失 → 未验证（level=-2）

#: 未验证 level 编码（与 -1 区分：-1=无法评估/未知方法；-2=关键上下文缺失）
LEVEL_UNVERIFIED = -2


class MissingResolution:
    """单条决策的 missing 三档解析结果。"""

    __slots__ = ("state", "missing_keys", "invalid_keys", "skipped_rule_ids", "warnings")

    def __init__(
        self,
        state: str = STATE_OK,
        missing_keys: Optional[list[str]] = None,
        invalid_keys: Optional[list[str]] = None,
        skipped_rule_ids: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ):
        self.state = state
        self.missing_keys = list(missing_keys or [])
        self.invalid_keys = list(invalid_keys or [])
        self.skipped_rule_ids = list(skipped_rule_ids or [])
        self.warnings = list(warnings or [])

    @property
    def unverified(self) -> bool:
        return self.state == STATE_UNVERIFIED


def rule_referenced_keys(rules: list[Rule]) -> set[str]:
    """候选规则引用的键集合（required_context 键 + context_constraints 键 + override 键）。

    M2.5（窗口 M）：override 条件键（如 G1.1 的 ``n_patients``）也是规则依赖——
    缺失时按 fail-closed 未验证处理（否则移除约束门后 n 键缺失会失去强制）。
    """
    refs: set[str] = set()
    for rule in rules:
        refs.update(rule.condition.required_context.keys())
        for constraint in rule.condition.context_constraints or []:
            m = re.match(r"(\w+)\s*(>=|<=|>|<|==|!=)\s*(\d+\.?\d*)", constraint)
            if m:
                refs.add(m.group(1))
        override = rule.scoring.override_n2 or {}
        cond = str(override.get("condition", "")).strip()
        m = re.match(r"(\w+)\s*(<=|<|>=|>)\s*(\d+\.?\d*)", cond)
        if m:
            refs.add(m.group(1))
    return refs


def _required_context_matches(rule: Rule, ctx: dict) -> bool:
    """required_context 匹配（与 rule_registry 同语义：列表 = any-of）。

    M2.5：候选级 override 的前置门——override 只在其规则的其它门（required_context）
    满足时生效；约束门（context_constraints）由 override 自身语义接管（n<=2 → L0），
    否则 G1.1 的 ``n_patients >= 3`` 约束会把 override（``n_patients <= 2``）变成
    死代码（D4 意图落空，fix-tracking A3 背景）。
    """
    for key, val in rule.condition.required_context.items():
        if key not in ctx:
            return False
        if isinstance(val, list):
            if ctx[key] not in val:
                return False
        elif ctx[key] != val:
            return False
    return True


def _schema_key_type_valid(schema_item: dict, value) -> Optional[str]:
    """按 schema type 校验值；None = 合法，否则返回原因（A5 类型强制 / A3 枚举外值）。"""
    t = schema_item["type"]
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"期望 int，实际 {type(value).__name__}={value!r}"
    elif t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"期望 float，实际 {type(value).__name__}={value!r}"
    elif t == "bool":
        if not isinstance(value, bool):
            return f"期望 bool，实际 {type(value).__name__}={value!r}"
    elif t == "enum":
        if not isinstance(value, str) or value not in schema_item.get("values", []):
            return f"期望 ∈ {schema_item.get('values')}，实际 {value!r}"
    elif t == "string":
        if not isinstance(value, str):
            return f"期望 string，实际 {type(value).__name__}={value!r}"
    return None


def resolve(
    parsed: ParsedStep,
    candidate_rules: list[Rule],
    ontology: Optional[Ontology] = None,
) -> MissingResolution:
    """missing 三档解析（先最严档位定决策状态，再规则求值——A5）。

    Parameters
    ----------
    parsed : ParsedStep
        归一化决策（decision_type + normalized_context）。
    candidate_rules : list[Rule]
        该决策类型的**候选规则**（registry 中该类型全部规则，非仅匹配结果——
        缺失键的档位判定必须先于规则匹配）。
    ontology : Ontology | None
        决策类型本体（默认包内）。
    """
    ont = ontology if ontology is not None else get_ontology()
    schema = (ont.get_type(parsed.decision_type) or {}).get("context_schema", [])
    if not schema:
        return MissingResolution()

    ctx = parsed.normalized_context
    refs = rule_referenced_keys(candidate_rules)

    missing_fc: list[str] = []
    missing_skip: list[str] = []
    missing_fo: list[str] = []
    invalid: list[str] = []
    for item in schema:
        key = item["key"]
        if key not in ctx:
            # 缺失 → 按档位（仅当被候选规则引用；无引用则不影响评分）
            if item["missing"] == "fail-closed" and key in refs:
                missing_fc.append(key)
            elif item["missing"] == "skip" and key in refs:
                missing_skip.append(key)
            elif item["missing"] == "fail-open" and key in refs:
                # A2 运行时断言：fail-open 键被候选规则引用 → 警告留痕
                # （静态校验器已禁止罚分规则引用 fail-open 键；此处防未来规则
                # 改动绕过，fail-open 语义本身仍按"视为满足"处理）
                missing_fo.append(key)
            continue
        # 类型/枚举校验（A5/A3）：非法 → 该键标 unverified → 按档位处理
        reason = _schema_key_type_valid(item, ctx[key])
        if reason is not None:
            invalid.append(f"{key}（{reason}）")
            if item["missing"] == "fail-closed" and key in refs:
                missing_fc.append(key)
            elif item["missing"] == "skip" and key in refs:
                missing_skip.append(key)

    if missing_fc:
        # 最严档位：fail-closed 缺失 → 决策未验证（不给 level）
        return MissingResolution(
            state=STATE_UNVERIFIED,
            missing_keys=sorted(set(missing_fc)),
            invalid_keys=invalid,
        )

    # skip 档：跳过依赖缺失键的规则（A1 交互规则溯源）
    skipped: list[str] = []
    if missing_skip:
        for rule in candidate_rules:
            rule_refs = set(rule.condition.required_context.keys())
            for constraint in rule.condition.context_constraints or []:
                m = re.match(r"(\w+)\s*(>=|<=|>|<|==|!=)\s*(\d+\.?\d*)", constraint)
                if m:
                    rule_refs.add(m.group(1))
            if rule_refs & set(missing_skip):
                skipped.append(rule.rule_id)

    return MissingResolution(
        state=STATE_OK,
        missing_keys=[],
        invalid_keys=invalid,
        skipped_rule_ids=sorted(set(skipped)),
        warnings=(
            [f"fail-open 键被候选规则引用但缺失（A2 运行时断言）: {sorted(set(missing_fo))}"]
            if missing_fo else []
        ),
    )


def build_unverified_score(parsed: ParsedStep, resolution: MissingResolution) -> DecisionScore:
    """未验证决策的 DecisionScore（level=-2，不给 numeric 评级；占位值 0.5 明示）。"""
    keys = ", ".join(resolution.missing_keys + resolution.invalid_keys) or "（无）"
    return DecisionScore(
        step_id=parsed.step_id,
        decision_type=parsed.decision_type,
        agent_choice=parsed.original.choice,
        agent_rationale=parsed.original.rationale,
        matched_rules=[],
        level=LEVEL_UNVERIFIED,
        numeric_score=0.5,  # 占位（与 -1 同策略：不参与聚合/检出/reward，值不具语义）
        explanation=(
            "未验证 — 关键上下文缺失（missing 三档强制，fail-closed）："
            f"{keys}。评估前提不成立，不给 level（与 -1 无法评估区分，"
            "窗口 M M2.4 裁决 2026-08-16）。此分数为占位值，不可作为质量判断依据。"
        ),
        evidence_citations=[],
        alternatives=[],
        reward_signal=0.5,
        missing_keys=resolution.missing_keys + resolution.invalid_keys,
    )


def score_decision(
    parsed: ParsedStep,
    matched_rules: list[Rule],
    candidate_rules: list[Rule],
    evaluator,
    ontology: Optional[Ontology] = None,
) -> DecisionScore:
    """统一评分入口（run_audit / audit_decision / golden 重放共用，防三处漂移）。

    流程（A5 顺序）：missing 三档解析（先定决策状态）→ 未验证则直接出 -2 分；
    否则剔除 skip 档跳过规则 → **候选级 override 检查**（M2.5：D4 override 在
    约束门之外独立生效——n<=2 → 所有方法 L0，修复约束门使 override 死代码
    的问题）→ 正常规则求值。
    """
    res = resolve(parsed, candidate_rules, ontology)
    if res.unverified:
        return build_unverified_score(parsed, res)
    if res.skipped_rule_ids:
        matched_rules = [
            r for r in matched_rules if r.rule_id not in res.skipped_rule_ids
        ]
    # M2.5：候选级 override（仅 required_context 门满足的候选规则；约束门由
    # override 语义接管）。命中 → 决策直接按 override 评级（D4："所有方法 → L0"）。
    override_level = None
    for rule in candidate_rules:
        if not _required_context_matches(rule, parsed.normalized_context):
            continue
        lvl = evaluator._check_overrides(rule, parsed)
        if lvl is not None and (override_level is None or lvl < override_level):
            override_level = lvl
    if override_level is not None:
        return evaluator.build_override_score(parsed, override_level, matched_rules)
    return evaluator.evaluate(parsed, matched_rules)


__all__ = [
    "STATE_OK",
    "STATE_UNVERIFIED",
    "LEVEL_UNVERIFIED",
    "MissingResolution",
    "rule_referenced_keys",
    "resolve",
    "build_unverified_score",
    "score_decision",
]
