# -*- coding: utf-8 -*-
"""A1 表单 JS→payload 路径的行为守卫（2026-09-15，表单交互改造后新增）。

背景：`ui/a1_form.html` 的内嵌脚本此前只有 `node --check`（语法门），JS 真正
「把控件值汇成提交 payload」的路径没有任何机器验证。这次表单把有通用值的字段
改成「下拉 + 其他（手填）」后，该路径被重写，因此补一组**产物级断言**：
在真实 DOM 上跑内嵌脚本，验证——

1. 默认状态下 build() 产出的 payload 形状与字段契约完全一致（键、默认值、
   类型：valid_for_seconds 必须仍是数字）；
2. 「其他（手填）」与导入回填：表外自定义值正确进入 payload、手填框正确显示；
3. 必填校验与「观测值必须等于声明方法」一致性校验仍能报缺/报错。

运行方式：无头 Chrome 打开表单 HTML 副本（file://），注入探针脚本执行并读回
结果。浏览器不可用（如 CI 的 ubuntu runner 未装 Chrome）时整组跳过——门禁只
在机器能跑的时候生效，不影响 CI 绿。
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
FORM = REPO / "ui" / "a1_form.html"

_BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_BROWSER_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _find_browser() -> str | None:
    for cand in _BROWSER_CANDIDATES:
        if pathlib.Path(cand).is_file():
            return cand
    for name in _BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


BROWSER = _find_browser()


def _probe_results() -> list[dict]:
    """注入探针、无头执行、读回结果；返回 [{name, ok, extra}]。"""
    assert BROWSER, "需要 Chrome/Edge 才能跑（检测不到浏览器时应被上层 skip）"
    html = FORM.read_text(encoding="utf-8")
    probe_html = html.replace("</body>", _PROBE_JS + "</body>")
    probe = REPO / "_form_js_probe.html"
    probe.write_text(probe_html, encoding="utf-8", newline="\n")
    try:
        dom = subprocess.run(
            [BROWSER, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=5000", "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
    finally:
        probe.unlink(missing_ok=True)
    matches = re.findall(r"PROBE_START(.*?)PROBE_END", dom.stdout or "", re.S)
    if not matches:
        pytest.fail("探针未产出结果——表单内嵌脚本可能运行时报错")
    return json.loads(matches[-1])


_PROBE_JS = """
<script>
(function () {
  var res = [];
  function chk(name, cond, extra) { res.push({ name: name, ok: !!cond, extra: extra == null ? '' : String(extra) }); }
  try {
    var a = build();
    chk('audit_id 非空', !!a.audit_id, a.audit_id);
    chk('context 七键齐', ['project_id','analysis_id','comparison','data_type','audit_scope','intended_use','exclusions'].every(function(k){ return a.analysis_context[k] !== undefined; }));
    chk('n_samples 默认 JSON 对象', !!a.analysis_context.n_samples && a.analysis_context.n_samples.treated === 8 && a.analysis_context.n_samples.control === 8, JSON.stringify(a.analysis_context.n_samples));
    chk('comparison 默认值', a.analysis_context.comparison === 'treated_vs_control', a.analysis_context.comparison);
    chk('exclusions 默认值', a.analysis_context.exclusions === 'none', a.analysis_context.exclusions);
    chk('七域齐且 confirmed', ['identity','version','source','binding','snapshot','permission','unique_authority'].every(function(k){ return a.integrity_metadata[k] && a.integrity_metadata[k].state === 'confirmed'; }));
    chk('material_ids 为数组', Array.isArray(a.integrity_metadata.material_ids) && a.integrity_metadata.material_ids[0] === 'material-web-1', JSON.stringify(a.integrity_metadata.material_ids));
    chk('五点齐', ['filtering','normalization','differential_method','multiple_testing_correction','significance_threshold'].every(function(k){ return !!a.decision_points[k]; }));
    var f = a.decision_points.filtering;
    chk('声明五键齐', ['method','threshold','sample_rule','unit','source'].every(function(k){ return f.decision_declaration[k] !== undefined; }), JSON.stringify(f.decision_declaration));
    chk('source 固定 declared', f.decision_declaration.source === 'declared');
    chk('方法默认 low_count_filter', f.decision_declaration.method === 'low_count_filter', f.decision_declaration.method);
    chk('阈值默认 not_applicable', f.decision_declaration.threshold === 'not_applicable');
    chk('样本规则默认', f.decision_declaration.sample_rule === 'at_least_two_samples');
    chk('单位默认 gene', f.decision_declaration.unit === 'gene');
    var ev = f.evidence_observations[0];
    chk('证据八键齐', ['source_type','verification','evidence_role','value','observed_at','valid_for_seconds','provenance','supports'].every(function(k){ return ev[k] !== undefined; }), JSON.stringify(ev).slice(0, 220));
    chk('观测值=声明方法', ev.value === 'low_count_filter', ev.value);
    chk('有效期是数字', typeof ev.valid_for_seconds === 'number' && ev.valid_for_seconds === 31536000);
    chk('观测时刻带时区', /[+-]\\d{2}:\\d{2}$/.test(ev.observed_at), ev.observed_at);

    var card = document.querySelector('[data-p="filtering"]');
    setField(card, 'method', 'my_custom_filter');
    chk('「其他」自定义方法进 payload', build().decision_points.filtering.decision_declaration.method === 'my_custom_filter');
    var sel = document.querySelector('#comparison');
    sel.value = '__other__'; toggleOther(sel);
    sel.parentNode.querySelector('[data-f-other="comparison"]').value = 'tumor_vs_normal';
    chk('「其他」手填比较组进 payload', build().analysis_context.comparison === 'tumor_vs_normal');
    setField(document, 'intended_use', 'clinical exploratory use');
    chk('导入回填：表外值走手填框', build().analysis_context.intended_use === 'clinical exploratory use');
    var box = document.querySelector('[data-f-other="intended_use"]');
    chk('导入回填：手填框可见', !!box && box.style.display !== 'none');

    setField(card, 'unit', '');
    var problems = validate();
    chk('必填校验能报缺', problems.some(function (p) { return p.indexOf('单位') >= 0; }), problems.join('|'));
    var ev2 = card.querySelector('.evs > .ev-row');
    setField(ev2, 'value', 'wrong_method');
    var problems2 = validate();
    chk('观测值≠方法被抓', problems2.some(function (p) { return p.indexOf('观测值必须与声明方法一致') >= 0; }), problems2.join('|'));
    document.getElementById('n_samples').value = 'not-json';
    var problems3 = validate();
    chk('n_samples 非 JSON 被抓', problems3.some(function (p) { return p.indexOf('分组样本量') >= 0; }), problems3.join('|'));
    document.getElementById('n_samples').value = '{"treated": 8, "control": 8}';
  } catch (e) {
    res.push({ name: '脚本异常', ok: false, extra: e && e.message ? e.message : String(e) });
  }
  var pre = document.createElement('pre');
  var M1 = 'PROBE' + '_START', M2 = 'PROBE' + '_END';
  pre.textContent = M1 + JSON.stringify(res) + M2;
  document.body.appendChild(pre);
})();
</script>
"""


@pytest.mark.skipif(BROWSER is None, reason="本机/CI 无 Chrome/Edge，无法跑无头表单探针")
def test_form_js_payload_shape_and_defaults():
    results = _probe_results()
    failed = [r for r in results if not r["ok"]]
    assert not failed, "表单 JS→payload 契约断言失败：\n" + "\n".join(
        f"  ✗ {r['name']}: {r['extra']}" for r in failed)


@pytest.mark.skipif(BROWSER is None, reason="本机/CI 无 Chrome/Edge，无法跑无头表单探针")
def test_form_js_other_fallback_and_import_backfill():
    results = {r["name"]: r for r in _probe_results()}
    for name in (
        "「其他」自定义方法进 payload",
        "「其他」手填比较组进 payload",
        "导入回填：表外值走手填框",
        "导入回填：手填框可见",
    ):
        assert results[name]["ok"], f"{name}：{results[name]['extra']}"


@pytest.mark.skipif(BROWSER is None, reason="本机/CI 无 Chrome/Edge，无法跑无头表单探针")
def test_form_js_validation_flags_missing_and_mismatch():
    results = {r["name"]: r for r in _probe_results()}
    for name in ("必填校验能报缺", "观测值≠方法被抓", "n_samples 非 JSON 被抓"):
        assert results[name]["ok"], f"{name}：{results[name]['extra']}"
