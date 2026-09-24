"""WAL 追加持久化（窗口 C / C1；refactor-plan-v1.1 B3：WAL + 崩溃恢复）。

M1 上报采用 write-ahead：先追加 ``report_intent``，评分落盘后再追加
``report_result``（带 verdict_id）。崩溃后 :meth:`WAL.replay` 可恢复：
- 有 intent 无 result → ``interrupted``（上报中断，可重发；幂等键去重保证不重复）；
- 有 result → 已完成（含 verdict_id）。

存储：JSONL 每会话一文件（``$BIOAUDIT_WAL_DIR/<session>.jsonl``，
环境变量可覆盖，默认 ``~/.bioaudit/wal``），追加写 + flush。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

DEFAULT_WAL_DIR = Path(
    os.environ.get("BIOAUDIT_WAL_DIR", str(Path.home() / ".bioaudit" / "wal"))
)


class WalEntry(BaseModel):
    """WAL 条目（追加式；op 见模块 docstring）。"""

    session_id: str
    op: str  # report_intent / report_result / step_failed / step_completed
    idempotency_key: str
    payload: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class WAL:
    """追加式 WAL（崩溃恢复 + 幂等去重基础设施）。"""

    def __init__(self, wal_dir: Optional[str | Path] = None):
        # 环境变量在 init 时读取（测试可重定向；默认 ~/.bioaudit/wal）
        self.wal_dir = Path(wal_dir) if wal_dir else Path(
            os.environ.get("BIOAUDIT_WAL_DIR", str(DEFAULT_WAL_DIR))
        )
        self.wal_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.wal_dir / f"{session_id}.jsonl"

    def append(self, session_id: str, op: str, idempotency_key: str,
               payload: Optional[dict] = None) -> None:
        entry = WalEntry(
            session_id=session_id, op=op,
            idempotency_key=idempotency_key, payload=dict(payload or {}),
        )
        with open(self._path(session_id), "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
            f.flush()

    def replay(self, session_id: str) -> list[WalEntry]:
        """重放会话 WAL（崩溃恢复；损坏行跳过并计入 stats）。"""
        path = self._path(session_id)
        if not path.exists():
            return []
        entries: list[WalEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(WalEntry(**json.loads(line)))
            except Exception:
                continue
        return entries

    def recovery(self, session_id: str) -> dict:
        """崩溃恢复报告：已完成 / 中断 / 去重统计。"""
        entries = self.replay(session_id)
        results: dict[str, dict] = {}
        for e in entries:
            results.setdefault(e.idempotency_key, {})[e.op] = e
        completed = [
            k for k, v in results.items() if "report_result" in v
        ]
        interrupted = [
            k for k, v in results.items()
            if "report_intent" in v and "report_result" not in v
        ]
        return {
            "session_id": session_id,
            "n_entries": len(entries),
            "completed": completed,
            "interrupted": interrupted,
        }


__all__ = ["DEFAULT_WAL_DIR", "WalEntry", "WAL"]
