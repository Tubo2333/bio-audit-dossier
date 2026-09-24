"""RuleEvaluator — 自 fullflow-demo 迁移（含 A3/B6/D3/D4/D5 修正），import 改为 bioaudit 包。

保留修正：
- A3: Take LOWEST (strictest) score, not highest
- B6: Fuzzy choice normalization for real agent output
- D3: MVP merges Level 3+4; only check methods in Level 3
- D4: Implement override_n2
- D5: Check conditions_when_acceptable（2026-08-13 修复：无条件提升已移除）
"""

import re
from bioaudit.models.rule import Rule
from bioaudit.models.decision import ParsedStep
from bioaudit.models.score import DecisionScore


# Level → numeric score mapping (MVP: nonlinear)
LEVEL_TO_SCORE = {4: 1.0, 3: 0.85, 2: 0.6, 1: 0.3, 0: 0.0, -1: 0.5}

LEVEL_LABELS = {
    4: "示范级 — 方法和参数都最优 (v0.2 LLM增强后启用)",
    3: "正确级 — 方法选择正确",
    2: "可接受 — 有微小瑕疵",
    1: "有风险 — 方法选择值得商榷",
    0: "危险 — 方法选择将导致错误结论",
    -1: "无法评估 — 没有适用的规则",
    # M2.4（窗口 M，2026-08-16）：missing 三档运行时强制——fail-closed 键缺失
    # 且被候选规则引用 → 决策"未验证"（评估前提不成立；与 -1 区分并在报告呈现）
    -2: "未验证 — 关键上下文缺失，无法评估（missing 三档强制，窗口 M）",
}


class RuleEvaluator:
    """Evaluates a single decision against matched rules.

    Philosophy: conservative/paranoid. When multiple rules match,
    take the LOWEST (strictest) score across all rules.
    """

    def evaluate(self, parsed: ParsedStep, rules: list[Rule]) -> DecisionScore:
        if not rules:
            return self._no_rule_matched(parsed)

        # A3 FIX: start at best possible, take LOWEST (strictest)
        best_level = 4
        all_rule_ids = []
        applicable = []  # K2: 识别了 choice 的规则（未识别的规则级跳过，不参与）

        for rule in rules:
            # K2: condition 命中的规则全部记录（含规则级跳过的——溯源"被考虑但未适用"）
            all_rule_ids.append(rule.rule_id)

            # D4: check overrides first (e.g., n=2)
            override_level = self._check_overrides(rule, parsed)
            if override_level is not None:
                level = override_level
            else:
                level = self._check_level(rule, parsed)
                if level is None:
                    # K2（2026-08-16 窗口 K2，A2 修复）：规则级跳过——
                    # choice 未命中该规则任何 level 词表 → 该规则不适用
                    # （未知方法 ≠ 错误，不再兜底 L0"危险"）；不贡献评级/证据
                    continue

            applicable.append(rule)
            # A3 FIX: < not > (take lowest/strictest)
            if level < best_level:
                best_level = level

        if not applicable:
            # K2: 全部匹配规则均未识别 choice（或全部未匹配）→ 决策 -1 无法评估
            return self._build_unrecognized(parsed, [r.rule_id for r in rules])

        # D5: check if conditions_when_acceptable could lift the score
        best_level = self._check_conditional_acceptability(rules, parsed, best_level)

        return self._build_score(parsed, all_rule_ids, applicable, best_level)

    def _check_level(self, rule: Rule, parsed: ParsedStep) -> int | None:
        """Check which scoring level the agent's choice falls into.

        B6 FIX: normalize choices for fuzzy matching.
        D3 FIX: Level 3 and Level 4 are merged for MVP.
            Only check methods in Level 3. Level 4 reserved for
            LLM-based rationale assessment (v0.2).
        K2 FIX（2026-08-16 窗口 K2，A2 修复）: choice 未命中任何 level 词表
        → 返回 None（规则级跳过：该规则不适用），由 evaluate() 汇总——
        全部规则跳过 → 决策 -1；不再兜底 L0"危险"。
        """
        choice = self._normalize_choice(parsed.original.choice)

        # Check Level 3 first (merged 3+4 for MVP)
        methods_3 = [self._normalize_choice(m) for m in rule.scoring.level_3.methods]
        if choice in methods_3:
            return 3

        # Check Level 2
        methods_2 = [self._normalize_choice(m) for m in rule.scoring.level_2.methods]
        if choice in methods_2:
            return 2

        # Check Level 1
        methods_1 = [self._normalize_choice(m) for m in rule.scoring.level_1.methods]
        if choice in methods_1:
            return 1

        # Check Level 0
        methods_0 = [self._normalize_choice(m) for m in rule.scoring.level_0.methods]
        if choice in methods_0:
            return 0

        # Check Level 4 as well (for future use)
        methods_4 = [self._normalize_choice(m) for m in rule.scoring.level_4.methods]
        if choice in methods_4:
            return 4

        # K2: Completely unrecognized → rule-level skip (None), NOT Level 0
        return None

    def _check_overrides(self, rule: Rule, parsed: ParsedStep) -> int | None:
        """D4: Check special override conditions (e.g., n=2).

        M2.5（窗口 M，2026-08-16）：**键映射修复**——不再硬编码
        ``n_replicates``：override 条件文本（``"n_patients <= 2"`` 等）解析出
        键名，按规则配置取键（G1.1 用 n_patients / M1.1 用 n_replicates，
        各规则自声明）；**n=0 falsy 漏判修复**（fix-tracking A3）：显式
        ``key in ctx`` 判定 + 数值强转比较，0 正确触发；键缺失 → 不触发
        （若该键 fail-closed 且被引用，已由 context_guard 未验证层拦截）。

        Returns override level or None if no override applies.
        """
        override = rule.scoring.override_n2
        if override and "condition" in override:
            cond = override["condition"]
            m = re.match(r"(\w+)\s*(<=|<)\s*(\d+\.?\d*)", str(cond).strip())
            if m:
                key, op, val_str = m.groups()
                ctx = parsed.normalized_context
                if key in ctx and ctx[key] is not None:
                    try:
                        actual = float(ctx[key])
                    except (TypeError, ValueError):
                        return None  # 非数值 → 不触发（类型问题由 context_guard 处理）
                    limit = float(val_str)
                    if (op == "<=" and actual <= limit) or (op == "<" and actual < limit):
                        return 0  # All methods → Level 0 for n<=2
        return None

    def build_override_score(
        self, parsed: ParsedStep, override_level: int, rules: list[Rule]
    ) -> DecisionScore:
        """M2.5（窗口 M）：候选级 override 命中（如 n<=2 → 所有方法 L0）。

        由 context_guard.score_decision 调用（D4 override 在约束门外独立生效）；
        保留 matched_rules 溯源（已匹配规则）+ 证据引用（与 _build_score 同款），
        explanation 注明 override 语义。
        """
        score = self._build_score(
            parsed,
            [r.rule_id for r in rules],
            rules,
            override_level,
        )
        score.explanation += (
            " — override: the observed sample size is too small to reliably "
            "estimate biological variability; the conclusion is downgraded to "
            "reporting fold change only, without statistical inference."
        )
        return score

    def _check_conditional_acceptability(
        self, rules: list[Rule], parsed: ParsedStep, current_level: int
    ) -> int:
        """D5 FIX（2026-08-13, refactor-plan-v1.1 A1/A2/A4）: 移除无条件提升。

        原实现（审计 H1）只检查 lvl.conditions_when_acceptable 文本是否存在、
        choice 是否命中该 level 的 methods，满足即 min(current_level+1, 3)
        无条件提升一级——条件文本从未被解析校验，导致：
          - TPM/LogNormalize/hard_threshold 等"有风险"方法被虚增为"可接受/正确"
          - skip/fail-open 缺键场景下等级反而抬高（最严规则被跳过）
        结构化条件校验将在阶段 1 本体落地时实现（ontology context_schema +
        missing 三档语义）。当前返回原等级，保证分数不虚增。
        """
        return current_level

    def _normalize_choice(self, choice: str) -> str:
        """B6 FIX: Normalize agent choices for fuzzy matching.

        Handles: 'DESeq2 (Wald test)' → 'deseq2'
                'Benjamini-Hochberg' → 'bh'
                'no_correction' → 'no_correction'
        """
        c = choice.lower().strip()
        # Remove parenthetical qualifiers
        c = re.split(r'[\(（]', c)[0].strip()
        # Replace spaces/underscores/hyphens with single separator
        c = re.sub(r'[\s_\-]+', '_', c)
        # Common abbreviations
        aliases = {
            "bh": "bh",
            "benjamini_hochberg": "bh",
            "fdr": "bh",
            "by": "by",
            "benjamini_yekutieli": "by",
            "bonferroni": "bonferroni",
            "no_correction": "no_correction",
            "uncorrected": "no_correction",
            "tmm": "tmm",
            "rle": "rle",
            "deseq2_median_of_ratios": "deseq2_median_of_ratios",
            "tpm": "tpm",
            "fpkm": "fpkm",
            "rpkm": "rpkm",
            "cpm": "cpm",
            "deseq2": "deseq2",
            "edger": "edger",
            "limma_voom": "limma_voom",
            "limma_trend": "limma_trend",
            "wilcoxon": "wilcoxon_rank_sum",
            "wilcoxon_rank_sum": "wilcoxon_rank_sum",
            "ttest": "ttest_equal_variance",
            "t_test": "ttest_equal_variance",
            "student_t": "ttest_equal_variance",
            # K2（2026-08-16）：t-test 家族拼写别名补齐（B6 归一化语义）——
            # Student's t-test 的常见拼写变体归一到词表命名，避免误落
            # "未识别"（pan_error D3 'Student_t_test' 语义 = M1.1 L0 t-test 家族）
            "student_t_test": "ttest_equal_variance",
            "students_t_test": "ttest_equal_variance",
            "student_s_t_test": "ttest_equal_variance",
            "student's_t_test": "ttest_equal_variance",
            "welch_t_test": "ttest_unequal_variance",
            "welch_s_t_test": "ttest_unequal_variance",
            "welch's_t_test": "ttest_unequal_variance",
        }
        return aliases.get(c, c)

    def _build_score(
        self, parsed: ParsedStep, rule_ids: list[str],
        rules: list[Rule], level: int
    ) -> DecisionScore:
        """Build DecisionScore with evidence ordered by confidence."""
        numeric = LEVEL_TO_SCORE.get(level, 0.5)

        # Sort evidence by confidence rank, then take top 5
        conf_rank = {"L-Confirmed": 5, "L-Consensus": 4, "L-Evidenced": 3,
                     "L-Emerging": 2, "L-Anecdotal": 1}
        all_evidence = []
        for rule in rules:
            for ev in rule.evidence:
                all_evidence.append((conf_rank.get(ev.confidence, 0), ev))

        all_evidence.sort(key=lambda x: x[0], reverse=True)
        evidence_citations = []
        for _, ev in all_evidence[:5]:
            if ev.pmid:
                # 首选 PubMed — 国内可访问 (doi.org 被 GFW 阻断)
                evidence_citations.append(
                    f"[{ev.confidence}] {ev.title} — "
                    f"**[PMID: {ev.pmid}](https://pubmed.ncbi.nlm.nih.gov/{ev.pmid}/)**"
                )
            elif ev.url:
                # 其次任意直链 (如 JSTOR)
                evidence_citations.append(
                    f"[{ev.confidence}] {ev.title} — "
                    f"**[查看原文]({ev.url})**"
                )
            elif ev.doi:
                # fallback: DOI 链接
                evidence_citations.append(
                    f"[{ev.confidence}] {ev.title} — "
                    f"**[DOI: {ev.doi}](https://doi.org/{ev.doi})**"
                )
            else:
                evidence_citations.append(f"[{ev.confidence}] {ev.title}")

        # Alternatives from higher levels
        alternatives = []
        if level <= 1:
            for lvl_key in ["level_3", "level_2"]:
                lvl = getattr(rules[0].scoring, lvl_key, None) if rules else None
                if lvl:
                    alternatives.extend(lvl.methods[:3])

        return DecisionScore(
            step_id=parsed.step_id,
            decision_type=parsed.decision_type,
            agent_choice=parsed.original.choice,
            agent_rationale=parsed.original.rationale,
            matched_rules=rule_ids,
            level=level,
            numeric_score=numeric,
            explanation=LEVEL_LABELS.get(level, "未知"),
            evidence_citations=evidence_citations,
            alternatives=alternatives,
            reward_signal=numeric,  # C3: documented as experimental/uncalibrated
        )

    def _no_rule_matched(self, parsed: ParsedStep) -> DecisionScore:
        """Sentinel: decision type has no active rules."""
        return DecisionScore(
            step_id=parsed.step_id,
            decision_type=parsed.decision_type,
            agent_choice=parsed.original.choice,
            agent_rationale=parsed.original.rationale,
            matched_rules=[],
            level=-1,
            numeric_score=0.5,
            explanation="Unable to evaluate: no applicable rule matched.",
            evidence_citations=[],
            alternatives=[],
            reward_signal=0.5,
        )

    def _build_unrecognized(
        self, parsed: ParsedStep, rule_ids: list[str]
    ) -> DecisionScore:
        """K2（2026-08-16 窗口 K2，A2 修复）: 全部匹配规则均未识别 choice。

        规则级跳过语义：匹配规则存在（condition 命中）但 choice 未命中任何
        level 词表 → 该规则不适用；全部规则不适用 → 决策 **-1 无法评估**
        （未知方法 ≠ 错误，不再兜底 L0"危险"）。matched_rules 保留规则 id
        供溯源（规则被考虑但未适用），-1 不参与聚合/检出/reward（mask）。
        """
        return DecisionScore(
            step_id=parsed.step_id,
            decision_type=parsed.decision_type,
            agent_choice=parsed.original.choice,
            agent_rationale=parsed.original.rationale,
            matched_rules=rule_ids,
            level=-1,
            numeric_score=LEVEL_TO_SCORE[-1],
            explanation=(
                "Unable to evaluate: the matching rules do not cover the declared "
                "method name. This does not mean the method is wrong — the rule "
                "library maps levels by method name, and this name is not in the "
                "mapping; the point is therefore treated as unevaluable, yielding "
                "no quality conclusion and altering no judgment."
            ),
            evidence_citations=[],
            alternatives=[],
            reward_signal=LEVEL_TO_SCORE[-1],
        )

    def evaluate_all_rules(
        self, parsed: ParsedStep, rules: list[Rule]
    ) -> dict[str, int]:
        """Public method: evaluate against ALL rules individually.
        Returns {rule_id: level} for conflict detection.
        Unlike evaluate(), this does NOT take the strictest — it returns
        each rule's independent evaluation.
        K2: 未识别 choice 的规则（_check_level → None）**跳过**（不参与冲突
        检测——该规则不适用，无评级可冲突）。
        """
        result = {}
        for rule in rules:
            level = self._check_level(rule, parsed)
            if level is not None:
                result[rule.rule_id] = level
        return result
