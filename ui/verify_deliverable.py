"""对外交付文档的产物级校验（B1 的 8 条硬约束 → 可执行的关卡）。

为什么需要它
------------
这份文档的失败模式很具体（`DP-PRODUCT-SURFACE-DECISIONS.md` §2.1 反面清单）：
它太容易被做成「带总体评价、把四轴合成一个可信度、省略限制、把使用上限写成可发布」的
「研究结论报告」。而它不应该有任何机器长得像那样。所以 8 条约束**从第一版就带自动门禁**，
不靠人眼每次重看。

判定口径沿用 `ui/verify_report.py` 的既有做法（同在 ui/ 下，同批代码）：
- 禁词按**句子级**判定：一句里出现禁词但整句是否定语（不/无/非/禁止/no/not…）时不算命中；
- `_mentions` 用词边界（`-`、`_` 也算分隔符），防「unstable 满足 stable」这类误认。

用法
----
    .\\.venv\\Scripts\\python.exe ui\\verify_deliverable.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

DELIVERABLE = pathlib.Path("ui/deliverable.html")
REPORT_JSON = pathlib.Path("ui/report.sample.json")

# 约束 6 的渲染来源锚（F-G4 修复：由硬编码字面量改为命名常量，可参数化）。
# 演示对默认指向报告数据文件；管线产物场景应替换为管线传入的账户锚
# （scripts/submit_analysis.py:102 source_label），测试以 mock.patch 覆盖。
SOURCE_ANCHOR = "ui/report.sample.json"

_DENIAL = re.compile(
    r"(不|无|非|禁止|不得|并非|莫|不应|no\b|not\b|never\b|none\b)", re.IGNORECASE)

# 结论词（约束 5）：文档里不得用它作肯定结论；否定句（「这不是通过判定」）允许。
_BANNED_CONCLUSIONS = ("通过", "合格", "可信", "已验证", "已验收", "已认证")
# 合成值（约束 2）：不得出现「把一个账户合成一个数」的措辞。
_BANNED_COMBINED = ("总分", "合计", "综合分", "综合评分", "总体评分")
# lane 被当数值用（约束 4）：lane 是使用上限，不是分数/排名。
_LANE_AS_VALUE = re.compile(r"lane[^。；;]{0,40}(分数|排名|置信分|score|ranking|confidence)",
                            re.IGNORECASE)


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def _first_words(text: str, n: int = 6) -> str:
    """取前 n 个词作为锚（避开把词切成一半的字符切片——那种锚会被词边界正则漏过）。"""
    return " ".join(str(text).split()[:n])


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"(?<=[。！？；;.!?])\s*", text):
        s = re.sub(r"\s+", " ", part).strip()
        if s:
            out.append(s)
    return out


def _mentions(text: str, word: str) -> bool:
    return re.search(rf"(?<![0-9A-Za-z_-]){re.escape(word)}(?![0-9A-Za-z_-])", text) is not None


def _assert_affirmatively_banned(sentences: list[str], word: str,
                                 skip_token: str | None = None) -> None:
    """一句话里出现该词、且整句没有任何否定记号 → 视为肯定使用，失败。

    `skip_token`：有界豁免（2C 笔 2，2026-09-15）——当原词 token 同句在场时
    跳过。用途只有一个：'已验证' 是 lane 词表（zh.LANE）里 `validated` 的中文
    标签，中文（原词）成对呈现后必然以肯定形式出现；那是**词表标签的呈现**，
    不是文档在作结论断言（与检查页第 2 项的 lane 豁免同一逻辑）。豁免锚死在
    「原词 token 同句在场」上，散文里的肯定式结论（不带 token）照样被抓。
    """
    for s in sentences:
        if _mentions(s, word) and not _DENIAL.search(s):
            if skip_token and _mentions(s, skip_token):
                continue
            _fail(f"出现肯定式结论词 {word!r}：{s[:80]}…")


def main() -> int:
    if not DELIVERABLE.exists():
        print(f"FAIL 找不到交付文档：{DELIVERABLE}")
        return 1
    if not REPORT_JSON.exists():
        print(f"FAIL 找不到报告数据：{REPORT_JSON}")
        return 1

    html_text = DELIVERABLE.read_text(encoding="utf-8")
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    body = re.sub(r"<[^>]+>", " ", html_text)
    sentences = _sentences(body)

    # ── 约束 1 + 7：渲染快照自述 + 中文层声明（一、界线在同一段里）──────────
    for token in ("渲染快照", "不是权威", "不产生", "不做总评"):
        if not _mentions(body, token):
            _fail(f"文档未自述「{token}」——必须说清它是渲染快照而非权威/总评")
    if not re.search(r"中文[是]?(为)?渲染层(的)?翻译", body) and \
            not re.search(r"中文是渲染层的翻译", body):
        _fail("文档未声明「中文是渲染层翻译」（约束 7）")
    print("ok 1/8  declares itself a rendering snapshot, not a second authority; zh layer declared")

    # ── 约束 2：四轴分开呈现，无合成值 ──────────────────────────────────────
    axes = report.get("per_point", {}).get("result_axes", {})
    for key in ("inference_type", "explanation_depth", "scope", "validation"):
        if key not in axes:
            _fail(f"报告里没有轴 {key}")
        if f'id="axis-{key}"' not in html_text:
            _fail(f"文档未分开呈现轴 {key}")
    for w in _BANNED_COMBINED:
        _assert_affirmatively_banned(sentences, w)
    print("ok 2/8  four axes rendered separately; no combined value wording")

    # ── 约束 3：限制与停止后果不得省略 ──────────────────────────────────────
    if not _mentions(body, "限制与停止后果"):
        _fail("缺少「限制与停止后果」一节")
    fl = report.get("findings_and_limitations") or []
    # 2C 笔 2：整体限制从「dict repr 照印」改为分字段中文呈现（类别中文（原词）+
    # 后果双语），断言锚同步从 repr 表面文字改为后果句内容——收紧（断言的是语义
    # 内容，不再被 repr 的偶然形状满足）。
    if fl:
        consequence = fl[0].get("consequence") or ""
        if consequence and not _mentions(body, _first_words(str(consequence))):
            _fail("文档未呈现报告里的整体限制后果原文")
    final = report.get("final_user_instruction") or ""
    if final and not _mentions(body, _first_words(final)):
        _fail("文档未呈现 final user instruction（能依赖什么）")
    print("ok 3/8  limitations and stop consequences carried, not omitted")

    # ── 约束 4：使用上限照抄、不抬高；lane 不作分数 ──────────────────────────
    lane_and_release = report.get("lane_and_release") or {}
    for d in (lane_and_release.get("decisions") or []):
        ceiling = d.get("lane_ceiling")
        if ceiling and not _mentions(body, ceiling):
            _fail(f"lane 上限 {ceiling!r} 未在文档中照抄（记录 {d.get('record_id')}）")
        reason = d.get("ceiling_reason") or ""
        if reason and not _mentions(body, _first_words(reason)):
            _fail(f"上限理由未照抄（记录 {d.get('record_id')}）")
    for m in _LANE_AS_VALUE.finditer(body):
        s = body[max(0, m.start() - 40):m.end() + 40]
        if not _DENIAL.search(s):
            _fail(f"lane 被当作分数/排名使用：{m.group(0)}")
    for w in ("可发布", "已授权发布", "可公开使用"):
        _assert_affirmatively_banned(sentences, w)
    print("ok 4/8  lane ceilings copied verbatim, not inflated; lane not used as a score")

    # ── 约束 5：结论词禁区（肯定式不得出现）─────────────────────────────────
    for w in _BANNED_CONCLUSIONS:
        if w == "已验证":
            # 有界豁免：'已验证' 同时是 lane 档位 `validated` 的中文标签
            # （见 _assert_affirmatively_banned 的 skip_token 说明）
            _assert_affirmatively_banned(sentences, w, skip_token="validated")
        else:
            _assert_affirmatively_banned(sentences, w)
    print("ok 5/8  no affirmative conclusion words (通过/合格/可信/已验证…)")

    # ── 约束 6：可核的锚（生成时刻 + 渲染来源）──────────────────────────────
    if "生成时刻" not in body or SOURCE_ANCHOR not in body:
        _fail(f"文档缺少生成时刻或渲染来源锚（期望 {SOURCE_ANCHOR!r}）")
    stamp = re.search(r"生成时刻：(\d{4}-\d{2}-\d{2}T[0-9:]{8})", body)
    if not stamp:
        _fail("生成时刻锚格式不可解析")
    print(f"ok 6/8  anchor present (generated at {stamp.group(1)}, source {SOURCE_ANCHOR})")

    # ── 约束 8：非声称（不构成 acceptance / release / closure）──────────────
    if not _mentions(body, "不构成"):
        _fail("文档缺少「不构成 … 主张」的非声称句")
    for term in ("acceptance", "release", "closure"):
        if term not in body:
            _fail(f"非声称句未覆盖 {term}")
    print("ok 7/8  no acceptance / release / closure claims")

    # ── 附加：机器记号不得外露（与页面第 8 项同一类病）──────────────────────
    for marker in ("**", "&amp;", "`"):
        if marker in body:
            _fail(f"可视文本里出现机器记号 {marker!r}")
    print("ok 8/8  no markdown/authoring markers leaked into visible text")

    # ── 链条总览（9）：主线已串起来，且不合成任何总评 ───────────────────────
    if not _mentions(body, "链条总览"):
        _fail("缺「链条总览」——五个决策点没有摆成一条主线")
    order = ["低表达过滤", "归一化", "差异分析方法", "多重检验校正", "显著性/效应量阈值"]
    pos = -1
    for name in order:
        at = body.find(name)
        if at < 0 or at < pos:
            _fail(f"链条步骤顺序不对（应在 {name} 之前出现的步骤没在前）")
        pos = at
    if not _mentions(body, "不是总评"):
        _fail("链条状态行未声明『不是总评』")
    # 收紧（2026-09-15 DP-COHERENCE 窗口）：原要求占位句「本页不判定」存在；
    # 现在占位句必须已删，改为要求连贯小段存在、三态对照齐全、且不构成总评。
    if not _mentions(body, "跨步骤科学连贯"):
        _fail("交付文档缺少跨步骤科学连贯小段")
    if "本页不判定" in body:
        _fail("旧的占位句『本页不判定』仍然存在，应已删除")
    for zh_cn in ("衔接一致", "需复核", "衔接矛盾"):
        if zh_cn not in body:
            _fail(f"交付文档未呈现锁定的连贯判词 {zh_cn}")
    if not _mentions(body, "不构成总评"):
        _fail("连贯小段未声明不构成总评")
    print("ok 9/9  chain overview: five steps in pipeline order, states summed not merged")

    # ── 链条总览（10）：主线的五步判定词必须来自报告（防链条自己发明词）──────
    points_judgments = [p.get("judgment") for p in report["per_point"]["points"]]
    for j in points_judgments:
        if j and j not in body:
            _fail(f"链条未呈现报告自带的判定词 {j!r}")
    print("ok 10/10  chain judgments reference the report's own per-point states")

    print("\nPASS — ui/deliverable.html satisfies the B1 delivery constraints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
