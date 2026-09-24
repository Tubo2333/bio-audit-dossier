"""A1 第一版实现（提交管线）的测试：端到端成功 + 三道门 + 确定性。

覆盖（与 `DP-A1-INPUT-CONTRACT.md` 的实测分层一一对应）：
- 完整五点提交 → 收账、出快照、出三份产物，五点可审；
- 机械合理性不合格（未来观测时点）→ 退出码 2，**不写任何账**；
- 缺决策点 → 批次级被拒，退出码 3，**不写任何账**；
- 同一 audit_id 重复提交 → 账本拒绝重复登记（append-only 的实际表现）；
- 钉住 --now → 两次提交的 report / deliverable **逐字节一致**（不退化比较墙钟）。

本文件不改产品代码、不动规则文件、不进公开面。
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(SCRIPTS))

_FIXED = "2026-09-14T10:00:00+00:00"


def _load(name: str):
    saved = sys.stdout
    try:
        sys.stdout = io.StringIO()
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


@pytest.fixture(scope="module")
def submit():
    return _load("submit_analysis")


@pytest.fixture(scope="module")
def ex():
    return _load("make_example_submission")


def _write_sub(tmp_path, sub: dict) -> str:
    p = tmp_path / "sub.json"
    p.write_text(json.dumps(sub, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8", newline="\n")
    return str(p)


def _ids(sub) -> str:
    return sub["audit_id"]


def test_submit_end_to_end_writes_the_account(submit, ex, tmp_path):
    out = tmp_path / "var"
    rc = submit.main(["--file", _write_sub(tmp_path, ex.build_submission()),
                      "--out-dir", str(out), "--now", _FIXED])
    assert rc == 0
    d = out / _ids(ex.build_submission())
    for f in ("report.json", "report.html", "deliverable.html",
              "ledger.points.jsonl", "ledger.authority.jsonl"):
        assert (d / f).exists(), f"缺产物 {f}"
    page = (d / "report.html").read_text(encoding="utf-8")
    assert 'id="chain"' in page and "链条总览" in page
    deliv = (d / "deliverable.html").read_text(encoding="utf-8")
    assert "链条总览" in deliv
    # 渲染来源必须指向本账户，而不是演示用的 report.sample.json
    assert "ui/report.sample.json" not in deliv
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    lane = report.get("lane_and_release") or {}
    assert not (lane.get("decisions") or []), "真实提交没有 lane 请求，不应有 lane 记录"


def test_submit_rejects_impossible_freshness_before_writing(submit, ex, tmp_path):
    sub = ex.build_submission()
    sub["decision_points"]["filtering"]["evidence_observations"][0]["observed_at"] = \
        "2099-01-01T00:00:00+00:00"
    out = tmp_path / "var"
    rc = submit.main(["--file", _write_sub(tmp_path, sub),
                      "--out-dir", str(out), "--now", _FIXED])
    assert rc == 2
    assert not (out / sub["audit_id"]).exists(), "被拒的提交不得留下任何产物"


def test_submit_rejects_missing_decision_point_at_batch_gate(submit, ex, tmp_path):
    sub = ex.build_submission()
    sub["decision_points"].pop("multiple_testing_correction")
    out = tmp_path / "var"
    rc = submit.main(["--file", _write_sub(tmp_path, sub),
                      "--out-dir", str(out), "--now", _FIXED])
    assert rc == 3
    # 「不写任何账」指账本/报告产物；被拒时目录可以是空壳，但不得留下任何产物
    d = out / sub["audit_id"]
    for f in ("ledger.points.jsonl", "report.json", "report.html", "deliverable.html"):
        assert not (d / f).exists(), f"被拒的提交不得留下 {f}"


def test_submit_refuses_duplicate_audit_id(submit, ex, tmp_path):
    """账本 append-only 的实际表现：同一 audit_id 不能登记第二次。"""
    out = tmp_path / "var"
    f = _write_sub(tmp_path, ex.build_submission())
    assert submit.main(["--file", f, "--out-dir", str(out), "--now", _FIXED]) == 0
    assert submit.main(["--file", f, "--out-dir", str(out), "--now", _FIXED]) == 3


def test_submit_is_deterministic_with_pinned_now(submit, ex, tmp_path):
    sub = ex.build_submission()
    f = _write_sub(tmp_path, sub)
    a, b = tmp_path / "a", tmp_path / "b"
    assert submit.main(["--file", f, "--out-dir", str(a), "--now", _FIXED]) == 0
    assert submit.main(["--file", f, "--out-dir", str(b), "--now", _FIXED]) == 0
    for name in ("report.json", "deliverable.html"):
        pa = a / sub["audit_id"] / name
        pb = b / sub["audit_id"] / name
        assert pa.read_bytes() == pb.read_bytes(), f"{name} 两次生成不一致"