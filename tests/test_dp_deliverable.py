"""对外交付文档（B1）的测试：确定性、渲染完整性、校验器会绿**也会红**。

背景
----
`DP-PRODUCT-SURFACE-DECISIONS.md` §二 给这份文档定了 8 条硬约束。
散文会漂，测试不会——所以：生成必须可复现（同输入同字节）、
校验器必须能过真实产物、**也必须在被污染的产物上变红**。
一个只证明会绿的校验器没有用；这正是本项目「产物级断言」的一贯纪律。

本文件不新增公共语义、不改产品代码、不动规则文件。
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
from unittest import mock

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
UI = REPO / "ui"

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(UI))  # generate_deliverable 内部 import generate_report（同目录）

_FIXED_NOW = "2026-09-13T12:00:00+00:00"


def _load(name: str):
    saved = sys.stdout
    try:
        sys.stdout = io.StringIO()
        spec = importlib.util.spec_from_file_location(name, UI / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load("generate_deliverable")


@pytest.fixture(scope="module")
def ver():
    return _load("verify_deliverable")


def _render(gen, now: str = _FIXED_NOW, source_label: str | None = None) -> str:
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    if source_label is not None:
        return gen.render_deliverable(report, now=now, generated_at=now,
                                      source_label=source_label)
    return gen.render_deliverable(report, now=now, generated_at=now)


def _run_verifier(ver, html_path: pathlib.Path, report_path: pathlib.Path,
                  source_anchor: str | None = None):
    with mock.patch.object(ver, "DELIVERABLE", html_path), \
         mock.patch.object(ver, "REPORT_JSON", report_path):
        if source_anchor is not None:
            with mock.patch.object(ver, "SOURCE_ANCHOR", source_anchor):
                return ver.main()
        return ver.main()


# ── ① 生成确定性 ───────────────────────────────────────────────────────────


def test_deliverable_is_deterministic(gen):
    a = _render(gen)
    b = _render(gen)
    assert a == b  # 含固定锚 → 两次生成逐字节一致


# ── ② 渲染完整性：五点 + 四轴都在，且锚在场 ────────────────────────────────


def test_deliverable_renders_all_five_points(gen):
    html_text = _render(gen)
    for name in ("filtering", "normalization", "differential_method",
                 "multiple_testing_correction", "significance_threshold"):
        assert f'id="point-{name}"' in html_text, f"缺 {name} 决策点"


def test_deliverable_renders_four_axes_separately(gen):
    html_text = _render(gen)
    for axis in ("inference_type", "explanation_depth", "scope", "validation"):
        assert f'id="axis-{axis}"' in html_text, f"缺轴 {axis}"


def test_deliverable_carries_anchor_and_source(gen):
    html_text = _render(gen)
    assert _FIXED_NOW in html_text
    assert "ui/report.sample.json" in html_text


# ── ③ 校验器会绿：真实产物必须通过 ─────────────────────────────────────────


def test_verifier_passes_on_generated_artifact(gen, ver, tmp_path):
    html_path = tmp_path / "deliverable.html"
    html_path.write_text(_render(gen), encoding="utf-8", newline="\n")
    assert _run_verifier(ver, html_path, UI / "report.sample.json") == 0


def test_verifier_passes_on_pipeline_shaped_artifact(gen, ver, tmp_path):
    """管线产物形状：lane_and_release=null（真实提交无 lane 请求）+ 账户源锚。

    修复前约束 4 对 null 调 .get("decisions") 崩溃（AttributeError）；修复后
    验证器必须正常通过，且渲染来源锚不再硬编码为演示串（SOURCE_ANCHOR 可参数化）。
    """
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    report["lane_and_release"] = None
    src = "project-a1-example/analysis-a1-example（audit audit-a1-example-0001）"
    html_text = _render(gen, source_label=src)
    html_path = tmp_path / "pipeline_deliverable.html"
    html_path.write_text(html_text, encoding="utf-8", newline="\n")
    report_path = tmp_path / "pipeline_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False),
                           encoding="utf-8", newline="\n")
    assert _run_verifier(ver, html_path, report_path, source_anchor=src) == 0


# ── ④ 校验器会红：被污染的产物必须被拦 ─────────────────────────────────────


def test_verifier_fails_on_affirmative_conclusion_word(gen, ver, tmp_path):
    html_text = _render(gen).replace("不是验收判定", "审计通过，本账户合格")
    html_path = tmp_path / "bad.html"
    html_path.write_text(html_text, encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as ei:
        _run_verifier(ver, html_path, UI / "report.sample.json")
    assert ei.value.code == 1


def test_verifier_fails_when_an_axis_is_merged(gen, ver, tmp_path):
    """把四轴压成一段（违规形态）→ 必须被拦。"""
    html_text = _render(gen)
    # 删掉两个轴块，只留两个 →「四轴分开呈现」不再成立
    html_text = html_text.replace('<section class="axis" id="axis-scope">', "")
    # 删掉对应闭合就不精确了：直接去掉整块结尾更稳——改为去掉轴 3 的内容主体
    import re as _re
    html_text = _re.sub(r'<section class="axis" id="axis-inference_type">.*?</section>',
                        "", html_text, flags=_re.S)
    html_text = _re.sub(r'<section class="axis" id="axis-validation">.*?</section>',
                        "", html_text, flags=_re.S)
    html_path = tmp_path / "merged.html"
    html_path.write_text(html_text, encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as ei:
        _run_verifier(ver, html_path, UI / "report.sample.json")
    assert ei.value.code == 1


def test_verifier_fails_when_anchor_is_missing(gen, ver, tmp_path):
    html_text = _render(gen).replace("ui/report.sample.json", "都删了")
    html_path = tmp_path / "noanchor.html"
    html_path.write_text(html_text, encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit) as ei:
        _run_verifier(ver, html_path, UI / "report.sample.json")
    assert ei.value.code == 1


# ── ⑤ 链条总览（两个形态共用同一片段，顺序是唯一的）────────────────────────


def test_chain_overview_is_the_spine_in_pipeline_order():
    chv = _load("chain_view")
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    frag = chv.chain_overview(report)
    names = ["低表达过滤", "归一化", "差异分析方法", "多重检验校正", "显著性/效应量阈值"]
    pos = -1
    for name in names:
        at = frag.find(name)
        assert at > pos, f"链条顺序不对：{name}"
        pos = at
    assert "不产生任何总分" in frag and "不是总评" in frag


def test_deliverable_contains_the_chain_overview(gen):
    html_text = _render(gen)
    assert "链条总览" in html_text
    names = ["低表达过滤", "归一化", "差异分析方法", "多重检验校正", "显著性/效应量阈值"]
    pos = -1
    for name in names:
        at = html_text.find(name)
        assert at > pos, f"交付文档链条顺序不对：{name}"
        pos = at


def test_page_renders_the_chain_overview():
    gr = _load("generate_report")
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    html_text = gr.render_html(report)
    assert 'id="chain"' in html_text
    # 顺序只在链条块内比：同名的步骤名在导读/卡片里也会出现，整页查找会误判
    start = html_text.find('id="chain"')
    end = html_text.find('id="act-one"', start)
    block = html_text[start:end]
    names = ["低表达过滤", "归一化", "差异分析方法", "多重检验校正", "显著性/效应量阈值"]
    pos = -1
    for name in names:
        at = block.find(name)
        assert at > pos, f"检查页链条顺序不对：{name}"
        pos = at


def test_page_navigation_targets_all_exist():
    """导航-章节-深度统一模型：nav 的每个 data-goto 必须指向页面里存在且唯一的 id。"""
    import re as _re
    gr = _load("generate_report")
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    html_text = gr.render_html(report)
    links = _re.findall(r'<a\b[^>]*data-goto="([a-z0-9_-]+)"', html_text)
    targets = _re.findall(r'<(?:section|article|details)\b[^>]*id="([a-z0-9_-]+)"', html_text)
    assert links, "导航不应为空"
    missing = [g for g in links if g not in set(targets)]
    assert missing == [], f"nav 有孤儿目标：{missing}"
    dups = [k for k in set(targets) if targets.count(k) > 1]
    assert dups == [], f"id 重复会导致锚点歧义：{dups}"


def test_page_point_details_are_targetable():
    """五步的点级详情都要能直接跳转（曾缺 id 导致导航点不到）。"""
    import re as _re
    gr = _load("generate_report")
    report = json.loads((UI / "report.sample.json").read_text(encoding="utf-8"))
    html_text = gr.render_html(report)
    targets = _re.findall(r'<(?:section|article|details)\b[^>]*id="([a-z0-9_-]+)"', html_text)
    for pid in ("point-filtering", "point-normalization", "point-differential_method",
                "point-multiple_testing_correction", "point-significance_threshold"):
        assert pid in targets, f"点级详情 {pid} 不可定位"