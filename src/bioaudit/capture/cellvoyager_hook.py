"""CellVoyager M1 hook（窗口 C / C1；wrapper 优先，**不 fork** CellVoyager）。

设计依据：trajectory-capture-design-v1 §六（注入审计回调——每步工具调用
前/后上报决策）。

- :class:`CellVoyagerM1Hook` 包装执行器的代码执行方法（鸭子类型，不 import
  CellVoyager）：``run_last_cell``（IdeaExecutor legacy）/ ``execute_cell`` /
  ``insert_execute_code_cell``（ClaudeJupyterExecutor）——**工具调用前**提取
  待执行代码 → signatures 预解析出候选决策（payload 含 decision_type/choice/
  context/provenance）→ M1Reporter 上报 → **工具调用后**记录完成/失败；
- **异常隔离（C1.2，F6 教训）**：hook 自身任何异常只记录日志，绝不影响
  CellVoyager 分析继续；``attach`` 失败不抛错（返回空列表）；
- payload provenance：``{来源: M1声明, 时间戳, 证据: CellVoyager hook
  before-execute <cell>}``；
- 未安装 bioaudit 时可用 :class:`NullM1Hook`（no-op，分析不受影响）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from bioaudit.capture.m1_reporter import M1Reporter
from bioaudit.capture.m3_parser import M3Parser
from bioaudit.capture.models import (
    PROVENANCE_SOURCE_M1,
    DecisionProvenance,
)
from bioaudit.capture.session import SessionWhitelist

logger = logging.getLogger(__name__)

#: 各执行器方法 → 从调用参数提取"即将执行的代码"
_CODE_EXTRACTORS: dict[str, Callable[[Any, tuple, dict], str]] = {}


def _extract_run_last_cell(owner: Any, args: tuple, kwargs: dict) -> str:
    nb = args[0] if args else kwargs.get("nb")
    cells = nb.cells if hasattr(nb, "cells") else []
    for cell in reversed(cells):
        ctype = getattr(cell, "cell_type", None) or (
            cell.get("cell_type") if isinstance(cell, dict) else None
        )
        if ctype == "code":
            source = getattr(cell, "source", None)
            return source if isinstance(source, str) else "".join(source or [])
    return ""


def _extract_execute_cell(owner: Any, args: tuple, kwargs: dict) -> str:
    # 真实调用两种形态都覆盖：位置参数（session.execute_cell(3)）与
    # 关键字参数（FastMCP 工具内 session.execute_cell(index=3)，窗口 G 实测）
    if args:
        index = args[0]
    elif "index" in kwargs:
        index = kwargs["index"]
    else:
        return ""
    read = getattr(owner, "read_cell", None)
    if callable(read):
        cell = read(index)
        return str(cell.get("source", "")) if isinstance(cell, dict) else ""
    return ""


def _extract_insert_execute(owner: Any, args: tuple, kwargs: dict) -> str:
    if args:
        return str(args[-1])
    return str(kwargs.get("source", ""))


_CODE_EXTRACTORS["run_last_cell"] = _extract_run_last_cell
_CODE_EXTRACTORS["execute_cell"] = _extract_execute_cell
_CODE_EXTRACTORS["insert_execute_code_cell"] = _extract_insert_execute


class NullM1Hook:
    """no-op hook（bioaudit 不可用 / 未启用采集时挂载；保证分析照跑）。"""

    def __init__(self, *args, **kwargs):
        pass

    def attach(self, executor: Any) -> list[str]:
        return []


class CellVoyagerM1Hook:
    """CellVoyager 执行引擎审计回调（wrapper；异常隔离）。

    Parameters
    ----------
    reporter : M1Reporter
        上报器（含 session/paradigm/白名单/WAL/verdict）。
    parser : M3Parser | None
        签名驱动预解析器（默认 M3Parser(act=reporter.paradigm)）。
    hook_points : tuple[str, ...]
        包装的执行器方法名（存在才包装）。
    """

    def __init__(
        self,
        reporter: M1Reporter,
        parser: Optional[M3Parser] = None,
        hook_points: tuple[str, ...] = (
            "run_last_cell", "execute_cell", "insert_execute_code_cell",
        ),
    ):
        self.reporter = reporter
        self.parser = parser if parser is not None else M3Parser(act=reporter.paradigm)
        self.hook_points = list(hook_points)
        self.n_reports = 0
        self.n_errors = 0  # hook 内部异常计数（隔离成功指标）

    # ── attach ──

    def attach(self, executor: Any) -> list[str]:
        """包装执行器方法（存在才包装）；失败只记录，不影响执行器。"""
        wrapped: list[str] = []
        if executor is None:
            return wrapped
        for name in self.hook_points:
            original = getattr(executor, name, None)
            if not callable(original) or name in ("attach",):
                continue
            try:
                setattr(executor, name, self._wrap(executor, original, name))
                wrapped.append(name)
            except Exception as exc:  # 隔离：attach 失败不影响执行器
                logger.warning("CellVoyager hook attach %s 失败（隔离）: %s", name, exc)
                self.n_errors += 1
        return wrapped

    def _wrap(self, owner: Any, original: Callable, method_name: str):
        def hooked(*args, **kwargs):
            self._before(owner, method_name, args, kwargs)
            try:
                result = original(*args, **kwargs)
                self._after(owner, method_name, args, ok=True)
                return result
            except Exception as exc:
                self._after(owner, method_name, args, ok=False, error=exc)
                raise  # 原始异常照常传播——hook 不改变 CellVoyager 行为
        return hooked

    # ── 工具调用前后 ──

    def _before(self, owner: Any, method_name: str, args: tuple,
                kwargs: dict | None = None) -> None:
        """工具调用前：签名预解析 → 逐决策上报（payload 含全要素）。"""
        try:
            extractor = _CODE_EXTRACTORS.get(method_name)
            if extractor is None:
                return
            code = extractor(owner, args, kwargs or {})
            if not code or not code.strip():
                return
            result = self.parser.parse_code(code, cell_index=self.n_reports)
            for cand in result.candidates:
                decision = cand.to_decision()
                decision["provenance"] = DecisionProvenance(
                    source=PROVENANCE_SOURCE_M1,
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    evidence=(
                        f"CellVoyager hook before-execute（{method_name}）: "
                        f"{cand.tool_call}"
                    ),
                    detail={"method": method_name},
                ).model_dump()
                report = self.reporter.report(decision)
                self.n_reports += 1
                if not report.get("ok"):
                    self.n_errors += 1
                    logger.warning(
                        "M1 上报被隔离（%s）: %s", cand.decision_type,
                        report.get("error"),
                    )
            # 不确定候选也留痕（不猜测：记录证据，供事后人工/交叉验证参考）
            for u in result.uncertain:
                logger.info(
                    "M1 hook 未定候选 %s（%s）: %s", u.decision_type,
                    u.tool_call, u.reason,
                )
        except Exception as exc:  # 异常隔离（C1.2）
            logger.exception("CellVoyager hook before-execute 失败（隔离）: %s", exc)
            self.n_errors += 1

    def _after(
        self, owner: Any, method_name: str, args: tuple,
        *, ok: bool, error: Optional[Exception] = None,
    ) -> None:
        """工具调用后：记录完成/失败（失败步骤的声明不可证实）。"""
        try:
            if ok:
                self.reporter.step_completed(f"cell_{self.n_reports}")
            else:
                self.reporter.step_failed(
                    f"cell_{self.n_reports}", str(error)
                )
        except Exception as exc:
            logger.warning("CellVoyager hook after-execute 失败（隔离）: %s", exc)
            self.n_errors += 1


def make_cellvoyager_hook(
    paradigm: str,
    session_id: Optional[str] = None,
    *,
    metadata: Optional[dict] = None,
    declared: Optional[dict] = None,
    whitelist: Optional[SessionWhitelist] = None,
) -> CellVoyagerM1Hook:
    """一站式构造：session 生成 + 白名单注册 + reporter + hook。

    declared : 评测者/数据事实声明（三级可信源：运行宪法/评测配置注入，
        如数据集平台 sequencing=smartseq2；**与 Agent claim（M1 声明）
        严格区分**——G-2 纪律：Agent 上报的键永远不进 declared）。
    """
    session_id = session_id or f"cv_{uuid.uuid4().hex[:10]}"
    wl = whitelist if whitelist is not None else SessionWhitelist()
    wl.register(session_id)
    reporter = M1Reporter(session_id, paradigm, whitelist=wl)
    reporter.start()  # WAL 崩溃恢复
    parser = M3Parser(act=paradigm, metadata=metadata, declared=declared)
    return CellVoyagerM1Hook(reporter, parser)


__all__ = [
    "NullM1Hook",
    "CellVoyagerM1Hook",
    "make_cellvoyager_hook",
]
