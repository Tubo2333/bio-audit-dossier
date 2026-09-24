"""交叉验证器（窗口 C / C3；设计 §三/§七；refactor-plan-v1.1 B4/B5）。

M1 声明 vs M3 事实的四类判定（验收项 9）：:

    一致       M1 声明的 choice 被 M3 已验证实例证实
    虚报       M1 声明但 M3 无对应执行证据（含 choice 不符 / 仅未定证据）
    漏报       M3 执行但 M1 未声明 → 自动补入审计（verdict final，来源 M3解析）
    未验证     预期决策点双方都无证据（绝不伪造）

对齐键（B5）：(session_id, decision_type) 为主键；双方 step_id 相同时叠加；
同类型多实例（迭代调参）按 instance_index 建模，operative = 最后实例——
声明命中已被取代的实例 → 一致但标注"被后续迭代取代"；未命中任何实例 → 虚报。

verdict 联动（B4）：一致 → provisional→final；虚报 → 撤销（revoked）；
漏报补入 → 新建 final verdict（事实证据链）。**报告与 reward 只消费 final**：
:func:`final_trajectory` 仅聚合 final verdict 的决策。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from bioaudit.capture.models import (
    PROVENANCE_SOURCE_EXPECTED,
    PROVENANCE_SOURCE_M1,
    PROVENANCE_SOURCE_M3,
    CapturedDecision,
    DecisionProvenance,
    ParseResult,
    UncertainCandidate,
)
from bioaudit.capture.verdict import VerdictRecord, VerdictStatus, VerdictStore

#: 四类判定（验收项 9）
STATUS_CONSISTENT = "consistent"           # 一致
STATUS_FALSE_POSITIVE = "false_positive"   # 虚报（声明未执行）
STATUS_FALSE_NEGATIVE = "false_negative"   # 漏报（执行未声明 → 自动补入）
STATUS_UNVERIFIED = "unverified"           # 未验证（双方都无）

ALIGNMENT_STATUSES = frozenset({
    STATUS_CONSISTENT, STATUS_FALSE_POSITIVE, STATUS_FALSE_NEGATIVE, STATUS_UNVERIFIED,
})

#: M1.1（窗口 M，2026-08-16）：预期决策缺失补入统计键（stats 独立计数，
#: 与四类判定并列——补入决策的对齐状态仍为 unverified，但补入行为单独可数）
STATUS_EXPECTED_ADDED = "expected_added"


class AlignmentRecord(BaseModel):
    """单条决策点对齐记录（M1 vs M3）。"""

    decision_type: str
    status: str  # consistent / false_positive / false_negative / unverified
    m1: Optional[dict] = None  # 声明（含 verdict_id）
    m3: Optional[dict] = None  # 证实实例（operative）
    instances: list[dict] = Field(default_factory=list)  # M3 同类型多实例（B5）
    detail: str = ""
    auto_added: bool = False  # 漏报 → 自动补入
    # M1.1（窗口 M）：预期决策点缺失 → 补入 provenance=expected（该做没做）。
    # 与虚报撤销/未验证并存于同一对齐记录（不新增行），补入行为经
    # ``added_decisions``（verdict final，来源 expected）落最终轨迹。
    expected_added: bool = False


class CrossValidationResult(BaseModel):
    """交叉验证输出：对齐记录 + 补入决策 + verdict 更新 + 统计。"""

    session_id: str
    act: Optional[str] = None
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    alignments: list[AlignmentRecord] = Field(default_factory=list)
    added_decisions: list[dict] = Field(default_factory=list)  # 漏报补入（含 verdict_id）
    verdict_updates: list[dict] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    def decisions(self) -> list[dict]:
        """**final-only 消费**（B4）：补入决策 + 一致声明的决策（仅 final）。"""
        return list(self.added_decisions)


def _to_captured(decision: CapturedDecision | dict, source: str) -> CapturedDecision:
    """统一为 CapturedDecision（dict → 模型；缺 provenance 时按来源补）。"""
    if isinstance(decision, CapturedDecision):
        return decision
    provenance = decision.get("provenance") or {
        "source": source,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "evidence": decision.get("evidence", "M1 声明"),
    }
    return CapturedDecision(
        step_id=str(decision.get("step_id", "")),
        decision_type=str(decision.get("decision_type", "")),
        choice=str(decision.get("choice", "")),
        rationale=str(decision.get("rationale", "")),
        context=dict(decision.get("context") or {}),
        context_trust=dict(decision.get("context_trust") or {}),
        unverified_keys=list(decision.get("unverified_keys") or []),
        tool_call=decision.get("tool_call"),
        code_snippet=decision.get("code_snippet"),
        provenance=DecisionProvenance(**provenance),
        instance_index=int(decision.get("instance_index", 1)),
        paradigm=decision.get("paradigm"),
    )


class CrossValidator:
    """M1/M3 交叉验证器（四类判定 + verdict 联动）。"""

    def __init__(self, act: Optional[str] = None):
        self.act = act

    def validate(
        self,
        m1_declarations: list[CapturedDecision | dict],
        m3: ParseResult | list[CapturedDecision | dict],
        *,
        session_id: str = "crossval",
        expected_types: Optional[list[str]] = None,
        expected_context: Optional[dict] = None,
        verdict_store: Optional[VerdictStore] = None,
    ) -> CrossValidationResult:
        """执行交叉验证。

        Parameters
        ----------
        m1_declarations : list
            M1 声明决策（含 ``verdict_id`` 键可联动 verdict 状态）。
        m3 : ParseResult | list
            M3 解析产物（notebook → ParseResult；或候选列表）。
        expected_types : list[str] | None
            预期决策点（M1.1：评测配置 per 范式×平台；调用方先应用 B7 豁免）。
            双方都无证据 → 未验证 + **补入 provenance=expected 参与评分**
            （该做没做；choice 优先取 M1 已撤销声明，无声明 → not_performed）。
        expected_context : dict | None
            补入决策的会话事实（declared + 数据元数据并集）；M1 声明存在时
            以其 context 优先（Agent 声明的事实，不伪造）。
        verdict_store : VerdictStore | None
            提供时联动 verdict 状态位（一致→final、虚报→revoked、漏报→新建 final、
            预期补入→新建 final（来源 expected））。
        """
        m1_list = [_to_captured(d, PROVENANCE_SOURCE_M1) for d in m1_declarations]
        if isinstance(m3, ParseResult):
            m3_candidates = list(m3.candidates)
            m3_uncertain: list[UncertainCandidate] = list(m3.uncertain)
        else:
            m3_candidates = [_to_captured(d, PROVENANCE_SOURCE_M3) for d in m3]
            m3_uncertain = []

        m1_by_type: dict[str, list[CapturedDecision]] = {}
        for c in m1_list:
            m1_by_type.setdefault(c.decision_type, []).append(c)
        m3_by_type: dict[str, list[CapturedDecision]] = {}
        for c in m3_candidates:
            m3_by_type.setdefault(c.decision_type, []).append(c)
        uncertain_by_type: dict[str, list[UncertainCandidate]] = {}
        for u in m3_uncertain:
            uncertain_by_type.setdefault(u.decision_type, []).append(u)

        vid_lookup = {
            (str(d.get("step_id")), str(d.get("decision_type"))): d["verdict_id"]
            for d in m1_declarations if isinstance(d, dict) and d.get("verdict_id")
        }
        alignments: list[AlignmentRecord] = []
        added: list[dict] = []
        updates: list[dict] = []
        expected = list(expected_types or [])
        expected_set = set(expected)
        expected_added_count = {"n": 0}
        expected_done: set[str] = set()

        def _add_expected(tid: str, decls: list[CapturedDecision]) -> None:
            """预期决策缺失补入（M1.1：该做没做；choice 优先取 M1 已撤销声明）。

            只落 ``added_decisions``（verdict final，来源 expected）——对齐记录
            由调用处标记 ``expected_added=True``，不新增对齐行。
            """
            if tid in expected_done:
                return
            expected_done.add(tid)
            expected_added_count["n"] += 1
            declared = decls[-1] if decls else None
            choice = declared.choice if declared else "not_performed"
            ctx = dict(declared.context) if declared else dict(expected_context or {})
            decision = {
                "step_id": declared.step_id if declared else f"expected_{tid}",
                "decision_type": tid,
                "choice": choice,
                "rationale": (
                    declared.rationale
                    if declared
                    else "预期决策缺失补入（expected_types；该做没做，B7 未豁免）"
                ),
                "context": ctx,
            }
            if verdict_store is not None:
                verdict = verdict_store.create(
                    session_id=session_id,
                    step_id=decision["step_id"],
                    decision_type=tid,
                    choice=choice,
                    paradigm=self.act or "scrna",
                    provenance_source=PROVENANCE_SOURCE_EXPECTED,
                    score_snapshot={
                        "context": ctx,
                        "explanation": decision["rationale"],
                    },
                    status=VerdictStatus.FINAL,
                    reason="预期决策缺失补入（expected_types）",
                )
                decision["verdict_id"] = verdict.verdict_id
                updates.append({
                    "verdict_id": verdict.verdict_id, "status": "final",
                    "decision_type": tid, "reason": "预期决策缺失补入",
                })
            added.append(decision)

        all_types = sorted(
            set(m1_by_type) | set(m3_by_type) | set(uncertain_by_type) | expected_set
        )
        for tid in all_types:
            m1s = m1_by_type.get(tid, [])
            m3s = sorted(m3_by_type.get(tid, []), key=lambda c: c.instance_index)
            unc = uncertain_by_type.get(tid, [])

            # ── 漏报：M3 执行但 M1 未声明 → 自动补入（verdict final）──
            m1_choices = {c.choice for c in m1s}
            for inst in m3s:
                if inst.choice in m1_choices:
                    continue
                detail = f"M3 执行 {inst.choice!r}（{inst.tool_call}）但 M1 未声明 → 自动补入"
                if verdict_store is not None:
                    verdict = verdict_store.create(
                        session_id=session_id,
                        step_id=inst.step_id,
                        decision_type=inst.decision_type,
                        choice=inst.choice,
                        paradigm=self.act or inst.paradigm or "scrna",
                        provenance_source=PROVENANCE_SOURCE_M3,
                        score_snapshot={},
                        status=VerdictStatus.FINAL,
                        reason=f"漏报自动补入（M3 事实证据链）：{inst.tool_call}",
                    )
                    updates.append({
                        "verdict_id": verdict.verdict_id, "status": "final",
                        "decision_type": tid, "reason": "漏报自动补入",
                    })
                decision = inst.to_decision()
                if verdict_store is not None:
                    decision["verdict_id"] = verdict.verdict_id
                added.append(decision)
                alignments.append(AlignmentRecord(
                    decision_type=tid, status=STATUS_FALSE_NEGATIVE,
                    m3=inst.model_dump(mode="json"), instances=[inst.model_dump(mode="json")],
                    detail=detail, auto_added=True,
                ))

            # ── 未验证 / 预期补入：预期决策点双方都无（不伪造）──
            if not m1s and not m3s:
                if tid in expected_set:
                    # M1.1：预期决策点缺失 → 补入 provenance=expected（该做没做）
                    alignments.append(AlignmentRecord(
                        decision_type=tid, status=STATUS_UNVERIFIED,
                        detail=(
                            "预期决策点双方都无证据 → 未验证 + 补入 "
                            "provenance=expected（该做没做）"
                        ),
                        expected_added=True,
                    ))
                    _add_expected(tid, [])
                continue

            # ── 虚报 / 一致：逐条 M1 声明 vs M3 事实 ──
            operative = m3s[-1] if m3s else None
            instances = [c.model_dump(mode="json") for c in m3s]
            for decl in m1s:
                verdict_id = vid_lookup.get((decl.step_id, decl.decision_type))
                if not m3s:
                    # 该类型无任何已验证实例：仅未定证据 → 声明无法证实
                    if unc:
                        detail = (
                            f"M3 仅有未定证据（{unc[0].evidence}；{unc[0].reason}），"
                            f"声明 {decl.choice!r} 无法被事实佐证 → 虚报"
                        )
                    else:
                        detail = f"M3 无 {tid} 任何执行证据，声明 {decl.choice!r} 未执行 → 虚报"
                    if verdict_store is not None and verdict_id:
                        verdict_store.revoke_matching(
                            session_id, decl.step_id, decl.decision_type, detail
                        )
                        updates.append({
                            "verdict_id": verdict_id, "status": "revoked",
                            "decision_type": tid, "reason": "虚报（声明未执行）",
                        })
                    alignment = AlignmentRecord(
                        decision_type=tid, status=STATUS_FALSE_POSITIVE,
                        m1=decl.model_dump(mode="json"), instances=instances, detail=detail,
                    )
                    # M1.1：该类型为预期决策点且 M3 无执行证据 → 虚报撤销之外，
                    # 按"该做没做"补入 provenance=expected（choice 取 Agent 声明）
                    if tid in expected_set:
                        alignment.expected_added = True
                        alignment.detail += (
                            "；预期决策点缺失 → 补入 provenance=expected（该做没做）"
                        )
                        _add_expected(tid, m1s)
                    alignments.append(alignment)
                    continue

                matched = next((c for c in m3s if c.choice == decl.choice), None)
                if matched is None:
                    executed = ", ".join(sorted({c.choice for c in m3s})) or "（无已验证实例）"
                    detail = (
                        f"M3 实际执行 {executed}，与声明 {decl.choice!r} 不符 → 虚报"
                        + (f"；未定证据: {unc[0].reason}" if unc else "")
                    )
                    if verdict_store is not None and verdict_id:
                        verdict_store.revoke_matching(
                            session_id, decl.step_id, decl.decision_type, detail
                        )
                        updates.append({
                            "verdict_id": verdict_id, "status": "revoked",
                            "decision_type": tid, "reason": "虚报（声明与事实不符）",
                        })
                    alignments.append(AlignmentRecord(
                        decision_type=tid, status=STATUS_FALSE_POSITIVE,
                        m1=decl.model_dump(mode="json"), instances=instances, detail=detail,
                    ))
                    continue

                # 一致：choice 被事实证实（参数级粒度对照，B5）
                param_diffs = self._param_level_diff(decl, matched)
                if matched is not operative:
                    detail = (
                        f"声明 {decl.choice!r} 已执行但被后续迭代取代"
                        f"（operative={operative.choice!r}，实例 {matched.instance_index} → "
                        f"{operative.instance_index}）"
                    )
                    status = STATUS_CONSISTENT
                elif param_diffs:
                    detail = f"choice 一致；参数级差异: {param_diffs}"
                    status = STATUS_CONSISTENT
                else:
                    detail = f"声明与 M3 事实一致（{matched.tool_call}）"
                    status = STATUS_CONSISTENT
                if verdict_store is not None and verdict_id:
                    verdict_store.transition(verdict_id, VerdictStatus.FINAL, detail)
                    updates.append({
                        "verdict_id": verdict_id, "status": "final",
                        "decision_type": tid, "reason": "交叉验证一致",
                    })
                alignments.append(AlignmentRecord(
                    decision_type=tid, status=status,
                    m1=decl.model_dump(mode="json"),
                    m3=matched.model_dump(mode="json"),
                    instances=instances, detail=detail,
                ))

        n_expected = expected_added_count["n"]
        result = CrossValidationResult(
            session_id=session_id,
            act=self.act,
            alignments=alignments,
            added_decisions=added,
            verdict_updates=updates,
            stats={
                STATUS_CONSISTENT: sum(
                    1 for a in alignments if a.status == STATUS_CONSISTENT
                ),
                STATUS_FALSE_POSITIVE: sum(
                    1 for a in alignments if a.status == STATUS_FALSE_POSITIVE
                ),
                STATUS_FALSE_NEGATIVE: sum(
                    1 for a in alignments if a.status == STATUS_FALSE_NEGATIVE
                ),
                STATUS_UNVERIFIED: sum(
                    1 for a in alignments if a.status == STATUS_UNVERIFIED
                ),
                STATUS_EXPECTED_ADDED: n_expected,
            },
        )
        return result

    @staticmethod
    def _param_level_diff(
        m1: CapturedDecision, m3: CapturedDecision
    ) -> list[str]:
        """参数级粒度对照（B5）：双方 context 共享键的值差异列表。

        元数据/声明来源的键双方同源（值应一致），因此对比所有共享键即可；
        差异聚焦调用参数（如 min_genes 200 vs 500）。
        """
        diffs: list[str] = []
        for key in sorted(set(m1.context) & set(m3.context)):
            if m1.context.get(key) != m3.context.get(key):
                diffs.append(f"{key}: M1={m1.context.get(key)!r} vs M3={m3.context.get(key)!r}")
        return diffs


def final_trajectory(
    store: VerdictStore,
    session_id: str,
    *,
    act: Optional[str] = None,
) -> dict:
    """**报告与 reward 只消费 final**（B4）：聚合会话内 final verdict 的决策。

    返回轨迹 v2 对象（version=2 + provenance.source=capture + decisions），
    可直接喂给 ``run_audit``。revoked/provisional 一律不进入。
    """
    from bioaudit.models.trajectory import (
        TRAJECTORY_SCHEMA_VERSION,
        TrajectoryProvenance,
    )

    records: list[VerdictRecord] = store.final_verdicts(session_id)
    decisions = []
    for r in records:
        snapshot = r.score_snapshot
        decisions.append({
            "step_id": r.step_id,
            "decision_type": r.decision_type,
            "choice": r.choice,
            "rationale": (
                snapshot.get("explanation", "")
                if snapshot else f"verdict {r.verdict_id}（final）"
            ),
            "context": {
                k: v for k, v in (snapshot.get("context") or {}).items()
                if not k.startswith("_")
            } if snapshot else {},
        })
    provenance = TrajectoryProvenance(
        source="capture",
        migrated_from=None,
        note=f"cross-validation final-only（session {session_id}；B4 报告/reward 只消费 final）",
    ).model_dump()
    return {
        "version": TRAJECTORY_SCHEMA_VERSION,
        "trajectory_id": f"session_{session_id}",
        "act": act,
        "provenance": provenance,
        "decisions": decisions,
    }


__all__ = [
    "STATUS_CONSISTENT",
    "STATUS_FALSE_POSITIVE",
    "STATUS_FALSE_NEGATIVE",
    "STATUS_UNVERIFIED",
    "STATUS_EXPECTED_ADDED",
    "ALIGNMENT_STATUSES",
    "AlignmentRecord",
    "CrossValidationResult",
    "CrossValidator",
    "final_trajectory",
]
