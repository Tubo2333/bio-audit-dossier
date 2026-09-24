"""对外交付文档生成器（B1：另出正式文档 · 单文件 HTML，可打印为 PDF）。

为什么是「渲染快照」而不是「报告」
--------------------------------
`DP-PRODUCT-SURFACE-DECISIONS.md` §二 给这份文档定了 8 条硬约束，第一条就是：
**它不能成为第二权威**——它是对某个账户的**渲染**，权威仍在唯一容器里。
所以本生成器只做一件事：把 **同一份报告数据**（`ui/report.sample.json`，与检查页同源）
渲染成**可存档的单文件 HTML**。它**不产生**新结论、不给新授权、不做总评。

为什么同源
----------
检查页读 `ui/report.sample.json`（`ui/verify_report.py` 的 `REPORT_JSON`）。
交付文档也读它——于是「页面讲的故事」与「文档讲的故事」永远来自同一份数据，
不会出现页面说 A、文档说 B 的分叉。

语言层（2C 笔 2，2026-09-15）
-----------------------------
与检查页同一套词表与译文：判词/状态词走 `zh_terms.py`（中文（原词）成对），
句子走 `zh_bi.py`（中文为主 + 英文原文成对，查不到对照时照录英文原文并标明）；
字段结构一律中文标签 + 原值代码样式，不再把 Python 字典 repr 印进文档。

**本文件不做什么**：不新增公共语义、不改规则文件、不写账本、不做判定、
不声称 acceptance / release / lane 授权 / runtime proof / deployment / Package A closure。

用法
----
    .\\.venv\\Scripts\\python.exe ui\\generate_deliverable.py --out ui\\deliverable.html
    # 固定生成时刻（可复现/可测试）：
    .\\.venv\\Scripts\\python.exe ui\\generate_deliverable.py --now 2026-09-13T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

# 注意：这里**故意不**像单机脚本那样改写 sys.stdout。
# 本模块会被 submit_analysis 等进程 import；若每个模块都 `sys.stdout = TextIOWrapper(...)`，
# 嵌套包装会把彼此的缓冲区关掉（实测踩过：I/O operation on closed file）。
# 统一靠 PYTHONIOENCODING=utf-8 保证控制台编码。

REPO = pathlib.Path(__file__).resolve().parent.parent
UI = pathlib.Path(__file__).resolve().parent
REPORT_JSON = UI / "report.sample.json"

# 报告原文里带轻量标记（`code`、**加粗**），必须渲染成排版效果而不是让记号外露——
# 与页面共用同一转换（单一事实源，避免两个渲染器对同一文本处理不一致）。
sys.path.insert(0, str(UI))
from generate_report import to_html as md  # noqa: E402  (同仓库同目录，页面渲染器)
import chain_view as chv  # noqa: E402  (链条总览：检查页与交付文档共用的单一事实源)
import zh_bi  # noqa: E402  (句子级中英对照，与检查页同表)
import zh_terms as zh  # noqa: E402  (状态词/判词/档位的中文（原词）对照，与检查页同表)

_AXIS_ORDER = ("inference_type", "explanation_depth", "scope", "validation")
_AXIS_NAMES = {
    "inference_type": "推断类型（inference_type）",
    "explanation_depth": "解释深度（explanation_depth）",
    "scope": "适用范围（scope）",
    "validation": "验证（validation）",
}


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _lab(zh_word: str, orig: Any) -> str:
    """固定词汇成对：中文 + 原词（小字）——判词/状态词/档位一律这种呈现。"""
    return (f'<span class="lab-zh">{esc(zh_word)}</span>'
            f'<span class="lab-orig">{esc(orig)}</span>')


def _bi(value: Any) -> str:
    """中文（主）+ 英文原文（副）——自由文本句子的双语对照。

    无对照时照录英文原文（不再出现「暂无中文对照」占位：值/句子照录即可对账）。
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    zh_text = zh_bi.zh_for(value)
    if not zh_text:
        return f'<span class="en">{md(value)}</span>'
    return (f'<span class="bi-zh">{md(zh_text)}</span> '
            f'<span class="en">{md(value)}</span>')


def _kv_html(label: str, body: str) -> str:
    if not body:
        return ""
    return f'<dt>{esc(label)}</dt><dd>{body}</dd>'


def _kv_code(label: str, value: Any) -> str:
    if value in (None, ""):
        return ""
    return _kv_html(label, f'<code>{esc(value)}</code>')


def _kv_bi(label: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return _kv_html(label, _bi(value))


def _kv_lab(label: str, table: dict[str, Any], value: Any) -> str:
    """枚举值：中文（原词）成对呈现（词表查不到则原词代码样式）。"""
    if value in (None, ""):
        return ""
    if isinstance(value, str) and value in table:
        entry = table[value]
        zh_word = entry[0] if isinstance(entry, tuple) else entry
        return _kv_html(label, _lab(zh_word, value))
    return _kv_code(label, value)


def _kv_bool(label: str, value: Any) -> str:
    if value is None:
        return ""
    return _kv_html(label, f'{"是" if value else "否"}<span class="lab-orig">{esc(str(value))}</span>')


def _axis_block(key: str, axis: dict[str, Any]) -> str:
    rows = []
    evaluable = axis.get("evaluable")
    if evaluable is not None:
        rows.append(_kv_bool(f'{zh.FIELD.get("evaluable", "能不能评估")}（evaluable）',
                             evaluable))
    for field in ("completeness", "scientific_evidence"):
        value = axis.get(field)
        if value in (None, ""):
            continue
        rows.append(_kv_lab(f'{zh.FIELD.get(field, field)}（{field}）', zh.LEVEL, value))
    auditability = axis.get("auditability")
    if auditability:
        rows.append(_kv_lab(f'{zh.FIELD.get("auditability", "可评估性")}（auditability）',
                            zh.AUDITABILITY, auditability))
    residual = axis.get("residual_unordered")
    if residual is not None:
        # 标签先取出再插值：f-string 的替换字段**不得跨物理行**（跨行是 PEP 701，
        # 3.12 才有的语法）。本项目 requires-python >= 3.10，CI 双矩阵跑 3.10，
        # 跨行会让 3.10 直接把本文件判为 SyntaxError。渲染结果不变。
        field_label = zh.FIELD.get("residual_unordered", "存在残留、且无排序含义")
        rows.append(_kv_bool(f'{field_label}（residual_unordered）', residual))
    conditions = axis.get("conditions") or []
    if conditions:
        cond_bits = []
        for c in conditions:
            zh_text = zh_bi.zh_for(str(c))
            if zh_text:
                cond_bits.append(_lab(zh_text, c))
            else:
                cond_bits.append(f'<code>{esc(c)}</code>')
        rows.append(_kv_html(f'{zh.FIELD.get("conditions", "要留意的点")}（conditions）',
                             "；".join(cond_bits)))
    prohibited = axis.get("prohibited_interpretations") or []
    if prohibited:
        items = "".join(f"<li>{_bi(p)}</li>" for p in prohibited)
        # 同上：替换字段不跨行（3.10 兼容）。渲染结果不变。
        field_label = zh.FIELD.get("prohibited_interpretations", "明令禁止的解释")
        rows.append(_kv_html(f'{field_label}（prohibited_interpretations）',
                             f"<ul class=\"plain\">{items}</ul>"))
    detail = axis.get("detail")
    if detail:
        rows.append(_kv_bi(f'{zh.FIELD.get("detail", "说明")}（detail）', detail))
    body = "\n".join(rows)
    if not body:
        body = "<p>（该轴无可呈现内容）</p>"
    return (f'<section class="axis" id="axis-{esc(key)}">'
            f'<h3>{esc(_AXIS_NAMES.get(key, key))}</h3><dl>{body}</dl></section>')


def _evidence_rows(items: list[Any]) -> str:
    rows = []
    for it in items or []:
        parts = []
        source = it.get("source_type")
        if source:
            parts.append("来源类型 " + _lab(zh.SOURCE.get(source, (source, ""))[0], source))
        verification = it.get("verification")
        if verification:
            parts.append("核实 " + _lab(zh.VERIFICATION.get(verification, (verification, ""))[0],
                                        verification))
        role = it.get("evidence_role")
        if role:
            parts.append("证据角色 " + _lab(zh.EVIDENCE_ROLE.get(role, (role, ""))[0], role))
        if it.get("value") is not None:
            parts.append(f"观测值：<code>{esc(it.get('value'))}</code>")
        if it.get("observed_at"):
            obs = f"观测时点 <code>{esc(it.get('observed_at'))}</code>"
            if it.get("freshness_meaning"):
                obs += f"（{_bi(it.get('freshness_meaning'))}）"
            parts.append(obs)
        if it.get("supports"):
            parts.append(f"为「{_bi(it.get('supports'))}」作证")
        if it.get("provenance"):
            parts.append(f"来源：{_bi(it.get('provenance'))}")
        if it.get("provenance_subject"):
            parts.append(f"关涉对象：{_bi(it.get('provenance_subject'))}")
        rows.append('<div class="ev"><span class="ev-lead">' + "；".join(parts)
                    + "</span></div>")
    return "\n".join(rows) if rows else "<p>（无观测证据）</p>"


def _gap_rows(gaps: list[Any]) -> str:
    out = []
    for g in gaps or []:
        if not isinstance(g, dict):
            out.append(f'<div class="ev"><span class="ev-lead">{md(str(g))}</span></div>')
            continue
        bits = []
        kind = g.get("kind")
        if kind:
            if kind in zh.GAP_KIND:
                bits.append("种类 " + _lab(zh.GAP_KIND[kind], kind))
            else:
                bits.append(f"种类 <code>{esc(kind)}</code>")
        source = g.get("source")
        if source:
            bits.append("来源 " + _lab(zh.SOURCE.get(source, (source, ""))[0], source))
        state = g.get("state")
        if state:
            bits.append("状态 " + _lab(zh.VERIFICATION.get(state, (state, ""))[0], state))
        target = g.get("target")
        if target:
            bits.append(f"针对什么 <code>{esc(target)}</code>")
        out.append('<div class="ev"><span class="ev-lead">' + "；".join(bits)
                   + "</span></div>")
    return "\n".join(out)


def _lim_rows(limitations: list[Any]) -> str:
    out = []
    for x in limitations or []:
        if not isinstance(x, dict):
            out.append(f'<div class="ev"><span class="ev-lead">{md(str(x))}</span></div>')
            continue
        bits = []
        kind = x.get("kind")
        if kind:
            if kind in zh.GAP_KIND:
                bits.append("种类 " + _lab(zh.GAP_KIND[kind], kind))
            else:
                bits.append(f"种类 <code>{esc(kind)}</code>")
        state = x.get("state")
        if state:
            bits.append("状态 " + _lab(zh.VERIFICATION.get(state, (state, ""))[0], state))
        detail = x.get("detail")
        if detail:
            bits.append("说明 " + _bi(detail))
        out.append('<div class="ev"><span class="ev-lead">' + "；".join(bits)
                   + "</span></div>")
    return "\n".join(out)


def _point_block(point: dict[str, Any]) -> str:
    name = point.get("name") or "?"
    purpose = point.get("purpose") or ""
    zh_name = zh_bi.zh_for(purpose) or purpose
    judgment = point.get("judgment") or "?"
    jz = zh.JUDGMENT.get(judgment, (judgment, ""))[0]
    lines = [
        f'<h3>{esc(zh_name)}<span class="lab-orig">{esc(name)}</span> '
        f'<span class="judge">{esc(jz)}</span><span class="lab-orig">{esc(judgment)}</span></h3>',
        _kv_bi("目的（purpose）", purpose),
        _kv_code("声明的做法（declared_method）", point.get("declared_method")),
        _kv_bi("诊断说明（diagnostic_explanation）", point.get("diagnostic_explanation")),
        "<h4>证据链</h4>",
        _evidence_rows(point.get("evidence")),
    ]
    gaps = _gap_rows(point.get("evidence_gaps"))
    if gaps:
        lines.append(_kv_html("证据缺口（evidence_gaps）", gaps))
    lims = _lim_rows(point.get("limitations"))
    if lims:
        lines.append(_kv_html("该点限制（limitations）", lims))
    return '<section class="point" id="point-%s">%s</section>' % (esc(name), "\n".join(lines))


def _lane_block(decision: dict[str, Any]) -> str:
    rows = [
        _kv_code("记录 id", decision.get("record_id")),
        _kv_lab("申请的使用档（requested）", zh.LANE, decision.get("requested_lane")),
        _kv_lab("判定的使用档（decided）", zh.LANE, decision.get("decided_lane")),
        _kv_lab("使用上限（lane_ceiling）", zh.LANE, decision.get("lane_ceiling")),
        _kv_bi("上限理由（ceiling_reason）", decision.get("ceiling_reason")),
        _kv_bi("范围", decision.get("scope")),
        _kv_code("记录时刻", decision.get("recorded_at")),
    ]
    pu = decision.get("prohibited_uses") or []
    if pu:
        rows.append("<dt>禁止用途</dt><dd>" + "；".join(_bi(x) for x in pu) + "</dd>")
    return '<section class="lane"><dl>' + "\n".join(rows) + "</dl></section>"


def render_deliverable(report: dict[str, Any], *, now: str, generated_at: str,
                       source_label: str = "ui/report.sample.json") -> str:
    c = report.get("context") or {}
    points = report.get("per_point", {}).get("points", [])
    axes = report.get("per_point", {}).get("result_axes", {})
    lane = report.get("lane_and_release") or {}
    lim = report.get("findings_and_limitations") or []
    reg = report.get("five_decision_register") or {}
    admin = report.get("review_and_admin") or {}
    hist = report.get("history_and_change") or {}
    vs = points[0].get("version_snapshot", {}) if points else {}

    excl = c.get("exclusions") or {}
    excl_rows = []
    if excl.get("note"):
        excl_rows.append(_kv_bi("排除项说明", excl.get("note")))
    if excl.get("declared_boundary"):
        excl_rows.append(_kv_bi("声明的边界（declared_boundary）", excl.get("declared_boundary")))
    if excl.get("declared_exclusions"):
        excl_rows.append(_kv_code("排除的内容（照录）", excl.get("declared_exclusions")))

    # 被审账户（六键 + exclusions + 版本三元组）
    identity = "\n".join([
        _kv_code("项目 / 分析", f"{c.get('project_id')} / {c.get('analysis_id')}"),
        _kv_code("比较", c.get("comparison")),
        _kv_code("数据类型", c.get("data_type")),
        _kv_code("审计范围", c.get("audit_scope")),
        _kv_code("预期用途", c.get("intended_use")),
        *excl_rows,
        _kv_code("规则集版本", vs.get("ruleset_version")),
        _kv_code("本体版本", vs.get("ontology_version")),
        _kv_code("引擎版本", vs.get("engine_version")),
    ])

    chain_html = f"<h2>一、链条总览：这一份分析从哪到哪</h2>\n{chv.chain_overview(report)}"

    axis_html = "\n".join(_axis_block(k, axes[k]) for k in _AXIS_ORDER if k in axes)
    point_html = "\n".join(_point_block(p) for p in points)

    lane_statement = _bi(lane.get("statement"))
    not_release = _bi(lane.get("not_release_note") or
                      "本页不含任何 release 或使用授权的主张。")
    lane_rows = "\n".join(_lane_block(d) for d in (lane.get("decisions") or []))

    lim_items = "\n".join(
        "<li>" + _lab(zh.FINDING_CATEGORY.get(str(x.get("category")),
                                              (str(x.get("category")), ""))[0],
                      x.get("category"))
        + ("：" + _bi(x.get("consequence")) if x.get("consequence") else "")
        + "</li>"
        for x in lim) if lim else "<li>（本次未记录其他整体限制）</li>"
    not_yet = reg.get("not_yet_available") or []
    not_yet_items = "\n".join(
        f'<li><span class="cap">{_bi(x.get("capability"))}</span>'
        f'<span class="stat">{_bi(x.get("status"))}</span></li>'
        for x in not_yet) if not_yet else \
        "<li>（五个决策点均已在此账户中有判定）</li>"

    stopped = _bi(report.get("final_user_instruction") or "")

    admin_stmt = _bi(admin.get("statement") or "")
    admin_roles = _bi(admin.get("roles_statement") or "")
    hist_stmt = _bi(hist.get("statement") or "")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>方法学选择审计 · 存档快照（{esc(generated_at)}）</title>
<style>
  @media print {{ body {{ font-size: 10.5pt; }} section {{ page-break-inside: avoid; }} }}
  body {{ font-family: "Noto Serif SC", "Source Han Serif SC", serif; max-width: 780px;
         margin: 0 auto 48px; padding: 0 24px; color: #1a1a1a; line-height: 1.6; }}
  h1 {{ font-size: 20pt; margin: 28px 0 4px; }} h2 {{ font-size: 14pt; margin-top: 26px;
        border-bottom: 1px solid #bbb; padding-bottom: 3px; }} h3 {{ font-size: 12pt; }}
  h4 {{ font-size: 10.5pt; margin: 10px 0 2px; }}
  .meta {{ color: #555; font-size: 9.5pt; }}
  .flag {{ border: 1px solid #999; padding: 10px 14px; margin: 14px 0; }}
  dl {{ display: grid; grid-template-columns: 170px 1fr; gap: 2px 10px; margin: 6px 0; }}
  dt {{ font-weight: 600; }} dd {{ margin: 0; }}
  .judge {{ background: #eef; padding: 1px 8px; border-radius: 8px; font-size: 9.5pt; }}
  .ev {{ margin: 2px 0 6px; }} .ev-lead {{ font-size: 9.5pt; color: #333; }}
  .axis, .point, .lane {{ margin: 10px 0; }}
  .lab-zh {{ }} .lab-orig {{ color: #666; font-size: 8.5pt; margin-left: 4px; }}
  .bi-zh {{ }} .en {{ color: #555; font-size: 9pt; margin-left: 6px; }}
  .cap {{ font-weight: 600; }} .stat {{ display: block; margin-left: 12px; }}
  ul.plain {{ margin: 2px 0; padding-left: 18px; }}
  .chv-table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
  .chv-table th, .chv-table td {{ text-align: left; border-bottom: 1px solid #ddd;
        padding: 4px 6px; vertical-align: top; }}
  .chv-judge {{ background: #eef; padding: 0 6px; border-radius: 8px; }}
  .chv-none {{ color: #a33; }} .chv-gap {{ color: #a33; font-size: 9pt; }}
  .chv-state {{ margin-top: 8px; font-size: 9.5pt; }}
  .chv-note {{ color: #555; font-size: 8.5pt; }}
  .chv-coh {{ margin-top: 8px; border-top: 1px dashed #bbb; padding-top: 6px; }}
  .chv-coh-head {{ font-weight: bold; margin: 2px 0 5px; font-size: 10pt; }}
  .chv-coh-list {{ list-style: none; margin: 0; padding: 0; }}
  .chv-coh-row {{ padding: 2px 0; border-bottom: 1px solid #eee; font-size: 9.5pt; }}
  .chv-coh-tag {{ display:inline-block; padding: 0 6px; border-radius: 6px; font-weight: bold;
                  margin-right: 4px; }}
  .chv-coh-coherent {{ background: #e1efe6; color: #1e6b3a; }}
  .chv-coh-needs_review {{ background: #fdf3d7; color: #8a6100; }}
  .chv-coh-incoherent {{ background: #fbe3e3; color: #a02626; }}
  .chv-coh-label {{ font-weight: bold; margin-right: 4px; }}
  .chv-coh-reason {{ color: #333; }}
  .chv-coh-act {{ color: #8a6100; font-size: 9pt; }}
  .chv-coh-note {{ color: #555; font-size: 8.5pt; margin-top: 5px; }}
  .chv-coh-missing {{ color: #555; font-size: 8.5pt; margin-top: 5px; }}
  footer {{ margin-top: 34px; border-top: 1px solid #bbb; padding-top: 8px;
           color: #555; font-size: 9pt; }}
</style>
</head>
<body>

<h1>方法学选择审计 · 存档快照</h1>
<p class="meta">生成时刻：{esc(generated_at)}　|　渲染来源：<code>{esc(source_label)}</code></p>

<div class="flag">
  <strong>这份文档是什么：</strong>对某个审计账户的<strong>渲染快照</strong>——固定下来、便于交付与回查。
  它<strong>不产生</strong>新结论、不给新授权、不做总评。
  <br><strong>这份文档不是什么：</strong>不是权威容器（权威仍在唯一账户中）；不是验收判定；
  不是 release；不是 Package A 关闭；<strong>中文是渲染层的翻译，不是权威表述</strong>。
</div>

{chain_html}

<h2>二、被审账户</h2>
<dl>{identity}</dl>

<h2>三、五个决策点</h2>
{point_html}

<h2>四、四轴（分开呈现，不合成为任何总分）</h2>
{axis_html}

<h2>五、使用上限与 lane（照抄报告，不抬高）</h2>
<p>{lane_statement}</p>
{lane_rows}
<p class="meta">{not_release}</p>

<h2>六、限制与停止后果（不可省略）</h2>
<h3>整体限制</h3>
<ul>{lim_items}</ul>
<h3>当前尚未具备的能力</h3>
<ul>{not_yet_items}</ul>
<h3>能依赖什么（<code>final_user_instruction</code>）</h3>
<p>{stopped}</p>

<h2>七、复核与管理 · 历史与变更</h2>
<p>{admin_stmt}</p>
<p>{admin_roles}</p>
<p>{hist_stmt}</p>

<footer>
  本文档是 <code>{esc(source_label)}</code> 的渲染快照，权威与完整性由唯一审计账户承载；
  本文档不构成 acceptance / release / lane 授权 / runtime proof / deployment / Package A closure 的任何主张。
  中文为渲染层翻译。锚：<code>{esc(generated_at)}</code> · <code>{esc(source_label)}</code>
</footer>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染对外交付文档（单文件 HTML）")
    ap.add_argument("--out", default=str(UI / "deliverable.html"))
    ap.add_argument("--report-json", default=str(REPORT_JSON))
    ap.add_argument("--now", default=None, help="覆盖生成时刻（ISO8601，便于可复现/测试）")
    args = ap.parse_args(argv)

    report_path = pathlib.Path(args.report_json)
    if not report_path.exists():
        print(f"FAIL 找不到报告数据：{report_path}")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))

    now = args.now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    generated_at = now  # 锚 = 生成时刻；显式传 --now 时两者同值 → 两次生成逐字节一致

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_deliverable(report, now=now, generated_at=generated_at),
                   encoding="utf-8", newline="\n")
    print(f"wrote {out} ({out.stat().st_size} bytes) · 锚 {generated_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
