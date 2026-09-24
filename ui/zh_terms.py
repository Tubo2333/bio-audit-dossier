"""Chinese-first reading layer for the report UI (DP-UI-REPORT).

The system's own data model speaks English (field names and state words). This
module holds the EN -> ZH reading layer so a human inspector can read the
generated page without knowing that vocabulary, while the original word is kept
in small type next to it (so the page can still be reconciled against the spec).

Reading rules:
- every state word gets 中文（原词）: Chinese first, original word in parentheses
- a short gloss says what the state MEANS, not just a literal translation
- nothing here invents a new state: terms not listed fall through to the original
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# 状态词：中文 + 人话解释
# --------------------------------------------------------------------------

JUDGMENT: dict[str, tuple[str, str]] = {
    'auditable': ('可审计', '这一步的问题与证据对得上，结论站得住，可以依赖。'),
    'scientifically_limited': ('科学上支持不足', '材料还撑不起这个结论——不是算错了，是还不到下结论的时候。'),
    'conflicted': ('冲突未解', '观察到两套互相打架的说法或做法；系统不挑赢家，交给人裁定。'),
    'not_auditable': ('无法评估', '本次无法对这个点做出判断（缺必要事实或权限）。'),
    'integrity_failed': ('完整性／权威失败', '必备的身份、来源、版本、权限等事实无法核实，受影响材料已被隔离。'),
    'pending_attempt': ('待验证的提交', '收到了一次尝试或提交，还没验证完，不能当结论用。'),
    'historical_only': ('仅历史', '这是旧账户，只作历史记录保留，永远不当作当前结论。'),
}

# 证据角色（P-02 §4.1 的 role 列举：support / limitation / conflict /
# historical / external / planned）。角色决定"这条证据过期会不会压上限"。
EVIDENCE_ROLE: dict[str, tuple[str, str]] = {
    'support': ('支持', '为所支持的主张积极作证'),
    'limitation': ('限制', '说明这个判断在哪儿不成立'),
    'conflict': ('冲突', '与别的材料对不上'),
    'historical': ('历史背景', '只说明过去发生过什么'),
    'external': ('外部观测', '来自这套账本之外的观测'),
    'planned': ('计划中的要求', '以后才生效的要求'),
}

SOURCE: dict[str, tuple[str, str]] = {
    'observed': ('观测', '系统或提交方实际看到的'),
    'declared': ('声明', '提交方自己写的，未经核实'),
    'referenced': ('引用', '来自引用的材料'),
    'computed': ('计算得出', '由其他数据算出'),
}

FRESHNESS: dict[str, tuple[str, str]] = {
    'current': ('当前有效', '还在提交方声明的有效期内'),
    'stale': ('已过期', '超出有效期；只停它所支持的主张，不整点作废'),
}

VERIFICATION: dict[str, tuple[str, str]] = {
    'confirmed': ('已核实', '这条材料的来源与内容都核对过。'),
    'unverified': ('未核实', '材料在那儿，但没人核实过，因此不算证据。'),
    'failed': ('核实失败', '核对没通过。'),
    'conflicted': ('互相冲突', '两条材料对同一件事说法不一致。'),
}

AUDITABILITY: dict[str, tuple[str, str]] = {
    'auditable': ('可评估', '这个账户可以被检验。注意：不等于每一步都被支持。'),
    'not_auditable': ('不可评估', '无法检验。'),
}

LEVEL: dict[str, tuple[str, str]] = {
    'complete': ('完整', '该说的都说了。'),
    'partial': ('部分', '只覆盖了一部分。'),
    'missing': ('缺失', '没有。'),
    'sufficient': ('充分', '够支持当前结论。'),
    'insufficient': ('不足', '不够支持当前结论。'),
}

CHANGE_STATE: dict[str, tuple[str, str]] = {
    'awaiting_human_decision': ('等待人来裁定', '这项变更要人或升级机制拍板，裁定前不得当作已定。'),
    'realized': ('已实现', '变更的后出侧就是当前账户，也就是这项变更已经落地。'),
    'proposed': ('仅提出', '只是有人提出，当前账户仍是先前那一侧。'),
}

ROLE: dict[str, tuple[str, str]] = {
    'authored': ('撰写', '谁写的这份东西。'),
    'reviewed': ('复核', '谁独立看过。只是「有人看过」的记录，不等于批准。'),
    'receipted': ('行政收件', '谁只是收下或登记了。不等于实质审查。'),
    'adjudicated': ('裁定', '谁做的决定。'),
}

RECEIPT_KIND: dict[str, str] = {
    'administrative': '行政收件',
    'point_in_time': '某一时点的收件',
}

ACTION: dict[str, tuple[str, str]] = {
    'request_evidence': ('要求补证据', '当前证据不足，需要补材料。'),
    'request_review': ('要求复核', '需要人来复核。'),
    'submit_correction': ('提交修正', '需要提交一次修正。'),
    'stop': ('停下', '受影响的主张必须停下。'),
}

NEXT_STATE: dict[str, str] = {
    'evidence_requested': '待补证据',
    'review_requested': '待复核',
    'correction_submitted': '已提交修正',
    'blocked': '已阻断',
}

CHANGE_CLASS: dict[str, str] = {
    'project_or_analysis_context': '项目或分析背景变了',
    'input_or_version': '输入或版本变了',
    'evidence_or_provenance': '证据或来源变了',
    'decision_interpretation': '结论解读变了',
    'limitation_or_conflict': '限制或冲突变了',
    'intended_use': '预期用途变了',
    'review_or_authority_boundary': '复核或权威边界变了',
}

GAP_KIND: dict[str, str] = {
    'declared_observed_conflict': '声明与观测冲突',
    'filtering_support': '过滤步骤的支持度',
    'evidence_observations': '证据观测',
    'missing_declaration': '缺声明',
}

FINDING_CATEGORY: dict[str, tuple[str, str]] = {
    'scientific_insufficiency': ('科学上支持不足', '证据不够支持所声明的主张。'),
    'integrity_failure': ('完整性／权威失败', '必备事实无法核实，受影响材料已隔离。'),
}

# 字段名 / 技术标签：只说清「这是什么」
FIELD: dict[str, str] = {
    'project_id': '项目编号',
    'analysis_id': '分析编号',
    'comparison': '比较对象',
    'data_type': '数据类型',
    'audit_scope': '本次审计范围',
    'intended_use': '预期用途',
    'exclusions': '不包含什么',
    'input_version': '输入与版本绑定',
    'ruleset_version': '规则集版本',
    'ontology_version': '本体版本',
    'engine_version': '引擎版本',
    'input_format_version': '输入格式版本',
    'adapter_version': '适配器版本',
    'purpose': '这一步在管什么',
    'scope': '所属位置',
    'judgment': '结论',
    'declared_method': '声明用的方法',
    'diagnostic_explanation': '系统的解释',
    'evidence_gaps': '证据缺口',
    'limitations': '限制',
    'evidence': '证据',
    'findings': '发现',
    'next_actions': '下一步动作',
    'source_type': '来源类型',
    'provenance': '来源',
    'provenance_subject': '来源主体',
    'supports': '为哪条主张作证',
    'evidence_role': '证据角色',
    'observed_at': '观测时点',
    'valid_for_seconds': '声明的有效期',
    'freshness': '时效（派生）',
    'verification': '核实状态',
    'value': '内容',
    'kind': '种类',
    'state': '状态',
    'detail': '说明',
    'target': '针对什么',
    'action': '动作',
    'reason': '理由',
    'point': '属于哪一步',
    'next_state': '之后的状态',
    'may_be_relied_on': '能依赖',
    'must_not_be_inferred': '别推断',
    'next_requirement': '要什么',
    'named_boundary': '具名边界',
    'planned_capabilities': '计划中的能力',
    'blockers': '阻断项',
    'ceiling': '上限',
    'freshness_meaning': '时效含义',
    'source': '来源',
    'completeness': '完整度',
    'scientific_evidence': '科学证据',
    'auditability': '可评估性',
    'evaluable': '能不能评估',
    'conditions': '要留意的点',
    'container_kind': '权威容器类型',
    'snapshot_id': '容器编号',
    'embedded_result_count': '内嵌结果个数',
    'stage_package_role': '输入材料的角色',
    'authority_basis': '权威依据（各项检查是否确认）',
    'basis': '依据',
    'unconfirmed_domains': '未确认的检查项',
    'minimum_meaning': '容器的最小含义',
    'version_binding': '版本绑定',
    'actor': '是谁',
    'role': '角色',
    'receipt_kind': '收件类型',
    'statement': '声明',
    'current_account': '当前账户',
    'prior_account': '先前账户',
    'prior_meaning': '先前账户的含义',
    'earlier_accounts': '更早的账户',
    'proposed_change': '待裁定变更',
    'from_account': '变更前账户',
    'to_account': '变更后账户',
    'change_classes': '变更类别',
    'differences': '差异',
    'submitter': '提交人',
    'requires_adjudication': '是否需要裁定',
    'adjudication_authority': '由哪类权威裁定',
    'declaration_basis': '声明来源',
    'escalation_required': '是否需要升级',
    'state_scope': '状态适用范围',
    'capability': '能力',
    'status': '现状',
    'history_supplied': '是否提供了历史',
    'history_statement': '历史／变更的含义',
    'other_changes_note': '另外还有变更',
    'counts': '计数',
    'recorded': '已有记录',
    'not_recorded': '尚无记录',
    'path': '改的是哪一项',
    'before': '原来',
    'after': '改成',
    'note': '说明',
    'category': '类别',
    'consequence': '后果',
    'residual_unordered': '存在残留、且无排序含义',
    'prohibited_interpretations': '明令禁止的解释',
    'decided_lane': '实际给到的使用上限',
    'requested_lane': '申请的使用上限',
    'lane_ceiling': '事实支持的最高上限',
    'ceiling_reason': '为什么不能再高',
    'decision_outcome': '结论',
    'version_currency': '谓词是否仍对应当前版本',
    'prohibited_uses': '明令禁止的用途',
    'required_next_evidence_refs': '还需要什么证据',
    'lane_predicate_evidence_refs': '谓词引用的证据',
    'authorising': '这条记录是否构成授权',
    'lane_predicate_note': '这些是谓词引用的证据编号',
    # NOTE: a lane-specific key, NOT 'scope'. Re-declaring 'scope' here shadowed the
    # generic '所属位置' label that every other section uses (ruff F601; review F10).
    'lane_scope': '声明的范围（档位只在范围内有意义）',
    'recorded_at': '这一条写进账本的时间（UTC）',
}

# C-04 的既有 lane 域：四档，含义是「使用／发布的封顶」，不是科学结论。
LANE: dict[str, tuple[str, str]] = {
    'research-draft': ('研究草稿（非授权返回态）', '只作研究参考，不构成任何发布或使用授权。'),
    'candidate': ('候选（受限）', '可在声明范围内被考虑，但带明示限制，不是批准。'),
    'validated': ('已验证', '需经授权的跨轴定序才够得着；本系统当前不可达。'),
    'production': ('生产', '需经授权的跨轴定序才够得着；本系统当前不可达。'),
}

LANE_OUTCOME: dict[str, tuple[str, str]] = {
    'accepted': ('接受', '在声明范围与限制内，所给上限站得住；不等于结果普遍为真。'),
    'rejected': ('拒绝', '所请求的判定在声明范围内站不住，不产生任何活跃授权。'),
    'return_for_evidence': ('退回补证据', '固定返回非授权返回态，并点名还需要哪些证据。'),
    'withdrawn': ('撤回', '记录不再具活跃效力；不改写科学结果、不产生替换措辞。'),
}

LANE_CURRENCY: dict[str, tuple[str, str]] = {
    'current': ('对应现版本', '记录时的版本绑定与当前运行时一致。'),
    'stale': ('已过期', '记录时的绑定与当前运行时不同，因此不得据此晋升。'),
}


def label(value: Any, table: dict[str, Any] | None = None) -> str:
    """中文（原词）；没有中文时原样返回。"""
    if not isinstance(value, str) or not value:
        return '—'
    if table and value in table:
        entry = table[value]
        zh = entry[0] if isinstance(entry, tuple) else entry
        return f'{zh}（{value}）'
    return value


def gloss(value: Any, table: dict[str, Any] | None = None) -> str:
    """该状态词的人话解释（有则给，无则空）。"""
    if table and isinstance(value, str) and value in table:
        entry = table[value]
        if isinstance(entry, tuple) and len(entry) > 1:
            return entry[1]
    return ''


def field_name(key: Any) -> str:
    if isinstance(key, str) and key in FIELD:
        return f'{FIELD[key]}（{key}）'
    return str(key)
