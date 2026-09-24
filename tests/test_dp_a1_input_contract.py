"""A1 输入面契约的测试（DP-A1-INPUT-CONTRACT.md 的可执行一半）。

为什么要有这个
--------------
`DP-A1-INPUT-CONTRACT.md` 把「缺什么会怎样」写成了**实测分层**。
散文会漂，测试不会——所以本文把那些分层**钉住**。
这些断言不是我自己发明的规矩：它们是对**现有实现行为**的记录，
所以一旦实现改了行为，这里必须先红，人才能知道契约文档该改。

本文**不新增公共语义**、**不改产品代码**、**不动规则文件**。
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

from bioaudit.api import audit_dp_register, render_dp_report  # noqa: E402
from bioaudit.dp01_store import DP01JSONLStore  # noqa: E402

_DOMAINS = ("identity", "version", "source", "binding", "snapshot",
            "permission", "unique_authority")
_POINTS = ("filtering", "normalization", "differential_method",
           "multiple_testing_correction", "significance_threshold")


def _load(name: str):
    """按路径加载 scripts/ 下的模块（scripts 不是包）。"""
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
def example():
    return _load("make_example_submission")


def _store(tmp_path, n):
    return DP01JSONLStore(tmp_path / f"ledger{n}.jsonl")


def _judgments(register):
    return {k: getattr(v, "judgment", "?") for k, v in register.items() if not k.startswith("_")}


# ── ① 完整五点必须真的可审（否则「契约填得出来」就是空话）────────────────


def test_complete_example_all_points_auditable(example, tmp_path):
    summary = _judgments(
        audit_dp_register(example.build_submission(), _store(tmp_path, 1)))
    assert set(summary) == set(_POINTS)
    assert set(summary.values()) == {"auditable"}, summary


def test_observation_value_must_equal_declared_method(example, tmp_path):
    """★ 契约里最容易踩的一条：`value` 写散文 → 该点必成 conflicted。

    这是**实现行为**（观测值集合与声明方法做精确匹配），不是我的偏好。
    """
    sub = example.build_submission()
    obs = sub["decision_points"]["differential_method"]["evidence_observations"]
    obs[0]["value"] = "DESeq(dds) with design=~batch+condition"   # 散文，不等于方法标识
    summary = _judgments(audit_dp_register(sub, _store(tmp_path, 2)))
    assert summary["differential_method"] == "conflicted", summary
    # 其余点不受影响（逐点判定，不牵连）
    assert summary["filtering"] == "auditable"


def test_dp01_requires_low_count_filter_literal(example, tmp_path):
    """DP-01 的受控标识：declared_method ≠ "low_count_filter" → scientifically_limited。"""
    sub = example.build_submission()
    sub["decision_points"]["filtering"]["decision_declaration"]["method"] = "filterByExpr"
    sub["decision_points"]["filtering"]["evidence_observations"][0]["value"] = "filterByExpr"
    summary = _judgments(audit_dp_register(sub, _store(tmp_path, 3)))
    assert summary["filtering"] == "scientifically_limited", summary


# ── ② 批次级拒绝：形状不对 → 一笔账都不写 ────────────────────────────────


def test_missing_decision_point_rejects_whole_batch(example, tmp_path):
    sub = example.build_missing_point()
    ledger = tmp_path / "rejected.jsonl"
    with pytest.raises(ValueError, match="exactly the five points"):
        audit_dp_register(sub, DP01JSONLStore(ledger))
    assert not ledger.exists() or ledger.stat().st_size == 0, "被拒的批次不应留下账"


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda s: s["integrity_metadata"].update(
        {"identity": {"state": "confirmed", "note": "多带的键"}}), id="域多带一个键"),
    pytest.param(lambda s: s["integrity_metadata"].update({"extra": 1}), id="顶层多一个键"),
    pytest.param(lambda s: s["integrity_metadata"].update(
        {"identity": {"state": "banana"}}), id="state 超范围取值"),
])
def test_malformed_integrity_shape_rejects_whole_batch(example, tmp_path, mutate):
    sub = example.build_submission()
    mutate(sub)
    with pytest.raises(ValueError, match="invalid DP-01 integrity metadata"):
        audit_dp_register(sub, _store(tmp_path, 4))


# ── ③ 逐点反映：内容不全 → 收下，但把「不可审」标在该点上 ──────────────────


def test_missing_declaration_key_only_fails_that_point(example, tmp_path):
    """实测：声明缺键**不拒整批**，该点 integrity_failed，其余点照常可审。"""
    sub = example.build_bad_declaration()
    summary = _judgments(audit_dp_register(sub, _store(tmp_path, 5)))
    assert summary["filtering"] == "integrity_failed"
    assert summary["normalization"] == "auditable"
    assert len(summary) == 5, "整批不拒，所以五点都应有判定"


def test_unconfirmable_integrity_domain_fails_every_point(example, tmp_path):
    """`unverified` 是**合法取值**（不抛错），但没有任何点能通过。"""
    sub = example.build_unconfirmed_integrity()
    summary = _judgments(audit_dp_register(sub, _store(tmp_path, 6)))
    assert set(summary.values()) == {"integrity_failed"}


def test_missing_context_gives_not_auditable_everywhere(example, tmp_path):
    """上下文缺键与 `exclusions` 留空都在**点内部**判 → 每点 not_auditable。"""
    sub = example.build_missing_context()
    summary = _judgments(audit_dp_register(sub, _store(tmp_path, 7)))
    assert set(summary.values()) == {"not_auditable"}


# ── ④ 骨架：机器能填的机器填，人填的留占位 ───────────────────────────────


def test_skeleton_autofills_version_triple_not_declarations(example, tmp_path):
    sk = _load("make_submission_skeleton")
    s = sk.build("2026-09-13T12:00:00+00:00", "audit-x")
    triple = s["_skeleton_meta"]["version_snapshot_autofilled"]
    # 版本三元组由系统代填，不是占位符
    assert all(isinstance(v, str) and not v.startswith("<") for v in triple.values())
    assert set(triple) == {"ruleset_version", "ontology_version", "engine_version"}
    # 声明与观测必须留占位（不能替提交方编）
    assert set(s["decision_points"]) == set(_POINTS)
    for key in _POINTS:
        decl = s["decision_points"][key]["decision_declaration"]
        assert set(decl) == {"method", "threshold", "sample_rule", "unit", "source"}
        assert all(v.startswith("<") for v in decl.values())
    # 骨架自称「未校验、不是提交」
    assert "待填" in s["_skeleton_meta"]["what_this_is"]
    assert "不是" in s["_skeleton_meta"]["what_this_is_not"]


def test_skeleton_is_deterministic(example):
    sk = _load("make_submission_skeleton")
    a = json.dumps(sk.build("2026-09-13T12:00:00+00:00", "x"), ensure_ascii=False, sort_keys=True)
    b = json.dumps(sk.build("2026-09-13T12:00:00+00:00", "x"), ensure_ascii=False, sort_keys=True)
    assert a == b


# ── ⑤ 机械合理性检查：既要能过，也必须能红 ────────────────────────────────


def _freshness():
    return _load("check_submission_freshness")


def test_freshness_passes_on_the_example(example):
    fr = _freshness()
    from datetime import datetime, timezone
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    problems, _ = fr.check(example.build_submission(), now=now, max_days=365 * 5)
    assert problems == []


@pytest.mark.parametrize("mutate,expect", [
    pytest.param(
        lambda s: s["decision_points"]["filtering"]["evidence_observations"][0].update(
            {"observed_at": "2099-01-01T00:00:00+00:00"}), "在未来", id="未来观测时点"),
    pytest.param(
        lambda s: s["decision_points"]["normalization"]["evidence_observations"][0].update(
            {"valid_for_seconds": 100 * 365 * 86400}), "超过上限", id="一百年有效期"),
    pytest.param(
        lambda s: s["decision_points"]["differential_method"]["evidence_observations"][0].update(
            {"valid_for_seconds": 0}), "不是正整数", id="有效期为零"),
    pytest.param(
        lambda s: s["decision_points"]["significance_threshold"]["evidence_observations"][0].update(
            {"observed_at": "2026-09-13T00:00:00"}), "带时区", id="观测时点没有时区"),
])
def test_freshness_catches_impossible_values(example, mutate, expect):
    """★ 检查器必须能**红**。只证明它会绿是没用的。"""
    fr = _freshness()
    from datetime import datetime, timezone
    sub = example.build_submission()
    mutate(sub)
    problems, _ = fr.check(sub, now=datetime(2026, 10, 1, tzinfo=timezone.utc),
                           max_days=365 * 5)
    assert any(expect in p for p in problems), problems


def test_freshness_reports_no_double_count_for_future_moment(example):
    """回归：未来时点曾把「超过上限」也一起误报（端点上界 vs 窗口长度上界）。"""
    fr = _freshness()
    from datetime import datetime, timezone
    sub = example.build_submission()
    sub["decision_points"]["filtering"]["evidence_observations"][0]["observed_at"] = \
        "2099-01-01T00:00:00+00:00"
    problems, _ = fr.check(sub, now=datetime(2026, 10, 1, tzinfo=timezone.utc),
                           max_days=365 * 5)
    assert len(problems) == 1, problems
    assert "在未来" in problems[0]


# ── ⑥ n_samples（跨步骤科学连贯输入）：传递带出 + 缺失 fail-closed ──────────


def test_example_carries_structured_n_samples(example):
    """示例提交的 n_samples 是结构化对象：每组一个正整数，组名与 comparison 对齐。"""
    ctx = example.build_submission()["analysis_context"]
    ns = ctx.get("n_samples")
    assert isinstance(ns, dict) and set(ns) == {"treated", "control"}, ns
    assert all(isinstance(v, int) and v > 0 for v in ns.values())


def test_skeleton_marks_n_samples_required(example):
    """骨架把 n_samples 标成必填占位（与其它人填字段同等对待）。"""
    sk = _load("make_submission_skeleton")
    s = sk.build("2026-09-13T12:00:00+00:00", "audit-x")
    ns = s["analysis_context"].get("n_samples")
    assert isinstance(ns, str) and ns.startswith("<必填"), ns


def test_report_carries_n_samples_when_declared(example, tmp_path):
    """声明了 n_samples → 报告 context 原样带出（OPEN-1 B1 的落点）。"""
    reg = audit_dp_register(example.build_submission(), _store(tmp_path, 8))
    report = render_dp_report(reg)
    assert report["context"]["n_samples"] == {"treated": 8, "control": 8}


def test_missing_n_samples_is_null_not_fabricated(example, tmp_path):
    """缺失 n_samples → 报告如实标 null（不拒收、不猜、不填默认值）→ 连贯判定层另看。"""
    sub = example.build_submission()
    sub["analysis_context"].pop("n_samples")
    reg = audit_dp_register(sub, _store(tmp_path, 9))
    report = render_dp_report(reg)
    assert report["context"]["n_samples"] is None
