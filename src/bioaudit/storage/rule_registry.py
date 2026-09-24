"""RuleRegistry — 自 fullflow-demo 迁移（含 A4/A6/C2 修正），路径改为包内锚定。

迁移变更（相对 fullflow-demo/src/storage/rule_registry.py）：
- 默认规则目录由相对 cwd 的旧默认 data/rules 改为 bioaudit.paths.RULES_DIR（F7 修复）
- 构造参数保持兼容：可显式传入 Path/str 覆盖

保留修正：
- A4: _eval_constraint 解析失败 fail-closed
- A6: forbidden_context 值防非 list
- C2: 同 rule_id 跨文件重复 → WARNING + 保留先加载者（去重后 39 唯一规则）
"""

import re
import logging
import yaml
from pathlib import Path
from typing import Optional

from bioaudit.models.rule import Rule
from bioaudit.paths import RULES_DIR

logger = logging.getLogger(__name__)


class RuleRegistry:
    """Loads YAML rules, builds indices, matches decisions to rules."""

    def __init__(self, rules_dir: Optional[str | Path] = None):
        self.rules_dir = Path(rules_dir) if rules_dir else RULES_DIR
        self._rules: dict[str, Rule] = {}
        self._by_type: dict[str, list[str]] = {}
        self._rule_sources: dict[str, str] = {}

    def load_all(self) -> int:
        """Load all YAML rule files. Returns count of active rules loaded."""
        count = 0
        # C2 FIX (2026-08-13): 同 rule_id 双副本去重告警。rglob 顺序不保证，
        # 重复 rule_id 时保留先加载者并告警，避免 matched_rules 出现同一
        # rule_id 两次（审计 C2：DEG/ 与 pancancer/ 同名分叉被加载两次）。
        for yaml_file in sorted(self.rules_dir.rglob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or "rule_id" not in data:
                    logger.warning(f"Skipping invalid rule file: {yaml_file}")
                    continue
                rule = Rule(**data)
                if rule.status == "active":
                    if rule.rule_id in self._rules:
                        logger.warning(
                            f"C2 duplicate rule_id {rule.rule_id!r}: already loaded "
                            f"from {self._rule_sources.get(rule.rule_id)}, "
                            f"skipping {yaml_file}"
                        )
                        continue
                    self._rules[rule.rule_id] = rule
                    self._rule_sources[rule.rule_id] = str(yaml_file)
                    dtype = rule.condition.decision_type
                    self._by_type.setdefault(dtype, []).append(rule.rule_id)
                    count += 1
            except Exception as e:
                logger.error(f"Failed to load rule {yaml_file}: {e}")
                # Continue loading other rules — don't let one bad file
                # prevent all rules from loading
        return count

    def match(self, decision_type: str, context: dict) -> list[Rule]:
        """Match decisions to rules in two phases:
        1. O(1) lookup by decision_type
        2. Per-candidate condition check
        """
        candidates = self._by_type.get(decision_type, [])
        matched = []
        for rule_id in candidates:
            rule = self._rules[rule_id]
            if self._condition_matches(rule.condition, context):
                matched.append(rule)
        return matched

    def rules_for_type(self, decision_type: str) -> list[Rule]:
        """该决策类型的全部候选规则（M2.4：missing 三档强制的引用键判定先于匹配）。"""
        return [self._rules[rid] for rid in self._by_type.get(decision_type, [])]

    def match_with_details(self, decision_type: str, context: dict) -> list[dict]:
        """透明匹配: 返回每条候选规则的逐条条件评估明细.

        每条记录包含:
        - rule_id / title: 规则标识
        - checks: [{"type", "expr", "expected", "actual", "pass"}, ...]
        - matched: 是否所有检查通过 (规则生效)
        """
        details = []
        for rule_id in self._by_type.get(decision_type, []):
            rule = self._rules[rule_id]
            checks = self._evaluate_condition(rule.condition, context)
            details.append({
                "rule_id": rule_id,
                "title": rule.title,
                "checks": checks,
                "matched": all(c["pass"] for c in checks),
            })
        return details

    def _evaluate_condition(self, condition, context: dict) -> list[dict]:
        """逐条评估规则条件, 返回每个检查项的明细 (透明化核心)."""
        checks = []

        # 1. 必需上下文 (required_context; G-2: 值为列表 = 任一命中, any-of)
        for key, val in condition.required_context.items():
            actual = context.get(key)
            if isinstance(val, list):
                checks.append({
                    "type": "required",
                    "expr": f"{key} ∈ {val!r}",
                    "expected": f"∈ {val}",
                    "actual": repr(actual) if key in context else "(缺失)",
                    "pass": key in context and actual in val,
                })
            else:
                checks.append({
                    "type": "required",
                    "expr": f"{key} == {val!r}",
                    "expected": str(val),
                    "actual": repr(actual) if key in context else "(缺失)",
                    "pass": key in context and actual == val,
                })

        # 2. 禁止上下文 (forbidden_context)
        for key, vals in condition.forbidden_context.items():
            vals_list = vals if isinstance(vals, list) else [vals]
            actual = context.get(key)
            checks.append({
                "type": "forbidden",
                "expr": f"{key} NOT IN {vals_list}",
                "expected": f"不在 {vals_list} 中",
                "actual": repr(actual) if key in context else "(缺失, 通过)",
                "pass": not (key in context and context[key] in vals_list),
            })

        # 3. 数值约束 (context_constraints)
        for constraint in condition.context_constraints or []:
            m = re.match(r'(\w+)\s*(>=|<=|>|<|==|!=)\s*(\d+\.?\d*)', constraint)
            if m:
                key, op, val_str = m.groups()
                actual = context.get(key)
                ok = self._eval_constraint(constraint, context)
                checks.append({
                    "type": "constraint",
                    "expr": constraint,
                    "expected": constraint,
                    "actual": repr(actual) if key in context else "(缺失)",
                    "pass": ok,
                })
            else:
                checks.append({
                    "type": "constraint",
                    "expr": constraint,
                    "expected": constraint,
                    "actual": "(无法解析)",
                    "pass": False,  # fail-closed
                })

        return checks

    def _condition_matches(self, condition, context: dict) -> bool:
        """Check if condition matches context. All checks must pass.

        G-2: required_context 值为列表 = any-of（任一命中即通过），
        用于平台键放宽（如 sequencing ∈ [10X_scRNA_seq, smartseq2]）。
        """
        # 1. Required context key-value pairs
        for key, val in condition.required_context.items():
            if key not in context:
                return False
            if isinstance(val, list):
                if context[key] not in val:
                    return False
            elif context[key] != val:
                return False

        # 2. Forbidden context values (A6 FIX: guard against non-list)
        for key, vals in condition.forbidden_context.items():
            if key in context:
                # A6: ensure vals is iterable
                vals_list = vals if isinstance(vals, list) else [vals]
                if context[key] in vals_list:
                    return False

        # 3. Context constraints (A1 FIX: field now exists on RuleCondition)
        for constraint in condition.context_constraints or []:
            if not self._eval_constraint(constraint, context):
                return False

        return True

    def _eval_constraint(self, constraint: str, context: dict) -> bool:
        """A4 FIX: fail-closed. Malformed constraints REJECT instead of passing.

        Returns True if constraint is satisfied, False otherwise.
        Unparseable constraints → False (fail-closed for safety).
        """
        match = re.match(
            r'(\w+)\s*(>=|<=|>|<|==|!=)\s*(\d+\.?\d*)', constraint
        )
        if not match:
            # A4 FIX: fail-closed — unparseable constraints REJECT
            logger.warning(
                f"Failed to parse constraint: {constraint!r} — REJECTING (fail-closed)"
            )
            return False

        key, op, val_str = match.groups()
        actual = context.get(key)
        if actual is None:
            return True  # Key not in context → constraint doesn't apply

        val = float(val_str)
        if op == ">=":
            return actual >= val
        if op == "<=":
            return actual <= val
        if op == ">":
            return actual > val
        if op == "<":
            return actual < val
        if op == "==":
            return actual == val
        if op == "!=":
            return actual != val
        return False  # Unknown operator → reject

    def get_rule(self, rule_id: str) -> Optional[Rule]:
        return self._rules.get(rule_id)

    def reload(self) -> int:
        """Reload all rules from disk. No restart needed."""
        self._rules.clear()
        self._by_type.clear()
        self._rule_sources.clear()
        return self.load_all()

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @property
    def type_count(self) -> int:
        return len(self._by_type)
