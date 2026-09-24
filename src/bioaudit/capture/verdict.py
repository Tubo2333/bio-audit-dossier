"""verdict 状态位（窗口 C / C3；refactor-plan-v1.1 B4/P3；设计 §三）。

生命周期（B4 定稿）：:

    provisional ── 交叉验证一致 ──▶ final
        │
        └── 交叉验证判虚报 ──▶ revoked（M3 判虚报后分数被推翻）

不变量：
- **报告与 reward 只消费 final**（B4）；revoked 的分数不得进入任何报告；
- 转换表唯一事实源：:data:`VERDICT_TRANSITIONS`；
- 非法转换 → :class:`VerdictTransitionError`（显式报错，不静默）；
- :class:`VerdictStore` JSONL 追加持久化（每会话一文件，崩溃可重放恢复）。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

#: verdict 存储目录（环境变量可覆盖；默认 ~/.bioaudit/verdicts，与 EventStore 同族）
DEFAULT_VERDICT_DIR = Path(
    os.environ.get("BIOAUDIT_VERDICT_DIR", str(Path.home() / ".bioaudit" / "verdicts"))
)


class VerdictStatus(str, Enum):
    """verdict 状态位（B4）：provisional → final / revoked。"""

    PROVISIONAL = "provisional"
    FINAL = "final"
    REVOKED = "revoked"


#: 合法转换表（provisional 可终态化或撤销；final 可被推翻；revoked 为终态）
VERDICT_TRANSITIONS: dict[VerdictStatus, frozenset[VerdictStatus]] = {
    VerdictStatus.PROVISIONAL: frozenset({VerdictStatus.FINAL, VerdictStatus.REVOKED}),
    VerdictStatus.FINAL: frozenset({VerdictStatus.REVOKED}),
    VerdictStatus.REVOKED: frozenset(),
}


class VerdictTransitionError(Exception):
    """非法 verdict 状态转换（如 revoked → final）。"""


class VerdictRecord(BaseModel):
    """单条决策的 verdict（B4）：携带分数快照，状态位驱动生命周期。

    - ``score_snapshot``：audit_decision 返回的 DecisionScore 快照（冻结当时分数；
      引擎后续变更不影响已落盘的 verdict）；
    - ``history``：状态转换流水（审计者也可审计，C5）。
    """

    verdict_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str
    step_id: str
    decision_type: str
    choice: str
    paradigm: str
    status: VerdictStatus = VerdictStatus.PROVISIONAL
    provenance_source: str  # M1声明 / M3解析
    score_snapshot: dict = Field(default_factory=dict)
    idempotency_key: Optional[str] = None  # M1 幂等键（B3）
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    history: list[dict] = Field(default_factory=list)  # [{status, at, reason}]

    def transition(self, new_status: VerdictStatus, reason: str) -> "VerdictRecord":
        """执行状态转换（校验合法表）；非法 → VerdictTransitionError。"""
        target = new_status if isinstance(new_status, VerdictStatus) else VerdictStatus(new_status)
        allowed = VERDICT_TRANSITIONS[self.status]
        if target not in allowed:
            raise VerdictTransitionError(
                f"非法 verdict 转换 {self.status.value} → {target.value}"
                f"（verdict_id={self.verdict_id}）"
            )
        self.status = target
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        self.history.append({
            "status": target.value,
            "at": self.updated_at,
            "reason": reason,
        })
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class VerdictStore:
    """verdict JSONL 追加存储（每会话一文件；崩溃可重放恢复）。

    - :meth:`create`：新建 verdict（默认 provisional；M3 事实证据可 final）；
    - :meth:`transition`：状态转换（校验合法表）+ 追加写盘；
    - :meth:`final_verdicts`：**报告与 reward 只消费 final**（B4 不变量）。
    """

    def __init__(self, store_dir: Optional[str | Path] = None):
        # 环境变量在 init 时读取（测试可重定向；默认 ~/.bioaudit/verdicts）
        self.store_dir = Path(store_dir) if store_dir else Path(
            os.environ.get("BIOAUDIT_VERDICT_DIR", str(DEFAULT_VERDICT_DIR))
        )
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, VerdictRecord]] = {}  # session → {verdict_id: record}

    def _path(self, session_id: str) -> Path:
        return self.store_dir / f"{session_id}.jsonl"

    def create(
        self,
        session_id: str,
        step_id: str,
        decision_type: str,
        choice: str,
        paradigm: str,
        provenance_source: str,
        score_snapshot: Optional[dict] = None,
        *,
        status: VerdictStatus = VerdictStatus.PROVISIONAL,
        idempotency_key: Optional[str] = None,
        reason: str = "",
    ) -> VerdictRecord:
        record = VerdictRecord(
            session_id=session_id,
            step_id=step_id,
            decision_type=decision_type,
            choice=choice,
            paradigm=paradigm,
            provenance_source=provenance_source,
            score_snapshot=dict(score_snapshot or {}),
            status=status,
            idempotency_key=idempotency_key,
        )
        if reason:
            record.history.append({
                "status": status.value, "at": record.updated_at, "reason": reason,
            })
        self._append(record)
        self._cache.setdefault(session_id, {})[record.verdict_id] = record
        return record

    def transition(
        self, verdict_id: str, new_status: VerdictStatus | str, reason: str
    ) -> VerdictRecord:
        """状态转换 + 写盘；非法转换 → VerdictTransitionError。"""
        for session, records in self._cache.items():
            if verdict_id in records:
                record = records[verdict_id]
                record.transition(new_status, reason)
                self._append(record)
                return record
        raise KeyError(f"verdict_id {verdict_id!r} 不在任何已加载会话中")

    def get(self, session_id: str) -> list[VerdictRecord]:
        """重放会话 verdict（崩溃恢复：文件即真相，内存缓存仅加速）。"""
        self._replay(session_id)
        return sorted(
            self._cache.get(session_id, {}).values(),
            key=lambda r: r.created_at,
        )

    def final_verdicts(self, session_id: str) -> list[VerdictRecord]:
        """**报告与 reward 只消费 final**（B4）：过滤 status == final。"""
        return [r for r in self.get(session_id) if r.status == VerdictStatus.FINAL]

    def revoke_matching(
        self,
        session_id: str,
        step_id: str,
        decision_type: str,
        reason: str,
    ) -> list[VerdictRecord]:
        """交叉验证判虚报 → 撤销该 (step_id, decision_type) 的非 revoked verdict。"""
        revoked: list[VerdictRecord] = []
        for record in self.get(session_id):
            if (
                record.step_id == step_id
                and record.decision_type == decision_type
                and record.status != VerdictStatus.REVOKED
            ):
                record.transition(VerdictStatus.REVOKED, reason)
                self._append(record)
                revoked.append(record)
        return revoked

    def _append(self, record: VerdictRecord) -> None:
        with open(self._path(record.session_id), "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def _replay(self, session_id: str) -> None:
        path = self._path(session_id)
        if not path.exists() or session_id in self._cache:
            return
        records: dict[str, VerdictRecord] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = VerdictRecord(**json.loads(line))
                records[record.verdict_id] = record
            except Exception:
                continue  # 损坏行跳过（与 EventStore.replay 同策略）
        self._cache[session_id] = records


__all__ = [
    "DEFAULT_VERDICT_DIR",
    "VerdictStatus",
    "VERDICT_TRANSITIONS",
    "VerdictTransitionError",
    "VerdictRecord",
    "VerdictStore",
]
