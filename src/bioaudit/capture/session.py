"""session 白名单（窗口 C / C1；refactor-plan-v1.1 B3：session_id 白名单）。

- 环境变量 ``BIOAUDIT_SESSION_WHITELIST``（逗号分隔）静态放行；
- :meth:`SessionWhitelist.register` 程序化注册（CellVoyager wrapper 启动时
  自动注册自己的 session）；
- :meth:`SessionWhitelist.require`：不在白名单 → ``BioAuditError``
  （validation-error，显式拒绝，不静默放行）。
"""

from __future__ import annotations

import os
from typing import Optional

from bioaudit.errors import BioAuditError, ErrorCode

#: 环境变量名（逗号分隔的 session_id 列表）
ENV_WHITELIST = "BIOAUDIT_SESSION_WHITELIST"


class SessionWhitelist:
    """M1 上报 session 白名单（B3）。"""

    def __init__(self, env: Optional[str] = None):
        raw = env if env is not None else os.environ.get(ENV_WHITELIST, "")
        self._static: set[str] = {
            s.strip() for s in raw.split(",") if s.strip()
        }
        self._registered: set[str] = set()

    def register(self, session_id: str) -> None:
        """程序化注册（wrapper/宿主启动时自注册）。"""
        self._registered.add(session_id)

    def allow(self, session_id: str) -> bool:
        return session_id in self._static or session_id in self._registered

    def require(self, session_id: str) -> None:
        """不在白名单 → BioAuditError(validation-error)（显式拒绝）。"""
        if not self.allow(session_id):
            raise BioAuditError(
                ErrorCode.VALIDATION_ERROR,
                f"session_id {session_id!r} 不在 M1 上报白名单"
                f"（env {ENV_WHITELIST} 或 register() 放行）",
                details={"session_id": session_id},
            )


__all__ = ["ENV_WHITELIST", "SessionWhitelist"]
