"""C1 M1 主动上报测试（窗口 C 验收项 1/2/3/4）。

- 1. hook 注入方式（wrapper 优先，不 fork）+ 工具调用前后 + payload 全要素
- 2. 异常隔离：hook/reporter 失败只记录日志，绝不影响分析继续（F6 教训）
- 3. session_id 白名单校验 + 同一步重复上报去重（幂等键）+ WAL 崩溃恢复（B3）
- 4. M1 决策走通 audit_decision 契约（paradigm 必填）→ 即时 verdict 返回
"""


import pytest

from bioaudit.capture.cellvoyager_hook import (
    CellVoyagerM1Hook,
    NullM1Hook,
    make_cellvoyager_hook,
)
from bioaudit.capture.m1_reporter import M1Reporter, idempotency_key
from bioaudit.capture.session import SessionWhitelist
from bioaudit.capture.verdict import VerdictStatus, VerdictStore
from bioaudit.capture.wal import WAL


class FakeCell:
    def __init__(self, source, cell_type="code"):
        self.source = source
        self.cell_type = cell_type


class FakeNotebook:
    def __init__(self, cells):
        self.cells = cells


class FakeExecutor:
    """最小执行器替身：run_last_cell 签名与 CellVoyager IdeaExecutor 一致。"""

    def __init__(self, notebook=None):
        self.notebook = notebook or FakeNotebook([
            FakeCell("import scanpy as sc\nadata = sc.read_h5ad('x.h5ad')"),
            FakeCell("sc.pp.filter_cells(adata, min_genes=200)"),
        ])
        self.executed = []
        self.fail_on = False

    def run_last_cell(self, nb):
        self.executed.append("run_last_cell")
        if self.fail_on:
            raise RuntimeError("kernel died (simulated)")
        return True, None, nb

    def execute_cell(self, index):
        self.executed.append(f"execute_cell:{index}")
        return {"ok": True}


def _reporter(tmp_path, session="sess-h", paradigm="scrna", audit_fn=None, wl=None):
    wl = wl if wl is not None else SessionWhitelist()
    wl.register(session)
    store = VerdictStore(tmp_path / "verdicts")
    wal = WAL(tmp_path / "wal")
    return M1Reporter(session, paradigm, whitelist=wl, verdict_store=store,
                      wal=wal, audit_fn=audit_fn)

# ── 1. hook 注入 + 工具调用前后 + payload ──


def test_hook_wraps_executor_before_after(tmp_path):
    reporter = _reporter(tmp_path)
    hook = CellVoyagerM1Hook(reporter)
    executor = FakeExecutor()
    wrapped = hook.attach(executor)
    assert "run_last_cell" in wrapped
    assert "execute_cell" in wrapped

    nb = FakeNotebook([
        FakeCell("sc.pp.normalize_total(adata)"),
        FakeCell("sc.pp.filter_cells(adata, min_genes=200)"),  # 最后执行的 cell
    ])
    ok, err, _ = executor.run_last_cell(nb)
    assert ok is True
    assert hook.n_reports >= 1
    # payload 全要素（验收项 1）：decision_type/choice/context/provenance 已落盘
    records = reporter.verdict_store.get("sess-h")
    assert records, "hook 上报应产生 verdict"
    r = next(x for x in records if x.decision_type == "qc_filtering")
    assert r.choice == "hard_threshold"
    assert r.paradigm == "scrna"
    assert r.status == VerdictStatus.PROVISIONAL
    assert r.provenance_source == "M1声明"
    assert r.score_snapshot  # DecisionScore 快照（含证据/替代方案）


def test_hook_after_records_completion_and_failure(tmp_path):
    reporter = _reporter(tmp_path)
    hook = CellVoyagerM1Hook(reporter)
    executor = FakeExecutor()
    hook.attach(executor)
    executor.run_last_cell(executor.notebook)
    executor.fail_on = True
    with pytest.raises(RuntimeError):  # 原始异常照常传播
        executor.run_last_cell(executor.notebook)
    entries = reporter.wal.replay("sess-h")
    ops = [e.op for e in entries]
    assert "step_completed" in ops
    assert "step_failed" in ops


def test_hook_null_and_missing_methods(tmp_path):
    assert NullM1Hook().attach(FakeExecutor()) == []
    hook = CellVoyagerM1Hook(_reporter(tmp_path))
    assert hook.attach(None) == []  # executor 缺失不炸
    assert hook.attach(object()) == []  # 无 hook 点不炸


# ── 2. 异常隔离（F6 教训）──


def test_hook_exception_does_not_break_analysis(tmp_path):
    """audit_fn 抛异常 → hook 只记录日志，分析照常完成。"""
    def broken_audit(decision, paradigm):
        raise RuntimeError("engine down")

    reporter = _reporter(tmp_path, audit_fn=broken_audit)
    hook = CellVoyagerM1Hook(reporter)
    executor = FakeExecutor()
    hook.attach(executor)
    ok, err, _ = executor.run_last_cell(executor.notebook)
    assert ok is True  # 分析继续
    assert hook.n_errors >= 1  # 隔离计数
    # 上报失败被隔离为 error 负载（不抛出）
    result = reporter.report({
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {},
    })
    assert result["ok"] is False
    assert result["isolated"] is True


def test_hook_before_raising_extractor_isolated(tmp_path):
    reporter = _reporter(tmp_path)
    hook = CellVoyagerM1Hook(reporter)

    class EvilExecutor:
        def run_last_cell(self, nb):
            return "still works"

    # 手动注入一个抛异常的 before 提取器（模拟 hook 内部 bug）
    import bioaudit.capture.cellvoyager_hook as cvh

    def boom(owner, args):
        raise ValueError("hook bug")

    old = cvh._CODE_EXTRACTORS["run_last_cell"]
    cvh._CODE_EXTRACTORS["run_last_cell"] = boom
    try:
        executor = EvilExecutor()
        hook.attach(executor)
        assert executor.run_last_cell(None) == "still works"  # 分析不受影响
        assert hook.n_errors >= 1
    finally:
        cvh._CODE_EXTRACTORS["run_last_cell"] = old


# ── 3. 白名单 + 幂等键 + WAL 崩溃恢复（B3）──


def test_session_whitelist_enforced(tmp_path):
    wl = SessionWhitelist()  # 未注册
    reporter = M1Reporter("sess-x", "scrna", whitelist=wl,
                          verdict_store=VerdictStore(tmp_path / "v"),
                          wal=WAL(tmp_path / "w"))
    result = reporter.report({
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {},
    })
    assert result["ok"] is False
    assert "白名单" in result["error"]
    wl.register("sess-x")
    assert reporter.report({
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {},
    })["ok"] is True


def test_env_whitelist(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAUDIT_SESSION_WHITELIST", "env-sess")
    reporter = M1Reporter("env-sess", "scrna",
                          whitelist=SessionWhitelist(),
                          verdict_store=VerdictStore(tmp_path / "v"),
                          wal=WAL(tmp_path / "w"))
    assert reporter.report({
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {},
    })["ok"] is True


def test_idempotent_duplicate_report_deduped(tmp_path):
    reporter = _reporter(tmp_path)
    decision = {
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {"min_genes": 200},
    }
    r1 = reporter.report(decision)
    r2 = reporter.report(decision)  # 同一步重复上报
    assert r1["ok"] and r2["ok"]
    assert r2["duplicate"] is True
    assert r2["verdict_id"] == r1["verdict_id"]
    assert len(reporter.verdict_store.get("sess-h")) == 1  # 只落盘一份


def test_idempotency_key_stable_and_context_sensitive():
    base = {"session_id": "s", "step_id": "s1", "decision_type": "qc_filtering",
            "choice": "hard_threshold"}
    assert idempotency_key(**base) == idempotency_key(**base)
    assert idempotency_key(**base) != idempotency_key(
        **{**base, "context": {"min_genes": 500}})
    assert idempotency_key(**base) != idempotency_key(
        **{**base, "step_id": "s2"})


def test_wal_crash_recovery_dedupes(tmp_path):
    """崩溃恢复：WAL 已有 report_result → start() 预载 → 重发被去重。"""
    reporter1 = _reporter(tmp_path)
    decision = {
        "step_id": "s1", "decision_type": "qc_filtering",
        "choice": "hard_threshold", "context": {},
    }
    r1 = reporter1.report(decision)
    # 模拟崩溃后重启：全新 reporter（同一 WAL/verdict 目录）
    wl = SessionWhitelist()
    wl.register("sess-h")
    reporter2 = M1Reporter(
        "sess-h", "scrna", whitelist=wl,
        verdict_store=VerdictStore(tmp_path / "verdicts"),
        wal=WAL(tmp_path / "wal"),
    )
    reporter2.start()  # 崩溃恢复
    r2 = reporter2.report(decision)
    assert r2["duplicate"] is True
    assert r2["verdict_id"] == r1["verdict_id"]
    recovery = WAL(tmp_path / "wal").recovery("sess-h")
    assert recovery["completed"]  # 完成态可见
    assert recovery["interrupted"] == []


def test_wal_intent_without_result_reported_interrupted(tmp_path):
    wal = WAL(tmp_path / "w")
    key = idempotency_key("sess-i", "s1", "qc_filtering", "hard_threshold", {})
    wal.append("sess-i", "report_intent", key, {})  # 无 result → 中断
    recovery = wal.recovery("sess-i")
    assert key in recovery["interrupted"]


# ── 4. 走通 audit_decision 契约 → 即时 verdict ──


def test_report_returns_immediate_verdict_with_score(tmp_path):
    """C1.4：M1 决策走 audit_decision 契约（paradigm 必填）→ 即时 verdict。"""
    reporter = _reporter(tmp_path, paradigm="deg")
    result = reporter.report({
        "step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
        "context": {"data_category": "raw_counts", "sequencing": "bulk_RNA_seq",
                    "design": "simple_two_group", "n_replicates": 6},
    })
    assert result["ok"] is True
    assert result["status"] == "provisional"
    assert result["verdict_id"]
    assert result["score"]["level"] == 3  # 真实引擎评分（bulk DEG DESeq2 → L3）
    assert result["score"]["matched_rules"]


def test_make_cellvoyager_hook_ready_to_attach(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAUDIT_VERDICT_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("BIOAUDIT_WAL_DIR", str(tmp_path / "w"))
    hook = make_cellvoyager_hook("scrna")
    assert hook.reporter.session_id.startswith("cv_")
    assert hook.reporter.whitelist.allow(hook.reporter.session_id)  # 自注册
    executor = FakeExecutor()
    wrapped = hook.attach(executor)
    assert wrapped
    ok, _, _ = executor.run_last_cell(executor.notebook)
    assert ok is True


# ── 5. 关键字参数调用（窗口 G 真实运行发现；FastMCP 工具以
#    session.execute_cell(index=...) 调用——C 窗口测试仅覆盖位置参数）──


class FakeSessionCell:
    def __init__(self, source):
        self._src = source

    def get(self, key, default=None):
        return self._src if key == "source" else default


class FakeNotebookSession:
    """模拟 NotebookSession 的关键字/位置调用形态（read_cell + execute_cell）。"""

    def __init__(self):
        self.cells_map = {
            1: FakeSessionCell("sc.read_h5ad('x.h5ad')"),
            2: FakeSessionCell("sc.pp.filter_cells(adata, min_genes=200)"),
        }
        self.executed = []

    def read_cell(self, index):
        return {"source": self.cells_map[index].get("source")}

    def execute_cell(self, index):
        self.executed.append(f"execute_cell:{index}")
        return {"ok": True}

    def insert_execute_code_cell(self, index=None, source=""):
        self.executed.append(f"insert_execute:{index}:{source[:20]}")
        return {"ok": True}


def test_hook_kwargs_calls_are_extracted_and_reported(tmp_path):
    """窗口 G：真实 FastMCP 以 execute_cell(index=...) 关键字调用 → 仍上报。"""
    reporter = _reporter(tmp_path, session="sess-kw")
    hook = CellVoyagerM1Hook(reporter)
    session = FakeNotebookSession()
    wrapped = hook.attach(session)
    assert "execute_cell" in wrapped
    assert "insert_execute_code_cell" in wrapped

    # 关键字调用（FastMCP 工具形态）
    session.execute_cell(index=2)
    assert hook.n_reports >= 1
    records = reporter.verdict_store.get("sess-kw")
    qc = next((r for r in records if r.decision_type == "qc_filtering"), None)
    assert qc is not None, "关键字调用也应提取代码并上报"
    assert qc.choice == "hard_threshold"

    # 位置调用（既有形态不回退）
    n_before = hook.n_reports
    session.execute_cell(2)
    assert hook.n_reports > n_before


def test_hook_insert_execute_kwargs_extracted(tmp_path):
    """insert_execute_code_cell(index=None, source=...) 关键字形态。"""
    reporter = _reporter(tmp_path, session="sess-kw2")
    hook = CellVoyagerM1Hook(reporter)
    session = FakeNotebookSession()
    hook.attach(session)
    session.insert_execute_code_cell(
        index=None, source="sc.pp.normalize_total(adata, target_sum=1e4)"
    )
    assert hook.n_reports >= 1
    records = reporter.verdict_store.get("sess-kw2")
    norm = next((r for r in records if r.decision_type == "scRNA_normalization"), None)
    assert norm is not None
    assert norm.choice == "LogNormalize"
