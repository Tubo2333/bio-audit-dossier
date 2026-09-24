"""M1Reporter — M1 主动上报（窗口 C / C1；refactor-plan-v1.1 B3）。

职责链（每次 :meth:`report`）：:

    白名单校验（session.py）→ 幂等键去重 → WAL append intent
        → audit_decision（契约：paradigm 必填，B2）→ verdict provisional 落盘
        → WAL append result → 返回即时 verdict

验收对应：
- C1.1 payload 含 decision_type/choice/context/provenance（decision dict + provenance）；
- C1.2 异常隔离：引擎/存储任何异常 → 返回 error 负载并记录日志，
  **绝不抛出**（F6 教训：hook 挂掉不能拖垮分析）；
- C1.3 白名单 + 幂等键 + WAL（B3）；
- C1.4 走通 audit_decision 契约（paradigm 必填）→ 即时 verdict（provisional）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Optional

from bioaudit.capture.models import (
    PROVENANCE_SOURCE_M1,
)
from bioaudit.capture.session import SessionWhitelist
from bioaudit.capture.verdict import VerdictStatus, VerdictStore
from bioaudit.capture.wal import WAL

logger = logging.getLogger(__name__)

#: 默认评分函数（可注入替换，测试隔离用）
def _default_audit_fn(decision: dict, paradigm: str) -> dict:
    from bioaudit.api.audit import audit_decision

    return audit_decision(decision, paradigm=paradigm)


def idempotency_key(
    session_id: str, step_id: str, decision_type: str, choice: str,
    context: Optional[dict] = None,
) -> str:
    """幂等键（B3）：session + 决策内容 + 规范化 context 的哈希。"""
    payload = json.dumps(
        {
            "session_id": session_id,
            "step_id": step_id,
            "decision_type": decision_type,
            "choice": choice,
            "context": context or {},
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


class M1Reporter:
    """M1 主动上报器（异常隔离 + 幂等 + WAL + verdict provisional）。

    Parameters
    ----------
    session_id : str
        会话 id（须过白名单；wrapper 启动时 register）。
    paradigm : str
        范式（deg/pan/scrna）——audit_decision 契约必填（B2）。
    whitelist / verdict_store / wal : 可注入（默认读环境变量）。
    audit_fn : 可注入评分函数（默认 bioaudit.api.audit_decision）。
    """

    def __init__(
        self,
        session_id: str,
        paradigm: str,
        *,
        whitelist: Optional[SessionWhitelist] = None,
        verdict_store: Optional[VerdictStore] = None,
        wal: Optional[WAL] = None,
        audit_fn: Optional[Callable[[dict, str], dict]] = None,
    ):
        self.session_id = session_id
        self.paradigm = paradigm
        self.whitelist = whitelist if whitelist is not None else SessionWhitelist()
        self.verdict_store = verdict_store if verdict_store is not None else VerdictStore()
        self.wal = wal if wal is not None else WAL()
        self.audit_fn = audit_fn if audit_fn is not None else _default_audit_fn
        self._seen: set[str] = set()  # 已处理幂等键（内存去重 + WAL 重放恢复）

    # ── 会话生命周期 ──

    def start(self) -> None:
        """崩溃恢复：重放 WAL，已完成的幂等键预载入去重集。"""
        for entry in self.wal.replay(self.session_id):
            if entry.op == "report_result":
                self._seen.add(entry.idempotency_key)

    # ── 上报 ──

    def report(self, decision: dict) -> dict:
        """上报一条 M1 决策 → 即时 verdict（provisional）。

        绝不抛出（异常隔离）：白名单/契约/引擎/存储失败均转为 error 负载。
        返回::

            {"ok": True, "verdict_id", "status": "provisional",
             "score": DecisionScore dict, "idempotency_key"}
            或 {"ok": False, "error": {...}, "isolated": True}
            或 {"ok": True, "duplicate": True, "verdict_id": <已存在>}
        """
        step_id = str(decision.get("step_id", ""))
        decision_type = str(decision.get("decision_type", ""))
        choice = str(decision.get("choice", ""))
        context = dict(decision.get("context") or {})

        # 1) 白名单（B3）：显式拒绝（仍不抛出——隔离由调用方观察）
        try:
            self.whitelist.require(self.session_id)
        except Exception as exc:
            logger.warning("M1 上报被白名单拒绝: %s", exc)
            return {"ok": False, "error": str(exc), "isolated": True}

        key = idempotency_key(self.session_id, step_id, decision_type, choice, context)

        # 2) 幂等去重（B3）：同一步重复上报 → 返回既有 verdict
        if key in self._seen:
            existing = [
                r for r in self.verdict_store.get(self.session_id)
                if r.idempotency_key == key
            ]
            if existing:
                r = existing[0]
                return {
                    "ok": True, "duplicate": True,
                    "verdict_id": r.verdict_id, "status": r.status.value,
                    "idempotency_key": key,
                }
            logger.warning("幂等键 %s 已处理但无 verdict（WAL 状态不一致）", key)

        # 3) WAL intent（write-ahead）
        try:
            self.wal.append(
                self.session_id, "report_intent", key,
                {"step_id": step_id, "decision_type": decision_type, "choice": choice},
            )
        except Exception as exc:
            logger.warning("WAL intent 写入失败: %s", exc)

        # 4) audit_decision（契约：paradigm 必填）
        payload = {
            "step_id": step_id,
            "decision_type": decision_type,
            "choice": choice,
            "rationale": str(decision.get("rationale", "")),
            "context": context,
            "tool_call": decision.get("tool_call"),
            "code_snippet": decision.get("code_snippet"),
        }
        try:
            score = self.audit_fn(payload, self.paradigm)
        except Exception as exc:
            # 异常隔离（C1.2）：引擎失败 → 只记录日志，绝不中断分析
            logger.error("M1 audit_decision 失败（隔离）: %s", exc)
            self.wal.append(self.session_id, "report_result", key,
                            {"ok": False, "error": str(exc)})
            self._seen.add(key)
            return {
                "ok": False, "error": str(exc), "isolated": True,
                "idempotency_key": key,
            }

        # 5) verdict provisional 落盘（B4：provisional → final/revoked 生命周期）
        try:
            # 窗口 G 实测修复：DecisionScore 不携带输入 context，快照并入
            # context 供 final_trajectory 重建（否则重建轨迹 context 全空、
            # 规则无法匹配 → 分数失真）
            snapshot = dict(score)
            snapshot.setdefault("context", {})
            snapshot["context"].update(payload.get("context") or {})
            record = self.verdict_store.create(
                session_id=self.session_id,
                step_id=step_id,
                decision_type=decision_type,
                choice=choice,
                paradigm=self.paradigm,
                provenance_source=PROVENANCE_SOURCE_M1,
                score_snapshot=snapshot,
                status=VerdictStatus.PROVISIONAL,
                idempotency_key=key,
                reason="M1 主动上报（即时 verdict，provisional）",
            )
        except Exception as exc:
            logger.warning("verdict 落盘失败: %s", exc)
            record = None

        # 6) WAL result
        try:
            self.wal.append(
                self.session_id, "report_result", key,
                {
                    "ok": True,
                    "verdict_id": record.verdict_id if record else None,
                    "status": VerdictStatus.PROVISIONAL.value,
                },
            )
        except Exception as exc:
            logger.warning("WAL result 写入失败: %s", exc)
        self._seen.add(key)

        return {
            "ok": True,
            "verdict_id": record.verdict_id if record else None,
            "status": VerdictStatus.PROVISIONAL.value,
            "score": score,
            "idempotency_key": key,
        }

    # ── 步骤生命周期（hook 用）──

    def step_completed(self, step_id: str) -> None:
        """工具调用成功回调（hook after）。"""
        try:
            self.wal.append(
                self.session_id, "step_completed",
                idempotency_key(self.session_id, step_id, "", ""),
                {"step_id": step_id},
            )
        except Exception as exc:
            logger.warning("step_completed 记录失败: %s", exc)

    def step_failed(self, step_id: str, error: str) -> None:
        """工具调用失败回调（hook after；失败步骤的声明不可证实）。"""
        try:
            self.wal.append(
                self.session_id, "step_failed",
                idempotency_key(self.session_id, step_id, "", ""),
                {"step_id": step_id, "error": error[:500]},
            )
        except Exception as exc:
            logger.warning("step_failed 记录失败: %s", exc)


__all__ = ["idempotency_key", "M1Reporter"]
