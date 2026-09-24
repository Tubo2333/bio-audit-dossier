"""AuditEvent 与 EventStore（自 fullflow-demo 迁移），窗口 C 可观测性增强。

迁移变更：默认日志目录改为 cwd 无关（``$BIOAUDIT_LOG_DIR`` → ``~/.bioaudit/logs/events``）。

窗口 C（C5，审计报告 F11）：
- **写失败显式告警**：``append()`` 写入失败 → 抛出 :class:`EventWriteError`
  （不再静默丢写）；同时累计 ``write_failures`` / ``last_write_error`` 供
  ``health()`` 检查。管道调用方负责捕获并写入 ``event_store_warnings``
  （audit.py 的 ``_append_event`` 助手；告警进审计输出，审计者也可审计）；
- **日志轮转**（H12 轻量版）：单会话文件超 ``BIOAUDIT_EVENT_ROTATE_BYTES``
  （默认 5MB）→ 轮转 ``<session>.1.jsonl``…，保留 ``BIOAUDIT_EVENT_MAX_FILES``
  （默认 5）份；``replay()`` 按序读全部轮转分片（崩溃恢复不丢事件）。
"""

import json
import logging
import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path(
    os.environ.get("BIOAUDIT_LOG_DIR", str(Path.home() / ".bioaudit" / "logs" / "events"))
)

#: H12：单会话事件文件轮转阈值（字节）与环境覆盖
DEFAULT_ROTATE_BYTES = 5 * 1024 * 1024
ROTATE_BYTES = int(os.environ.get("BIOAUDIT_EVENT_ROTATE_BYTES", DEFAULT_ROTATE_BYTES))
#: H12：轮转分片保留上限
DEFAULT_MAX_FILES = 5
MAX_FILES = int(os.environ.get("BIOAUDIT_EVENT_MAX_FILES", DEFAULT_MAX_FILES))


class EventWriteError(Exception):
    """事件写入失败（C5/F11）：显式告警信号，不再静默丢写。"""


class AuditEvent(BaseModel):
    """Immutable audit event for event sourcing."""
    schema_version: str = "1.0"  # For future migration
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    event_type: str  # "rule_matched" | "decision_scored" | "conflict_detected" |
                     # "human_overrode" | "aggregation_complete" |
                     # "propagation_traced" | "report_generated" |
                     # "parse_complete" | "error_occurred"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    node: str
    payload: dict = Field(default_factory=dict)


class EventStore:
    """Append-only JSONL event store. One file per audit session."""

    def __init__(self, log_dir: Optional[str | Path] = None):
        # B3: log_dir 未显式传入时读环境变量（测试可重定向；默认 ~/.bioaudit）
        self.log_dir = Path(log_dir) if log_dir else Path(
            os.environ.get("BIOAUDIT_LOG_DIR", str(DEFAULT_LOG_DIR))
        )
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id: str | None = None
        self._file_path: Path | None = None
        # H12：轮转参数 init 时读环境（测试可重定向）
        self.rotate_bytes = int(
            os.environ.get("BIOAUDIT_EVENT_ROTATE_BYTES", DEFAULT_ROTATE_BYTES)
        )
        self.max_files = int(
            os.environ.get("BIOAUDIT_EVENT_MAX_FILES", DEFAULT_MAX_FILES)
        )
        # C5（F11）：写失败显式告警状态
        self.write_failures: int = 0
        self.last_write_error: str | None = None

    def start_session(self, session_id: str):
        self._session_id = session_id
        self._file_path = self.log_dir / f"{session_id}.jsonl"

    def append(self, event: AuditEvent):
        """追加事件；写入失败 → :class:`EventWriteError`（C5：不再静默丢写）。

        调用方应捕获该异常并显式告警（audit.py ``_append_event`` →
        state["event_store_warnings"]），管道主流程不受影响。
        """
        if self._file_path is None:
            raise RuntimeError("No active session. Call start_session() first.")
        try:
            self._rotate_if_needed()
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        except (OSError, IOError) as e:
            self.write_failures += 1
            self.last_write_error = str(e)
            logger.error(f"EventStore write failed (F11 显式告警): {e}")
            raise EventWriteError(
                f"事件写入失败（session={self._session_id}, event={event.event_type}）: {e}"
            ) from e

    # ── H12：容量上限 + 轮转 ──

    def _rotate_if_needed(self) -> None:
        """当前分片超阈值 → 轮转；分片从新到旧编号 .1…，超过保留上限的最老
        分片删除（H12：容量上限；保留窗口内事件不丢）。"""
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            if self._file_path.stat().st_size < self.rotate_bytes:
                return
        except OSError:
            return
        part = self._file_path
        for i in range(self.max_files, 0, -1):
            older = part.with_name(f"{part.stem}.{i}.jsonl")
            if older.exists():
                if i == self.max_files:
                    older.unlink(missing_ok=True)  # 超出保留上限 → 删除最老分片
                else:
                    older.replace(part.with_name(f"{part.stem}.{i + 1}.jsonl"))
        part.rename(part.with_name(f"{part.stem}.1.jsonl"))

    def _part_files(self, session_id: str) -> list[Path]:
        """会话事件分片（最老 → 最新：轮转分片倒序 + 当前主文件）。"""
        base = self.log_dir / f"{session_id}.jsonl"
        rotated = [
            base.with_name(f"{base.stem}.{i}.jsonl")
            for i in range(self.max_files, 0, -1)
        ]
        return [p for p in [*rotated, base] if p.exists()]

    # ── 读取 / 恢复 ──

    def replay(self, session_id: str) -> list[AuditEvent]:
        """重放会话全部事件（含轮转分片；损坏行跳过）。"""
        events = []
        for file_path in self._part_files(session_id):
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(AuditEvent(**json.loads(line)))
                except Exception:
                    continue  # Skip corrupt lines
        return events

    def list_sessions(self) -> list[str]:
        parts = tuple(f".{i}" for i in range(1, self.max_files + 1))
        return sorted([
            f.stem for f in self.log_dir.glob("*.jsonl")
            if not f.stem.endswith(parts)
        ], reverse=True)

    def health(self) -> dict:
        """存储健康状态（C5：写失败可见）。"""
        return {
            "write_failures": self.write_failures,
            "last_write_error": self.last_write_error,
            "ok": self.write_failures == 0,
        }
