"""判据面板：把"这一点按哪条规则判、这一条的标准长什么样、凭什么、库自己什么状态"
如实呈现给读者。

设计依据（人类已追认）：
  - DP-CRITERIA-PANEL-ACKNOWLEDGEMENT.md —— 允许呈现的范围（档位阶梯 / 出处 / 置信档 /
    规则库真实接入状态），以及"_md('档位只告知、不判定')"这条边界。
  - DP-NEXT-UNIT-JUDGEMENT.md §五 —— {_bold("今天不推导"你落在哪一档"")}：规则里写的是方法名
    （filterByExpr），提交里写的是声明名（low_count_filter），两套词之间的映射尚未建立。
    所以本模块只_md('并排展示')标准与声明，并_md('明说归属未推导')，绝不猜。

三条自我约束：
  1. 不产生分数、排名或总评（C-02 / C-04 / 报告自述"no total score"）；
  2. 不改变任何判定词（档位只告知、不判定）；
  3. 不猜：读不到的字段就明说读不到。
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

RULES_DATA = (pathlib.Path(__file__).resolve().parent.parent
              / "src" / "bioaudit" / "rules" / "data")
# 声明方法 → 规则档位的映射表。放在 rules/ 下而非 data/ 下：data/ 是**规则目录**
# （清单生成器把那里的 yaml 全当规则），映射表不是规则，混进去会改规则计数。
# 曾一度误放进 data/，已移出——教训记在这里。
METHOD_ALIASES = RULES_DATA.parent / "method_aliases.yaml"

# 报告里的 data_type → 规则目录名（分析家族）。这是_md('既定映射')，不新增语义：
# 报告数据里本来就有 data_type，规则库本来也按家族分目录。
DATA_TYPE_FAMILY = {
    "bulk_rnaseq": "DEG",
    "bulk_rna_seq": "DEG",
    "scrna": "scRNA",
    "single_cell": "scRNA",
    "pancancer": "pancancer",
}

# 五个决策点 → 每个家族里的规则文件（由实测得到，见 DP-RULE-LIBRARY-STATUS §一）
POINT_RULES: dict[str, dict[str, str]] = {
    "filtering": {
        "DEG": "D1.2-DEG-001_filtering.yaml",
        "pancancer": "D1.2-DEG-001_filtering.yaml",
    },
    "normalization": {
        "DEG": "D1.3-DEG-001_normalization.yaml",
        "pancancer": "D1.3-DEG-001_normalization.yaml",
    },
    "differential_method": {
        "DEG": "M1.1-DEG-001_method_selection.yaml",
        "pancancer": "M1.1-DEG-001_method_selection.yaml",
        "scRNA": "G1.3-DEG-003_method.yaml",
    },
    "multiple_testing_correction": {
        "DEG": "M1.2-DEG-001_multiple_testing.yaml",
        "pancancer": "M1.2-DEG-001_multiple_testing.yaml",
        "scRNA": "G1.2-DEG-002_multiple_testing.yaml",
    },
    "significance_threshold": {
        "DEG": "M1.3-DEG-001_threshold.yaml",
        "pancancer": "M1.3-DEG-001_threshold.yaml",
        "scRNA": "G1.4-DEG-004_significance_threshold.yaml",
    },
}

_LEVEL_ORDER = ("level_4", "level_3", "level_2", "level_1", "level_0")


def family_for(data_type: Any) -> str | None:
    """报告里的 data_type → 规则家族目录名。读不出就返回 None（不猜）。"""
    return DATA_TYPE_FAMILY.get(str(data_type or "").strip().lower())


def load_rule(family: str, filename: str) -> dict[str, Any] | None:
    """读一条规则；读不到返回 None（调用方必须如实说明"读不到"）。"""
    p = RULES_DATA / family / filename
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _esc(v: Any) -> str:
    """转义为 HTML 文本。

    注意：**不要在这里转义 `&`**。调用方有的把结果拼进已经被转义过的文本里，
    再转一次就会出现 `&amp;` 直接显示给人看（验证器抓到过 3 处，来自证据标题里的
    真 `&`，例如 Robinson & Oshlack (2010)）。`<`、`>` 仍要转。
    """
    s = "" if v is None else str(v)
    return s.replace("<", "&lt;").replace(">", "&gt;")


def _codespan(text: Any) -> str:
    """把文本里的反引号片段渲染成 ``<code>``（先转义，再替换）。

    规则库与映射表里的说明文字会用反引号标出方法名/表达式（例如
    ``padj <= 0.05, |logFC| >= 1.0``）。页面要显示的是**排版效果**，不是反引号本身——
    界面准则 6：机器记号绝不出现在人眼前。验证器抓到过 2 个反引号外露，就是漏了这一步。
    """
    escaped = _esc(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def _md(text: str) -> str:
    """Markdown 粗体 → ``<strong>``（调用时用单引号，见本文件里的用法）。"""
    return _bold(text)


def _bold(text: str) -> str:
    """把 Markdown 的粗体标记渲染成 ``<strong>``。

    本模块原先直接把 Markdown 星号写进 HTML 模板，于是页面上原样显示了 10 个星号——
    违反界面准则 6「机器记号绝不出现在人眼前」。这个函数是那次修复的产物：
    要强调就调它，不要再写 Markdown。
    """
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _normalise(name: Any) -> str:
    """方法名归一：小写 + 把连字符与空格都当分隔符。

    规则里同时存在 `limma-voom` 与 `limma_voom`、`TMM` 与 `tmm`，比较前必须归一，
    否则"同一物的写法差异"会被误判成"对不上"。
    """
    return re.sub(r"[\s\-]+", "_", str(name or "").strip().lower())


def load_method_aliases() -> dict[str, Any]:
    """读映射表；读不到返回空 dict（调用方据此显示"无法推导"，不猜）。"""
    if not METHOD_ALIASES.exists():
        return {}
    try:
        data = yaml.safe_load(METHOD_ALIASES.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def tier_for(point_name: str, declared: Any, family: str | None,
             aliases: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """声明的方法 → 它落在哪一档。**只做归属，不改变任何判定词。**

    返回四种结果之一，调用方必须分开显示：

    ``None``
        表里没有这一点或这个家族（读不到）——显示"本页无法归属"。
    ``{"level": "level_3", "source": ..., "matched_name": ...}``
        归属到某一档。``source`` 说明依据（exact / alias / interpreted）。
    ``{"level": None, "unmapped": True, "why": ...}``
        **按名无法归属**，附原因——必须如实显示"对不上"，**绝不能显示成"落在最低档"**。
    ``{"level": None, "unknown": True}``
        表里没有这个声明名——同样是"无法归属"，不是"最差"。
    """
    aliases = aliases if aliases is not None else load_method_aliases()
    table = ((aliases.get("families") or {}).get(family) or {}).get(point_name)
    if not table:
        return None
    entry = table.get(_normalise(declared))
    if not entry:
        return {"level": None, "unknown": True}
    # 映射表用 `source: unmapped` 表达"按名对不上"（见 method_aliases.yaml 的注释），
    # 而本函数对调用方承诺的是 `unmapped: True` 这个标志。两者必须在这里对齐——
    # 第一版没有对齐，于是"对不上"的点原样返回，调用方既不认作已归属、也不认作对不上，
    # 面板上那两点就什么档位说明都不显示（被第 16 项断言抓到）。
    # 修法是**在此处归一**，而不是让每个调用方各判两种形态。
    result = dict(entry)
    if str(result.get("source")) == "unmapped":
        result["unmapped"] = True
        result.setdefault("level", None)
    return result


def _levels_of(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """档位阶梯，按 level_4 → level_0 排（高到低）。每档给出说明与是否已实现。"""
    scoring = rule.get("scoring") or {}
    out = []
    for key in _LEVEL_ORDER:
        block = scoring.get(key)
        if not isinstance(block, dict):
            continue
        methods = block.get("methods") or []
        out.append({
            "key": key,
            "n": int(key.split("_")[1]),
            "desc": block.get("description") or block.get("note") or "",
            "rationale": block.get("rationale") or "",
            "methods": [str(m) for m in methods],
            "planned": str(block.get("status") or "").strip().lower() in
                       ("planned", "not_implemented"),
        })
    return out


def _evidence_of(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """出处逐条。没有可核标识的如实留空，不补齐。"""
    out = []
    for e in (rule.get("evidence") or []):
        if not isinstance(e, dict):
            continue
        out.append({
            "title": e.get("title") or e.get("source_type") or "",
            "doi": e.get("doi"),
            "pmid": e.get("pmid"),
            "confidence": e.get("confidence"),
            "supports": e.get("supports_levels") or [],
        })
    return out


def _tier_cell(tier: dict[str, Any] | None, declared: Any) -> str:
    """把"你落在哪一档"渲染成三种互不混淆的显示。

    这是全项目最容易出错的一处显示：**"对不上"绝不能被读成"最差"**。
    所以三种情况各有一套措辞，且 unmapped/unknown 明确写"不判断"。
    """
    if tier is None:
        return ('<div class="muted small">本页<strong>无法归属档位</strong>：'
                '映射表里没有这一点的条目（或本次分析家族未覆盖），'
                '因此<strong>不判断档位</strong>。</div>')
    if tier.get("unmapped") or tier.get("unknown"):
        why = tier.get("why") or "映射表里没有这个声明名。"
        head = ("按名<strong>无法归属</strong>" if tier.get("unmapped")
                else "映射表里<strong>没有这个声明名</strong>")
        return (f'<div class="small" style="margin-top:4px">{head}：{_codespan(why)}</div>'
                f'<div class="muted small">这不是档位高低问题，只是按声明名对不上；'
                f'可在下方档位阶梯里逐档核对。</div>')
    level = tier.get("level")
    src = tier.get("source")
    src_txt = {"exact": "名字相同（含大小写/连字符差异）",
               "alias": "同一物的常见别名",
               "interpreted": "由声明语义归类（待领域复核）"}.get(src, src or "")
    review = ('<span class="chip warn">待领域复核</span>'
              if tier.get("needs_review") else '')
    matched_name = tier.get("matched_name") or ""
    return (f'<div class="small" style="margin-top:4px">档位归属：'
            f'<span class="chip accent">{_esc(level)}</span> {review}'
            f'　依据：{_esc(src_txt)}'
            f'{f"（对应规则里的 <code>{_esc(matched_name)}</code>）" if matched_name else ""}'
            f'</div>'
            f'<div class="muted small">只对应声明名，'
            f'<strong>不改变任何判定，也不构成好坏评价</strong>。</div>')


def _library_state(rule: dict[str, Any]) -> str:
    """库自己怎么陈述这条规则的状态（复核时点 / 窗口 / 未复核）。"""
    prov = rule.get("provenance") or {}
    upd = rule.get("update_policy") or {}
    reviewed = prov.get("reviewed_at")
    window = upd.get("review_window_months")
    if not reviewed:
        txt = "_md('尚未人工复核')"
        if window:
            txt += f"（声明：超过 {window} 个月未复核应重看）"
        return txt
    return f"最近复核：{reviewed}" + (f"（复核窗口 {window} 个月）" if window else "")


def _rule_card(point: dict[str, Any], rule: dict[str, Any] | None,
               family: str | None, filename: str | None,
               matched: tuple[str, ...],
               tier: dict[str, Any] | None = None) -> str:
    """一个决策点的判据卡。

    ``matched`` 是该点实际命中的规则 ID（来自渲染层传入的既有事实）。
    非空 => 这一点真的接上了规则匹配；空 => 规则在库里但_md('实施未调用它')。
    """
    name = point.get("name") or ""
    purpose = point.get("purpose") or ""
    declared = point.get("declared_method")
    wired = bool(matched)

    head = f"""
      <h4>{_esc(purpose)}<span class="muted small">（{_esc(name)}）</span></h4>"""

    if rule is None:
        fam_txt = _esc(family) if family else "未识别"
        file_txt = _esc(filename) if filename else "未映射"
        return (head + f"""
      <p class="muted small">规则库里读不到这一点的规则文件
      （家族 {fam_txt} / 文件 {file_txt}），
      因此本点<strong>没有可呈现的判据</strong>。</p>""")

    rid = rule.get("rule_id")
    levels = _levels_of(rule)
    evidence = _evidence_of(rule)

    # 接入状态：如实写（人类已裁定：未接入就写未接入）
    if wired:
        wiring = (f'<span class="chip ok">已接入</span> 实际命中：'
                  f'<code>{_esc("、".join(matched))}</code>')
    else:
        wiring = (f'<span class="chip warn">未接入</span> '
                  f'规则库里存在 <code>{_esc(rid)}</code>，但实施未调用；'
                  f'本点没有档位参与判定。'
                  f'<span class="muted small">接入=判据参与判定；未接入=只陈列不生效。</span>')

    level_rows = []
    for lv in levels:
        tag = ('<span class="chip warn">计划中</span>' if lv["planned"]
               else '<span class="chip accent">已实现</span>')
        methods = ("　方法：" + "、".join(f"<code>{_esc(m)}</code>"
                                        for m in lv["methods"])) if lv["methods"] else ""
        level_rows.append(f"""
        <div class="kv">
          <dt>{_esc(lv['key'])}</dt>
          <dd>{tag} {_esc(lv['desc'])}{methods}
            {f'<div class="muted small">理由：{_esc(lv["rationale"])}</div>'
             if lv['rationale'] else ''}</dd>
        </div>""")

    ev_rows = []
    for e in evidence:
        ids = " ".join(x for x in [
            f"DOI {_esc(e['doi'])}" if e["doi"] else "",
            f"PMID {_esc(e['pmid'])}" if e["pmid"] else "",
        ] if x) or '<span class="muted">（此条无可核标识）</span>'
        conf = f'<span class="chip">{_esc(e["confidence"])}</span>' if e["confidence"] else ""
        ev_rows.append(f"""
        <div class="kv">
          <dt>{conf}</dt>
          <dd>{_esc(e['title'])} <span class="muted small">{ids}</span></dd>
        </div>""")

    return head + f"""
      <div class="kv" style="margin:6px 0">
        <dt>按哪条规则</dt>
        <dd><code>{_esc(rid)}</code>　{_esc(rule.get('title'))}
          <div class="small" style="margin-top:4px">{wiring}</div></dd>
      </div>
      <div class="kv">
        <dt>声明的方法</dt>
        <dd><code>{_esc(declared)}</code>{_tier_cell(tier, declared)}</dd>
      </div>
      <h5>这一条规则的档位阶梯（高 → 低）</h5>
      <div class="kv-list">{''.join(level_rows)}</div>
      <h5>档位出处</h5>
      <div class="kv-list">{''.join(ev_rows)}</div>
      <p class="muted small">库自述状态：{_library_state(rule)}</p>
    """


def render_criteria_panel(report: dict[str, Any],
                          matched: dict[str, tuple[str, ...]] | None = None) -> str:
    """整块判据面板。常驻呈现；不产生分数、排名或总评。

    ``matched`` 由渲染层传入（每个点实际命中的规则 ID）。缺省为空字典——
    那会让每个点都显示"未接入"，_md('这正是没有该事实时的诚实呈现')。
    """
    matched = matched or {}
    ctx = report.get("context") or {}
    data_type = ctx.get("data_type")
    family = family_for(data_type)
    points = (report.get("per_point") or {}).get("points") or []

    intro = f"""
      <p class="lede">这一节列每个判断点的判定依据：<strong>规则、档位阶梯与出处</strong>。
      不给分数，不对档位加总；档位只告知，不参与判定。</p>
      <div class="kv">
        <dt>本次分析家族</dt>
        <dd>{_esc(data_type)} → 规则家族 <code>{_esc(family) if family else "未识别"}</code></dd>
      </div>
      <div class="callout warn">
        <h4>档位归属尚未推导</h4>
        <p>规则档位按方法名列（例如 <code>filterByExpr</code>），提交里写的是
        声明的方法（例如 <code>low_count_filter</code>）。两套词还没有映射，
        本页不判断声明落在哪一档；建立映射属新增语义，需另行裁定。</p>
      </div>"""

    aliases = load_method_aliases()
    cards = []
    for point in points:
        name = point.get("name") or ""
        mapping = POINT_RULES.get(name) or {}
        filename = mapping.get(family) if family else None
        rule = load_rule(family, filename) if (family and filename) else None
        tier = tier_for(name, point.get("declared_method"), family, aliases)
        cards.append(_rule_card(point, rule, family, filename,
                                tuple(matched.get(name) or ()), tier))

    return intro + "".join(f'<div class="card">{c}</div>' for c in cards)
