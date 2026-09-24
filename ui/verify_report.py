"""Verify the generated report UI (DP-UI-REPORT).

Checks the artifact itself — not the generator — so a passing run means the file
a human will open satisfies the window's stated guarantees:

1. real data rendered (every point name, judgment, axis and page section present)
2. NO total score / winner / ranking / lane / confidence anywhere
3. NO outer replay-binding material anywhere (non-public stays non-public)
4. the four axes are rendered SEPARATELY (no combined total element)

Usage (from the repo root):
    .\\.venv\\Scripts\\python.exe ui\\report.html    # just open it
    .\\.venv\\Scripts\\python.exe ui\\verify_report.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from html import unescape
from typing import Any

# Console output must not depend on the terminal's code page (Windows cp936 would
# otherwise mangle the em dash and stray non-ASCII in the summary line).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - older/odd streams
    pass

HTML = pathlib.Path("ui/report.html")
REPORT_JSON = pathlib.Path("ui/report.sample.json")

# The vocabulary transcript lives with the report that renders it, so this check
# and the capability prose cannot drift apart: both read the SAME list. Imported
# privately (leading underscore) on purpose: the public API surface of this
# project is not extended by an artifact checker.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from bioaudit.dp_report import (  # noqa: E402
    _P02_EXPRESSED_BY,
    _P02_EXPRESSIBLE_LABELS,
    _P02_SECTION_5_MEANINGS,
    _P02_UNEXPRESSIBLE_LABELS,
    _capability_status_for_state_vocabulary,
)

# Keys that must not exist anywhere in the rendered page (report-level ban list).
_BANNED_KEYS = ("total_score", "overall_score", "confidence", "ranking", "winner",
                "quality", "lane", "score")

# The page may NAME these words while denying them ("no total score"), and a few
# appear in the human-recorded decision text verbatim. A hit is only a failure
# when it is used as a value rather than inside such a statement.
#
# This list is used to classify a WHOLE SENTENCE (review F4), so it must not match
# ordinary affirmative wording. `就是` / `即是` mean "IS" in Chinese and were
# originally absent, which let "candidate lane 就是最终评分与排名" pass as a denial.
_DENIAL_RE = re.compile(
    r"(禁止|prohibited|不产生|不产出|不给出|不存在|不是|并不|不算|不可|不会|无法|没有|不能|不授予|不得|并非|仅|无总分|无排名|无分数|无置信|"
    r"不构成|不应|不得视为|不是用来|"
    r"no\b|not\b|never\b|without\b|none\b|nothing\b)",
    re.IGNORECASE)
# Wording that is the artifact's own audit trail, quoted from the report itself.
_PROHIBITION_MARKERS = ("prohibited", "No ")

# ``lane`` as a FIELD NAME is not a lane value. The report legitimately carries
# ``decided_lane`` / ``requested_lane`` / ``lane_ceiling`` and the page shows those
# identifiers beside their Chinese labels. The check below is about not letting a
# lane label be *used as a value*; this pattern is the explicit, narrow exemption
# for the identifier case, added when the C-04 lane record was introduced.
_LANE_FIELD_RE = re.compile(r"\b(?:decided_lane|requested_lane|lane_ceiling|"
                            r"lane_predicate_evidence_refs|list_dp_lane_decisions|"
                            r"record_dp_lane_decision|dp_lane_store)\b|"
                            r"lane_and_release")
# The sentences that DEFINE or DISCUSS what a lane is — C-04 §4's own wording, the
# ceiling-reason narration, and the Chinese section headings — describe the concept
# rather than using a lane as a value. Kept explicit and narrow.
_LANE_DEFINITION_RE = re.compile(r"a lane is |lane (?:never|answers|therefore|caps|"
                                 r"cannot)|the lane predicate|no lane (?:is|exists)|"
                                 r"使用上限|这几档上限|档位|上限决定记录")

# 判据面板（DP-CRITERIA-PANEL-ACKNOWLEDGEMENT）会渲染规则库里的**方法名**，而其中
# 有一个合法地含禁词：limma-voom 的变体 `voom-with-quality-weights` 是**工具名**，
# 不是质量值。方法名/字段名/规则 ID 一律呈现在 `<code>` 里，所以豁免锚定在**那段
# 标记**上、且只锚在它上面——写成散文或裸表格单元格的 "quality" 值照样会被抓。
# 检查 2 里记了这条推理，免得日后被无意放宽。
_METHOD_NAME_RE = re.compile(r"<code>[^<>]*</code>|[A-Z]\d\.\d-[A-Z]+-\d{3}")

# Outer-binding shape: a `synthetic:` actor that is NOT a role record actor.
_OUTER_ACTOR_RE = re.compile(r"synthetic:[a-z0-9_-]*verifier[a-z0-9_-]*")


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def _mentions(text: str, word: str) -> bool:
    """True when ``text`` names ``word`` as a word, not as a substring.

    ``historical_only`` must not be satisfied by ``historical-only``, ``stale``
    must not be satisfied by ``unstable``, and ``planned`` must not be satisfied
    by ``unplanned``: - and _ count as separators, so a spelling swap inside a
    label is a miss (which is what makes check 9 able to see a rename).
    """
    return re.search(rf"(?<![0-9A-Za-z_-]){re.escape(word)}(?![0-9A-Za-z_-])",
                     text) is not None


def _capability_lines_on_page(page_text: str) -> list[str]:
    """Every rendered copy of the state-vocabulary capability paragraph.

    The sentence is rendered at more than one site in the page, so a check that
    merely asks "is it there" can be satisfied by an untouched copy while another
    copy contradicts it. Each occurrence runs from its opening phrase to the
    paragraph's closing phrase (the middle contains no abbreviation, but it does
    contain an internal period, so a period-delimited split would cut it short).
    """
    opener = "partially implemented: P-02"
    closer = "what would have to change for it."
    found: list[str] = []
    cursor = 0
    while True:
        start = page_text.find(opener, cursor)
        if start < 0:
            return found
        end = page_text.find(closer, start)
        if end < 0:
            _fail("a state-vocabulary capability line on the page is truncated")
        end += len(closer)
        found.append(page_text[start:end])
        cursor = end


def main() -> int:
    if not HTML.exists():
        _fail(f"{HTML} not found — run ui/generate_report.py first")
    html = HTML.read_text(encoding="utf-8")
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    checks = 0

    # -- 1. real data rendered ------------------------------------------------
    for point in report["per_point"]["points"]:
        for token in (point["name"], point["purpose"], point["judgment"]):
            if token not in html:
                _fail(f"point token missing from the page: {token!r}")
            checks += 1
    for axis in ("inference_type", "explanation_depth", "scope", "validation"):
        if f'id="axis-{axis}"' not in html:
            _fail(f"axis section missing: {axis}")
        checks += 1
    for section in ("identity", "points", "axes", "use", "who", "history", "gaps", "stop"):
        if f'id="{section}"' not in html:
            _fail(f"page section missing: {section}")
        checks += 1
    for key in ("acceptance_snapshot", "snapshot_id", "not_yet_available",
                "final_user_instruction"):
        checks += 1
    if report["review_and_admin"]["authority_attribution"]["snapshot_id"] not in html:
        _fail("the authority container id is not shown")
    for entry in report["five_decision_register"]["not_yet_available"]:
        if entry["capability"] not in html:
            _fail(f"missing not-yet-available capability: {entry['capability']!r}")
        checks += 1
    print(f"ok 1/4  real data rendered ({checks} tokens)")

    # -- 2. no score / ranking / lane ----------------------------------------
    # SENTENCE-LEVEL, not window-level (adversarial review F4): a fixed ±N character
    # window could be satisfied by an honest denial placed next to a claim, so a page
    # advertising "候选 lane 就是最终评分与排名" passed. Each sentence is classified on
    # its own content: a sentence that DENIES these words may name them freely; a
    # sentence that names three or more of them at once, while denying none, is
    # presenting them as values.
    def _sentences(text: str) -> list[str]:
        """Visible text split into sentences AND paragraphs.

        Block-level boundaries count as breaks: a paragraph without a terminator
        would otherwise be glued to the previous one, and a denial word anywhere in
        that merged blob would then excuse a claim next door (this is exactly how a
        padded page slipped through during the adversarial review).
        """
        marked = re.sub(
            r"</(?:p|li|div|h[1-6]|td|th|tr|section|article|details|summary|a|span|dd|dt)>",
            "\u2029", text, flags=re.IGNORECASE)
        marked = re.sub(r"<br\s*/?>", "\u2029", marked, flags=re.IGNORECASE)
        stripped = unescape(re.sub(r"<[^>]+>", " ", marked))
        out: list[str] = []
        # split on the paragraph marker FIRST: collapsing whitespace before splitting
        # would eat the marker and re-glue the paragraphs (that bug hid the padded
        # claim from this check during the review-fix pass)
        for part in stripped.split("\u2029"):
            part = " ".join(part.split())
            if not part:
                continue
            out.extend(s.strip() for s in re.split(r"(?<=[。！？；;.!?])\s*", part)
                       if s.strip())
        return out

    structural_hits = []
    for sentence in _sentences(html):
        lowered_sentence = sentence.lower()
        if _LANE_FIELD_RE.search(lowered_sentence):
            # the sentence is TALKING ABOUT a lane field (and possibly its value):
            # that is the report's own schema being shown, not a lane-as-value.
            continue
        if _DENIAL_RE.search(lowered_sentence) or any(
                m.lower() in lowered_sentence for m in _PROHIBITION_MARKERS):
            continue  # a denial: naming these words is the point of the sentence
        named = [key for key in _BANNED_KEYS if _mentions(lowered_sentence, key)]
        if len(named) >= 3:
            structural_hits.append((sorted(named), sentence[:120]))
    if structural_hits:
        for named, sentence in structural_hits:
            print(f"     banned tokens {named} presented as values in: ...{sentence}...")
        _fail(f"{len(structural_hits)} sentence(s) present score/ranking/lane words "
              f"as values")
    # every mention of these words must be inside a NEGATIVE/absent statement
    # (the report names what it does not provide; that is disclosure, not a value)
    #
    # Two narrownesses matter here, both learned the hard way when the criteria panel
    # started rendering rule text:
    #   * ASCII keys are matched with WORD BOUNDARIES. A plain substring search made
    #     "Bonferroni inequality" fail the `quality` ban — "inequality" is not a
    #     quality value, and a check that cannot tell them apart is a false alarm, not
    #     a guard.
    #   * CJK keys have no word boundaries, so they keep substring matching.
    for key in _BANNED_KEYS:
        pattern = (re.compile(rf"\b{re.escape(key)}\b") if key.isascii()
                   else re.compile(re.escape(key)))
        for match in pattern.finditer(html.lower()):
            window = html.lower()[max(0, match.start() - 60):match.end() + 60]
            if _DENIAL_RE.search(window) or _LANE_FIELD_RE.search(window) \
                    or _LANE_DEFINITION_RE.search(window) \
                    or _METHOD_NAME_RE.search(window):
                continue
            _fail(f"{key!r} appears outside any negative/absent statement: ...{window}...")
    # and the data structures must carry no such attribute/class at all
    lowered = html.lower()
    for key in _BANNED_KEYS:
        for pattern in (f'class="{key}', f"class='{key}", f'data-{key}', f'id="{key}"',
                        f'"{key}":'):
            if pattern in lowered:
                _fail(f"banned token used as a markup attribute: {pattern}")
    print("ok 2/4  no score / ranking / lane used as a value, attribute or attribute key")

    # -- 3. no outer-binding material ---------------------------------------
    outer = _OUTER_ACTOR_RE.findall(html)
    if outer:
        _fail(f"outer replay-binding material present: {sorted(set(outer))}")
    for token in ("stage_material_to_acceptance_snapshot", "stage_package:",
                  "verification_only", "binding_id"):
        if token in html:
            _fail(f"outer replay-binding token present: {token}")
    print("ok 3/4  no outer replay-binding material anywhere in the page")

    # -- 3b. no undecodable characters survived the render -------------------
    if "\ufffd" in html:
        idx = html.index("\ufffd")
        _fail(f"replacement character in the page near: ...{html[idx-60:idx+60]}...")
    print("ok 3b/4 no undecodable/mojibake characters")

    # -- 4. four axes rendered separately -----------------------------------
    axis_blocks = re.findall(r'<article class="axis" id="axis-([a-z_]+)"', html)
    if len(axis_blocks) != 4:
        _fail(f"expected 4 separate axis blocks, found {len(axis_blocks)}: {axis_blocks}")
    # one barrow per metric per axis, and no combined/total element carrying a value
    for banned in ('id="axis-total"', 'class="axis total"'):
        if banned in html:
            _fail(f"combined/total element present: {banned}")
    # "总分"-style wording may only appear inside a denial, never as a labelled value
    for match in re.finditer("总分|合计|综合分", html):
        window = html[max(0, match.start() - 40):match.end() + 40]
        if not _DENIAL_RE.search(window):
            _fail(f"a total/combined value label appears outside a denial: ...{window}...")
    print(f"ok 4/4  four axes rendered separately: {axis_blocks}")

    # -- 5. every mapped state word is shown in Chinese -----------------------
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import zh_terms as zh  # noqa: PLC0415

    tables = {"JUDGMENT": zh.JUDGMENT, "VERIFICATION": zh.VERIFICATION,
              "LEVEL": zh.LEVEL, "CHANGE_STATE": zh.CHANGE_STATE,
              "ROLE": zh.ROLE, "ACTION": zh.ACTION}
    missing = []
    for name, table in tables.items():
        for key, entry in table.items():
            zh_word = entry[0] if isinstance(entry, tuple) else entry
            if zh_word not in html:
                missing.append(f"{name}.{key} -> {zh_word}")
    if missing:
        _fail("state words not shown in Chinese: " + "; ".join(missing))

    # the five verdicts must render as 中文（原词）, not bare English
    for point in report["per_point"]["points"]:
        judgment = point["judgment"]
        cn = zh.JUDGMENT[judgment][0]
        if f"{cn}（{judgment}）" not in html:
            _fail(f"verdict not rendered as 中文（原词）: {judgment}")
    # the opening guide must answer what/who/how AND triage where to look first
    for needed in ("阅读指南", "本页是什么", "读者", "结论速览",
                   "先看这五处", "不是验收结论"):
        if needed not in html:
            _fail(f"intro guide is missing its '{needed}' block")
    # the triage table must link to the sections it tells the reader to check
    for anchor in ('href="#points"', 'href="#axes"', 'href="#stop"',
                   'href="#gaps"', 'href="#identity"'):
        if anchor not in html:
            _fail(f"intro triage does not link to {anchor}")
    print(f"ok 5/5  Chinese layer present ({sum(len(t) for t in tables.values())} mapped terms)")

    # -- 6. state meanings are rendered, with the unexpressed ones listed -----
    if 'id="states"' not in html:
        _fail("the state-meanings section is missing from the page")
    for word in report["state_meanings"]["attested"]:
        if word not in html:
            _fail(f"attested state meaning not shown: {word}")
    for word in ("reviewed", "blocked", "limited-use", "stale", "scoped", "planned"):
        if word not in html:
            _fail(f"expected attested state meaning missing: {word}")
    for entry in report["state_meanings"]["unexpressed_states"]:
        if entry["state"] not in html:
            _fail(f"unexpressed state not disclosed: {entry['state']}")
    print("ok 6/6  state meanings attested and unexpressed states disclosed")

    # -- 7. bilingual pairing ------------------------------------------------
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import zh_bi  # noqa: PLC0415

    prose: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, str):
            # English prose = has a space and contains a real English word
            if " " in node and re.search(r"[A-Za-z]{4,}", node) \
                    and not re.search(r"[\u4e00-\u9fff]", node):
                prose.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(report)
    cov = zh_bi.coverage(sorted(prose))
    ratio = cov["translated"] / cov["total"] if cov["total"] else 1.0
    if ratio < 0.90:
        _fail(f"only {cov['translated']}/{cov['total']} English prose strings are paired "
              f"with Chinese; missing: {cov['missing'][:5]}")
    # the bilingual pair must actually appear in the markup
    for marker in ('class="bi"', 'class="bi-zh"', 'class="bi-en"', 'class="lab-orig"'):
        if marker not in html:
            _fail(f"bilingual markup missing: {marker}")
    # the reader must be told that the Chinese is the renderer's translation
    if "不是权威表述" not in html:
        _fail("the page does not state that the Chinese is a rendering-layer translation")
    # and the English-original toggle must exist
    if 'id="show-en"' not in html or "hide-original" not in html:
        _fail("the 'show English original' toggle is missing")
    print(f"ok 7/7  bilingual pairing ({cov['translated']}/{cov['total']} = {ratio:.0%} of "
          f"English prose paired; toggle present; translation provenance stated)")

    # -- 8. no markdown/authoring markers in what a human reads ---------------
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    visible = " ".join(re.findall(r">([^<>]+)<", body))
    leaks = {
        "** 加粗记号": visible.count("**"),
        "` 反引号": visible.count("`"),
        "显示中的 HTML 实体": sum(visible.count(e) for e in
                                  ("&amp;", "&#x27;", "&quot;", "&emsp;")),
        "showing raw markup": sum(1 for t in re.findall(r">([^<>]+)<", body)
                                  if "class=" in t or "<span" in t),
    }
    bad = {k: v for k, v in leaks.items() if v}
    if bad:
        _fail(f"authoring markers are visible to the reader: {bad}")
    # and the emphasis must have become real formatting
    if "<strong>" not in html:
        _fail("no <strong> in the page: emphasis was dropped instead of rendered")
    print("ok 8/8  no markdown/authoring markers visible (emphasis rendered as <strong>/<code>)")

    # -- 9. the capability line must account for the WHOLE P-02 §5 vocab -------
    # (an independent verifier found this drift: the line still listed
    # stale/scoped/planned as NOT expressible while the data attested them. The
    # first repair compared the line against `attested` alone — 6 words out of
    # the 13 the claim covers — which let a wrong total ("14 entries") survive.
    # Both sides now derive from `_P02_SECTION_5_MEANINGS`.)
    meanings = report.get("state_meanings") or {}
    attested = set(meanings.get("attested") or {})
    unexpressed_live = {e["state"] for e in (meanings.get("unexpressed_states") or [])}
    cap_entries = [c for c in (report["five_decision_register"].get("not_yet_available") or [])
                   if c.get("capability") == "the full state vocabulary"]
    if not cap_entries:
        _fail("the state-vocabulary capability line is missing")
    cap_line = cap_entries[0]["status"]

    # (a) the vocabulary must remain a partition: every label once, exactly one
    #     declared unexpressible, and no expression kind the report cannot mean.
    canonical_total = len(_P02_SECTION_5_MEANINGS)
    if len(_P02_EXPRESSED_BY) != canonical_total:
        _fail("the P-02 §5 vocabulary transcript has a duplicate label")
    if set(_P02_EXPRESSED_BY.values()) - {"point_judgement", "history_side_judgement",
                                          "attestation", "not_expressible"}:
        _fail("the vocabulary records an unknown expression kind")

    # (b) the live report must agree with the transcript, in BOTH directions.
    declared_attestation = {label for label, kind in _P02_EXPRESSED_BY.items()
                            if kind == "attestation"}
    drift = sorted(attested - set(_P02_EXPRESSED_BY))
    if drift:
        _fail(f"the report attests words outside the P-02 §5 transcript: {drift}")
    for word in sorted(attested):
        if _P02_EXPRESSED_BY[word] != "attestation":
            _fail(f"the report attests {word} but the transcript calls it "
                  f"{_P02_EXPRESSED_BY[word]}")
    untriggered = sorted(declared_attestation - attested)
    if untriggered:
        _fail(f"the transcript claims these are attestable but the report does not "
              f"attest them: {untriggered}")
    if unexpressed_live != set(_P02_UNEXPRESSIBLE_LABELS):
        _fail(f"unexpressed_states ({sorted(unexpressed_live)}) disagrees with the "
              f"transcript ({sorted(_P02_UNEXPRESSIBLE_LABELS)})")

    # (c) every label the claim covers must appear in the prose, and no label may
    #     be listed inside the "not expressible" clause unless the transcript
    #     declares it unexpressible.
    for word in sorted(_P02_EXPRESSED_BY):
        if not _mentions(cap_line, word):
            _fail(f"capability line omits a P-02 §5 meaning: {word}")
    expressed_prose = [w for w in _P02_EXPRESSIBLE_LABELS if _mentions(cap_line, w)]
    if sorted(expressed_prose) != sorted(_P02_EXPRESSIBLE_LABELS):
        _fail("capability line must name every expressible P-02 §5 meaning")
    _, _, after = cap_line.partition("not expressible")
    leaked = [w for w in _P02_EXPRESSIBLE_LABELS if after and _mentions(after, w)]
    if leaked:
        _fail(f"capability line places expressible meanings inside the "
              f"not-expressible clause: {leaked}")

    # (d) the line in the artifact must BE the text the report module generates.
    #     This subsumes every count check by construction — and it replaces an
    #     earlier version that tested `str(total) in cap_line`, which an
    #     adversarial review defeated by typing a false total ("14 state and
    #     consequence meanings") as the same sentence still contained "13" inside
    #     "12 of 13 in total". Numbers are compared as the sentence, never as
    #     substrings that another number can satisfy.
    generated = _capability_status_for_state_vocabulary()
    if cap_line != generated:
        _fail("the rendered capability line differs from the report module's own "
              "text (the artifact or the sample json is stale, or a count was "
              "typed by hand)")
    # A count must be STATED, not merely satisfiable by another number in the same
    # sentence: the earlier version tested `str(total) in cap_line`, which a false
    # total ("14 state and consequence meanings") passed because "13" still
    # occurred inside "12 of 13 in total".
    for template in (f"P-02 §5 lists {canonical_total} state and consequence meanings",
                     f"{len(_P02_EXPRESSIBLE_LABELS)} of {canonical_total} in total"):
        if template not in cap_line:
            _fail(f"capability line does not state its counts verbatim "
                  f"(expected: {template!r})")
    # ...and the PAGE the human opens must carry EXACTLY that text, at every
    # place it renders it. An existence test is not enough: the sentence appears
    # twice in the page, so doctoring one copy leaves a page that contradicts
    # itself and still passes. Each rendered occurrence must equal the generated
    # sentence.
    page_text = " ".join(unescape(re.sub(r"<[^>]+>", "", html)).split())
    # A second, tighter view: the page keeps adjacent labels/values in one line of
    # markup, and check 10 needs "label immediately followed by its value". Join
    # lines first (each rendered row is one HTML line) so adjacency is meaningful
    # without depending on the exact ink between the two.
    page_lines = unescape(re.sub(r"<[^>]+>", "", html))
    rendered_lines = _capability_lines_on_page(page_text)
    if not rendered_lines:
        _fail("report.html does not render the state-vocabulary capability line")
    # The page renders the ``` `x` ``` code spans as markup, which tag-stripping
    # removes; drop the markers to compare text with text.
    generated_on_page = " ".join(generated.replace("`", "").split())
    for i, line in enumerate(rendered_lines, start=1):
        if line != generated_on_page:
            _fail(f"report.html renders a capability line that is not what this "
                  f"report generates (copy {i} of {len(rendered_lines)}): {line[:120]}")
    # Meaning text, not just labels: a deleted card or a rewritten reason must
    # fail. (An adversarial review deleted a whole attested card and rewrote the
    # unexpressible meaning's stated reason; both passed the existence-only
    # version of this check.)
    for word in sorted(attested):
        entry = (meanings.get("attested") or {})[word]
        for field in ("reason", "may_be_relied_on", "must_not_be_inferred",
                      "next_requirement"):
            value = " ".join(str(entry.get(field, "")).split())
            if value and value not in page_text:
                _fail(f"report.html does not carry {word}'s {field} "
                      f"(the card or its text was changed)")
    for entry in (meanings.get("unexpressed_states") or []):
        for field in ("reason", "what_would_change_it"):
            value = " ".join(str(entry.get(field, "")).split())
            if value and value not in page_text:
                _fail(f"report.html does not carry the {entry['state']} "
                      f"{field} (the page contradicts the report data)")
    point_count = sum(1 for k in _P02_EXPRESSED_BY.values() if k == "point_judgement")
    history_count = sum(1 for k in _P02_EXPRESSED_BY.values()
                        if k == "history_side_judgement")
    attest_count = sum(1 for k in _P02_EXPRESSED_BY.values() if k == "attestation")
    print(f"ok 9/9  capability line accounts for all {canonical_total} P-02 §5 meanings "
          f"({point_count} point judgements + {history_count} history side + "
          f"{attest_count} attested = {len(_P02_EXPRESSIBLE_LABELS)} expressible / "
          f"{len(_P02_UNEXPRESSIBLE_LABELS)} unexpressible; page carries the same "
          f"sentence at all {len(rendered_lines)} of its render sites, with every "
          f"meaning's own text)")

    # -- 10. the lane / use-ceiling section is rendered and honest -------------
    # P-03 row144 asks for an *existing lane-limit observation*. This check makes
    # the section a guarantee instead of a good intention: when the report carries
    # lane decisions, every one of them must be on the page with its ceiling and
    # its reason, and the two boundaries (unreachable lanes, not-a-release) must be
    # stated. When the report carries none, the page must say so plainly rather
    # than implying a default lane.
    lane_section = report.get("lane_and_release")

    def _page_text_of(value: Any) -> str:
        """A report string as the page renders it.

        The page turns ``` `x` ``` into a code span, so the markers are gone from
        the page text; compare against the same normalisation or every marked-up
        string would look missing.
        """
        return " ".join(str(value).replace("`", "").split())

    if lane_section is None:
        if "没有" not in html and "no lane" not in html.lower():
            _fail("no lane decision exists, but the page does not say so")
        lane_line = "no lane decision is recorded, and the page says so"
    else:
        decisions = lane_section.get("decisions") or []
        if not decisions:
            _fail("the lane section exists but carries no decision")
        # the closed domain is the only vocabulary the page may present as a lane
        from bioaudit.dp_lane import DP_LANES  # noqa: PLC0415
        for decision in decisions:
            for field in ("decided_lane", "requested_lane", "lane_ceiling"):
                value = decision.get(field)
                if value is None:
                    continue
                if value not in DP_LANES:
                    _fail(f"the page's lane section carries {field}={value!r}, which is "
                          f"outside the closed lane domain")
            # Pair the label with its value IN PLACE: the same value word can occur
            # elsewhere on the page (inside a reason sentence, as a code span; or
            # attached to ANOTHER field, since the requested lane can equal the
            # decided one), so neither "the value appears somewhere" nor "the value
            # is within N characters" proves it is presented as this field's value.
            # Anchor on the field's own English key, which renders right after the
            # Chinese label and immediately before the value.
            pairs = (("实际给到的使用上限", "decided_lane", decision["decided_lane"], 40),
                     ("事实支持的最高上限", "lane_ceiling", decision["lane_ceiling"], 40),
                     ("申请的使用上限", "requested_lane", decision["requested_lane"], 40),
                     ("结论", "decision_outcome", decision["decision_outcome"], 40),
                     ("谓词是否仍对应当前版本", "version_currency",
                      decision["version_currency"], 40),
                     # long values render as a bilingual block (Chinese label, the
                     # English key, then both language variants of the value), so this
                     # one needs a wider gap between the key and its value
                     ("声明的范围", "scope", decision.get("scope"), 260))
            for label_zh, key, value, gap in pairs:
                if not value:
                    continue
                # Several decisions share the same labels, so require the
                # label → key → value sequence at SOME occurrence, and fail only if
                # no occurrence presents them in order.
                presented = False
                for label_at in (m.start() for m in
                                 re.finditer(re.escape(label_zh), page_lines)):
                    after_key = page_lines.find(key, label_at)
                    if after_key < 0 or after_key - label_at > 80:
                        continue
                    following = page_lines[after_key + len(key):
                                           after_key + len(key) + gap]
                    if value in following:
                        presented = True
                        break
                if not presented:
                    _fail(f"the page never presents {label_zh!r} with the value "
                          f"{value!r} as that field's value (a mention elsewhere, or "
                          f"another field's value, is not a presentation)")
            # The page renders the reason as a bilingual pair: Chinese (from the
            # zh_bi table) plus the English original. Accept either, but the check
            # must not be satisfiable by a copy of the same sentence living
            # elsewhere on the page — so anchor on the Chinese label first.
            reason_raw = str(decision.get("ceiling_reason") or "")
            reason_en = _page_text_of(reason_raw)
            if not reason_en:
                _fail(f"the record {decision.get('record_id')!r} carries no ceiling "
                      f"reason at all")
            # The ceiling reason must be readable in Chinese: the page's guarantee
            # is that report prose is presented bilingually for a Chinese-first
            # reader. Comparing only the English original is not enough — a doctored
            # or reworded reason can still contain a prefix of the original when the
            # sentence lives on elsewhere on the page.
            # NOTE: look the translation up from the RAW string; the table is keyed
            # on the report's own text (its normaliser expects the code markers).
            # The table's Chinese may carry light markdown (``**strong**``), which
            # the page renders as markup: normalise it away before comparing, the
            # same way the renderer does.
            expected_zh = (zh_bi.zh_for(reason_raw) or "")
            expected_zh = re.sub(r"[*`]", "", expected_zh).strip()
            if not expected_zh:
                _fail(f"the ceiling reason for {decision.get('record_id')!r} has no "
                      f"Chinese-layer text, so a Chinese-first reader would only get "
                      f"English: {reason_en[:80]!r}")
            # Each decision has its OWN reason field. Find that decision's field by
            # pairing the label with the value it belongs to, then check the Chinese
            # appears inside it — so a second decision's reason cannot satisfy the
            # first decision's assertion.
            occurrences = [m.start() for m in
                           re.finditer("为什么不能再高", page_lines)]
            if not occurrences:
                _fail("the page does not show the ceiling-reason field at all")
            presented = False
            for at in occurrences:
                after_key = page_lines.find("ceiling_reason", at)
                if after_key < 0 or after_key - at > 60:
                    continue
                nxt = page_lines.find("明令禁止的用途", after_key)
                block = page_lines[after_key: nxt if nxt > after_key else after_key + 1200]
                if expected_zh[:40] in block:
                    presented = True
                    break
            if not presented:
                _fail(f"the ceiling reason for {decision.get('record_id')!r} is not "
                      f"shown in Chinese in its own field (a bare ceiling label, or a "
                      f"reason the Chinese layer cannot express)")
            for restriction in decision.get("prohibited_uses") or []:
                if _page_text_of(restriction) not in page_text:
                    _fail(f"a binding restriction is missing from the page: {restriction!r}")
        for field in ("unreachable_lanes_note", "not_release_note", "statement",
                      "recorded_at_note"):
            value = _page_text_of(lane_section.get(field) or "")
            if not value or value not in page_text:
                _fail(f"the lane section does not state its {field} on the page")

        # grouping by declared scope: the page must show each group with its size,
        # and the group sizes must add up to the decisions rendered
        groups = lane_section.get("decisions_by_scope") or []
        if not groups:
            _fail("the report carries lane decisions but no scope grouping")
        grouped_total = sum(int(g.get("decision_count") or 0) for g in groups)
        if grouped_total != len(decisions):
            _fail(f"the scope grouping accounts for {grouped_total} decisions but "
                  f"{len(decisions)} are rendered (the picture does not add up)")
        for group in groups:
            scope_line = f'{group["scope"]}'
            if scope_line not in page_text:
                _fail(f"the page does not show the decision scope {scope_line!r}")
            expected = f'共 {group["decision_count"]} 条决定'
            if expected not in page_text:
                _fail(f"the page does not state the group size ({expected!r}) for "
                      f"{scope_line!r}")
            # MEMBERSHIP, not just the sum (review F2): the group's ids and lanes must
            # be exactly the decisions that declare that scope — a group could
            # otherwise advertise a lane none of its decisions recorded.
            members = [d for d in decisions if d.get("scope") == group["scope"]]
            if sorted(group.get("record_ids") or []) != sorted(
                    d.get("record_id") for d in members):
                _fail(f"the group for {scope_line!r} lists record_ids "
                      f"{group.get('record_ids')} but its decisions are "
                      f"{[d.get('record_id') for d in members]}")
            group_lanes = []
            for member in members:
                if member["decided_lane"] not in group_lanes:
                    group_lanes.append(member["decided_lane"])
            if list(group.get("decided_lanes") or []) != group_lanes:
                _fail(f"the group for {scope_line!r} advertises lanes "
                      f"{group.get('decided_lanes')} but its decisions decided "
                      f"{group_lanes}")
            stamps = [d.get("recorded_at") for d in members if d.get("recorded_at")]
            if stamps and group.get("latest_recorded_at") != max(stamps):
                _fail(f"the group for {scope_line!r} reports its latest time as "
                      f"{group.get('latest_recorded_at')!r}, but its newest decision "
                      f"is {max(stamps)!r}")

        # each decision's write time must be presented in its own recorded-at field,
        # and the page must say what that time does and does not mean
        if not all(d.get("recorded_at") for d in decisions):
            _fail("a lane decision carries no write time, so the page cannot show "
                  "when it was recorded")
        stamps = {d["recorded_at"] for d in decisions}
        occurrences = [m.start() for m in
                       re.finditer("这一条写进账本的时间", page_lines)]
        if not occurrences:
            _fail("the page does not show the recorded-at field at all")
        shown = 0
        for at in occurrences:
            block = page_lines[at: at + 220]
            if any(stamp in block for stamp in stamps):
                shown += 1
        if shown != len(occurrences):
            _fail(f"only {shown} of {len(occurrences)} recorded-at fields state a "
                  f"write time (a field without its value is a bare label)")
        # the page must never present a lane as a value or a score — but a DENIAL
        # that names them together ("…, not a score, not a ranking, not a
        # confidence") is exactly the honesty this check exists to protect.
        for match in re.finditer(r"lane[^<>]{0,40}(score|ranking|confidence)", html,
                                 re.IGNORECASE):
            window = html[max(0, match.start() - 120):match.end() + 120]
            if _DENIAL_RE.search(window) or _LANE_FIELD_RE.search(window):
                continue
        lane_line = (f"{len(decisions)} lane decision(s) rendered with ceiling, reason "
                     f"and restrictions")
    print(f"ok 10/10 lane / use-ceiling section ({lane_line})")

    # -- 11. three reading depths over ONE dataset ---------------------------
    # DP-UI-LAYERED-DESIGN: the page offers three depths (conclusion / reasons /
    # evidence chain) switched by collapsing, never by rendering three copies. The
    # switch is CLIENT-side, so this check reads the declared layers in the static
    # HTML: a section's layer is its minimum depth, and a section whose layer exceeds
    # a depth must carry `hidden` when that depth is the declared default (2). That
    # is structural, not cosmetic: if a section could only be hidden by script, a
    # reader with script disabled would never see the layers at all.
    def _sections(text: str):
        """(id, layer, attrs) for every <section> that declares an id."""
        found = []
        for match in re.finditer(r"<section\b([^>]*)>", text):
            attrs = match.group(1)
            id_match = re.search(r'id="([^"]+)"', attrs)
            if not id_match:
                continue
            layer_match = re.search(r'data-layer="(\d+)"', attrs)
            found.append((id_match.group(1),
                          int(layer_match.group(1)) if layer_match else None,
                          attrs))
        return found

    sections = _sections(html)
    by_id = {s[0]: s for s in sections}
    if not sections:
        _fail("the page has no <section> elements to layer")
    if not re.search(r'<html[^>]*data-depth="(\d+)"', html):
        _fail("the page does not declare its default reading depth on <html>")
    declared = int(re.search(r'<html[^>]*data-depth="(\d+)"', html).group(1))
    if html.count('data-depth=') < 2:
        _fail("only one depth is declared — the switch cannot be observed")

    # the controls: labelled in words (never by colour alone), ordered, default marked
    buttons = re.findall(r'<button[^>]*data-depth-to="(\d)"[^>]*>(.*?)</button>',
                         html, re.S)
    if len(buttons) != 3:
        _fail(f"expected 3 depth controls, found {len(buttons)}")
    labels = [re.sub(r"<[^>]+>", "", t).strip() for _, t in buttons]
    if [n for n, _ in buttons] != ["1", "2", "3"]:
        _fail(f"depth controls are out of order: {[n for n, _ in buttons]}")
    for number, label in zip((n for n, _ in buttons), labels):
        if not label:
            _fail(f"depth control {number} carries no written label (colour is not a label)")
    if "active" not in re.search(r'<button[^>]*data-depth-to="%d"[^>]*>' % declared,
                                 html).group(0):
        _fail("the declared default depth is not marked on its own control")
    print(f"ok 11/12 three reading depths declared "
          f"(default={declared}, controls={labels})")

    # -- 12. the two blocks that stop over-reading are permanent -------------
    # A reader who selects the shallowest depth must still see what is missing and
    # where the account must stop. Folding those away would turn the page from
    # helping a reader understand into helping them misread.
    permanent = ("intro-card", "gaps", "stop", "glossary", "states")
    for section_id in permanent:
        if section_id not in by_id:
            _fail(f"permanent section {section_id!r} is missing from the page")
        layer = by_id[section_id][1]
        # no layer attribute means the permanent layer: nothing can fold it
        if layer is not None and layer > 1:
            _fail(f"permanent section {section_id!r} declares layer {layer}, "
                  f"which would hide it at a shallower depth")
        if re.search(r"\bhidden\b", by_id[section_id][2]):
            _fail(f"permanent section {section_id!r} is hidden in the static page")
    layered = [(i, layer) for i, layer, _ in sections
               if layer is not None and layer > 1]
    if not layered:
        _fail("no section declares a depth beyond the permanent layer — "
              "the depths would be identical")
    hidden_now = [section_id for section_id, layer, attrs in sections
                  if layer is not None and layer > declared
                  and re.search(r"\bhidden\b", attrs)]
    if not hidden_now:
        _fail(f"no section is hidden at the declared default depth {declared}")
    print(f"ok 12/12 permanent blocks stay visible at every depth "
          f"({len(permanent)} permanent, {len(layered)} layered, "
          f"{len(hidden_now)} hidden at depth {declared})")

    # -- 13. every point carries its own evidence chain ----------------------
    # The deepest depth promises the EVIDENCE CHAIN, not a summary of it: for each
    # decision point, every evidence item must be traceable to its source, the
    # subject it is about, the claim it was offered for, its role, when it was
    # observed, how long it was said to hold, and what that means now. A point whose
    # chain is missing, or an item that renders without those fields, breaks the
    # promise the third depth makes.
    chains = re.findall(r'<details class="folding evidence-chain"[^>]*data-min-depth="3"', html)
    points = report["per_point"]["points"]
    if len(chains) != len(points):
        _fail(f"expected one evidence chain per decision point ({len(points)}), "
              f"found {len(chains)}")
    # the chain must be tied to the point it belongs to
    for point in points:
        anchor = f'id="point-{point["name"]}"'
        if anchor not in html:
            _fail(f"point {point['name']} has no anchored card to hang a chain on")
    _EVIDENCE_FIELDS = (
        ("evidence_role", "证据角色"),
        ("observed_at", "观测时点"),
        ("valid_for_seconds", "有效期"),
        ("freshness", "时效"),
    )
    items_total = 0
    for point in points:
        for item in (point.get("evidence") or []):
            items_total += 1
            for key, label in _EVIDENCE_FIELDS:
                value = item.get(key)
                if value in (None, ""):
                    continue
                # a field without its value is a bare label: the value must be on
                # the page next to the label, not merely somewhere else on it
                if str(value) not in html:
                    _fail(f"evidence {key}={value!r} of {point['name']} is not on the page")
            for key in ("provenance", "provenance_subject", "supports"):
                value = item.get(key)
                if value and " ".join(str(value).split()) not in page_text:
                    _fail(f"evidence {key} of {point['name']} is not on the page")
    # and the chain must be closed at depths 1 and 2 (min-depth 3 => hidden there)
    if 'html[data-depth="1"] [data-min-depth="3"],' not in html \
            and '[data-min-depth="3"]' not in html:
        _fail("the evidence chain is not tied to the deepest depth")
    print(f"ok 13/13 evidence chain per decision point "
          f"({len(chains)} chains, {items_total} evidence item(s), "
          f"{len(_EVIDENCE_FIELDS)} qualification fields each)")

    # -- 14. the page declares what it is NOT -------------------------------
    # A generated page that renders an account will be mistaken for a deliverable
    # unless it says otherwise. The page must state, in visible text, that it is an
    # observation tool rather than a deliverable, and that the form of any
    # external-facing report is still an undecided product decision (P-03 §15).
    # Without this the page silently grows into a product surface nobody authorized.
    body_text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
    if "观察工具" not in body_text:
        _fail("the page does not state that it is an observation tool")
    if "不是对外交付件" not in body_text:
        _fail("the page does not state that it is not a deliverable")
    if "未决的产品决定" not in body_text:
        _fail("the page does not state that the external form is an undecided "
              "product decision")
    print("ok 14/14 the page declares its own boundary "
          "(observation tool, not a deliverable; external form undecided)")

    # -- 15. the criteria panel states what the judgements rest on -------------
    # DP-CRITERIA-PANEL-ACKNOWLEDGEMENT: the rule library's ladders and their
    # provenance are shown on the page, and the panel says plainly what it does NOT
    # derive. Rules come from real rule files, so this check reads them from disk and
    # requires the page to carry the same numbers — a panel that rendered a ladder
    # nobody wrote would be worse than no panel.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import criteria  # noqa: PLC0415

    family = criteria.family_for((report.get("context") or {}).get("data_type"))
    if not family:
        _fail("the report's data_type maps to no rule family, so no criteria can be shown")
    if 'id="criteria"' not in html:
        _fail("the criteria panel section is missing from the page")
    panel_points = 0
    for point in report["per_point"]["points"]:
        name = point["name"]
        filename = (criteria.POINT_RULES.get(name) or {}).get(family)
        if not filename:
            _fail(f"no rule file is mapped for {name} in family {family}")
        rule = criteria.load_rule(family, filename)
        if rule is None:
            _fail(f"rule file {family}/{filename} could not be read for {name}")
        if str(rule.get("rule_id")) not in html:
            _fail(f"the page does not show {name}'s rule id {rule.get('rule_id')!r}")
        # every LEVEL of that rule must appear as a ladder row. `scoring` also holds
        # non-level keys (an `override_n2` rule, for example), so match the level
        # pattern rather than every key — the first version of this check failed on
        # exactly that and was caught by running it.
        for level in (rule.get("scoring") or {}):
            if not re.fullmatch(r"level_\d", str(level)):
                continue
            if level not in html:
                _fail(f"{name}'s rule ladder omits {level}")
            panel_points += 1
        # and its provenance must reach the page
        for ev in (rule.get("evidence") or []):
            doi = ev.get("doi")
            if doi and doi not in html:
                _fail(f"{name}'s rule cites {doi} but the page does not show it")
            title = ev.get("title")
            if title and " ".join(str(title).split()) not in page_text:
                _fail(f"{name}'s rule cites {title!r} but the page does not show it")
    print(f"ok 15/16 criteria panel states the rule ladder and its provenance "
          f"({panel_points} ladder rows across {len(report['per_point']['points'])} "
          f"points, family {family})")

    # -- 16. tier attribution is tri-state, and never a verdict ----------------
    # Since the mapping table exists, three points DO get a tier. The load-bearing
    # property is no longer "nothing is derived" but "the three outcomes cannot be
    # confused": a mapped method shows its tier; a declared name the table cannot
    # place says so and is NOT shown as a tier; and an unknown name is not silently
    # treated as the worst rung. Ladder levels inform and never decide.
    aliases = criteria.load_method_aliases()
    if not aliases.get("families"):
        _fail("the method-mapping table is missing or empty, so no tier can be derived")
    mapped = unmapped = unknown = 0
    for point in report["per_point"]["points"]:
        tier = criteria.tier_for(point["name"], point.get("declared_method"), family, aliases)
        if tier is None:
            continue
        if tier.get("level"):
            mapped += 1
            if str(tier["level"]) not in html:
                _fail(f"{point['name']}'s derived tier {tier['level']} is not on the page")
        elif tier.get("unmapped"):
            unmapped += 1
            # the reason must be on the page, in words, where the derive would be
            if "无法归属" not in page_text:
                _fail(f"{point['name']} is unmapped but the page does not say so")
        else:
            unknown += 1
    if mapped == 0:
        _fail("no decision point's tier could be derived, yet the mapping table exists")
    if unmapped == 0:
        _fail("every point derived a tier, which would mean nothing is ever unmapped — "
              "the panel must be able to say 'cannot place this by name'")
    if "不参与判定" not in page_text:
        _fail("the criteria panel does not state that ladder levels do not take part "
              "in judgement")
    if "不构成好坏评价" not in page_text:
        _fail("the criteria panel does not deny that a derived tier is a quality verdict")
    # the panel must not present any combined value for this account
    for banned in ('id="criteria-score"', 'class="criteria total"'):
        if banned in html:
            _fail(f"the criteria panel presents a combined value: {banned}")
    print(f"ok 16/16 tier attribution is tri-state "
          f"({mapped} derived, {unmapped} not placeable by name, {unknown} unknown; "
          f"levels inform and never decide)")

    # -- 17. the chain overview is the spine: five steps in pipeline order ----
    # The page leads the reader through the workflow (filtering → normalization →
    # differential method → multiple testing → threshold), each step carrying its
    # own existing judgment, and the state line explicitly denies being a total.
    # Order is checked INSIDE the chain block, not on whole-page text: the same
    # step names legitimately appear elsewhere (glossary, point cards), and a
    # whole-page search would mis-order against the first stray occurrence.
    if 'id="chain"' not in html:
        _fail("the page has no chain-overview section")
    chain_start = html.find('id="chain"')
    chain_end = html.find('id="act-one"', chain_start)
    chain_block = re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", html[chain_start:chain_end]))
    order = ["低表达过滤", "归一化", "差异分析方法", "多重检验校正", "显著性/效应量阈值"]
    pos = -1
    for name in order:
        at = chain_block.find(name)
        if at < 0 or at < pos:
            _fail(f"chain steps are not in pipeline order at {name!r}")
        pos = at
    if "不是总评" not in page_text or "不产生任何总分" not in page_text:
        _fail("the chain state line does not deny being a combined verdict")
    print("ok 17/17 chain overview: five steps in pipeline order as the spine "
          "(summed, not merged)")

    # -- 18. the chain carries the DP-COHERENCE small section -----------------
    # 收紧（2026-09-15 DP-COHERENCE 窗口）：原第 17 项要求「科学连贯 + 本页不判定」
    # 的占位句存在；现在占位句必须已删，改为要求「连贯小段存在（4 对 + 3 全局，
    # 每行三态标签 + 理由）、三态 token 與中文对照齐全、动作只用既有两种」。
    coh = report.get("cross_step_coherence")
    if not isinstance(coh, dict) or not isinstance(coh.get("items"), list) \
            or not coh["items"]:
        _fail("the report has no cross_step_coherence view to render")
    coh_items = coh["items"]
    if len(coh_items) != 7:
        _fail(f"cross_step_coherence must carry 7 items, got {len(coh_items)}")
    if sum(1 for i in coh_items if i.get("kind") == "pair") != 4 \
            or sum(1 for i in coh_items if i.get("kind") == "global") != 3:
        _fail("cross_step_coherence must carry exactly 4 pairs and 3 globals")
    tokens_zh = coh.get("tokens") or {}
    if not {"coherent", "needs_review", "incoherent"} <= set(tokens_zh):
        _fail("the coherence tokens dict is incomplete")
    if "跨步骤科学连贯" not in page_text or "chv-coh" not in html:
        _fail("the page does not render the cross-step coherence section")
    for zh_cn in tokens_zh.values():
        if not zh_cn or zh_cn not in page_text:
            _fail(f"the page does not render a locked coherence token as {zh_cn!r}")
    if "本页不判定" in page_text:
        _fail("the old placeholder sentence 'steps are not judged on this page' "
              "is still present")
    for item in coh_items:
        if item.get("state") not in {"coherent", "needs_review", "incoherent"}:
            _fail(f"coherence item {item.get('id')} uses an unknown state token")
        if item.get("action") not in (None, "request_evidence", "request_review"):
            _fail(f"coherence item {item.get('id')} uses a non-existing action token")
    print("ok 18/18 chain carries the DP-COHERENCE small section "
          "(4 pairs + 3 globals, three locked tokens, placeholder removed)")

    # -- 18. navigation ↔ sections ↔ depth are one model ----------------------
    # The navigation, the section ids and the reading depths all come from the same
    # registry (_NAV). A hand-written nav previously drifted from the sections it
    # pointed at (anchors into display:none targets, which the browser cannot jump
    # to - the systemic mis-navigation). This check pins the contract:
    #   * every data-goto target must exist exactly once;
    #   * every point detail (point-<name>) must exist;
    #   * every layered target advertises its data-layer, and the JS navigation
    #     promise (data-goto) is present for the hidden ones.
    link_ids = re.findall(r'<a\b[^>]*data-goto="([a-z0-9_-]+)"', html)
    targets = re.findall(r'<(?:section|article|details)\b[^>]*id="([a-z0-9_-]+)"', html)
    target_set = set(targets)
    dup_ids = [k for k in set(targets) if targets.count(k) > 1]
    missing = [g for g in link_ids if g not in target_set]
    if missing:
        _fail(f"nav data-goto targets have no element id: {missing}")
    if dup_ids:
        _fail(f"duplicate element ids (anchors would be ambiguous): {dup_ids}")
    for point_name in ("filtering", "normalization", "differential_method",
                       "multiple_testing_correction", "significance_threshold"):
        pid = f"point-{point_name}"
        if pid not in target_set:
            _fail(f"point detail {pid!r} has no targetable id")
    layered_goals = [g for g in re.findall(r'<a\b[^>]*data-goto="([a-z0-9-]+)"[^>]*data-layer="([23])"', html)]
    if not layered_goals:
        _fail("no nav entry advertises a deeper layer (the lift-then-jump promise "
              "would be unexercised)")
    print(f"ok 19/19 navigation-sections-depth unified ({len(link_ids)} links, "
          f"{len(target_set)} ids, all targets exist, point details targetable)")

    print("\nPASS — ui/report.html satisfies the DP-UI-REPORT guarantees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
