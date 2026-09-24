"""跨步骤科学连贯判定（DP-COHERENCE）的单测。

覆盖（对应 ``DP-COHERENCE-CHECKLIST.md``，人类 2026-09-15 逐条拍板通过）：
- 三态 token 与中文对照锁定（改名即红）；
- 纯函数/确定性：同输入两次调用逐字相同；
- 不合成：无总分、无排名、无加权；条目形状固定；
- C1–C4 四条相邻对的全部决定性组合；
- G1–G3 三条全局（含 n_samples 缺失/形状不对/组名对不上/自由文本比较组）；
- fail-closed：声明缺失 → 「未知」→ needs_review，不抛错、不猜；
- 动作映射：coherent→无，needs_review→request_review，incoherent→request_evidence。

这些测试**不改产品代码**、**不写账本的判定**（register 照常走真实 seam）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bioaudit.api import audit_dp_register
from bioaudit.dp01_store import DP01JSONLStore
from bioaudit.dp_coherence import (
    ACTION_FOR_STATE,
    COHERENCE_TOKENS,
    COHERENCE_ZH,
    derive_cross_step_coherence,
)

_OBSERVED_AT = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc).isoformat(
    timespec="seconds")

_DOMAINS = ("identity", "version", "source", "binding", "snapshot",
            "permission", "unique_authority")


def _decl(method, threshold="not_applicable", sample_rule="at_least_two_samples",
          unit="gene"):
    return {"method": method, "threshold": threshold,
            "sample_rule": sample_rule, "unit": unit, "source": "declared"}


def _obs(method):
    return {"source_type": "observed", "verification": "confirmed",
            "value": method, "observed_at": _OBSERVED_AT,
            "valid_for_seconds": 365 * 24 * 3600}


def _ctx(comparison="treated_vs_control", n_samples=None):
    ctx = {
        "project_id": "p-coh", "analysis_id": "a-coh",
        "comparison": comparison, "data_type": "bulk_rnaseq",
        "audit_scope": "five-point-mvp", "intended_use": "scientific_analysis",
        "exclusions": "none",
    }
    if n_samples is not None:
        ctx["n_samples"] = n_samples
    return ctx


def _make_register(points, context, tmp_path, audit_id="coh-x"):
    entries = {}
    for key, decl in points.items():
        if isinstance(decl, dict) and ("decision_declaration" in decl
                                       or "evidence_observations" in decl):
            # 已是「点条目」形状（如故意缺声明的反例）
            entries[key] = dict(decl)
        else:
            entries[key] = {"decision_declaration": decl,
                            "evidence_observations": [_obs(decl["method"])]}
    request = {
        "audit_id": audit_id,
        "analysis_context": context,
        "integrity_metadata": {
            **{d: {"state": "confirmed"} for d in _DOMAINS},
            "material_ids": ["m-1"], "pending_targets": ["d"],
        },
        "decision_points": entries,
    }
    return audit_dp_register(request, DP01JSONLStore(tmp_path / f"{audit_id}.jsonl"))


def _derive(points, context, tmp_path, audit_id="coh-x"):
    register = _make_register(points, context, tmp_path, audit_id=audit_id)
    return derive_cross_step_coherence(register, context), register


def _items(view):
    return {item["id"]: item for item in view["items"]}


def _full_points(method="deseq2", correction="BH", threshold="padj_0.05",
                 filtering="low_count_filter", normalization="tmm",
                 sample_rule="at_least_two_samples", f_threshold="min_count_10"):
    return {
        "filtering": _decl(filtering, threshold=f_threshold),
        "normalization": _decl(normalization),
        "differential_method": _decl(method, sample_rule=sample_rule),
        "multiple_testing_correction": _decl(correction),
        "significance_threshold": _decl(threshold, threshold="padj<=0.05"),
    }


# ── token 与中文对照锁定 ──────────────────────────────────────────────────


def test_tokens_and_zh_are_locked():
    assert COHERENCE_TOKENS == ("coherent", "needs_review", "incoherent")
    assert COHERENCE_ZH == {"coherent": "衔接一致", "needs_review": "需复核",
                            "incoherent": "衔接矛盾"}
    assert set(ACTION_FOR_STATE) == set(COHERENCE_TOKENS)


# ── 纯函数 / 确定性 ───────────────────────────────────────────────────────


def test_pure_and_deterministic(tmp_path):
    points = _full_points()
    ctx = _ctx(n_samples={"treated": 8, "control": 8})
    view_a, _ = _derive(points, ctx, tmp_path, audit_id="coh-det-a")
    view_b, _ = _derive(points, ctx, tmp_path, audit_id="coh-det-b")
    assert repr(view_a) == repr(view_b)
    assert view_a == view_b


# ── 不合成：无总分/排名/加权 ───────────────────────────────────────────────


def test_no_aggregation(tmp_path):
    view, _ = _derive(_full_points(), _ctx(n_samples={"treated": 8, "control": 8}),
                      tmp_path)
    assert set(view) == {"items", "tokens", "note"}
    assert len(view["items"]) == 7
    for item in view["items"]:
        assert set(item) == {"id", "kind", "label", "state", "reason", "action",
                             "inputs"}
        assert item["state"] in COHERENCE_TOKENS
        assert item["action"] in (None, "request_evidence", "request_review")
        assert not any(k in item for k in ("score", "rank", "total", "weight"))


def test_kinds_are_4_pairs_plus_3_globals(tmp_path):
    view, _ = _derive(_full_points(), _ctx(n_samples={"treated": 8, "control": 8}),
                      tmp_path)
    kinds = [item["kind"] for item in view["items"]]
    assert kinds.count("pair") == 4 and kinds.count("global") == 3


# ── C1 · 过滤 → 归一化 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("filtering,normalization,expected", [
    ("low_count_filter", "tmm", "coherent"),
    ("low_count_filter", "raw_counts_no_normalization", "coherent"),
    ("no_filtering", "tpm", "coherent"),
    ("low_count_filter", "tpm", "needs_review"),
    ("low_count_filter", "some_unknown_normalizer", "needs_review"),
    ("some_unknown_filter", "tmm", "needs_review"),
])
def test_c1_filtering_to_normalization(tmp_path, filtering, normalization, expected):
    view, _ = _derive(_full_points(filtering=filtering, normalization=normalization),
                      _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    assert _items(view)["pair_1_filtering_to_normalization"]["state"] == expected


# ── C2 · 归一化 → 差异方法 ─────────────────────────────────────────────────


@pytest.mark.parametrize("normalization,method,expected", [
    ("tpm", "deseq2", "incoherent"),
    ("rpkm", "edger", "incoherent"),
    ("tmm", "deseq2", "coherent"),
    ("raw_counts_no_normalization", "deseq2", "coherent"),
    ("tpm", "wilcoxon_rank_sum", "coherent"),
    ("tpm", "ttest_equal_variance", "needs_review"),
    ("tmm", "ttest_equal_variance", "needs_review"),
    ("tmm", "some_unknown_method", "needs_review"),
])
def test_c2_normalization_to_differential(tmp_path, normalization, method, expected):
    view, _ = _derive(_full_points(normalization=normalization, method=method),
                      _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    assert _items(view)["pair_2_normalization_to_differential"]["state"] == expected


# ── C3 · 差异方法 → 多重检验 ───────────────────────────────────────────────


@pytest.mark.parametrize("method,correction,expected", [
    ("deseq2", "BH", "coherent"),
    ("edger", "bh", "coherent"),
    ("deseq2", "Bonferroni", "needs_review"),
    ("deseq2", "q_value_storey", "needs_review"),
    ("wilcoxon_rank_sum", "BH", "coherent"),
    ("ttest_equal_variance", "BY", "coherent"),
    ("deseq2", "no_correction", "coherent"),
    ("wilcoxon_rank_sum", "no_correction", "coherent"),
])
def test_c3_differential_to_correction(tmp_path, method, correction, expected):
    view, _ = _derive(_full_points(method=method, correction=correction),
                      _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    assert _items(view)["pair_3_differential_to_correction"]["state"] == expected


# ── C4 · 多重检验 → 阈值 ───────────────────────────────────────────────────


@pytest.mark.parametrize("correction,threshold,expected", [
    ("BH", "padj_0.05", "coherent"),
    ("BH", "padj_and_logFC", "coherent"),
    ("no_correction", "padj_0.05", "incoherent"),
    ("no_correction", "raw_p_value", "coherent"),
    ("BH", "raw_p_value", "needs_review"),
    ("BH", "logFC_only", "needs_review"),
    ("no_correction", "logFC_only", "needs_review"),
    ("BH", "no_threshold", "needs_review"),
])
def test_c4_correction_to_threshold(tmp_path, correction, threshold, expected):
    view, _ = _derive(_full_points(correction=correction, threshold=threshold),
                      _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    assert _items(view)["pair_4_correction_to_threshold"]["state"] == expected


# ── G1 · 过滤口径 ↔ 阈值口径 ───────────────────────────────────────────────


@pytest.mark.parametrize("filtering,f_threshold,threshold,expected", [
    ("low_count_filter", "min_count_10", "padj_0.05", "coherent"),
    ("low_count_filter", "min_count_10", "padj_and_logFC", "coherent"),
    ("no_filtering", "not_applicable", "padj_0.05", "coherent"),
    ("low_count_filter", "min_count_10", "raw_p_value", "needs_review"),
    ("low_count_filter", "not_applicable", "padj_0.05", "needs_review"),
    ("low_count_filter", "min_count_10", "no_threshold", "needs_review"),
])
def test_g1_filter_scope_vs_threshold(tmp_path, filtering, f_threshold, threshold,
                                      expected):
    view, _ = _derive(_full_points(filtering=filtering, f_threshold=f_threshold,
                                   threshold=threshold),
                      _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    assert _items(view)["global_1_filter_scope_vs_threshold_scope"]["state"] == expected


# ── G2 · 样本量 ↔ 方法敏感性（依赖 n_samples） ─────────────────────────────


def test_g2_missing_n_samples_fails_closed(tmp_path):
    view, _ = _derive(_full_points(), _ctx(), tmp_path)  # 无 n_samples
    item = _items(view)["global_2_group_sizes_vs_method_sensitivity"]
    assert item["state"] == "needs_review"
    assert "未知" in item["reason"]
    assert item["action"] == "request_review"


def test_g2_malformed_n_samples_fails_closed(tmp_path):
    view, _ = _derive(_full_points(),
                      _ctx(n_samples={"treated": 0, "control": 8}), tmp_path)
    item = _items(view)["global_2_group_sizes_vs_method_sensitivity"]
    assert item["state"] == "needs_review"
    assert "未知" in item["reason"]


def test_g2_group_names_must_align_with_comparison(tmp_path):
    view, _ = _derive(_full_points(),
                      _ctx(n_samples={"case": 5, "control": 5}), tmp_path)
    item = _items(view)["global_2_group_sizes_vs_method_sensitivity"]
    assert item["state"] == "needs_review"
    assert "对不上" in item["reason"]


def test_g2_free_text_comparison_marked_for_review(tmp_path):
    view, _ = _derive(_full_points(),
                      _ctx(comparison="tumor vs normal tissue",
                           n_samples={"tumor": 5, "normal": 5}), tmp_path)
    item = _items(view)["global_2_group_sizes_vs_method_sensitivity"]
    assert item["state"] == "needs_review"


@pytest.mark.parametrize("n_samples,method,expected", [
    ({"treated": 1, "control": 1}, "deseq2", "incoherent"),
    ({"treated": 1, "control": 3}, "edger", "incoherent"),
    ({"treated": 1, "control": 1}, "wilcoxon_rank_sum", "incoherent"),
    ({"treated": 2, "control": 2}, "deseq2", "needs_review"),
    ({"treated": 3, "control": 3}, "deseq2", "coherent"),
    ({"treated": 8, "control": 8}, "limma_voom", "coherent"),
    ({"treated": 5, "control": 5}, "wilcoxon_rank_sum", "coherent"),
    ({"treated": 5, "control": 5}, "ttest_equal_variance", "needs_review"),
])
def test_g2_min_n_vs_method_sensitivity(tmp_path, n_samples, method, expected):
    view, _ = _derive(_full_points(method=method),
                      _ctx(n_samples=n_samples), tmp_path)
    assert _items(view)["global_2_group_sizes_vs_method_sensitivity"]["state"] == expected


# ── G3 · 样本规则 ↔ 方法假设 ───────────────────────────────────────────────


@pytest.mark.parametrize("sample_rule,n_samples,method,expected", [
    ("no_replication", {"treated": 4, "control": 4}, "deseq2", "incoherent"),
    ("no_replication", {"treated": 4, "control": 4}, "wilcoxon_rank_sum", "incoherent"),
    ("no_replication", None, "deseq2", "incoherent"),
    ("at_least_three_samples", {"treated": 2, "control": 3}, "deseq2", "incoherent"),
    ("at_least_two_samples", {"treated": 1, "control": 2}, "deseq2", "incoherent"),
    ("at_least_two_samples", {"treated": 3, "control": 3}, "deseq2", "coherent"),
    ("paired_by_batch", {"treated": 3, "control": 3}, "ttest_equal_variance", "needs_review"),
    ("paired_by_batch", {"treated": 3, "control": 3}, "deseq2", "needs_review"),
    ("not_applicable", {"treated": 3, "control": 3}, "deseq2", "needs_review"),
    ("at_least_two_samples", None, "deseq2", "needs_review"),
])
def test_g3_sample_rule_vs_method(tmp_path, sample_rule, n_samples, method, expected):
    ctx = _ctx()
    if n_samples is not None:
        ctx["n_samples"] = n_samples
    view, _ = _derive(_full_points(method=method, sample_rule=sample_rule), ctx,
                      tmp_path)
    assert _items(view)["global_3_sample_rule_vs_method_assumptions"]["state"] == expected


# ── fail-closed：声明缺失 / 点缺失 ────────────────────────────────────────


def test_missing_declarations_mark_unknown_not_crash(tmp_path):
    """声明缺失（fail-closed 的点，declaration=None）→ 涉它的条目如实「未知」，不抛错。"""
    points = _full_points()
    points["normalization"] = {"evidence_observations": [_obs("tmm")]}  # 缺 decision_declaration
    view, _ = _derive(points, _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    items = _items(view)
    # 归一化声明缺失 → 涉及它的一对都只标记复核
    assert items["pair_1_filtering_to_normalization"]["state"] == "needs_review"
    assert items["pair_2_normalization_to_differential"]["state"] == "needs_review"
    assert all(item["state"] in COHERENCE_TOKENS for item in view["items"])


# ── 动作映射 ───────────────────────────────────────────────────────────────


def test_actions_follow_state_mapping(tmp_path):
    view, _ = _derive(_full_points(), _ctx(), tmp_path)  # 无 n_samples
    incoherent_items = [i for i in view["items"] if i["state"] == "incoherent"]
    reviewed = [i for i in view["items"] if i["state"] == "needs_review"]
    # 缺 n_samples 的整条链上不应出现 incoherent（保守：全部只标记复核）
    assert incoherent_items == []
    assert all(i["action"] == "request_review" for i in reviewed)


def test_incoherent_emits_request_evidence(tmp_path):
    points = _full_points(normalization="tpm")  # C2：长度归一化 + 计数模型
    view, _ = _derive(points, _ctx(n_samples={"treated": 8, "control": 8}), tmp_path)
    item = _items(view)["pair_2_normalization_to_differential"]
    assert item["state"] == "incoherent"
    assert item["action"] == "request_evidence"


# ── 报告集成：键存在、位置、确定性 ────────────────────────────────────────


def test_report_carries_derived_coherence_view(tmp_path):
    register = _make_register(_full_points(), _ctx(n_samples={"treated": 8, "control": 8}),
                              tmp_path)
    from bioaudit.api import render_dp_report
    report = render_dp_report(register)
    assert list(report).index("cross_step_coherence") == list(report).index("per_point") + 1
    view = report["cross_step_coherence"]
    assert len(view["items"]) == 7
    # 派生视图不落账：报告里不携带任何新的账本形态
    assert view["note"]


# ── 呈现层（chain_view 与两处页面共用）：表下小段 + 占位句已删 ──────────────


def _chain_overview(report):
    import importlib.util
    import sys
    from pathlib import Path
    ui = Path(__file__).resolve().parent.parent / "ui"
    if "chain_view" not in sys.modules:
        spec = importlib.util.spec_from_file_location("chain_view", ui / "chain_view.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["chain_view"] = mod
    return sys.modules["chain_view"].chain_overview(report)


def test_chain_view_renders_coherence_small_section(tmp_path):
    register = _make_register(_full_points(), _ctx(n_samples={"treated": 8, "control": 8}),
                              tmp_path)
    from bioaudit.api import render_dp_report
    html = _chain_overview(render_dp_report(register))
    assert "跨步骤科学连贯" in html
    for zh in ("衔接一致", "需复核", "衔接矛盾"):
        assert zh in html, zh
    assert "本页不判定" not in html
    assert html.count("chv-coh-row") == 7


def test_chain_view_renders_honest_line_when_view_missing(tmp_path):
    """旧报告/夹具没有派生视图时：如实渲一行「未派生」，不静默省略。"""
    register = _make_register(_full_points(), _ctx(n_samples={"treated": 8, "control": 8}),
                              tmp_path)
    from bioaudit.api import render_dp_report
    report = render_dp_report(register)
    report.pop("cross_step_coherence")
    html = _chain_overview(report)
    assert "未派生跨步骤科学连贯视图" in html
    assert "本页不判定" not in html
