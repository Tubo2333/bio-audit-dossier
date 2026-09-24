"""RuleMatcher — 自 fullflow-demo 迁移（含 B1/B8 修正），B2 本体化接线。

迁移变更（F7 修复）：默认映射文件由相对 cwd 的旧默认 data/mappings/*.yaml 改为
bioaudit.paths.MAPPINGS_DIR；构造参数保持兼容（可显式传路径）。

B2 变更（本体化，ontology-design-v1 §五）：
- 别名归一化（匹配通道）默认从**本体 input_synonyms.yaml** 读取
  （原 data/mappings/type_aliases.yaml 内容移入本体，条目一一对应，行为不变）；
- 跨范式**同源声明**（本体 aliases.yaml / 各类型 `aliases` 键）不是匹配通道
  （§二.1），仅作为注释写入 ParsedStep.homologous_types（知识图谱/聚合统计用）；
- 未知决策类型 → ParsedStep.unclassified=True（§五：标 unclassified + 待补清单），
  不影响评分（评分仍走 evaluator 的 -1 无法评估路径）。

保留修正：
- B1: Split into .parse() and .match_parsed() to avoid duplicate work
- B8: Alias/key mappings moved to data/mappings/
"""

import yaml
from pathlib import Path

from bioaudit.storage.rule_registry import RuleRegistry
from bioaudit.models.decision import Decision, ParsedStep
from bioaudit.models.rule import Rule
from bioaudit.ontology.loader import get_ontology
from bioaudit.paths import MAPPINGS_DIR


class RuleMatcher:
    """Matches agent decisions to applicable scientific rules.

    Two-phase design (B1 fix):
    1. parse(Decision) → ParsedStep (normalization only)
    2. match_parsed(ParsedStep) → list[Rule] (rule matching only)
    """

    def __init__(self, registry: RuleRegistry, mappings_dir: Path | str | None = None,
                 ontology=None):
        self.registry = registry
        self._mappings_dir = Path(mappings_dir) if mappings_dir else None
        self._ontology = ontology if ontology is not None else get_ontology()
        if self._mappings_dir is not None:
            # 兼容：显式传 mappings 目录 → 旧 data/mappings 文件
            self._aliases = self._load_mapping(self._mappings_dir / "type_aliases.yaml")
        else:
            # B2: 本体 input_synonyms（匹配通道；条目与旧 type_aliases.yaml 一致）
            self._aliases = dict(self._ontology.input_synonyms)
        self._key_map = self._load_mapping(
            (self._mappings_dir or MAPPINGS_DIR) / "context_keys.yaml"
        )

    def _load_mapping(self, path: Path) -> dict:
        """B8: Load mapping from YAML, fallback to defaults."""
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}

    def parse(self, decision: Decision) -> ParsedStep:
        """B1: Parse only — normalize decision type and context."""
        raw_type = decision.decision_type
        canonical = self._normalize_type(raw_type)
        return ParsedStep(
            step_id=decision.step_id,
            original=decision,
            decision_type=canonical,
            normalized_context=self._normalize_context(decision.context),
            # B2: 跨范式同源注释（非匹配通道）——知识图谱/跨范式聚合统计用
            homologous_types=self._ontology.aliases_for(canonical),
            # B2: 未知决策类型标记（§五 unclassified；评分仍走 -1 无法评估）
            unclassified=not self._ontology.is_known(canonical),
        )

    def match_parsed(self, parsed: ParsedStep) -> list[Rule]:
        """B1: Match only — find rules for an already-parsed step."""
        return self.registry.match(
            parsed.decision_type, parsed.normalized_context
        )

    def match(self, decision: Decision) -> tuple[ParsedStep, list[Rule]]:
        """Convenience: parse + match in one call (for non-pipeline use)."""
        parsed = self.parse(decision)
        rules = self.match_parsed(parsed)
        return parsed, rules

    def _normalize_type(self, raw_type: str) -> str:
        """Normalize decision type using configurable alias map."""
        return self._aliases.get(raw_type, raw_type)

    def _normalize_context(self, context: dict) -> dict:
        """Normalize context keys using configurable key map."""
        normalized = {}
        for k, v in context.items():
            normalized[self._key_map.get(k, k)] = v
        return normalized
