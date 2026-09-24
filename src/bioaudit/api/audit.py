"""审计 API 三入口（B3 契约完成版；v1 蓝图：run_audit / audit_decision / match_details）。

自 fullflow-demo/src/orchestration/graph.py 迁移 7 步管道，B1/B2/B3 变更：
- 路径全部包内锚定（bioaudit.paths），零 cwd 依赖（F7）
- **B3 契约**（refactor-plan-v1.1 B1/B2；audit-report A5/A7/A15）：
  * 三入口输入全部 pydantic schema 校验，非法输入**显式报错**（错误码，
    :class:`bioaudit.errors.BioAuditError`），不静默降级——
    如 act 未知不再静默回退全量规则，而是 paradigm-not-found；
  * ``audit_decision`` **必填 paradigm 参数**（deg_method 同名异构消歧）；
  * ``human_overrides`` 校验：int 且 -1..4，非法**拒绝**并记录
    ``invalid_override_rejected`` 事件（A7）；
  * 内部异常统一包装为 BioAuditError 或写入 state["error_code"]，不裸抛；
  * 错误码体系与契约文档：docs/api-contract.md（B3 验收项 4/5）。
- 支持按范式（act）加载规则集：deg / pan / scrna（与 golden 复算口径一致）；
  不传 act 则加载全量规则（C2 去重后 39 唯一规则）

返回 dict（与旧 run_audit state 兼容）：
session_id / parsed_steps / matched_rules / step_scores / conflicts /
dimension_scores / trajectory_score / eval_verdict / critical_issues /
error_chains / report / error / error_code（仅管道内部失败时非空）
"""

import logging
import uuid

from bioaudit.api.contract import (
    parse_trajectory_payload,
    validate_human_overrides,
    validate_paradigm,
)
from bioaudit.engine.aggregator import ScoreAggregator
from bioaudit.engine.conflict_detector import ConflictDetector
from bioaudit.engine.error_tracer import ErrorPropagationTracer
from bioaudit.engine.evaluator import LEVEL_TO_SCORE, RuleEvaluator
from bioaudit.engine.matcher import RuleMatcher
from bioaudit.errors import BioAuditError, ErrorCode, validation_error
from bioaudit.models.decision import ParsedStep
from bioaudit.models.score import DecisionScore
from bioaudit.paths import rules_dir_for
from bioaudit.storage.event_store import AuditEvent, EventStore, EventWriteError
from bioaudit.storage.rule_registry import RuleRegistry

logger = logging.getLogger(__name__)


def _append_event(event_store: EventStore, state: dict, event: AuditEvent) -> None:
    """C5（F11）：事件写入显式告警——写失败不再静默丢写。

    失败 → EventWriteError → 记入 state["event_store_warnings"]（进审计输出），
    管道主流程继续（告警可见，审计者也可审计）。
    """
    try:
        event_store.append(event)
    except EventWriteError as exc:
        warning = f"事件写入失败（F11 显式告警）: {exc}"
        state.setdefault("event_store_warnings", []).append(warning)
        logger.error(warning)


def _registry_for(act: str | None = None) -> RuleRegistry:
    """按范式建 registry；None → 全量规则（39 唯一）。act 已由契约层校验。"""
    registry = RuleRegistry(rules_dir_for(act))
    registry.load_all()
    return registry


def _record_input_rejected(event_store: EventStore, code: str, message: str) -> None:
    """输入校验失败事件（C5 可观测性：审计者也要可审计）。"""
    try:
        event_store.append(AuditEvent(
            event_type="input_rejected", node="input_validation",
            payload={"error_code": code, "message": message},
        ))
    except Exception:  # 事件写入失败不得掩盖主错误
        logger.warning("EventStore 记录 input_rejected 失败（非致命）")


def _reward_block(state: dict, act: str | None, snapshot: dict) -> dict:
    """E4.10：从已算出的 step_scores 生成报告 reward 块（纯函数，不重跑管道）。

    失败时返回降级块（含 error 说明），绝不使审计主流程失败——reward 是
    外围输出层，报告主结构（分数/verdict）永远先行。
    """
    try:
        from bioaudit.reward.api import report_reward_block

        return report_reward_block(
            step_scores=state.get("step_scores", []),
            act=act, recipe="B", snapshot=snapshot,
        )
    except Exception as exc:  # reward 层失败不拖垮报告（外围层纪律）
        logger.warning("reward 块生成失败（非致命）: %s", exc)
        return {
            "status": "experimental_uncalibrated",
            "error": f"reward 块生成失败（非致命）: {exc}",
            "trajectory_reward": None,
            "step_rewards": [],
        }


def run_audit(
    trajectory: list[dict] | dict,
    act: str | None = None,
    profile_id: str = "default",
    session_id: str | None = None,
    human_overrides: dict | None = None,
) -> dict:
    """Run a complete 7-step audit pipeline over a trajectory of decisions.

    Parameters
    ----------
    trajectory : list[dict] | dict
        决策列表（v1），或含 ``decisions`` 键的对象（轨迹 v2，见
        ``bioaudit.models.trajectory``）；每条满足 Decision schema
        （step_id / decision_type / choice 必填；rationale / context /
        tool_call / code_snippet 可选；未知字段报错，A15）。
    act : str | None
        范式规则集："deg" | "pan" | "scrna"；None = 全量规则（39 唯一）。
        非合法范式 → ``paradigm-not-found``（不再静默回退）。
    human_overrides : dict[str, int] | None
        {step_id: level} 人工覆写；level 必须为 int 且 -1..4（A7），
        非法 → ``validation-error`` 并记录 ``invalid_override_rejected`` 事件。
    profile_id / session_id : str | None
        兼容旧管道参数。

    Returns
    -------
    dict
        完整审计状态（含 report）。管道内部失败时 state["error"] 非空且
        state["error_code"] 给出错误码（不裸抛）。

    Raises
    ------
    BioAuditError
        输入校验失败（bad-request / validation-error / paradigm-not-found）。
    """
    # ── B3 契约：输入校验（显式报错，不静默降级）──
    validate_paradigm(act)

    session_id = session_id or f"audit_{uuid.uuid4().hex[:8]}"
    event_store = EventStore()
    event_store.start_session(session_id)

    try:
        decisions = parse_trajectory_payload(trajectory)
    except BioAuditError as exc:
        _record_input_rejected(event_store, exc.code, exc.message)
        raise

    def _record_override_rejected(payload: dict) -> None:
        try:
            event_store.append(AuditEvent(
                event_type="invalid_override_rejected",
                node="input_validation", payload=payload,
            ))
        except Exception:
            logger.warning("EventStore 记录 invalid_override_rejected 失败（非致命）")

    try:
        overrides = validate_human_overrides(human_overrides, _record_override_rejected)
    except BioAuditError as exc:
        _record_input_rejected(event_store, exc.code, exc.message)
        raise

    registry = _registry_for(act)
    matcher = RuleMatcher(registry)
    evaluator = RuleEvaluator()
    aggregator = ScoreAggregator()
    conflict_detector = ConflictDetector()
    error_tracer = ErrorPropagationTracer()

    state: dict = {
        "session_id": session_id,
        "profile_id": profile_id,
        "act": act,
        "error": None,
        "error_code": None,
        "event_store_warnings": [],  # C5（F11）：事件写入失败显式告警（进报告）
    }

    # ── Step 1: Parse（输入已由 TrajectoryPayload 校验，此处仅归一化）──
    try:
        parsed_steps = []
        for decision in decisions:
            parsed, _ = matcher.match(decision)
            parsed_steps.append(parsed.model_dump())
        state["parsed_steps"] = parsed_steps
        _append_event(event_store, state, AuditEvent(
            event_type="parse_complete", node="parse",
            payload={"n_decisions": len(parsed_steps)},
        ))
    except BioAuditError:
        raise
    except Exception as exc:
        state["error"] = f"Parse failed: {exc}"
        state["error_code"] = ErrorCode.INTERNAL_ERROR
        logger.exception("parse 阶段失败")
        return state

    # ── Step 2: Match rules ──
    try:
        matched = {}
        for step_dict in state["parsed_steps"]:
            parsed = ParsedStep(**step_dict)
            rules = matcher.match_parsed(parsed)
            matched[parsed.step_id] = [r.rule_id for r in rules]
            _append_event(event_store, state, AuditEvent(
                event_type="rule_matched", node="match",
                payload={"step_id": parsed.step_id, "n_rules": len(rules),
                         "rule_ids": [r.rule_id for r in rules]},
            ))
        state["matched_rules"] = matched
    except BioAuditError:
        raise
    except Exception as exc:
        state["error"] = f"Rule matching failed: {exc}"
        state["error_code"] = ErrorCode.INTERNAL_ERROR
        logger.exception("match 阶段失败")
        return state

    # ── Step 3: Evaluate decisions（M2.4：missing 三档强制先定决策状态再求值，A5）──
    try:
        step_scores = []
        for step_dict in state["parsed_steps"]:
            parsed = ParsedStep(**step_dict)
            rule_ids = state["matched_rules"].get(parsed.step_id, [])
            # B3：匹配到的规则缺失 → rule-not-found，不再静默丢弃（不静默降级）
            rules = []
            for rid in rule_ids:
                rule = registry.get_rule(rid)
                if rule is None:
                    raise BioAuditError(
                        ErrorCode.RULE_NOT_FOUND,
                        f"规则 {rid!r} 未在注册表找到（matched_rules 引用悬空）",
                        details={"rule_id": rid, "step_id": parsed.step_id},
                    )
                rules.append(rule)

            # M2.4（窗口 M）：先按最严档位定决策状态（fail-closed 键缺失且被
            # 候选规则引用 → 未验证 level=-2），再规则求值；skip 档剔除依赖键的规则
            from bioaudit.engine.context_guard import score_decision

            candidate_rules = registry.rules_for_type(parsed.decision_type)

            override = overrides.get(parsed.step_id)
            if override is not None:
                score = score_decision(parsed, rules, candidate_rules, evaluator)
                score.level = override
                score.numeric_score = LEVEL_TO_SCORE.get(override, 0.5)
                score.explanation += f" [human override: Lvl -> {override}]"
                _append_event(event_store, state, AuditEvent(
                    event_type="human_overrode", node="evaluate",
                    payload={"step_id": parsed.step_id, "new_level": override},
                ))
            else:
                score = score_decision(parsed, rules, candidate_rules, evaluator)

            step_scores.append(score.model_dump())
            _append_event(event_store, state, AuditEvent(
                event_type="decision_scored", node="evaluate",
                payload={"step_id": parsed.step_id, "level": score.level,
                         "agent_choice": parsed.original.choice},
            ))
        state["step_scores"] = step_scores
    except BioAuditError as exc:
        state["error"] = exc.message
        state["error_code"] = exc.code
        return state
    except Exception as exc:
        state["error"] = f"Evaluation failed: {exc}"
        state["error_code"] = ErrorCode.INTERNAL_ERROR
        logger.exception("evaluate 阶段失败")
        return state

    # ── Step 4: Detect conflicts ──
    try:
        all_conflicts = []
        for step_dict in state["parsed_steps"]:
            step_id = step_dict["step_id"]
            rule_ids = state["matched_rules"].get(step_id, [])
            rules = [registry.get_rule(rid) for rid in rule_ids]
            rules = [r for r in rules if r is not None]
            if len(rules) >= 2:
                parsed = ParsedStep(**step_dict)
                scores_per_rule = evaluator.evaluate_all_rules(parsed, rules)
                conflicts = conflict_detector.detect(
                    step_id, rules, scores_per_rule
                )
                all_conflicts.extend(conflicts)
                for c in conflicts:
                    _append_event(event_store, state, AuditEvent(
                        event_type="conflict_detected",
                        node="detect_conflicts", payload=c,
                    ))
        state["conflicts"] = all_conflicts
    except Exception as exc:
        logger.warning(f"Conflict detection failed (non-fatal): {exc}")
        state["conflicts"] = []

    # ── Step 5: Aggregate ──
    try:
        scores = [DecisionScore(**s) for s in state["step_scores"]]
        agg = aggregator.aggregate(scores)
        state["dimension_scores"] = agg.dimension_scores
        state["trajectory_score"] = agg.trajectory_score
        state["eval_verdict"] = agg.verdict
        state["critical_issues"] = agg.critical_issues
        _append_event(event_store, state, AuditEvent(
            event_type="aggregation_complete", node="aggregate",
            payload={"trajectory_score": agg.trajectory_score,
                     "verdict": agg.verdict},
        ))
    except Exception as exc:
        state["error"] = f"Aggregation failed: {exc}"
        state["error_code"] = ErrorCode.INTERNAL_ERROR
        logger.exception("aggregate 阶段失败")
        return state

    # ── Step 6: Trace error propagation ──
    try:
        scores = [DecisionScore(**s) for s in state["step_scores"]]
        chains = error_tracer.trace(scores)
        state["error_chains"] = [c.model_dump() for c in chains]
        _append_event(event_store, state, AuditEvent(
            event_type="propagation_traced", node="trace",
            payload={"n_error_chains": len(chains)},
        ))
    except Exception as exc:
        logger.warning(f"Error tracing failed (non-fatal): {exc}")
        state["error_chains"] = []

    # ── Step 7: Generate report（C1 三元组快照：engine + ruleset + ontology，B5 正式绑定）──
    try:
        from bioaudit.report import current_snapshot

        snapshot = current_snapshot()  # B5: ruleset_version 读 ruleset.json；ontology 读本体
        report = {
            "audit_id": state["session_id"],
            "profile": state["profile_id"],
            "act": act,
            "engine_version": snapshot.engine_version,
            "ruleset_version": snapshot.ruleset_version,
            "ontology_version": snapshot.ontology_version,
            "snapshot": snapshot.as_dict(),  # 三元组完整快照（C1/P2 可复现性底线）
            "n_decisions": len(state.get("parsed_steps", [])),
            "n_rules_matched": sum(
                len(v) for v in state.get("matched_rules", {}).values()
            ),
            "trajectory_score": state.get("trajectory_score", 0),
            "verdict": state.get("eval_verdict", "unknown"),
            "critical_issues": state.get("critical_issues", []),
            "dimension_scores": state.get("dimension_scores", {}),
            "error_chains": state.get("error_chains", []),
            "conflicts_needing_review": [
                c for c in state.get("conflicts", [])
                if c.get("resolution") == "NEEDS_HUMAN_REVIEW"
            ],
            # C5（F11）：事件写入失败显式告警进报告（不再静默丢写）
            "event_store_warnings": state.get("event_store_warnings", []),
            # E4.10（阶段 4）：reward 字段进报告——**experimental/未校准标注**
            # （C3 语义不变；外围输出层，不改变任何评分路径/既有字段）
            "reward": _reward_block(state, act, snapshot.as_dict()),
        }
        state["report"] = report
        _append_event(event_store, state, AuditEvent(
            event_type="report_generated", node="report", payload=report,
        ))
    except Exception as exc:
        state["error"] = f"Report generation failed: {exc}"
        state["error_code"] = ErrorCode.INTERNAL_ERROR
        logger.exception("report 阶段失败")

    return state


def audit_decision(
    decision: dict,
    paradigm: str,
    mappings_dir=None,
    session_id: str | None = None,
) -> dict:
    """单决策审计（B1/B2/B3 契约入口之一；reward 入口在阶段 4）。

    Parameters
    ----------
    decision : dict
        单条决策，满足 Decision schema（字段校验失败 → validation-error）。
    paradigm : str
        **必填**。deg / pan / scrna——deg_method 同名异构消歧（v1.1 B2）：
        同一 choice 在不同范式下按各自规则集评分；未知范式 →
        paradigm-not-found。
    session_id : str | None
        C5（可观测）：提供时把本次审计写入该 session 的事件流
        （审计者也可审计；不提供则不落盘）。

    Returns
    -------
    dict
        DecisionScore 的 model_dump()，含 matched_rules / level / explanation /
        evidence_citations / alternatives / reward_signal。

    Raises
    ------
    BioAuditError
        validation-error（决策字段非法）/ paradigm-not-found（范式未知）/
        internal-error（内部异常，不裸抛）。
    """
    from bioaudit.engine.evaluator import RuleEvaluator
    from bioaudit.engine.matcher import RuleMatcher

    # 契约：paradigm 必填（缺参 → TypeError，由签名强制）
    try:
        validate_paradigm(paradigm)
    except BioAuditError:
        raise

    try:
        from bioaudit.api.contract import AuditDecisionRequest

        request = AuditDecisionRequest(decision=decision, paradigm=paradigm)
    except BioAuditError:
        raise
    except Exception as exc:
        raise validation_error(
            "audit_decision 输入校验失败（Decision 必填: step_id/decision_type/choice）",
            exc,
        ) from exc

    # C5：session 级审计过程日志（审计者也可审计）
    event_store = None
    state_trace: dict = {"event_store_warnings": []}
    if session_id is not None:
        event_store = EventStore()
        event_store.start_session(session_id)

    try:
        registry = _registry_for(paradigm)
        matcher = RuleMatcher(registry, mappings_dir)
        evaluator = RuleEvaluator()
        parsed, rules = matcher.match(request.decision)
        # M2.4（窗口 M）：missing 三档强制（fail-closed 缺失 → 未验证 level=-2）
        from bioaudit.engine.context_guard import score_decision

        candidate_rules = registry.rules_for_type(parsed.decision_type)
        score = score_decision(parsed, rules, candidate_rules, evaluator)
        if event_store is not None:
            _append_event(event_store, state_trace, AuditEvent(
                event_type="decision_scored", node="evaluate",
                payload={
                    "step_id": parsed.step_id, "level": score.level,
                    "agent_choice": parsed.original.choice,
                    "paradigm": paradigm,
                    "matched_rules": score.matched_rules,
                },
            ))
        if state_trace.get("event_store_warnings"):
            score.explanation += (
                " [event_store_warning: "
                + "; ".join(state_trace["event_store_warnings"]) + "]"
            )
        return score.model_dump()
    except BioAuditError:
        raise
    except Exception as exc:
        raise BioAuditError(
            ErrorCode.INTERNAL_ERROR,
            f"audit_decision 内部错误: {exc}",
            details={"error_type": type(exc).__name__},
        ) from exc


def match_details(
    decision_type: str,
    context: dict,
    act: str | None = None,
) -> list[dict]:
    """透明匹配明细（UI 展示"引擎检查了什么"；规则/条件逐条评估）。

    Parameters
    ----------
    decision_type : str
        决策类型（非空字符串；非法 → validation-error）。
    context : dict
        决策上下文（必须为对象；非法 → validation-error）。
    act : str | None
        范式（deg/pan/scrna；None = 全量规则）；未知 → paradigm-not-found。

    Returns
    -------
    list[dict]
        每条候选规则的逐条条件评估明细（rule_id/title/checks/matched）。
    """
    validate_paradigm(act)
    if not isinstance(decision_type, str) or not decision_type.strip():
        raise BioAuditError(
            ErrorCode.VALIDATION_ERROR,
            f"decision_type 必须为非空字符串，收到 {decision_type!r}",
            details={"decision_type": repr(decision_type)},
        )
    if not isinstance(context, dict):
        raise BioAuditError(
            ErrorCode.VALIDATION_ERROR,
            f"context 必须为对象，收到 {type(context).__name__}",
            details={"actual_type": type(context).__name__},
        )
    try:
        registry = _registry_for(act)
        return registry.match_with_details(decision_type, context)
    except BioAuditError:
        raise
    except Exception as exc:
        raise BioAuditError(
            ErrorCode.INTERNAL_ERROR,
            f"match_details 内部错误: {exc}",
            details={"error_type": type(exc).__name__},
        ) from exc
