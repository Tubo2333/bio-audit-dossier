"""Generate the human-facing report UI (DP-UI-REPORT) — 中文优先的检查页.

Renders the REAL output of ``bioaudit.api.render_dp_report`` into one
self-contained static HTML file (no external CSS/JS/fonts, no network).

Audience decision (human, recorded in ``DP-UI-REPORT-INSTRUCTION.md``): this is
an **inspector page for the accountable owner** — Chinese-first, every state word
paired with its original term, with a deliberate reading hierarchy. A future
formal "show it to others" report is a separate, not-yet-made product decision;
this page only reserves a documented spot for it.

Usage (from the repo root):
    .\\.venv\\Scripts\\python.exe ui\\generate_report.py --out ui\\report.html
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import chain_view  # noqa: E402  (链条总览：与交付文档共用的单一事实源)
import criteria
import zh_bi  # noqa: E402
import zh_terms as zh  # noqa: E402

from bioaudit.api import (  # noqa: E402
    audit_dp_register,
    build_dp_acceptance_snapshot,
    dp_acceptance_snapshot_ref,
    dp_authority_store,
    dp_lane_store,
    record_dp_lane_decision,
    render_dp_report,
)
from bioaudit.dp01_store import DP01JSONLStore  # noqa: E402

# --------------------------------------------------------------------------
# tone classes (colour is a SECOND channel — every state is also written out)
# --------------------------------------------------------------------------

_TONE = {
    'auditable': 'ok',
    'scientifically_limited': 'warn',
    'conflicted': 'conflict',
    'integrity_failed': 'bad',
    'not_auditable': 'bad',
    'pending_attempt': 'pending',
    'historical_only': 'muted',
    'confirmed': 'ok',
    'unverified': 'pending',
    'failed': 'bad',
    'complete': 'ok',
    'partial': 'warn',
    'missing': 'bad',
    'sufficient': 'ok',
    'insufficient': 'warn',
    'awaiting_human_decision': 'warn',
    'realized': 'ok',
    'proposed': 'pending',
}

_COMPLETENESS_WIDTH = {'complete': 100, 'partial': 55, 'missing': 12}
_EVIDENCE_WIDTH = {'sufficient': 100, 'insufficient': 45, 'missing': 12, 'unverified': 45}

_AXIS = {
    'inference_type': ('推理类型', 'Inference type', '这一步的结论是怎么推出来的、能推到哪。'),
    'explanation_depth': ('解释深度', 'Explanation depth', '解释到哪一层：只看现象，还是说到了机制。'),
    'scope': ('适用范围', 'Scope', '结论在哪个项目/分析/比较范围内成立。'),
    'validation': ('有效性', 'Validation', '这个账户本身是否可被检验（不等于每一步都被支持）。'),
}

_STATE_ZH = {
    'reviewed': '已复核',
    'blocked': '已阻断',
    'limited-use': '限定使用',
    'integrity-failed': '完整性／权威失败',
    'stale': '版本核对：未过时',
    'scoped': '范围受限',
    'planned': '计划中',
}

# 四轴不是总分这件事，写在四轴之前，用最直白的话。
_NOT_SCORE_NOTE = (
    '下面四条是四个彼此独立的结果，不是一个总分的四个部分：它们不相加、不加权、不比较大小、'
    '不产生排名，也不能合成一句话结论。这是本产品的既成规则：结果不得被读成一个分数。'
)

# 编码值（不需要"翻译"的字段）：照录即可——它们是记录里的键、引用与标识。
# 2026-09-14（2C）定稿：编号、版本、时间、方法名、引用串一律不翻译、不做双语段。
_VERBATIM_KEYS = {
    'valid_for_seconds', 'conditions', 'named_boundary', 'named', 'differing',
    'planned_capabilities', 'blockers', 'ceiling',
    'points', 'prohibited_uses', 'required_next_evidence_refs',
    'lane_predicate_evidence_refs', 'acceptance_snapshot_ref', 'record_id',
    'snapshot_id', 'recorded_at', 'observed_at', 'scope', 'target', 'actor',
}


def _clean(text: Any) -> str:
    """Repair one upstream mojibake character (§ arrives double-encoded)."""
    if not isinstance(text, str):
        return ''
    return text.replace('P-02 \ufffd\ufffd5.2', 'P-02 §5.2').replace('\ufffd', '§')


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_CODE = re.compile(r"`([^`]+)`")


def to_html(text: Any) -> str:
    """把文本里的轻量标记渲染成排版效果，而不是让记号本身出现在人眼前。

    `**加粗**` → 真正的 <strong>；`反引号` → 等宽样式 <code>（表示"这是字段名"）；
    `&emsp;` 之类的排版实体 → 真字符。先转义再转换，所以内容本身不会被当 HTML 执行。
    """
    if text is None:
        return ""
    out = html.escape(str(text), quote=True)
    out = _MD_BOLD.sub(r"<strong>\1</strong>", out)
    out = _MD_CODE.sub(r"<code>\1</code>", out)
    out = out.replace("&amp;emsp;", "\u2003")
    out = out.replace("&amp;nbsp;", "\u00a0")
    # HTML 里绝不会出现、只可能是原本就写在文本里的实体：还原成字符
    out = (out.replace("&amp;", "&").replace("&#x27;", "'")
           .replace("&quot;", '"').replace("&#34;", '"'))
    return out


def esc(value: Any) -> str:
    """单行文本：转义 + 渲染轻量标记。"""
    return to_html(_clean(value))



def L(value: Any, table: dict[str, Any] | None = None) -> str:
    """中文（原词）"""
    return esc(zh.label(value, table))


def _pct(value: str | None, table: dict[str, int]) -> int:
    return table.get(value or '', 0)


# --------------------------------------------------------------------------
# pipeline: one real five-point account
# --------------------------------------------------------------------------

_DEMO_CONTEXT = {
    'project_id': 'PRJ-Alpha',
    'analysis_id': 'AN-001',
    'comparison': 'treated_vs_control',
    'n_samples': {'treated': 8, 'control': 8},
    'data_type': 'bulk_rnaseq',
    'audit_scope': 'five-point MVP slice',
    'intended_use': 'internal scientific review',
    'exclusions': 'batch 3 excluded (instrument drift); no external release intended',
}
_DEMO_INTEGRITY = {
    **{d: {'state': 'confirmed'} for d in
       ('identity', 'version', 'source', 'binding', 'snapshot', 'permission',
        'unique_authority')},
    'material_ids': ['material-1', 'material-2'],
    'pending_targets': ['decision'],
}


def _decl(method: str) -> dict[str, Any]:
    return {'method': method, 'threshold': 6, 'sample_rule': 'at_least_two_samples',
            'unit': 'gene', 'source': 'declared'}


def _obs(value: Any, verification: str = 'confirmed', *,
         observed_at: str | None = None, valid_for_seconds: int | None = None,
         provenance: str | None = None, provenance_subject: str | None = None,
         supports: str | None = None, evidence_role: str = 'support') -> dict[str, Any]:
    """One qualified observation (P-02 §4.1) for the demo account.

    The demo declares its own observation moments and windows rather than leaning on
    the default, so the page can show what a real reader would see: when each piece
    of material was observed, how long it was claimed to hold, where it came from,
    and which claim it is offered for.
    """
    item: dict[str, Any] = {
        'source_type': 'observed' if provenance is None else 'referenced',
        'verification': verification,
        'value': value,
        'observed_at': observed_at or _DEMO_OBSERVED_AT,
        'valid_for_seconds': valid_for_seconds or _DEMO_VALIDITY_SECONDS,
    }
    if provenance is not None:
        item['provenance'] = provenance
    if provenance_subject is not None:
        item['provenance_subject'] = provenance_subject
    if supports is not None:
        item['supports'] = supports
    if evidence_role != 'support':
        item['evidence_role'] = evidence_role
    return item


#: The demo account is dated 20 days before its ledger is written, inside the
#: declared window: the page then shows a CURRENT item, not a default-looking one.
# One fixed moment for the whole demo report, so the generated artifacts are
# reproducible: regenerating twice from the same commit gives byte-identical
# output, and the page and its sample JSON can never drift apart by a clock tick.
# A demo that re-stamps itself on every run makes "the page disagrees with the
# data" indistinguishable from a real regression.
_DEMO_NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

_DEMO_OBSERVED_AT = (_DEMO_NOW - timedelta(days=20)).isoformat(
    timespec='seconds')
_DEMO_VALIDITY_SECONDS = 180 * 24 * 3600


def _lane_stamp(days_ago: int) -> str:
    return (_DEMO_NOW - timedelta(days=days_ago)).isoformat(
        timespec='seconds')


def build_demo_report() -> dict[str, Any]:
    """One real account: 3 auditable + 1 conflicted + 1 scientifically limited."""
    points = {
        'filtering': ('low_count_filter', [_obs(
            'low_count_filter',
            provenance='the pipeline log for this run (rule match + executed method)',
            provenance_subject='PRJ-Alpha / AN-001 filtering step',
            supports='the declared low-count filtering method was the executed one')]),
        'normalization': ('tmm', [_obs(
            'tmm', provenance='the analysis script parameters recorded for this run',
            provenance_subject='PRJ-Alpha / AN-001 normalization step',
            supports='the declared TMM normalization was what ran')]),
        'differential_method': ('deseq2', [
            _obs('deseq2', provenance='the methods section of the submitted write-up',
                 provenance_subject='PRJ-Alpha / AN-001 differential analysis',
                 supports='the declared DESeq2 method'),
            _obs('edgeR', provenance='the executed workflow log',
                 provenance_subject='PRJ-Alpha / AN-001 differential analysis',
                 supports='the executed differential method', evidence_role='conflict'),
        ]),
        'multiple_testing_correction': ('BH', [_obs(
            'BH', provenance='the analysis script parameters recorded for this run',
            provenance_subject='PRJ-Alpha / AN-001 correction step',
            supports='the declared BH correction was applied')]),
        'significance_threshold': ('padj_0.05', [_obs(
            'padj_0.05', 'unverified',
            provenance='a threshold stated in the write-up, with no execution record',
            provenance_subject='PRJ-Alpha / AN-001 threshold',
            supports='the declared threshold choice',
            evidence_role='limitation')]),
    }
    request = {
        'audit_id': 'ui-demo-1',
        'analysis_context': _DEMO_CONTEXT,
        'integrity_metadata': _DEMO_INTEGRITY,
        'decision_points': {
            name: {'decision_declaration': _decl(m), 'evidence_observations': ev}
            for name, (m, ev) in points.items()},
    }
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='dp-ui-'))
    point_store = DP01JSONLStore(tmp / 'points.jsonl')
    authority_store = dp_authority_store(tmp / 'authority.jsonl')
    register = audit_dp_register(request, point_store)
    snapshot = build_dp_acceptance_snapshot(request, point_store, authority_store,
                                            snapshot_id='snapshot:ui-demo-1',
                                            register=register)
    # Deliberately NO role records and NO change context: the report then states
    # that honestly instead of the page faking a review (P-03 row146 non-claim).
    #
    # One lane decision IS recorded, because P-03 row144 asks for an observable
    # existing lane-limit: without a recorded decision the page could only repeat a
    # denial, which is exactly the gap this section exists to close. The decision
    # below is derived by the real lane code (not hand-written here), and it lands
    # at `candidate` because the account carries a conflicted point and one
    # unverified observation.
    # Each decision carries its OWN write time (a stored field since the review fix),
    # so the demo can show two decisions made at different times without the old
    # file-mtime trick — and a copied ledger keeps these times.
    first_time = _lane_stamp(3)
    second_time = _lane_stamp(1)
    third_time = _lane_stamp(0)
    scope_a = 'DP-01..DP-05 filtering slice / research use within this project'
    lane_store = dp_lane_store(tmp / 'lane.jsonl')
    # The demo exercises BOTH freshness gates by handing each decision the account's
    # OWN evidence for that point: the stale-support fact is then computed from real
    # material rather than set by hand. When the supporting items are inside their
    # declared window the gate passes; when they are not, the ceiling falls to the
    # return state and the page has to say why.
    filtering_evidence = tuple(getattr(register['filtering'], 'evidence', ()) or ())
    normalization_evidence = tuple(getattr(register['normalization'], 'evidence', ()) or ())
    differential_evidence = tuple(
        getattr(register['differential_method'], 'evidence', ()) or ())
    record_dp_lane_decision(
        lane_store,
        record_id='ceiling-ui-demo-1',
        acceptance_snapshot_ref=(
            dp_acceptance_snapshot_ref(snapshot)['acceptance_snapshot_ref']['snapshot_id']),
        requested_lane='validated',
        decision_outcome='accepted',
        scope=scope_a,
        integrity_ok=True,
        axes_auditable=True,
        residual_unordered=True,
        has_restrictions=True,
        lane_predicate_evidence_refs=('ev-filtering-observed', 'ev-normalization-declared'),
        prohibited_uses=('clinical or diagnostic use', 'any use outside the named project'),
        note='请求了 validated，但事实只支持受限的 candidate——这是如实降档，不是拒绝。',
        now=first_time,
        evidence=filtering_evidence,
    )
    record_dp_lane_decision(
        lane_store,
        record_id='ceiling-ui-demo-2',
        acceptance_snapshot_ref=(
            dp_acceptance_snapshot_ref(snapshot)['acceptance_snapshot_ref']['snapshot_id']),
        requested_lane='candidate',
        decision_outcome='return_for_evidence',
        scope=scope_a,
        integrity_ok=True,
        axes_auditable=True,
        residual_unordered=False,
        has_restrictions=True,
        lane_predicate_evidence_refs=('ev-significance-declared',),
        prohibited_uses=('clinical or diagnostic use',),
        required_next_evidence_refs=('ev-significance-observed',),
        resubmission_id='resub-ui-demo-2',
        note='证据不足时退回补证据，而不是硬给一档。',
        now=second_time,
        evidence=differential_evidence,
    )
    # A third decision under a DIFFERENT scope, so the grouped view shows two groups
    # rather than one, and one plainly refused request.
    record_dp_lane_decision(
        lane_store,
        record_id='ceiling-ui-demo-3',
        acceptance_snapshot_ref=(
            dp_acceptance_snapshot_ref(snapshot)['acceptance_snapshot_ref']['snapshot_id']),
        requested_lane='validated',
        decision_outcome='rejected',
        scope='normalization only / teaching use',
        integrity_ok=True,
        axes_auditable=True,
        residual_unordered=False,
        has_restrictions=False,
        lane_predicate_evidence_refs=('ev-normalization-declared',),
        note='该范围不在本次授权内，请求被拒绝。',
        now=third_time,
        evidence=normalization_evidence,
    )
    # A fourth decision that DEMONSTRATES the second freshness gate: its supporting
    # material is past the validity window its submitter declared, so the ceiling
    # falls to the return state and the page says which fact blocked it — instead of
    # the page merely asserting that such a rule exists.
    from bioaudit.dp01_models import EvidenceItem  # noqa: PLC0415

    expired_support = EvidenceItem(
        source_type='observed', verification='confirmed',
        value='batch-1 execution log',
        observed_at=_DEMO_NOW - timedelta(days=400),
        valid_for_seconds=90 * 24 * 3600,
        provenance='the execution log of an earlier batch',
        provenance_subject='PRJ-Alpha / AN-001 filtering step',
        supports='the declared filtering method was the executed one')
    record_dp_lane_decision(
        lane_store,
        record_id='ceiling-ui-demo-4',
        acceptance_snapshot_ref=(
            dp_acceptance_snapshot_ref(snapshot)['acceptance_snapshot_ref']['snapshot_id']),
        requested_lane='candidate',
        decision_outcome='return_for_evidence',
        scope=scope_a,
        integrity_ok=True,
        axes_auditable=True,
        residual_unordered=False,
        has_restrictions=False,
        evidence=(expired_support,),
        lane_predicate_evidence_refs=('ev-filtering-observed',),
        required_next_evidence_refs=('ev-filtering-current-run',),
        resubmission_id='resub-ui-demo-4',
        note='支撑证据已过期，因此封顶在返回态，并点名需要一份当前的执行记录。',
        now=third_time,
    )
    decisions = lane_store.all()
    report = render_dp_report(register, authority=snapshot, lane=decisions)
    # 渲染层自用：每个点实际命中的规则 ID（既有事实，账本里就有；见 _matched_rules）。
    report['_matched_rules'] = {
        name: tuple(getattr(register.get(name), 'matched_rule_ids', ()) or ())
        for name in ('filtering', 'normalization', 'differential_method',
                     'multiple_testing_correction', 'significance_threshold')}
    return report


# --------------------------------------------------------------------------
# small building blocks
# --------------------------------------------------------------------------

def _chip(text: str, tone: str = 'muted') -> str:
    return f'<span class="chip {tone}">{esc(text)}</span>'


def _state_chip(value: Any, table: dict[str, Any], tone: str | None = None) -> str:
    tone = tone or _TONE.get(value if isinstance(value, str) else '', 'muted')
    return f'<span class="chip {tone}">{L(value, table)}</span>'


def _lab(zh_text: str, orig: str) -> str:
    """固定词汇的成对标签：中文 + 原词（小字、必然成对，不需要折叠）。"""
    return f'<span class="lab-zh">{esc(zh_text)}</span><span class="lab-orig">{esc(orig)}</span>'


def _bilingual(en: Any, *, zh_override: str | None = None, tag: str = '') -> str:
    """中文（主）+ 英文原文（副）—— 凡出现英文散文的地方都成对呈现。

    `zh_override` 供调用方给出表里没有的措辞；`tag` 说明那段英文原文到底是什么。
    表里没有对照时，英文照录并声明"此处无对照"，而不是无声地中英混排。
    """
    if not isinstance(en, str) or not en.strip():
        return ''
    zh_text = zh_override if zh_override is not None else zh_bi.zh_for(en)
    if not zh_text:
        return (f'<div class="bi"><div class="bi-en">{esc(en)}</div>'
                f'<div class="bi-note">（此处暂无中文对照；英文原文照录）</div></div>')
    return (f'<div class="bi">'
            f'<div class="bi-zh">{to_html(zh_text)}</div>'
            f'<div class="bi-en"><span class="bi-tag">{esc(tag)}</span>{to_html(en)}</div>'
            f'</div>')


def _is_wordlike(value: str) -> bool:
    """单 token（无空格）——状态词/枚举值，而不是句子。"""
    return bool(value) and ' ' not in value.strip()


def _bi_inline(en: Any) -> str:
    """表格单元用的一行式对照：中文 + 原文（小字）。"""
    if not isinstance(en, str) or not en.strip():
        return '—'
    zh_text = zh_bi.zh_for(en)
    if not zh_text:
        return f'<span class="en">{esc(en)}</span>'
    return f'{esc(zh_text)}<span class="en">{esc(en)}</span>'


def _bi_value(value: Any) -> str:
    """数据值：值本身照录（不翻译）；词表里有对照的加中文角色说明。"""
    if not isinstance(value, str) or not value.strip():
        return '—'
    zh_text = zh_bi.zh_for(value)
    if zh_text and _is_wordlike(value):
        return f'{_lab(zh_text, value)}'
    return f'<code>{esc(value)}</code>'


def _rows(pairs: list[tuple[str, Any]], dim: bool = False) -> str:
    out = []
    for key, value in pairs:
        if value in (None, '', [], {}):
            continue
        label = zh.FIELD.get(key, key)
        cls = 'kv dim' if dim else 'kv'
        out.append(f'<div class="{cls}"><dt>{_lab(label, key)}</dt>'
                   f'<dd>{_render_value(key, value)}</dd></div>')
    return '\n'.join(out) or '<div class="empty">没有记录</div>'


def _render_value(key: str, value: Any) -> str:
    """按状态渲染：已知的枚举值给中文对照，自由文本照录。"""
    if isinstance(value, bool):
        return '是' if value else '否'
    table_by_key = {
        'judgment': zh.JUDGMENT,
        'verification': zh.VERIFICATION,
        'auditability': zh.AUDITABILITY,
        'completeness': zh.LEVEL,
        'scientific_evidence': zh.LEVEL,
        'role': zh.ROLE,
        'category': zh.FINDING_CATEGORY,
        'action': zh.ACTION,
        'decided_lane': zh.LANE,
        'requested_lane': zh.LANE,
        'lane_ceiling': zh.LANE,
        'decision_outcome': zh.LANE_OUTCOME,
        'version_currency': zh.LANE_CURRENCY,
        'freshness': zh.FRESHNESS,
        'source_type': zh.SOURCE,
        'verification': zh.VERIFICATION,
        'evidence_role': zh.EVIDENCE_ROLE,
        'kind': None,
    }
    if key in table_by_key and isinstance(value, str):
        zh_text = zh.label(value, table_by_key[key])
        return _lab(zh_text.split('（')[0], value)
    if key == 'state' and value in zh.CHANGE_STATE:
        return _lab(zh.CHANGE_STATE[value][0], value)
    if key == 'prior_meaning' and value in zh.JUDGMENT:
        return _lab(zh.JUDGMENT[value][0], value)
    if key == 'receipt_kind':
        return _lab(zh.RECEIPT_KIND.get(value, value), value)
    if key == 'change_classes':
        items = value if isinstance(value, list) else [value]
        return '　'.join(_lab(zh.CHANGE_CLASS.get(i, i), i) for i in items)
    if key == 'kind':
        return _lab(zh.GAP_KIND.get(value, value), value)
    if key == 'next_state':
        return _lab(zh.NEXT_STATE.get(value, value), value)
    if key == 'adjudication_authority':
        zh_map = {'governance authority': '治理方', 'scientific review authority': '科学复核方'}
        return _lab(zh_map.get(value, value), value)
    if key == 'declaration_basis':
        zh_map = {'declared_by_submitter': '由提交方自己声明（系统不判实质）'}
        return _lab(zh_map.get(value, value), value)
    if key == 'data_type':
        return _lab('bulk RNA-seq（批量转录组测序）', value)
    if key in _VERBATIM_KEYS:
        joined = '、'.join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
        return f'<code>{esc(joined)}</code>'
    if isinstance(value, str):
        # 自由文本：中文对照 + 英文原文；无对照的值按原样展示，
        # 不再出现「暂无中文对照」占位。
        zh_text = zh_bi.zh_for(value)
        if zh_text:
            return _bilingual(value)
        if _is_wordlike(value) or re.fullmatch(r"[\w.:/\-]+", value.strip() or ""):
            return f'<code>{esc(value)}</code>'
        return _bilingual(value)
    from collections.abc import Mapping as _MappingCls
    if isinstance(value, _MappingCls):
        # 报告的结构化值域（dp01_models._freeze）把映射冻结成 mappingproxy；
        # json.dumps 不能直接序列化，这里转成普通 dict 再展示。
        return f'<code>{esc(json.dumps(dict(value), ensure_ascii=False))}</code>'
    return f'<code>{esc(json.dumps(value, ensure_ascii=False))}</code>'


def _entries(items: list[Any], table: dict[str, Any] | None = None,
             skip: tuple[str, ...] = ()) -> str:
    if not items:
        return '<div class="empty">没有记录</div>'
    out = []
    for item in items:
        if isinstance(item, dict):
            bits = []
            for k, v in item.items():
                if k in skip or v in (None, '', [], {}):
                    continue
                bits.append(f'<span class="entry-label">{esc(zh.FIELD.get(k, k))}</span>'
                            f'{_render_value(k, v)}')
            out.append(f'<div class="entry">{"".join(bits)}</div>')
        else:
            out.append(f'<div class="entry">{_bilingual(item) if isinstance(item, str) else esc(item)}</div>')
    return '\n'.join(out)


def _field(key: str, value: Any, *, en_tag: str = '') -> str:
    """单列堆叠字段：标签一行、内容一行。

    用于内容较长的卡（状态含义卡）。不用 `_rows` 的固定标签列，否则中文对照 +
    英文原文会挤在同一行里互相压字（窄窗口尤其明显）。
    """
    if value in (None, '', [], {}):
        return ''
    label = f'<div class="field-label">{_lab(zh.FIELD.get(key, key), key)}</div>'
    if isinstance(value, bool):
        return f'<div class="field">{label}<div class="field-body">{"是" if value else "否"}</div></div>'
    if isinstance(value, (list, tuple)):
        value = '；'.join(str(v) for v in value)
    if key in _VERBATIM_KEYS:
        joined = '、'.join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
        return (f'<div class="field">{label}'
                f'<div class="field-body"><code>{esc(joined)}</code></div></div>')
    if isinstance(value, str):
        return (f'<div class="field">{label}'
                f'<div class="field-body">{_bilingual(value, tag=en_tag)}</div></div>')
    return (f'<div class="field">{label}'
            f'<div class="field-body"><code>{esc(json.dumps(value, ensure_ascii=False))}</code></div></div>')


def _lead(title: str, body: str, tone: str = '') -> str:
    # ``body`` may carry inline HTML (``<strong>``) from callers, and it may also
    # carry light markdown in text that comes from the report (backticks around a
    # field name). Render the markdown so the reader never sees the markers, while
    # leaving the caller's own markup intact.
    rendered = _MD_CODE.sub(r"<code>\1</code>", body)
    rendered = _MD_BOLD.sub(r"<strong>\1</strong>", rendered)
    return (f'<div class="callout {tone}"><h4>{esc(title)}</h4>'
            f'<p>{rendered}</p></div>')


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def _intro(report: dict[str, Any]) -> str:
    """Opening guide: what the page is, how to read it, and where to focus.

    Written for a reader who has never seen the system: the reading order is the
    account's own structure (object -> judgements -> meaning -> limits), and the
    "只看这五处" table names the five places worth checking first, because a
    21-block page without triage is unreadable.
    """
    return """
    <div class="card intro">
      <h2 class="intro-title">阅读指南</h2>
      <div class="intro-grid">
        <div>
          <h5>本页是什么</h5>
          <p>这一页把账户记录读成人话——<strong>不添油加醋，也不替你判断</strong>。</p>
        </div>
        <div>
          <h5>读者</h5>
          <p>给<strong>要对这个分析负责的人</strong>做检查用，不是对外交付件。</p>
        </div>
        <div>
          <h5>双语约定</h5>
          <p>状态词写成<strong>中文（原词）</strong>；括号里是系统原词，核对规格时用，
          完整对照在第 14 段。</p>
        </div>
      </div>
      <h4>先看这五处</h4>
      <table class="howto">
        <thead><tr><th>看哪</th><th>它在回答什么</th><th>你可以自问</th></tr></thead>
        <tbody>
          <tr><td><a href="#points">第 5 段 · 五步</a></td>
              <td>每一步判成了什么（全部结论都在这里）</td>
              <td>它判「可审计」的，凭什么？<br>判「科学上支持不足」的，你同意吗？</td></tr>
          <tr><td><a href="#axes">第 6 段 · 四条轴</a></td>
              <td>从四个方向看结果，不是总分</td>
              <td>它说「不能依赖」的方向，你认吗？</td></tr>
          <tr><td><a href="#stop">第 12 段 · 何时停</a></td>
              <td>什么情况必须停、停下归谁</td>
              <td>它点的责任方，找对人了吗？</td></tr>
          <tr><td><a href="#gaps">第 11 段 · 还没有的能力</a></td>
              <td>这片系统边界在哪（别高估它）</td>
              <td>有没有你<strong>以为它有、其实没有</strong>的东西？</td></tr>
          <tr><td><a href="#identity">第 3 段 · 审的是谁</a></td>
              <td>对象、以及「不含什么」是否写清</td>
              <td>和你手里的这次分析，是同一个吗？</td></tr>
        </tbody>
      </table>

      <details class="folding">
        <summary>其余各段的用途</summary>
        <ul>
          <li><strong>第 6 段 四条轴</strong>：不看总分，只看四个维度分别怎样（这个产品明确禁止把结果压成一个分数）。</li>
          <li><strong>第 8 段 能用在哪</strong>：「使用范围」和「能不能发布」是两件事，这段说明当前只到哪一步。</li>
          <li><strong>第 9 段 权威与角色</strong>：哪份记录才是「官方结论」，以及谁写的、谁看过、谁只是收件。</li>
          <li><strong>第 10 段 历史与变更</strong>：有没有人提过修改、提了什么、要谁拍板。</li>
          <li><strong>第 13 段 判据</strong>：凭什么这么判——依据的规则、档位阶梯与出处。</li>
          <li><strong>第 14 段 词表</strong>：页面上出现过的英文原词，都在这里配了中文解释（查词用）。</li>
        </ul>
      </details>

      <div class="callout warn">
        <h4>前提</h4>
        <p>这一页<strong>不是验收结论、不是批复、不是发布授权</strong>，它只是把账户记录摊开给你读。
        交付是否达标，需另按验收清单逐条核，不由本页判定。</p>
      </div>
    </div>
    """


def _page_header(report: dict[str, Any]) -> str:
    """常驻页头：对象 + 本次状态 + 英文原文折叠开关（不用滚就能看到）。"""
    ctx = report['context']
    points = (report.get('per_point') or {}).get('points') or []
    limited = [p for p in points if p.get('judgment') != 'auditable']
    att = (report.get('state_meanings') or {}).get('attested') or {}
    blocked = att.get('blocked') or {}
    blocked_on = 'no required decision' not in blocked.get('reason', '')
    findings = report.get('findings_and_limitations') or []
    has_integrity = any(f.get('category') == 'integrity_failure' for f in findings)
    return f"""
    <header class="top">
      <div class="top-line">
        <h1>审计记录</h1>
        <span class="badge sub">审计检查页（内部名）</span>
        <span class="badge">{esc(ctx.get('project_id'))} / {esc(ctx.get('analysis_id'))}</span>
        <span class="badge">{esc(ctx.get('data_type'))}</span>
        <span class="badge ok">非验收结论</span>
        <span class="badge {'warn' if limited else 'ok'}">
          {len(limited)} 个点未达「可审计」</span>
        <span class="badge {'bad' if has_integrity else 'ok'}">
          {'有完整性失败' if has_integrity else '无完整性失败'}</span>
        <span class="badge {'warn' if blocked_on else 'ok'}">
          {'有未解决阻断' if blocked_on else '无未解决阻断'}</span>
        <label class="env-toggle" title="默认显示英文原词；取消勾选后只看中文">
          <input type="checkbox" id="show-en" checked> 显示英文原词
        </label>
        <div class="depth-controls" role="group" aria-label="阅读深度：同一份数据的三种读法">
          <span class="depth-label">阅读深度</span>
          <button type="button" data-depth-to="1">只看结论</button>
          <button type="button" data-depth-to="2" class="active">看理由</button>
          <button type="button" data-depth-to="3">看证据链</button>
        </div>
      </div>
      <p class="top-note">判定、依据、缺口与边界按顺序列出。
      <strong>词以英文原词为准，中文是这一页的阅读对照，不是权威表述；对照不改变任何判定。</strong></p>
    </header>
    """


def _conclusion_card(report: dict[str, Any]) -> str:
    """结论与行动（全页唯一的结论区）：对象一行 → 结论表 → 边界一句。

    取代旧「主线总览 + 一屏摘要」两卡：同一份结论不换两套话说。结论列全称
    状态词；含义列用判词含义（唯一句）；「要补什么」只对有问题的点出现。
    """
    ctx = report.get('context') or {}
    points = (report.get('per_point') or {}).get('points') or []
    actions = report.get('next_actions') or []
    exclusions = (ctx.get('exclusions') or {}).get('declared_exclusions') or ''
    rows = []
    for p in points:
        judgment = p.get('judgment', '')
        cn, gloss = zh.JUDGMENT.get(judgment, (judgment, ''))
        tone = {'auditable': 'ok', 'scientifically_limited': 'warn',
                'conflicted': 'conflict', 'not_auditable': 'bad',
                'integrity_failed': 'bad', 'pending_attempt': 'pending'}.get(judgment, 'muted')
        point_actions = [a for a in actions if a.get('point') == p.get('name')]
        need = '；'.join(_bi_inline(a.get('reason')) for a in point_actions) \
            if point_actions else '—'
        rows.append(
            f'<tr><td>{esc(p.get("purpose"))}'
            f'<span class="fk">{esc(p.get("name"))}</span></td>'
            f'<td><span class="verdict-mini {tone}"><span class="dot"></span>'
            f'{esc(cn)}</span><span class="lab-orig">{esc(judgment)}</span></td>'
            f'<td>{esc(gloss)}</td>'
            f'<td>{need}</td></tr>')
    object_line = (f'<code>{esc(ctx.get("project_id"))}</code> / '
                   f'<code>{esc(ctx.get("analysis_id"))}</code>'
                   f' · 比较 <code>{esc(ctx.get("comparison"))}</code>'
                   f' · 类型 <code>{esc(ctx.get("data_type"))}</code>')
    return f"""
    <div class="card summary">
      <h2 class="intro-title">结论速览</h2>
      <p class="intro-lede">审计对象：{object_line}。
      每步判定按三道关核对：身份对得上、证据撑得住、边界说得清。</p>
      <table class="howto">
        <thead><tr><th>判断点</th><th>结论</th><th>含义</th><th>要补什么</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      {f'<p class="muted small" style="margin-top:10px">边界照录：{esc(exclusions)}。</p>' if exclusions else ''}
    </div>
    """


def _identity(report: dict[str, Any]) -> str:
    ctx = report['context']
    excl = ctx.get('exclusions') or {}
    declared = excl.get('declared_exclusions')
    return f"""
    <div class="card">
      <div class="kv-list">
        {_rows([('project_id', ctx.get('project_id')), ('analysis_id', ctx.get('analysis_id')),
                ('comparison', ctx.get('comparison')), ('n_samples', ctx.get('n_samples')),
                ('data_type', ctx.get('data_type')),
                ('audit_scope', ctx.get('audit_scope')), ('intended_use', ctx.get('intended_use'))])}
      </div>
      {_lead('不包含什么（exclusions）',
             esc(excl.get('note')) + (f'<br><strong>声明：{esc(declared)}</strong>' if declared else ''),
             'warn')}
      <details class="folding">
        <summary>输入与版本绑定</summary>
        <div class="kv-list dim">{_rows(list((ctx.get('input_version') or {}).items()), dim=True)}</div>
      </details>
    </div>
    """


def _risk_section(report: dict[str, Any]) -> str:
    """第 2 段：什么叫"审计一次"——风险在哪、过哪三道关、为什么这就够了。

    这一段是本页此前最缺的一层：读者看得懂每个名词，却不知道"为什么这样查就
    说明结论站得住"。这里把判据讲成人话，并指出本页**没有**用分数或排名代替判断。
    """
    ctx = report.get('context') or {}
    version = ctx.get('input_version') or {}
    binding = '、'.join(f'{k}={v}' for k, v in list(version.items())[:4]) or '（未记录）'
    return _section(
        'risk', '4', '审计方法与依据',
        '这一节讲审计按什么标准核',
        f"""
    <div class="card">
      <p class="lede">这一页核对的是结论的支撑：<strong>被什么撑着、撑得够不够、能用到哪儿</strong>。
      不核对算理，也不下"这篇分析对不对"的结论。</p>
      <details class="folding">
        <summary>审计机制：三道关、四类风险</summary>

      <h4>风险在哪里</h4>
      <ul>
        <li><strong>方法跟问题对不上</strong>：两组样本量差很大，却用了对样本量敏感的差异分析方法。</li>
        <li><strong>参数随手挑、理由没留下</strong>：阈值从 10 改成 5、p 值从 0.01 改成 0.05，
            材料里查不到"为什么这么定"。</li>
        <li><strong>两套说法打架</strong>：声明写 A 方法，实际观测到 B 方法。
            系统不替人选，冲突停下来交给人裁定。</li>
        <li><strong>材料换版本没留痕</strong>：规则、引擎、输入格式都绑定版本；
            版本一变，当时的结论就不一定还成立。</li>
      </ul>

      <h4>每一步过三道关</h4>
      <div class="grid-2">
        <div class="callout"><h4>第一关：身份对得上</h4>
          <p>分析编号与输入版本绑定（本次实测绑定：
             <code>{esc(binding)}</code>）都能对上。对不上，结论不成立。</p></div>
        <div class="callout"><h4>第二关：证据撑得住</h4>
          <p>每条判断背后要有证据，且要说清证据是什么、核过没有。目前证据只带三项：
             <strong>来源类型 / 是否核实 / 具体值</strong>——更细的来源链、时效、逐项权限<strong>还没有</strong>，
             这一点在下面如实列了出来，不假装有。</p></div>
      </div>
      <div class="callout"><h4>第三关：边界说得清</h4>
        <p>结论只在声明的项目、范围、用途里成立；本页<strong>不产出"总分"或排名</strong>。</p></div>

      <h4>为什么这三关够用</h4>
      <p>因为这三关各自可复核：<strong>对得上号——能当场比对；有材料撑着——能点开看是哪条；说得清边界——能照着自己那份分析套一遍。</strong>
         换句话说，读者不必相信这套系统，他只需要能自己核——这也是为什么本页处处标注"这页不是权威、不是结论"。</p>
      {_lead('三道关与五步的对应', '五步逐条见第 5 段。', 'info')}
      </details>
    </div>
    """, layer=2, hidden=True)


def _evidence_chain(point: dict[str, Any]) -> str:
    """What a reader needs to trace ONE decision's evidence back to its source.

    This is the deepest reading depth, so nothing here may be summarised away: for
    every item, the source and the subject it is about, the claim it was offered
    for, its role, when it was observed, how long its submitter said it holds, and
    what that means at the instant the report was rendered. A stale item keeps its
    place in the chain — it is not deleted, it is shown as no longer actively
    supporting the claim, because "the evidence aged out" and "there was never any
    evidence" are different facts.
    """
    items = point.get("evidence") or []
    if not items:
        return ('<details class="folding evidence-chain" data-min-depth="3">'
                '<summary>证据链（没有条目）</summary>'
                '<p class="muted small">这一点没有任何证据条目落账——'
                '这不是"证据齐全但没写"，而是<strong>没有可追溯的观测</strong>。</p>'
                '</details>')
    blocks = []
    for index, item in enumerate(items, start=1):
        role = L(item.get("evidence_role"), zh.EVIDENCE_ROLE)
        source = L(item.get("source_type"), zh.SOURCE)
        fresh = L(item.get("freshness"), zh.FRESHNESS)
        verification = L(item.get("verification"), zh.VERIFICATION)
        valid = item.get("valid_for_seconds")
        if valid:
            days = int(valid) // 86400
            valid_text = f"{valid} 秒（约 {days} 天）"
        else:
            valid_text = None
        blocks.append(f"""
        <div class="chain-item">
          <h5>证据 {index}：{esc(item.get('value'))}</h5>
          <div class="kv-list dim">
            {_rows([
                ('source_type', source),
                ('verification', verification),
                ('provenance', item.get('provenance')),
                ('provenance_subject', item.get('provenance_subject')),
                ('supports', item.get('supports')),
                ('evidence_role', role),
                ('observed_at', item.get('observed_at')),
                ('valid_for_seconds', valid_text),
                ('freshness', fresh),
            ], dim=True)}
            {f'<p class="chain-meaning muted small">{esc(item.get("freshness_meaning"))}</p>'
             if item.get('freshness_meaning') else ''}
          </div>
        </div>""")
    return (f'<details class="folding evidence-chain" data-min-depth="3">'
            f'<summary>证据链（{len(items)} 条）</summary>'
            f'{"".join(blocks)}</details>')


def _point_card(point: dict[str, Any]) -> str:
    """一个决策点的卡片：异常点（非可审计）全展开，正常点压成结论行。

    正常点（可审计）的细则（依据/限制/下一步）收进折叠——结论本身已说明问题与
    证据对得上；异常点保留完整布局，因为读者要在这里逐条核缺什么。
    """
    judgment = point.get('judgment', '')
    tone = _TONE.get(judgment, 'muted')
    g = zh.gloss(judgment, zh.JUDGMENT)
    header = f"""
      <header class="point-head">
        <div class="point-title">
          <h3>{esc(point.get('purpose'))}<span class="muted small">（{esc(point.get('name'))}）</span></h3>
        </div>
        <div class="verdict {tone}">
          <span class="dot"></span>
          <span class="verdict-text">{L(judgment, zh.JUDGMENT)}</span>
        </div>
      </header>
      {f'<p class="verdict-gloss">{esc(g)}</p>' if g else ''}
      <div class="kv-list">
        {_rows([('declared_method', point.get('declared_method'))])}
      </div>"""
    detail = f"""
      <div class="grid-2">
        <section>
          <h4>依据</h4>
          {_entries(point.get('evidence') or [], skip=())}
          <h4>证据缺口</h4>
          {_entries(point.get('evidence_gaps') or [])}
        </section>
        <section>
          <h4>系统声明的限制</h4>
          {_entries(point.get('limitations') or [])}
          <h4>发现</h4>
          {_entries(point.get('findings') or [])}
        </section>
      </div>
      <section>
        <h4>下一步</h4>
        {_entries(point.get('next_actions') or [])}
      </section>"""
    tech = f"""
      <details class="folding">
        <summary>技术信息（scope）</summary>
        <div class="kv-list dim">{_rows([('scope', point.get('scope'))], dim=True)}</div>
      </details>"""
    if judgment == 'auditable':
        return f"""
    <article class="point" id="point-{esc(point.get('name'))}">
      {header}
      <details class="folding" data-min-depth="2">
        <summary>这一步的依据、限制与下一步</summary>
        {detail}
      </details>
      {_evidence_chain(point)}
      {tech}
    </article>
    """
    return f"""
    <article class="point" id="point-{esc(point.get('name'))}">
      {header}
      <p class="explain">{_bi_inline(point.get('diagnostic_explanation'))}</p>
      <div class="explain muted small" style="margin-top:4px">来源：规则库匹配说明。</div>
      {detail}
      {_evidence_chain(point)}
      {tech}
    </article>
    """


def _points_section(report: dict[str, Any]) -> str:
    five = report['five_decision_register']
    overview = '\n'.join(
        f'<li class="toc-point"><span class="dot {_TONE.get(p.get("judgment"), "muted")}"></span>'
        f'<a href="#point-{esc(p.get("name"))}">{esc(p.get("purpose"))}</a>'
        f'<span>{L(p.get("judgment"), zh.JUDGMENT)}</span></li>'
        for p in five['points'])
    return f"""
    <div class="card">
      <p class="lede">这五个判断点<strong>各自独立</strong>：每个点有自己的问题、证据、限制和停止后果；
      它们不能互相替代、也不能合并成一个结论。</p>
      <ul class="toc-points">{overview}</ul>
    </div>
    {''.join(_point_card(p) for p in report['per_point']['points'])}
    """


def _axis_card(key: str, axis: dict[str, Any]) -> str:
    zh_name, en_name, meaning = _AXIS.get(key, (key, key, ''))
    rows = [
        ('完整度', 'completeness', axis.get('completeness'), _COMPLETENESS_WIDTH),
        ('科学证据', 'scientific_evidence', axis.get('scientific_evidence'), _EVIDENCE_WIDTH),
        ('可评估性', 'auditability', axis.get('auditability'), None),
    ]
    bars = '\n'.join(
        f'<div class="barrow"><span class="barlabel">{esc(zh_name_)}'
        f'<span class="dim small">（{esc(eng)}）</span></span>'
        f'<span class="bar {_TONE.get(value, "muted")}">'
        f'<i style="width:{width if width is not None else (100 if value == "auditable" else 30)}%"></i></span>'
        f'<span class="barval">{L(value, zh.AUDITABILITY if eng == "auditability" else zh.LEVEL)}</span></div>'
        for zh_name_, eng, value, width in rows)
    conditions = axis.get('conditions') or []
    return f"""
    <article class="axis" id="axis-{esc(key)}">
      <header class="axis-head">
        <div>
          <h3>{esc(zh_name)}<span class="muted small">（{en_name}）</span></h3>
          <p class="muted small">{esc(meaning)}</p>
        </div>
        <div class="axis-flags">
          {_state_chip('auditable' if axis.get('evaluable') else 'not_auditable',
                       zh.AUDITABILITY)}
          {_chip('存在残留、且无排序含义', 'warn') if axis.get('residual_unordered') else ''}
        </div>
      </header>
      <div class="bars">{bars}</div>
      <p class="explain">{esc(axis.get('detail'))}</p>
      {'<div class="kv-list">' + _rows([('conditions', '、'.join(conditions))]) + '</div>' if conditions else ''}
      <details class="folding">
        <summary>这一轴明令禁止的解释（{len(axis.get('prohibited_interpretations') or [])} 条）</summary>
        <ul>{''.join(f'<li>{_bilingual(_clean(p))}</li>' for p in (axis.get('prohibited_interpretations') or []))}</ul>
      </details>
    </article>
    """


def _axes_section(report: dict[str, Any]) -> str:
    axes = report['per_point']['result_axes']
    cards = '\n'.join(_axis_card(k, axes[k]) for k in
                      ('inference_type', 'explanation_depth', 'scope', 'validation') if k in axes)
    return _lead('这不是评分表', esc(_NOT_SCORE_NOTE), 'warn') + f'<div class="axes">{cards}</div>'


def _states_section(report: dict[str, Any]) -> str:
    section = report.get("state_meanings") or {}
    attested = section.get("attested") or {}
    cards = []
    for word, entry in attested.items():
        tone = {"reviewed": "accent", "blocked": "warn", "limited-use": "warn",
                "integrity-failed": "bad"}.get(word, "muted")
        cn, _ = zh.JUDGMENT.get(word, ('', ''))
        cn = _STATE_ZH.get(word, cn)
        reason = entry.get('reason') or ''
        triggered = not reason.startswith(('NO ', 'no '))
        extra = ''
        if entry.get('named'):
            names = '；'.join(f'{n.get("actor")} → {n.get("target")}（{n.get("scope")}）'
                              for n in entry['named'])
            extra += _field('named', names)
        if entry.get('points'):
            stale = '；'.join(
                f'{p.get("point")}: ' + ', '.join(
                    f'{k} 记录为 {v.get("recorded")} / 当前 {v.get("current")}'
                    for k, v in (p.get('differing') or {}).items())
                for p in entry['points'])
            extra += _field('points', stale)
        if entry.get('named_boundary'):
            extra += _field('named_boundary',
                            '、'.join(f'{k}={v}' for k, v in entry['named_boundary'].items()))
        if entry.get('planned_capabilities'):
            extra += _field('planned_capabilities',
                            '；'.join(f'{c.get("capability")} — {c.get("status")}'
                                      for c in entry['planned_capabilities']))
        if entry.get('blockers'):
            extra += _field('blockers',
                            '；'.join(f'{b.get("kind")}（释放方：{b.get("released_by")}）'
                                      for b in entry['blockers']))
        if entry.get('ceiling'):
            extra += _field('ceiling', entry['ceiling'])
        # 堆叠式字段（单列），避免中英对照在窄列里互相压字
        fields = ''.join(_field(k, entry.get(k)) for k in
                         ('may_be_relied_on', 'must_not_be_inferred', 'next_requirement'))
        cards.append(f"""
        <article class="card state-card">
          <header class="point-head">
            <h3 class="state-title">{esc(cn or word)}
              <span class="lab-orig">{esc(word)}</span></h3>
            <div class="verdict {tone}"><span class="dot"></span>
              <span class="verdict-text">{'成立' if triggered else '未触发'}</span></div>
          </header>
          {_bilingual(reason, tag='何意')}
          <div class="fields">{fields}</div>
          {extra}
        </article>""")
    unexpressed = '\n'.join(
        f'<li><span class="cap">{esc(e.get("state"))}</span>'
        f'<div class="muted">{_bilingual(e.get("reason"), tag="何意")}'
        f'<div class="field-label" style="margin-top:6px">要什么</div>'
        f'{_bilingual(e.get("what_would_change_it"), tag="要什么")}</div></li>'
        for e in (section.get('unexpressed_states') or []))
    return f"""
    <div class="callout">
      <h4>每个词按本页手里的记录判断</h4>
      <p>每一条写明四件事：<strong>何意 / 能依赖 / 别推断 / 要什么</strong>。</p>
    </div>
    <details class="folding">
      <summary>{len(attested)} 个状态词的含义</summary>
      <div class="state-list">{''.join(cards)}</div>
    </details>
    <details class="folding" open>
      <summary>本系统<strong>无法表达</strong>的状态含义
      （{len(section.get('unexpressed_states') or [])} 个）</summary>
      <ul class="not-yet">{unexpressed}</ul>
    </details>
    """


def _lane_card(decision: dict[str, Any]) -> str:
    """一条 lane 决定记录：申请/实给/上限/理由/限制/还需什么证据。"""
    rows = _rows([
        ('decided_lane', decision.get('decided_lane')),
        ('requested_lane', decision.get('requested_lane')),
        ('decision_outcome', decision.get('decision_outcome')),
        ('lane_ceiling', decision.get('lane_ceiling')),
        ('version_currency', decision.get('version_currency')),
        ('recorded_at', decision.get('recorded_at')),
        ('acceptance_snapshot_ref', decision.get('acceptance_snapshot_ref')),
        ('record_id', decision.get('record_id')),
    ])
    extra = []
    # 每条决定都绑在「声明的范围」上（C-04 §5）：档位离开范围就没有意义，所以
    # 范围单独占一行。用 lane 专用标签键，**不要**复用通用 'scope' 键——那会
    # 把其它小节的「所属位置」标签一起改掉（复核发现 F10）。
    if decision.get('scope'):
        extra.append(
            f'<div class="field"><div class="field-label">'
            f'{_lab(zh.FIELD.get("lane_scope", "声明的范围"), "scope")}</div>'
            f'<div class="field-body">{_bilingual(decision.get("scope"))}</div></div>')
    reason = decision.get('ceiling_reason')
    if reason:
        extra.append(_field('ceiling_reason', reason))
    restrictions = decision.get('prohibited_uses') or []
    if restrictions:
        extra.append(_field('prohibited_uses', restrictions))
    needed = decision.get('required_next_evidence_refs') or []
    if needed:
        extra.append(_field('required_next_evidence_refs', needed))
    refs = decision.get('lane_predicate_evidence_refs') or []
    if refs:
        extra.append(_field('lane_predicate_evidence_refs', refs))
    # 「是否构成授权」是本页对记录的**读法**，不是记录自称：明确说明来源。
    authorising = '是' if decision.get('authorising') else '否'
    extra.append(f'<div class="field"><div class="field-label">'
                 f'{_lab("这条记录是否构成授权", "authorising")}</div>'
                 f'<div class="field-body">{authorising}'
                 f'<span class="lab-orig">（本页从记录读出的结论，不是记录自称）</span>'
                 f'</div></div>')
    return f'<div class="card"><dl class="kv-list">{rows}</dl>{"".join(extra)}</div>'


def _lane_groups(section: dict[str, Any]) -> str:
    """按声明的范围分组：一个范围一组，组内列出各条决定。

    规格 C-04 §5 把每条决定都绑在「声明的范围」上，所以分组维度用 scope——
    不是为了好看，而是因为**离开范围的档位没有意义**。
    """
    groups = section.get('decisions_by_scope') or []
    if not groups:
        return ''
    blocks = []
    for group in groups:
        scope = group.get('scope') or '(未声明范围)'
        lanes = '、'.join(L(lane, zh.LANE) for lane in (group.get('decided_lanes') or []))
        header = (f'<div class="entry-label">{esc(scope)}</div>'
                  f'<div class="entry">共 {group.get("decision_count")} 条决定；'
                  f'实给档位：{lanes or "—"}'
                  f'{"；最近一条：" + esc(group["latest_recorded_at"]) if group.get("latest_recorded_at") else ""}'
                  f'</div>')
        blocks.append(f'<div class="entry">{header}</div>')
    return ('<details class="fold" open><summary>按范围分组：'
            f'{len(groups)} 个范围 / {section.get("decision_count")} 条决定</summary>'
            + ''.join(blocks) + '</details>')


def _lane_block(report: dict[str, Any]) -> str:
    """使用上限（lane）段：让「能用在哪」与「能不能发布」的分离**可被观察**。

    在这段出现之前，页面只能说一句「本系统没有发布或 lane 机制」——一句否认，
    背后没有可看的对象。P-03 行144 要的是"既有 lane 上限的观察"，这一段就是它。
    """
    section = report.get('lane_and_release')
    if not section:
        return f"""
      {_lead('使用上限（lane）：本次没有 lane 决定记录',
             '本页<strong>没有</strong>任何 lane 决定可呈现，因此不对本次审计给出任何使用上限；'
             '这不等于默认给了一档上限，也不等于可以照常用。',
             'warn')}
    """
    decisions = section.get('decisions') or []
    body = ''.join(_lane_card(d) for d in decisions) or \
        '<div class="empty">没有 lane 决定记录</div>'
    return f"""
      {_lead('使用上限的含义', _bilingual(section.get('statement') or ''), 'info')}
      {_lane_groups(section)}
      <details class="fold" open>
        <summary>使用上限决定记录：共 {len(decisions)} 条</summary>
        {body}
      </details>
      {_lead('各档上限的含义', _bilingual(section.get('unreachable_lanes_note') or ''), 'info')}
      {_lead('记录时间的含义', _bilingual(section.get('recorded_at_note') or ''), 'warn')}
      {_lead('记录不等于发布', _bilingual(section.get('not_release_note') or ''), 'warn')}
    """


def _use_section(report: dict[str, Any]) -> str:
    return f"""
    <div class="card">
      <p class="lede">{_bilingual(report['bounded_use'])}</p>
      {_lead('它不等于「已批准发布」',
             '系统能记录使用上限决定，并把「能用在哪」与「能不能发布」分开呈现——'
             '但<strong>没有任何发布机制</strong>：不会自动晋升、不回滚、不部署，'
             '记录本身也不授予发布权。',
             'warn')}
      {_lane_block(report)}
    </div>
    """


def _who_section(report: dict[str, Any]) -> str:
    review = report['review_and_admin']
    auth = review.get('authority_attribution') or {}
    roles = review.get('roles') or []
    roles_html = '\n'.join(
        f'<div class="entry"><span class="fk">角色</span>{_state_chip(r.get("role"), zh.ROLE)}'
        f'<span class="fk">是谁</span><code>{esc(r.get("actor"))}</code>'
        f'<span class="fk">对什么</span>{esc(r.get("target"))}'
        f'<span class="fk">范围</span>{esc(r.get("scope"))}'
        + (f'<span class="fk">收件类型</span>{esc(zh.RECEIPT_KIND.get(r.get("receipt_kind"), r.get("receipt_kind")))}'
           if r.get('receipt_kind') else '')
        + '</div>' for r in roles) or f'<div class="empty">{_bilingual(review.get("roles_statement"))}</div>'
    cats = review.get('role_categories') or {}
    recorded = '、'.join(zh.ROLE.get(c, (c,))[0] for c in (cats.get('recorded') or [])) or '无'
    not_recorded = '、'.join(zh.ROLE.get(c, (c,))[0] for c in (cats.get('not_recorded') or [])) or '无'
    return f"""
    <div class="card">
      <h4>权威容器</h4>
      <div class="kv-list">
        {_rows([('container_kind', auth.get('container_kind')),
                ('snapshot_id', auth.get('snapshot_id')),
                ('embedded_result_count', auth.get('embedded_result_count')),
                ('authority_basis', (auth.get('authority_basis') or {}).get('basis'))])}
      </div>
      <p class="explain">{_bilingual(auth.get('minimum_meaning'))}</p>
      <div class="chips">
        {_chip('本报告不是那个容器', 'ok')}{_chip('本报告不是第二份结果', 'ok')}
        {_chip('权威依据已确认' if (auth.get('authority_basis') or {}).get('confirmed')
               else '权威依据未确认',
               'ok' if (auth.get('authority_basis') or {}).get('confirmed') else 'bad')}
      </div>
      <h4>角色记录</h4>
      <p class="muted small">{_bilingual(review.get('statement'))}</p>
      <div class="chips">{_chip(f'已有记录：{recorded}', 'ok')}{_chip(f'尚无记录：{not_recorded}', 'pending')}</div>
      {roles_html}
    </div>
    """


def _history_section(report: dict[str, Any]) -> str:
    history = report['history_and_change']
    proposed = history.get('proposed_change')
    if not proposed:
        return f'<div class="card"><div class="empty">{_bilingual(history.get("history_statement"))}</div></div>'
    diffs = '\n'.join(
        f'<div class="diff"><span class="fk">改的是哪一项</span>{esc(d.get("path"))}'
        f'<span class="before">{esc(d.get("before"))}</span><span class="arrow">→</span>'
        f'<span class="after">{esc(d.get("after"))}</span></div>'
        for d in (proposed.get('differences') or []))
    return f"""
    <div class="card">
      <p class="lede">{_bilingual(history.get('statement'))}</p>
      <div class="kv-list">
        {_rows([('current_account', history.get('current_account')),
                ('prior_account', proposed.get('prior_account')),
                ('prior_meaning', proposed.get('prior_meaning')),
                ('state', proposed.get('state')),
                ('change_classes', proposed.get('change_classes')),
                ('reason', proposed.get('reason')),
                ('submitter', proposed.get('submitter')),
                ('requires_adjudication', proposed.get('requires_adjudication')),
                ('adjudication_authority', proposed.get('adjudication_authority')),
                ('declaration_basis', proposed.get('declaration_basis')),
                ('state_scope', proposed.get('state_scope'))])}
      </div>
      <h4>差异</h4>
      {diffs}
    </div>
    """


def _gaps_section(report: dict[str, Any]) -> str:
    five = report['five_decision_register']
    items = '\n'.join(
        f'<li><span class="cap">{_bilingual(e.get("capability"))}</span>'
        f'<span class="muted">{esc(e.get("status"))}</span></li>'
        for e in (five.get('not_yet_available') or []))
    return (_lead('边界声明', _bilingual(five.get('not_yet_available_note')), 'warn')
            + f'<ul class="not-yet">{items}</ul>')


def _stop_section(report: dict[str, Any]) -> str:
    findings = report['findings_and_limitations']
    f_html = '\n'.join(
        (f'<div class="callout {"bad" if f.get("category") == "integrity_failure" else "warn"}">'
         f'<h4>{esc(zh.FINDING_CATEGORY.get(f.get("category"), (f.get("category"), ""))[0])}'
         f'<span class="lab-orig">{esc(f.get("category"))}</span></h4>'
         + _bilingual(f.get('consequence'), tag='后果') + '</div>')
        for f in findings) or _lead('没有需要停下的发现', '本次账户没有触发停止条件。', 'ok')
    actions = '\n'.join(
        f'<div class="entry"><span class="entry-label">属于哪一步</span>'
        f'{_lab(zh.FIELD.get("point", "属于哪一步"), a.get("point"))}'
        f'<span class="entry-label">动作</span>'
        f'{_lab(zh.ACTION.get(a.get("action"), (a.get("action"), ""))[0], a.get("action") or "")}'
        f'<span class="entry-label">针对什么</span>{esc(a.get("target"))}'
        f'<span class="entry-label">理由</span>{_bi_inline(a.get("reason"))}</div>'
        for a in report['next_actions']) or '<div class="empty">没有待办动作</div>'
    return f"""
    <div class="card">
      <h4>最终指令</h4>
      {_bilingual(report['final_user_instruction'], tag='报告 §7.9 最终指令')}
      <h4>停止条件</h4>
      {f_html}
      <h4>下一步</h4>
      {actions}
    </div>
    """


def _glossary() -> str:
    rows = []
    for table, head in ((zh.JUDGMENT, '五个判断点的结论'), (zh.VERIFICATION, '证据的核实状态'),
                        (zh.LEVEL, '轴上的等级词'), (zh.CHANGE_STATE, '变更状态')):
        for key, (cn, gloss) in table.items():
            rows.append(f'<tr><td><strong>{esc(cn)}</strong><br><code>{esc(key)}</code></td>'
                        f'<td>{esc(gloss)}</td></tr>')
    for key, (cn, gloss) in zh.ROLE.items():
        rows.append(f'<tr><td><strong>{esc(cn)}</strong><br><code>{esc(key)}</code></td>'
                    f'<td>{esc(gloss)}</td></tr>')
    for key, (cn, gloss) in zh.ACTION.items():
        rows.append(f'<tr><td><strong>{esc(cn)}</strong><br><code>{esc(key)}</code></td>'
                    f'<td>{esc(gloss)}</td></tr>')
    return f'<details class="folding"><summary>英文原词对照表</summary><table class="glossary"><thead><tr><th>词</th><th>什么意思</th></tr></thead><tbody>{"".join(rows)}</tbody></table></details>'


def _depth_note() -> str:
    """一句常驻提示：摘要不等于材料。

    常驻、不可折叠：选到最浅那一档的人，仍然必须被告知判定与限制在下面，
    并且这一页不是验收结论。把这句话藏起来，这一页就会从"帮人看明白"
    变成"帮人过度引用"。
    """
    return """
    <div class="callout info" id="depth-note" hidden>
      <h4>当前仅显示结论</h4>
      <p>这一页的<strong>判定、限制和证据在下面第二档与第三档</strong>；
      只看结论不等于看过材料。另外：<strong>本页不是验收结论，也不能代替原始材料。</strong></p>
    </div>
    """


def _section(id_: str, number: str, title: str, subtitle: str, body: str,
             *, layer: int = 1, hidden: bool = False) -> str:
    """One numbered section.

    ``layer`` is the SHALLOWEST reading depth that shows this section (1 = always
    shown). ``hidden`` means the section is closed in the static page, so the
    declared default depth is observable from the bytes rather than from running
    the switch: a reader with script disabled still gets the default reading, and
    the verifier can check the layering without trusting the script.
    """
    layer_attr = f' data-layer="{layer}"' if layer != 1 else ''
    hidden_attr = ' hidden' if hidden else ''
    return f"""
    <section class="section" id="{id_}"{layer_attr}{hidden_attr}>
      <header class="section-head">
        <span class="section-no">{esc(number)}</span>
        <div><h2>{esc(title)}</h2><p class="muted small">{esc(subtitle)}</p></div>
      </header>
      {body}
    </section>
    """


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

def _act(id_: str, number: str, title: str, subtitle: str, body: str,
         *, layer: int = 1, hidden: bool = False) -> str:
    """一幕：把若干小节包成一段完整的叙事，并给读者一句"这一幕在讲什么"。"""
    layer_attr = f' data-layer="{layer}"' if layer != 1 else ''
    hidden_attr = ' hidden' if hidden else ''
    return f"""
    <section class="act" id="{id_}"{layer_attr}{hidden_attr}>
      <header class="act-head">
        <span class="act-no">第{number}幕</span>
        <div><h2>{esc(title)}</h2><p class="muted small">{subtitle}</p></div>
      </header>
      {body}
    </section>
    """


def _matched_rules(report: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """每个决策点实际命中的规则 ID。

    值来自 `build_demo_report` 里那次真实 register 调用的返回值（挂在
    `report['_matched_rules']` 上）。之所以走这条边路而不往报告里加字段：
    `matched_rule_ids` 是**既有事实**（账本里就存着），但把它加进报告的对外
    形状属产品层改动；面板只是渲染层，用渲染层自己的数据即可，不动报告契约。
    """
    raw = report.get('_matched_rules') or {}
    return {str(k): tuple(str(x) for x in (v or ())) for k, v in raw.items()}


# --------------------------------------------------------------------------
# 导航注册表（单一事实源）：导航、章节 id、阅读深度三样都由它驱动。
# 每项 = (id, 编号, 中文名, data-layer, 是否子条目)。
# 曾经导航是手写列表、章节是另一处手写构造，两处漂移过（出现指向 hidden
# 元素的锚点、跳转系统性错位）。现在 nav 由它生成，JS 跳转读它的 layer，
# verify_report 的第 18 项把它和页面实际 id 集合对齐——一份定义，四处使用。
# --------------------------------------------------------------------------
_NAV: list[tuple[str, str, str, int, bool]] = [
    ("intro-card", "1", "阅读指南", 1, False),
    ("chain", "2", "链条总览（主线）", 1, False),
    ("act-one", "幕一", "审计对象与判定", 1, False),
    ("identity", "3", "审计对象与范围", 2, False),
    ("risk", "4", "审计方法与依据", 2, False),
    ("points", "5", "五个决策点的判定", 2, False),
    ("point-filtering", "", "低表达过滤", 2, True),
    ("point-normalization", "", "归一化", 2, True),
    ("point-differential_method", "", "差异分析方法", 2, True),
    ("point-multiple_testing_correction", "", "多重检验校正", 2, True),
    ("point-significance_threshold", "", "显著性/效应量阈值", 2, True),
    ("axes", "6", "四条轴（无总分）", 2, False),
    ("act-two", "幕二", "结论的含义与边界", 1, False),
    ("states", "7", "状态词的含义", 1, False),
    ("use", "8", "使用范围与上限", 2, False),
    ("who", "9", "权威与角色", 3, False),
    ("history", "10", "历史与变更", 3, False),
    ("act-three", "幕三", "边界与下一步", 1, False),
    ("gaps", "11", "尚未实现的能力", 1, False),
    ("stop", "12", "停止条件与下一步", 1, False),
    ("criteria", "13", "判定依据（规则与出处）", 1, False),
    ("glossary", "14", "词表（英文原词对照）", 1, False),
]


def _nav_html() -> str:
    """由 _NAV 生成导航（每条带 data-goto 与 data-layer，供 JS 联动）。"""
    out = []
    for nav_id, number, label, layer, child in _NAV:
        cls = ' class="nav-child"' if child else ''
        core = f"<b>{esc(number)}</b>{esc(label)}" if number else f"<b>·</b>{esc(label)}"
        out.append(f'<a href="#{esc(nav_id)}" data-goto="{esc(nav_id)}" '
                   f'data-layer="{layer}"{cls}>{core}</a>')
    return '\n'.join(out)


def render_html(report: dict[str, Any]) -> str:
    ctx = report['context']
    meta = report['_report_meta']
    matched = _matched_rules(report)
    nav_html = _nav_html()

    chain_section = _section(
        'chain', '2', '链条总览：这一份分析从哪到哪',
        '五步的顺序、各自的判定与证据摘要——先看主线，再看细节。不含总评。',
        chain_view.chain_overview(report), layer=1)

    # NOTE: the act subtitles are built as plain strings and passed in, because an
    # f-string that reuses the outer quote character needs Python 3.12 and this
    # project declares >=3.10 (pyproject target-version py310).
    #
    # Reading depths (DP-UI-LAYERED-DESIGN): depth 1 = conclusion only, depth 2 =
    # reasons (the declared default), depth 3 = the evidence chain. `layer` is the
    # shallowest depth that shows a block; the default closes everything deeper than
    # 2, and the two blocks that stop over-reading (missing capabilities, where the
    # account must stop) are NEVER layered — they stay visible at every depth.
    act_one = (
        _section('identity', '3', '审计对象与范围',
                 '这份账户对应哪一次分析、给什么用、不含什么',
                 _identity(report), layer=2, hidden=True)
        + _risk_section(report)
        + _section('points', '5', '五个决策点的判定',
                   '每一步：要回答什么、怎么定的、证据够不够、卡在哪',
                   _points_section(report), layer=2, hidden=True)
        + _section('axes', '6', '四条轴（无总分）',
                   '同一个结果从四个方向分别看：能不能验证、解释到哪层、适用多宽、科学上够不够',
                   _axes_section(report), layer=2, hidden=True))
    act_two = (
        _section('states', '7', '状态词的含义',
                 '每词说明：何意 / 能依赖 / 别推断 / 要什么',
                 _states_section(report))
        + _section('use', '8', '使用范围与上限',
                   '使用边界与能用到的档位——与能不能发布是两回事',
                   _use_section(report), layer=2, hidden=True)
        + _section('who', '9', '权威与角色',
                   '唯一权威容器与角色记录：哪个结果是权威、谁写的、谁看过',
                   _who_section(report), layer=3, hidden=True)
        + _section('history', '10', '历史与变更',
                   '新判断是新的有界判断，不是悄悄改写旧结论',
                   _history_section(report), layer=3, hidden=True))
    act_three = (
        _section('gaps', '11', '尚未实现的能力',
                 '边界声明：以下能力没有',
                 _gaps_section(report))
        + _section('stop', '12', '停止条件与下一步',
                   '停止条件与责任归属', _stop_section(report)))
    # DP-CRITERIA-PANEL-ACKNOWLEDGEMENT：判据面板常驻（不随阅读深度折叠，
    # 2026-09-15 起默认折叠成 <details>，内容仍在页内可查可验）。
    # 它回答"凭什么这么判"，属于标准本身，不属于某一档阅读深度。
    criteria_section = _section(
        'criteria', '13', '判定依据（规则与出处）',
        '判定依据：规则、档位阶梯与出处；不给分数、不做总评',
        '<details class="folding"><summary>判据面板：按哪条规则 / 档位阶梯 / 出处 / 接入状态</summary>'
        + criteria.render_criteria_panel(report, matched) + '</details>', )
    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-depth="2">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>审计检查页 · {esc(ctx.get('project_id'))} / {esc(ctx.get('analysis_id'))}</title>
<style>
:root {{
  --bg:#080d12; --panel:#0e1620; --panel2:#121d29; --line:#1e2c3a;
  --ink:#e7f0f6; --muted:#7f95a6; --dim:#5d7183; --accent:#43d6d6; --accent2:#2aa7b8;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --conflict:#c084fc; --pending:#60a5fa;
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
  --sans:"Inter","Segoe UI",system-ui,"PingFang SC","Microsoft YaHei",sans-serif;
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; padding:0; background:var(--bg); color:var(--ink); font-family:var(--sans); }}
body {{ font-size:16.5px; line-height:1.62; }}
a {{ color:var(--accent); text-decoration:none; }}
h1,h2,h3,h4,h5 {{ margin:0 0 .3em; line-height:1.28; font-weight:650; }}
h1 {{ font-size:22px; }}
h2 {{ font-size:21px; }}
h3 {{ font-size:18px; }}
h4 {{ font-size:14px; text-transform:none; letter-spacing:.03em; color:var(--muted);
      margin-top:1.1em; font-weight:600; }}
h5 {{ font-size:14px; color:var(--accent); letter-spacing:.02em; }}
code {{ font-family:var(--mono); font-size:.86em; color:var(--dim); }}
.muted {{ color:var(--muted); }}
.dim {{ color:var(--dim); }}
.small {{ font-size:13.5px; }}
.top {{ position:sticky; top:0; z-index:10; background:rgba(8,13,18,.95); backdrop-filter:blur(8px);
        border-bottom:1px solid var(--line); padding:12px 26px; display:flex; gap:14px;
        align-items:baseline; flex-wrap:wrap; }}
.top .badge {{ font-family:var(--mono); font-size:11px; padding:2px 9px; border:1px solid var(--line);
               border-radius:999px; color:var(--muted); }}
.wrap {{ display:grid; grid-template-columns:226px minmax(0,1fr); gap:22px; padding:18px 24px 30px;
         max-width:1320px; margin:0 auto; }}
nav {{ position:sticky; top:70px; align-self:start; display:flex; flex-direction:column; gap:1px; }}
nav a {{ display:flex; gap:10px; align-items:baseline; padding:7px 10px; border-radius:7px;
         color:var(--ink); border-left:2px solid transparent; font-size:13.5px; }}
nav a:hover {{ background:var(--panel); border-left-color:var(--accent); }}
nav b {{ font-family:var(--mono); font-size:11px; color:var(--accent2); min-width:12px; }}
main {{ min-width:0; display:flex; flex-direction:column; gap:26px; }}
.section {{ scroll-margin-top:76px; }}
.act {{ display:flex; flex-direction:column; gap:22px; margin:6px 0 26px; }}
.act-head {{ display:flex; gap:14px; align-items:flex-start; padding:14px 16px;
             background:linear-gradient(90deg,#0f1e26,transparent); border-left:3px solid var(--accent);
             border-radius:0 10px 10px 0; }}
.act-no {{ font-size:13px; color:#04282b; background:var(--accent); border-radius:6px;
           padding:3px 8px; white-space:nowrap; margin-top:3px; font-weight:700; }}
.act-head h2 {{ font-size:22px; }}
.card.story {{ border-color:var(--accent); box-shadow:0 0 0 1px rgba(67,214,214,.15) inset; }}
.section-head {{ display:flex; gap:14px; align-items:flex-start; padding-bottom:10px;
                 border-bottom:1px solid var(--line); margin-bottom:16px; }}
.section-no {{ font-family:var(--mono); font-size:12px; color:var(--accent);
               border:1px solid var(--accent2); border-radius:6px; padding:3px 7px; margin-top:4px; }}
.card,.point,.axis {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
                      padding:16px 18px; margin-bottom:12px; }}
.card.intro {{ border-color:var(--accent2); background:linear-gradient(180deg,#0f1e26,#0e1620); }}
.intro-title {{ font-size:19px; margin-bottom:10px; }}
.intro-lede {{ font-size:15.5px; margin:0 0 14px; color:#d6e6ef; }}
table.howto {{ width:100%; border-collapse:collapse; font-size:14px; margin:6px 0 4px; }}
table.howto th, table.howto td {{ text-align:left; padding:9px 12px;
  border-bottom:1px solid var(--line); vertical-align:top; }}
table.howto th {{ color:var(--muted); font-weight:600; font-size:12px; }}
table.howto td:first-child {{ white-space:nowrap; width:190px; font-weight:600; }}
table.howto td:nth-child(3) {{ color:#c3d5e0; }}
.intro-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px 26px; }}
.intro-grid p {{ margin:4px 0 0; font-size:14px; }}
@media (max-width:900px) {{ .intro-grid {{ grid-template-columns:1fr; }} }}
.lede {{ font-size:15.5px; }}
.explain {{ font-size:14px; color:#c3d5e0; }}
.kv-list {{ display:grid; gap:6px; margin:8px 0 4px; }}
.kv {{ display:grid; grid-template-columns:auto minmax(0,1fr); gap:12px; align-items:baseline; }}
.kv dt {{ color:var(--muted); font-size:14px; margin:0; white-space:nowrap; }}
.kv dd {{ margin:0; word-break:break-word; }}
.kv.dim dt, .kv.dim dd {{ color:var(--dim); font-size:13.5px; }}
@media (max-width:620px) {{ .kv {{ grid-template-columns:1fr; gap:2px; }} }}
/* 长内容卡：标签一行、内容一行（中英对照不再挤进窄列） */
.fields {{ display:grid; gap:10px; margin:10px 0 4px; }}
.field {{ border-top:1px dashed var(--line); padding-top:8px; }}
.field-label {{ font-size:13.5px; color:var(--muted); margin-bottom:2px; }}
.field-body {{ font-size:16px; }}
.state-list {{ display:flex; flex-direction:column; gap:12px; }}
.state-title {{ margin:0; }}
.not-yet li {{ display:block; }}
.grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
@media (max-width:900px) {{ .wrap {{ grid-template-columns:1fr; }}
  nav {{ position:static; flex-direction:row; flex-wrap:wrap; }} .grid-2 {{ grid-template-columns:1fr; }} }}
.entry {{ border-top:1px dashed var(--line); padding:8px 0; font-size:14px; }}
.entry .fk {{ font-size:11.5px; color:var(--dim); margin:0 6px 0 12px; }}
.entry .fk:first-child {{ margin-left:0; }}
.gloss {{ font-size:12.5px; color:var(--muted); margin-top:2px; }}
.empty {{ color:var(--muted); font-size:14px; padding:6px 0; }}
.chip {{ display:inline-block; font-size:12px; padding:2px 9px; border-radius:999px;
         border:1px solid var(--line); color:var(--muted); margin:2px 4px 2px 0; }}
.chip.ok {{ color:#052e1b; background:var(--ok); border-color:var(--ok); }}
.chip.warn {{ color:#3a2600; background:var(--warn); border-color:var(--warn); }}
.chip.bad {{ color:#3b0a0a; background:var(--bad); border-color:var(--bad); }}
.chip.pending {{ color:#04213f; background:var(--pending); border-color:var(--pending); }}
.chip.conflict {{ color:#2a0a3a; background:var(--conflict); border-color:var(--conflict); }}
.chip.accent {{ color:#04282b; background:var(--accent); border-color:var(--accent); }}
.point-head {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; }}
.point-title h3 {{ margin-bottom:0; }}
.verdict {{ display:flex; align-items:center; gap:9px; border:1px solid var(--line);
            border-radius:10px; padding:8px 14px; white-space:nowrap; }}
.verdict-text {{ font-size:16px; font-weight:650; }}
.verdict .dot,.dot {{ width:10px; height:10px; border-radius:50%; background:var(--muted);
                      display:inline-block; flex:none; }}
.verdict.ok {{ border-color:var(--ok); }} .verdict.ok .dot,.dot.ok {{ background:var(--ok); }}
.verdict.warn {{ border-color:var(--warn); }} .verdict.warn .dot,.dot.warn {{ background:var(--warn); }}
.verdict.bad,.verdict.conflict {{ border-color:var(--conflict); }}
.verdict.bad .dot,.dot.bad {{ background:var(--bad); }}
.verdict.conflict .dot,.dot.conflict {{ background:var(--conflict); }}
.verdict.pending .dot,.dot.pending {{ background:var(--pending); }}
.verdict-gloss {{ margin:2px 0 10px; color:#c3d5e0; font-size:14px; }}
.callout {{ background:var(--panel2); border:1px solid var(--line); border-left:3px solid var(--accent2);
            border-radius:8px; padding:12px 15px; margin:12px 0; }}
.callout.warn {{ border-left-color:var(--warn); }}
.callout.bad {{ border-left-color:var(--bad); }}
.callout.ok {{ border-left-color:var(--ok); }}
.callout h4 {{ margin-top:0; font-size:14px; color:var(--ink); }}
.callout p {{ margin:6px 0 0; }}
.chips {{ margin:6px 0 10px; }}
.toc-points {{ list-style:none; margin:0; padding:0; display:grid; gap:2px; }}
.toc-point {{ display:grid; grid-template-columns:16px 1fr auto; gap:10px; align-items:center;
              padding:8px 0; border-top:1px dashed var(--line); font-size:15px; }}
.toc-point a {{ color:var(--ink); }}
.axes {{ display:flex; flex-direction:column; gap:14px; }}
.axis-head {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; }}
.axis-flags {{ text-align:right; }}
.bars {{ display:flex; flex-direction:column; gap:9px; margin:14px 0 6px; }}
.barrow {{ display:grid; grid-template-columns:150px 1fr 150px; gap:14px; align-items:center; }}
.barlabel {{ font-size:13.5px; color:var(--ink); }}
.bar {{ height:9px; background:#0a1218; border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--accent2); }}
.bar.ok i {{ background:var(--ok); }} .bar.warn i {{ background:var(--warn); }}
.bar.bad i {{ background:var(--bad); }}
.barval {{ font-size:13px; color:var(--ink); }}
details.folding {{ margin-top:14px; }}
details summary {{ cursor:pointer; font-size:12.5px; color:var(--muted); }}
details summary::before {{
  /* 折叠指示：自绘 chevron（不依赖系统三角/图片）。合上指向右（可展开），
     展开后旋转 180° 指向下（可收起）。 */
  content:''; display:inline-block; width:.92em; height:.92em; margin-right:.55em;
  vertical-align:-.12em;
  background:var(--accent2);
  --chev: polygon(79% 30%, 50% 59%, 21% 30%, 12% 43%, 50% 82%, 88% 43%);
  clip-path:var(--chev);
  -webkit-clip-path:var(--chev);
  transition:transform .15s ease, background-color .15s ease;
}}
details[open] > summary::before {{ transform:rotate(180deg); background:var(--accent); }}
details[open] > summary {{ color:var(--ink); }}
details ul {{ margin:8px 0 0; padding-left:18px; font-size:13.5px; color:#c3d5e0; }}
.not-yet {{ list-style:none; padding:0; margin:0; display:grid; gap:1px; }}
.not-yet li {{ display:grid; grid-template-columns:300px 1fr; gap:16px; padding:10px 0;
               border-top:1px dashed var(--line); font-size:14px; }}
.not-yet .cap {{ font-weight:600; }}
@media (max-width:760px) {{ .not-yet li {{ grid-template-columns:1fr; gap:2px; }}
  .barrow {{ grid-template-columns:1fr; }} .kv {{ grid-template-columns:1fr; }} }}
.diff {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }}
.diff .before {{ color:var(--muted); text-decoration:line-through; }}
.arrow {{ color:var(--accent); }}
table.glossary {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
table.glossary th, table.glossary td {{ text-align:left; padding:8px 10px;
  border-bottom:1px solid var(--line); vertical-align:top; }}
table.glossary th {{ color:var(--muted); font-weight:600; font-size:12px; }}
table.glossary td:first-child {{ white-space:nowrap; width:210px; }}
footer {{ color:var(--muted); font-size:12.5px; padding:10px 0 30px; border-top:1px solid var(--line); }}
.future-note {{ display:flex; gap:10px; align-items:flex-start; }}

/* ── 双语对照：中文为主、英文原文为副 ── */
.bi {{ margin:5px 0 8px; }}
.bi-zh {{ color:var(--ink); }}
/* 重点词：亮色 + 加粗 + 淡高亮底 —— 深色底上单靠加粗跳不出来 */
.bi-zh strong, .field-body strong, .entry strong, .lede strong, .bi-en strong,
.summary-actions strong, table.howto strong {{
  color:#8ff0f0; font-weight:700;
  background:linear-gradient(180deg, rgba(67,214,214,.16), rgba(67,214,214,.06));
  padding:0 3px; border-radius:3px;
}}
.bi-en {{ color:var(--dim); font-size:12px; line-height:1.5; margin-top:3px;
          padding-left:9px; border-left:2px solid var(--line); opacity:.75; }}
.bi-tag {{ font-size:10px; color:var(--accent2); border:1px solid var(--line);
           border-radius:4px; padding:0 4px; margin-right:6px; white-space:nowrap;
           opacity:.8; }}
.bi-note {{ color:var(--warn); font-size:12.5px; margin-top:2px; }}
.lab-zh {{ color:var(--ink); }}
.lab-orig {{ color:var(--dim); font-family:var(--mono); font-size:10.5px; margin-left:6px;
             opacity:.7; }}
.fk {{ color:var(--dim); font-family:var(--mono); font-size:10.5px; margin-left:6px;
       opacity:.65; }}
.entry-label {{ color:var(--muted); font-size:12px; margin:0 6px 0 12px; opacity:.85; }}
.entry-label:first-child {{ margin-left:0; }}
.val-zh {{ margin-right:6px; }}
/* 折叠英文原文时，只留中文（避免中英混排的割裂感） */
body.hide-original .bi-en,
body.hide-original .lab-orig,
body.hide-original .en,
body.hide-original .fk,
body.hide-original code {{ display:none; }}
body.hide-original .bi-note {{ display:none; }}
.top-line {{ display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }}
.top h1 {{ font-size:19px; }}
.top-note {{ margin:8px 0 0; color:var(--muted); font-size:13px; }}
.badge.ok {{ color:var(--ok); border-color:var(--ok); }}
.badge.warn {{ color:var(--warn); border-color:var(--warn); }}
.badge.bad {{ color:var(--bad); border-color:var(--bad); }}
.env-toggle {{ margin-left:auto; font-size:13px; color:var(--muted); cursor:pointer;
               user-select:none; border:1px solid var(--line); border-radius:8px; padding:4px 10px; }}
.card.summary {{ border-color:var(--accent2); }}
.verdict-mini {{ display:inline-flex; align-items:center; gap:7px; font-weight:600; }}
.verdict-mini.ok {{ color:var(--ok); }} .verdict-mini.warn {{ color:var(--warn); }}
.verdict-mini.bad {{ color:var(--bad); }} .verdict-mini.conflict {{ color:var(--conflict); }}
.verdict-mini.pending {{ color:var(--pending); }}
.summary-actions {{ margin:6px 0 0; padding-left:18px; font-size:14px; }}
/* ---- reading depths (DP-UI-LAYERED-DESIGN) --------------------------
   A section's `data-layer` is the shallowest depth that shows it. Depth 1 is the
   conclusion, 2 the reasons (default), 3 the evidence chain. The two blocks that
   stop over-reading (the capabilities still missing, and where the account must
   stop) carry no layer, so no depth can fold them away. */
html[data-depth="1"] .section[data-layer="2"],
html[data-depth="1"] .section[data-layer="3"],
html[data-depth="2"] .section[data-layer="3"],
html[data-depth="1"] [data-min-depth="3"],
html[data-depth="2"] [data-min-depth="3"] {{ display:none; }}
.chain-item {{ margin:10px 0; padding:9px 11px; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; }}
.chain-item h5 {{ margin:0 0 4px; }}
.chain-meaning {{ margin:6px 0 0; }}
.depth-controls {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap;
  margin-left:auto; }}
.depth-label {{ font-size:12px; color:var(--dim); letter-spacing:.03em; }}
.depth-controls button {{ font:inherit; font-size:12px; cursor:pointer;
  background:var(--panel2); color:var(--muted); border:1px solid var(--line);
  border-radius:7px; padding:4px 10px; line-height:1.3; }}
.depth-controls button:hover {{ color:var(--ink); border-color:var(--accent2); }}
.depth-controls button.active {{ color:var(--bg); background:var(--accent);
  border-color:var(--accent); font-weight:600; }}
@media (max-width:620px) {{ .depth-controls {{ margin-left:0; width:100%; }} }}
main section[id], main article[id], main details[id] {{ scroll-margin-top: 16px; }}
.chv-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
.chv-table th, .chv-table td {{ text-align:left; border-bottom:1px solid var(--line);
  padding:6px 8px; vertical-align:top; }}
.chv-table th {{ color:var(--dim); font-weight:600; }}
.chv-judge {{ background:var(--panel2); padding:1px 8px; border-radius:8px;
  color:var(--ink); }}
.chv-none {{ color:#c0392b; }} .chv-gap {{ color:#c0392b; font-size:12px; }}
.chv-state {{ margin:8px 0 4px; }}
.chv-note {{ color:var(--dim); font-size:12px; }}
.chv-coh {{ margin-top:10px; border-top:1px dashed var(--line); padding-top:8px; }}
.chv-coh-head {{ font-weight:600; margin:2px 0 6px; }}
.chv-coh-list {{ list-style:none; margin:0; padding:0; }}
.chv-coh-row {{ display:flex; flex-wrap:wrap; gap:6px; align-items:baseline;
                padding:3px 0; border-bottom:1px solid var(--line); font-size:12.5px; }}
.chv-coh-tag {{ padding:1px 8px; border-radius:8px; font-weight:600; white-space:nowrap; }}
.chv-coh-coherent {{ background:#e1efe6; color:#1e6b3a; }}
.chv-coh-needs_review {{ background:#fdf3d7; color:#8a6100; }}
.chv-coh-incoherent {{ background:#fbe3e3; color:#a02626; }}
.chv-coh-label {{ font-weight:600; }}
.chv-coh-reason {{ color:var(--fg); }}
.chv-coh-act {{ color:#8a6100; font-size:12px; }}
.chv-coh-note {{ color:var(--dim); font-size:12px; margin:6px 0 0; }}
.chv-coh-missing {{ color:var(--dim); font-size:12px; margin:6px 0 0; }}
</style>
</head>
<body>
{_page_header(report)}
<div class="wrap">
  <nav>{nav_html}</nav>
  <main>
    <section class="section" id="intro-card">{_conclusion_card(report)}{_depth_note()}{_intro(report)}</section>

    {chain_section}

    {_act('act-one', '幕一', '审计对象与判定', '发生了什么 → 怎么理解 → 能干什么；这一幕是前两件事。', act_one)}

    {_act('act-two', '幕二', '结论的含义与边界', '这一幕讲每个结论是什么意思、能信到什么程度。', act_two)}

    {_act('act-three', '幕三', '边界与下一步', '这一幕是行动：还没有什么、何时必须停、下一步谁做什么。', act_three)}

    {criteria_section}

    {_section('glossary', '14', '词表（英文原词对照）', '页面上出现的英文原词，都在这里配了中文对照', _glossary())}
    <footer>
      这一页只是把 <code>render_dp_report</code> 的真实输出渲染成人能读的样子：
      它不创造状态、不产生分数、不构成验收／发布／权威。
      <br><strong>它是观察工具，不是对外交付件。</strong>
      谁能用、在什么场景用、以什么形态交付，仍属未决的产品决定（P-03 §15）；
      这一页不承担对外报告的职责。
      报告自带的自述标记：explanatory={esc(meta.get('explanatory'))} ·
      not_authority={esc(meta.get('not_authority'))} · not_acceptance={esc(meta.get('not_acceptance'))}。
      <br>以后若要给外部展示的正式报告，属另行授权的产品决定，本页不承担该职责。
    </footer>
  </main>
</div>
<script>
(function () {{
  // 英文原文默认显示；取消勾选只留中文（选择记在本地，刷新后保持）。
  var box = document.getElementById('show-en');
  if (!box) return;
  var KEY = 'dp-report-show-original';
  try {{
    var saved = window.localStorage.getItem(KEY);
    if (saved === '0') {{ box.checked = false; }}
  }} catch (e) {{ /* localStorage 不可用时就按默认显示 */ }}
  function apply() {{
    document.body.classList.toggle('hide-original', !box.checked);
    try {{ window.localStorage.setItem(KEY, box.checked ? '1' : '0'); }} catch (e) {{}}
  }}
  box.addEventListener('change', apply);
  apply();
}})();

(function () {{
  // 阅读深度 × 导航：一套模型。切换深度只改 <html data-depth>，段落节点一个不动；
  // 导航点击则「先按需提升深度，再平滑滚动」——否则跳向 display:none 的目标会
  // 停在上一可见处（系统性错位的旧病根）。
  var KEY = 'dp-report-depth';
  var buttons = [].slice.call(document.querySelectorAll('[data-depth-to]'));
  function setDepth(depth, focusButton) {{
    var value = String(depth);
    document.documentElement.setAttribute('data-depth', value);
    buttons.forEach(function (b) {{
      var on = b.getAttribute('data-depth-to') === value;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    }});
    var note = document.getElementById('depth-note');
    if (note) {{ note.hidden = value !== '1'; }}
    if (focusButton) {{ focusButton.focus(); }}
    try {{ window.localStorage.setItem(KEY, value); }} catch (e) {{}}
  }}
  var saved = null;
  try {{ saved = window.localStorage.getItem(KEY); }} catch (e) {{ saved = null; }}
  setDepth(saved === '1' || saved === '2' || saved === '3' ? saved : '2', null);
  buttons.forEach(function (b) {{
    b.addEventListener('click', function () {{
      setDepth(b.getAttribute('data-depth-to'), b);
    }});
  }});

  // 目标可见层 = 自身与所有祖先中最大的 data-layer；跳到它之前先把深度提到这一层。
  function layerNeeded(el) {{
    var need = 1, node = el;
    while (node && node !== document) {{
      var L = node.getAttribute && node.getAttribute('data-layer');
      if (L) {{ var n = +L; if (n > need) need = n; }}
      node = node.parentElement;
    }}
    return need;
  }}
  window.__dpGoTo = function (id) {{
    var el = document.getElementById(id);
    if (!el) return false;
    var cur = +(document.documentElement.getAttribute('data-depth') || '2');
    var need = layerNeeded(el);
    if (need > cur) setDepth(need, null);
    // 用 auto（非 smooth）：smooth 动画会被下一次跳转打断（连续点击时后一个目标
    // 停在原地——实测踩过）。可靠性优先：每击必达。
    el.scrollIntoView({{ behavior: 'auto', block: 'start' }});
    return true;
  }};
  var links = [].slice.call(document.querySelectorAll('a[data-goto]'));
  links.forEach(function (a) {{
    a.addEventListener('click', function (ev) {{
      if (window.__dpGoTo(a.getAttribute('data-goto'))) ev.preventDefault();
    }});
  }});
}})();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description='Render the report UI (Chinese-first).')
    parser.add_argument('--out', default='ui/report.html')
    parser.add_argument('--report-json', default=None)
    args = parser.parse_args()

    report = build_demo_report()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding='utf-8', newline='\n')
    if args.report_json:
        pathlib.Path(args.report_json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print(f'wrote {out} ({out.stat().st_size} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
