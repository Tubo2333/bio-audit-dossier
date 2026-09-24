"""ConflictDetector — 自 fullflow-demo 迁移（含 A7 修正），未改动逻辑。"""

from bioaudit.models.rule import Rule


class ConflictDetector:
    """Detects when two active rules give contradictory scores to the same decision."""

    def detect(self, step_id: str, matched_rules: list[Rule],
               scores_per_rule: dict[str, int]) -> list[dict]:
        """Find conflicting rule pairs (score difference >= 3 levels)."""
        conflicts = []
        rule_ids = list(scores_per_rule.keys())

        for i in range(len(rule_ids)):
            for j in range(i + 1, len(rule_ids)):
                a, b = rule_ids[i], rule_ids[j]
                diff = abs(scores_per_rule[a] - scores_per_rule[b])
                if diff >= 3:
                    rule_a = self._find_rule(a, matched_rules)
                    rule_b = self._find_rule(b, matched_rules)
                    if rule_a and rule_b:
                        conflicts.append({
                            "step_id": step_id,
                            "rule_a": a, "score_a": scores_per_rule[a],
                            "rule_b": b, "score_b": scores_per_rule[b],
                            "confidence_a": self._get_confidence(rule_a),
                            "confidence_b": self._get_confidence(rule_b),
                            "resolution": self._resolve(rule_a, rule_b),
                        })
        return conflicts

    @staticmethod
    def _find_rule(rule_id: str, rules: list[Rule]) -> Rule | None:
        for r in rules:
            if r.rule_id == rule_id:
                return r
        return None

    @staticmethod
    def _get_confidence(rule: Rule) -> str:
        """A7 FIX: Safe access with empty-list guard."""
        if rule.evidence and len(rule.evidence) > 0:
            return rule.evidence[0].confidence
        return "L-Anecdotal"

    @staticmethod
    def _resolve(a: Rule, b: Rule) -> str:
        conf_a = ConflictDetector._get_confidence(a)
        conf_b = ConflictDetector._get_confidence(b)
        if conf_a == conf_b:
            return "NEEDS_HUMAN_REVIEW"
        conf_rank = {
            "L-Confirmed": 5, "L-Consensus": 4,
            "L-Evidenced": 3, "L-Emerging": 2, "L-Anecdotal": 1,
        }
        winner = a if conf_rank.get(conf_a, 0) > conf_rank.get(conf_b, 0) else b
        return f"USE_HIGHER_CONFIDENCE: {winner.rule_id}"
