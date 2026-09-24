"""Bilingual reading layer for the report UI (DP-UI-REPORT): 中文 + 英文原文.

Policy (human decision, recorded in DP-UI-REPORT-INSTRUCTION.md):
  * Wherever the page shows English prose, it shows the English ORIGINAL
    (unedited) together with a Chinese rendering underneath.
  * The Chinese is the RENDERING LAYER's translation: not system output, not
    reviewed by anyone. The page says so plainly, and the English original is
    always kept so a reader can check the translation.
  * DATA VALUES are not translated (method names, ids, versions, units):
    deseq2, padj_0.05, PRJ-Alpha. They get a role label instead, because
    translating a name would be wrong rather than helpful.

SENTENCES  — full statements emitted by the report (exact match).
SENTENCE_PATTERNS — the few dynamic ones (regex with variables).
PHRASES    — short labels and value role labels.

zh_for() returns '' when nothing is known; callers then show English alone, and
coverage() lets the verifier assert the size of any remaining gap.
"""

from __future__ import annotations

import re
from typing import Any

SENTENCES: dict[str, str] = {
    "What can be relied on: each point's judgment as established by the "
    "authoritative account (not this explanatory report). What cannot be claimed: "
    "this report is not a second result authority, and no total score, ranking, "
    "lane, or confidence can be inferred from it. What must stop: where any "
    "required identity, evidence, integrity, or authority fact is unverified, "
    "reliance on the affected claim must stop and the affected material is "
    "isolated. What evidence or review is required next: the per-point evidence "
    "gaps and next actions above identify what supporting evidence or review is "
    "required before the account can be depended on. Whether human escalation is "
    "required: where a conflict, integrity failure, or not-auditable condition "
    "persists, the affected decision routes to the named human authority for "
    "adjudication.":
        "能依赖什么：各点由**权威账户**确立的判断（不是这份解释性报告）。"
        "不能主张什么：本报告不是第二个结果权威，也不能从它推出任何总分、排名、lane 或置信度。"
        "什么必须停下：凡必需的 身份／证据／完整性／权威 事实未核实，受影响主张的依赖必须停止，"
        "受影响材料已被隔离。下一步需要什么证据或复核：上面的逐点证据缺口与下一步动作，"
        "标明了在账户可被依赖之前需要哪些支撑证据或复核。"
        "是否需要人类升级：凡冲突、完整性失败或无法评估的状态持续存在，受影响的决定交给具名人类权威裁定。",
    "This report explains the authoritative account within the named project and "
    "analysis only. It is not a release decision, not a lane authority, and not a "
    "deployment decision.":
        "本报告只解释**具名项目与分析范围内**的那个权威账户。它**不是**发布决定、"
        "**不是** lane 权威、**也不是**部署决定。",
    "This report has not itself performed independent review and does not "
    "constitute a receipt, approval, or adjudication.":
        "本报告**本身没有做过独立复核**，也**不构成**收件、批准或裁定。",
    "acceptance_snapshot is the sole result-authority container; stage_package is "
    "its pre-snapshot input; the container holds exactly one embedded "
    "structured_strength_result. This report explains that account; it is not the "
    "container and not a second result.":
        "acceptance_snapshot 是**唯一的结果权威容器**；stage_package 是它的快照前输入；"
        "容器内**恰好内嵌一个** structured_strength_result。本报告只是**解释**那个账户，"
        "它**不是**那个容器，也**不是**第二份结果。",
    "A later audited account is a new bounded judgment related to, not a silent "
    "rewrite of, the current account. Prior material keeps its original scope and "
    "time meaning and is never current result authority.":
        "后出的审计账户是一个**新的有界判断**，与当前账户**相关**，但**不是**对它的悄悄改写。"
        "在先的材料保留其**原有范围与时间**的含义，**永远不是**当前的结果权威。",
    "No change context was provided to this report, so it cannot say what changed "
    "relative to any earlier account.":
        "本报告**没有收到变更上下文**，所以它**无法说明**相对于更早的账户发生了什么变化。",
    "No role records were provided to this report, so it cannot say who authored, "
    "independently reviewed, adjudicated, or administratively receipted this account.":
        "本报告**没有收到角色记录**，所以它**无法说明**：谁撰写、谁独立复核、谁裁定、谁只是行政收件。",
    "These product capabilities are not implemented here and are NOT being claimed; "
    "against each one the current account is unavailable. This list is a boundary "
    "statement, not a plan, a roadmap, or a release/lane authority.":
        "下面这些产品能力**这里没有实现**，也**没有被主张**；对其中每一项，当前账户都是**不可用**的。"
        "这份清单是**边界陈述**，不是计划、不是路线图、**也不是**发布／lane 权威。",
    "First bounded set of five decision points, not the complete product decision set.":
        "五个决策点的**第一个有界集合**，**不是**完整的产品决策集。",
    "These are the P-02 §5 state meanings this report can attest from facts it "
    "holds. Each entry states its reason and what it requires; `unexpressed_states` "
    "lists the meanings this system cannot express and what would have to change.":
        "以下是本报告**能从自己掌握的事实证明**的 P-02 §5 状态含义。每一条都写明：凭什么成立、"
        "以及它需要什么；unexpressed_states 列出本系统**无法表达**的含义及「要改变它需要什么」。",
    "Auditability here means the account can be evaluated; it does NOT mean every "
    "decision point is fully supported. Points with insufficient evidence or "
    "unresolved conflict are listed in conditions and flagged by scientific_evidence "
    "(insufficiency is never relabeled as not-auditable, P-02 §5.2).":
        "这里的「可评估」指的是**这个账户可以被检验**，**不等于**每一步都被支持。证据不足或有未解冲突的"
        "点会出现在 conditions 里、并由 scientific_evidence 标出（**科学上支持不足 永远不会被改名成**"
        "「无法评估」，P-02 §5.2）。",
    "The scope names the project, analysis, comparison, and audit scope for this "
    "combined account.":
        "这一轴点明这个合并账户的**项目、分析、比较对象与审计范围**。",
    "Explanation depth reflects per-point diagnostic explanations, evidence gaps, "
    "and limitations.":
        "这一轴反映**逐点**的诊断说明、证据缺口与限制到哪一层。",
    "Inference is supported where a point is auditable; conflicting points preserve "
    "residual/unordered meaning with no arbitrary winner; points listed in "
    "conditions either lack sufficient evidence or support no inference.":
        "凡是**可审计**的点，推理就成立；**冲突**的点保留「残留／无排序」的含义，**不挑赢家**；"
        "出现在 conditions 里的点，要么证据不足、要么不支持任何推断。",
    "Point is supportable only as scientifically limited or conflicting; the "
    "evidence available does not support the named claim. (This is NOT the same as "
    "a fact that could not be verified — see integrity_failure.)":
        "这个点**只能作为「科学上支持不足」或「冲突」**成立；手头的证据**不足以支持**所声明的主张。"
        "（这**不是**「必要事实无法核实」那种情况——那是 完整性／权威失败。）",
    "A required fact could not be verified — identity, source, version, binding, "
    "snapshot, permission, authority, or the critical context and declaration "
    "provenance a point needs to be auditable at all; the affected material is "
    "isolated.":
        "有一项**必要事实无法核实**——可能是 身份／来源／版本／绑定／快照／权限／权威，也可能是这个点"
        "要成其为「可审计」所需的**关键上下文与声明来源**；受影响材料已被隔离。",
    "NO reviewer record was supplied to this report":
        "本报告**没有收到任何复核人记录**",
    "a named reviewer record is present for a named target and scope":
        "存在一条具名复核人记录，且**目标与范围都具名**",
    "nothing on this basis: nothing was reviewed here":
        "**什么都不能**——这里没有任何复核",
    "that a review happened outside this report's view":
        "**不能**推断「在本报告视野之外发生过复核」",
    "supply a named reviewer record for a named target/scope":
        "提供一条具名复核人记录，写清**目标与范围**",
    "that review's bounded conclusion only":
        "**只**能依赖那次复核的**有界结论**",
    "a required decision, review or fact prevents the affected claim from proceeding":
        "有一项**必需的 决定／复核／事实**使受影响主张**无法继续**",
    "no required decision, review or fact is currently pending":
        "当前**没有**待决的决定、复核或事实",
    "the stopping reason and the named next authority":
        "**停下的理由**与**具名的下达权威**",
    "silence as approval, or permission to substitute":
        "**不能**把沉默当作批准，也**不能**当作可以替代",
    "what decision or evidence releases the block":
        "**什么决定或证据**能解除这个阻断",
    "the named human authority (scientific review for scientific shortfalls, "
    "governance for unverifiable facts)":
        "**具名的人类权威**（科学上的短板找**科学复核方**；无法核实的事实找**治理方**）",
    "no separate governed use/release ceiling is recorded here":
        "这里**没有记录**任何单独的使用／发布上限决定",
    "the account's stated scope only":
        "**只**能依赖这个账户**声明的范围**",
    "that a release or lane decision exists — this system grants none":
        "**不能**推断存在某项发布或 lane 决定——本系统**不授予**任何这类决定",
    "a governance decision, which this report cannot make":
        "**需要一项治理决定**，而这不是本报告能做的",
    "every point's version binding matches the current runtime":
        "每一个点的**版本绑定都与当前运行版本一致**",
    "at least one point was recorded under a different version binding than the "
    "current runtime":
        "至少有一个点是在**与当前运行版本不同**的版本绑定下记录的",
    "the account within its stated scope":
        "**在它声明的范围内**依赖这个账户",
    "that a future runtime change cannot make it stale":
        "**不能**推断「以后运行版本变了它也不会过时」",
    "nothing: re-checked against the current runtime":
        "**无需动作**：已与当前运行版本重新核对过",
    "a current observation or decision under the current version":
        "在当前版本下**重新观测或重新决定**",
    "historical meaning within that point's FORMER scope and time only":
        "**只**能按该点**当时的范围与时间**理解为历史含义",
    "this account is bounded to a named project/analysis/comparison/scope/use boundary":
        "这个账户被限定在一个**具名的 项目／分析／比较／范围／用途 边界**内",
    "the conclusion within exactly that boundary":
        "**恰好在这个边界内**的结论",
    "global product, phase or package clearance; or that the same conclusion holds "
    "for another object":
        "**不能**推断成全局产品／阶段／包级别的清关，也**不能**推断同样的结论对别的对象成立",
    "what boundary is named, and whether expansion is separately authorized":
        "具名的是**哪个边界**，以及扩张是否**另行授权**过",
    "the report lists future capabilities that are described but not built — "
    "REPORT-LEVEL intent, not a state of this account":
        "报告列出了「描述了但**没建成**」的未来能力——这是**报告级**的意图，**不是**这个账户的状态",
    "awareness of future intent or need":
        "**只**能知道有这些未来意图或需要",
    "implementation, evidence, proof, deployment or closure; and never as this "
    "account's outcome":
        "**不能**推断已实现／已有证据／已证明／可部署／已收尾；**更不能**把它当作本账户的结果",
    "is this only a plan, or is separate evidence present?":
        "这**只是计划**，还是另有证据？",
    "at least one point reports a fact that could not be verified":
        "至少有一个点报告了**无法核实的事实**",
    "the unavailability and the containment consequence":
        "**不可用**这一事实，以及**隔离**的后果",
    "ordinary scientific weakness, or automatic downgrade":
        "**不能**当成普通的科学薄弱，也**不能**当成自动降级",
    "which integrity/authority fact failed, and what must stop":
        "**哪一项**完整性／权威事实失败了，以及**什么必须停**",
    "not implemented: bounded-use is stated in prose only":
        "**未实现**：使用边界目前只有一段文字声明",
    "not implemented":
        "**未实现**",
    "not implemented and not claimed":
        "**未实现，也未主张**",
    "not performed: the system records roles, it does not conduct review":
        "**未执行**：系统只**记录**角色，它**不做**复核",
    "not implemented: five RNA-seq points only":
        "**未实现**：目前只有这五个 RNA-seq 决策点",
    "not implemented: evidence carries source type, verification and value":
        "**未实现**：证据目前只带 来源类型／核实状态／值 三个字段",
    "not available: an inspector page for the accountable owner exists "
    "(ui/report.html) and renders this report for reading, but a user-facing product "
    "surface and any external-facing presentation remain undecided product decisions "
    "(P-03 §14)":
        "**不可用**：给负责人看的**检查页**已经存在（ui/report.html，把本报告渲染成可读形式），"
        "但**面向用户的产品界面**与**任何对外呈现**仍属**未决的产品决定**（P-03 §14）",
    "partially implemented: 6 of the 13 P-02 §5 meanings are expressible as point "
    "judgements (auditable, scientifically_limited, conflicted, not_auditable, "
    "pending_attempt, integrity_failed, plus historical_only on the history side), "
    "and reviewed / blocked / limited-use are attested in `state_meanings`; stale, "
    "scoped, external and planned are NOT expressible — see "
    "`state_meanings.unexpressed_states` for the reason and what would have to "
    "change for each":
        "**部分实现**：P-02 §5 的 14 个词条里**13 个可表达**——6 个作为点判断（可审计／科学上支持"
        "不足／冲突未解／无法评估／待验证的提交／完整性失败，另有历史侧的「仅历史」），"
        "另有 已复核／已阻断／限定使用／过时／范围受限／计划中 在 state_meanings 里作证；"
        "**只剩 external（外部观测）无法表达**——原因与「要改变它需要什么」见 "
        "state_meanings.unexpressed_states",
    "the current data model has no outside-the-artifact physical observation to "
    "describe: nothing in an audit carries a byte- or encoding-level observation "
    "made outside the artifact":
        "当前数据模型里**没有**「工件之外的物理观测」可以描述：一次审计里**没有任何东西**"
        "承载「在工件之外做出的、字节或编码层面的观测」",
    "an authorized input for external physical observations (a new input contract, "
    "hence a governance decision)":
        "需要**另行授权**一个「外部物理观测」的输入（那是新的输入契约，因此是**治理决定**）",
    "competing method executions observed; no arbitrary winner is selected":
        "观察到**两套互相竞争的**方法执行记录；系统**不挑赢家**",
    "normalization decision is supportable for the declared context":
        "在所声明的上下文里，标准化这一步的决定**站得住**",
    "multiple-testing correction choice is supportable for the declared context":
        "在所声明的上下文里，多重检验校正的选择**站得住**",
    "threshold label alone is not evidence for the declared claim and intended use":
        "**只写一个阈值名称**并不构成证据，无法支持所声明的主张与预期用途",
    "resolve which observed methods are the executed pipeline":
        "**先查清**到底哪一套观测到的方法才是实际执行的那条流程",
    "support the threshold choice with point-local evidence":
        "用**属于这一步的**证据来支持阈值的选取",
    "provide a supported threshold choice or rationale":
        "给出**有支撑的**阈值选择或理由",
    "competing method executions require adjudication":
        "**两套竞争的方法执行**需要人来裁定",
    "Declared exclusions are shown verbatim.":
        "所声明的排除项**原样展示**。",
    "No explicit exclusions were declared for this audit.":
        "本次审计**没有声明**任何显式排除项。",
    "integrity domains confirmed": "完整性各域**已确认**",
    "integrity domains not confirmed": "完整性各域**未确认**",

    # ---- 2026-09-14（2C）补录：全页唯一句 / 值标签 / 判据面板口径 ----
    "five-point MVP slice": "五个方法学决策点（MVP 口径）",
    "No total scientific score.": "不存在总分。",
    "No confidence score standing in for the result vector.": "不存在替代结果向量的置信分。",
    "No universal ranking.": "不存在通用排名。",
    "No single quality number.": "不存在单一质量数。",
    "No lane-as-science interpretation.": "不存在把使用档位当作科学结论的解释。",
    "No local summary that substitutes for the embedded result authority.": "不存在代替内嵌结果的局部摘要。",
    "A lane is a governed use/release ceiling for the named scope. It is not a scientific result, not a score, not a ranking, and not authority for any use beyond the stated scope and restrictions.": "使用上限是对具名范围的一条受治理的使用/发布天花板；它不是科学结论、不是分数、不是排名，也不授权任何超出所声明范围与限制的用途。",
    "acceptance_snapshot is the sole result-authority container; stage_package is its pre-snapshot input; the container holds exactly one embedded structured_strength_result. This report explains that account; it is not the container and not a second result.": "权威容器是唯一的结果权威；stage_package 是它生成前的输入；容器内恰好内嵌一个结构化结果。本页解释这份账户——本页不是那个容器，也不是第二份结果。",

    # ---- 2026-09-15（2C 笔 2）：引擎解释句—账户数据改规范英文后的对照 ----
    # 与 src/bioaudit/engine/evaluator.py 的两条成稿句 + 无规则句逐字配对
    # （页面 zh-first 呈现；判词/状态词 token 不动）。
    "Unable to evaluate: the matching rules do not cover the declared method "
    "name. This does not mean the method is wrong — the rule library maps levels "
    "by method name, and this name is not in the mapping; the point is therefore "
    "treated as unevaluable, yielding no quality conclusion and altering no "
    "judgment.":
        "无法评估：匹配规则未覆盖该声明的方法名。这不是说方法错了——规则库目前"
        "按方法名对档位，而这个名字不在对照表里，因此本点按「无法评估」处理："
        "不产生质量性结论，也不改动任何判定。",
    "Unable to evaluate: no applicable rule matched.":
        "无法评估：没有适用的规则。",

    # ---- 2026-09-14（2C）第 11 章：能力 + 现状（定稿句式「能力：现状。」）----
    "no release machinery / no lane grant": "发布机制与档位授予",
    "deployment and Package A closure": "部署与包级收尾",
    "runtime proof / execution evidence": "运行期证明 / 执行证据",
    "independent reviewer approval": "独立复核人批注",
    "additional decision points beyond the five": "五个决策点之外的扩展点",
    "other analysis types beyond bulk RNA-seq": "bulk RNA-seq 之外的分析类型",
    "evidence provenance / freshness fields": "证据来源 / 时效字段",
    "the full state vocabulary": "完整状态词表",
    "a formal report for showing to others": "对外展示的正式报告",
    "not implemented and not claimed": "未实现，未声称",
    "not performed: the system records roles, it does not conduct review": "不执行——系统只记录角色，不代替复核",
    "not implemented: five RNA-seq points only": "未实现——本审计只覆盖五个 RNA-seq 决策点",
    "not implemented": "未实现",
    "partially implemented: every evidence item now carries its observation time, the validity window its submitter declared, an optional provenance and subject, the claim it is offered for, and its role; the report derives and shows whether each item is still current. NOT implemented: per-item authority permission (who may rely on it), and any judgement of whether the declared provenance is genuine — the system records the declaration and does not adjudicate it": "部分实现——每条证据已带观测时点、声明的有效期、来源与主体、所支撑的主张与角色；逐项权限（谁可以依赖它）与来源真伪的判断仍未实现——系统只记录声明，不裁定真伪",
    "partially implemented: P-02 §5 lists 13 state and consequence meanings; auditable, scientifically_limited, conflicted, not_auditable and integrity_failed are expressible as point judgements, historical_only on the history side, and reviewed, blocked, limited-use, stale, scoped and planned are attested from facts this report holds — 12 of 13 in total, and external is not expressible, because no input carries an outside-the-artifact physical observation; see `state_meanings.unexpressed_states` for its reason and what would have to change for it.": "部分实现——13 项含义里，可审计/科学上支持不足/冲突/不可审计/完整性失败可作逐点判词；其余词以账户级状态呈现；external 无法表达，原因见状态词段（何意/要什么）",
    "not available: an inspector page for the accountable owner exists (ui/report.html) and renders this report for reading, but a user-facing product surface and any external-facing presentation remain undecided product decisions (P-03 §14)": "未提供——本页是给责任人的检查页；面向外部的产品面与呈现形式仍未定（属未决产品决定）",
    "partially implemented: no lane is granted and no release authority exists, but a use/release ceiling can be recorded and rendered with its evidence, restrictions and required next evidence (see lane_and_release). This is not release machinery: no automatic promotion, transition, rollback or deployment; the decision caps use and never authorises it by itself": "部分实现——不授予任何档位与发布权；但使用上限决定可以记录并连同其证据、限制与所需证据一起呈现。这不是发布机制：不自动晋升、不迁移、不回滚、不部署；决定只封顶使用，自己不授权",
}

SENTENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^critical (.+) context is missing: (.+)$"),
     "关键的{0}上下文缺失：{1}（因此这个点**无法评估**：必要事实不足以做出判断）"),
    (re.compile(r"^(.+) declaration source must be declared$"),
     "{0} 的声明来源必须是 declared（即由提交方声明）"),
    (re.compile(r"^integrity metadata requires resolution: (.+)$"),
     "完整性元数据需要先解决：{0}"),
    (re.compile(r"^not implemented: (.+)$"), "**未实现**：{0}"),
    # 动作型：「<action> on <point>」
    (re.compile(r"^(request_review|request_evidence|stop|submit_correction) on (.+)$"),
     "对 {1} 的{0}"),
    # 轴上的条件项：「<point>: <label>」
    (re.compile(r"^(\w+): (evidence gaps|limitations)$"),
     "{0}：{1}"),
    # 点的位置标识：「DP-0N/<name>」
    (re.compile(r"^DP-0\d/(\w+)$"), "DP-{0} 位置"),
    # 账户引用（id 不译，只标它的角色）
    (re.compile(r"^acceptance_snapshot:(.+)$"), "权威容器：{0}（账户引用）"),
    (re.compile(r"^snapshot:(.+)$"), "快照：{0}"),
]

# 长句的前缀兜底：报告偶尔会在句尾增补一句，此时按前缀仍给出翻译，
# 避免整段回退成英文（前缀必须足够长，不能误配到别的句子）。
PREFIX_FALLBACK: list[tuple[str, str]] = [
    ("not available: an inspector page for the accountable owner exists "
     "(ui/report.html) and renders this report for reading",
     "未提供——本页是给责任人的检查页；面向外部的产品面与呈现形式仍未定（属未决产品决定）"),
    ("What can be relied on: each point's judgment as established by the "
     "authoritative account",
     "能依赖什么：各点由**权威账户**确立的判断（不是这份解释性报告）。"
     "不能主张什么：本报告不是第二个结果权威，也不能从它推出任何总分、排名、lane 或置信度。"
     "什么必须停下：凡必需的 身份／证据／完整性／权威 事实未核实，受影响主张的依赖必须停止，"
     "受影响材料已被隔离。下一步需要什么证据或复核：逐点证据缺口与下一步动作标明了还需要什么。"
     "是否需要人类升级：凡冲突、完整性失败或无法评估的状态持续存在，受影响的决定交给具名人类权威裁定；"
     "停下时的责任方类别在下面点名，而不是留空。"),
]

PHRASES: dict[str, str] = {
    "filtering": "低表达过滤",
    "normalization": "标准化",
    "differential_method": "差异分析方法",
    "multiple_testing_correction": "多重检验校正",
    "significance_threshold": "显著性／效应量阈值",
    "low-expression filtering": "低表达基因过滤",
    "differential-analysis method": "差异分析方法",
    "multiple-testing correction": "多重检验校正",
    "significance / effect-size threshold": "显著性／效应量阈值",
    "auditable": "可审计",
    "scientifically_limited": "科学上支持不足",
    "conflicted": "冲突未解",
    "not_auditable": "无法评估",
    "integrity_failed": "完整性／权威失败",
    "pending_attempt": "待验证的提交",
    "confirmed": "已核实",
    "unverified": "未核实",
    "insufficient": "不足",
    "sufficient": "充分",
    "complete": "完整",
    "partial": "部分",
    "declared": "由提交方声明",
    "observed": "观测到",
    "reviewed": "已复核",
    "authored": "撰写",
    "receipted": "行政收件",
    "adjudicated": "裁定",
    "blocked": "已阻断",
    "stale": "过时",
    "scoped": "范围受限",
    "planned": "计划中",
    "external": "外部观测",
    "limited-use": "限定使用",
    "scientific_insufficiency": "科学上支持不足",
    "integrity_failure": "完整性／权威失败",
    "request_evidence": "要求补证据",
    "request_review": "要求复核",
    "submit_correction": "提交修正",
    "evidence_requested": "待补证据",
    "review_requested": "待复核",
    "correction_submitted": "已提交修正",
    "pre_snapshot_input": "快照前输入",
    "the full state vocabulary": "完整状态词表",
    "the auditable structured result carries explicit limitations or prohibited uses, so at most a restricted `candidate` is supported (C-04 §6 row 4)": "可审计的结构化结果带有明示限制或禁用用途，因此最多支持一个**受限的**「候选」档（C-04 §6 第 4 行）。",
    "the auditable structured result retains residual unordered axes, so at most a restricted `candidate` is supported (C-04 §6 row 5)": "可审计的结构化结果仍带残余、无排序含义的轴，因此最多支持一个**受限的**「候选」档（C-04 §6 第 5 行）。",
    "the auditable structured result is a cross-axis vector with no authorised cross-axis ordering, so at most `candidate` is supported (C-04 §6 row 3)": "可审计的结构化结果是一个跨轴向量，且没有任何经授权的跨轴定序，因此最多支持「候选」档（C-04 §6 第 3 行）。",
    "a required axis fact is not auditable; an integrity-class gap must not be reinterpreted as scientific insufficiency (C-04 §6 row 2)": "某个必需的轴事实无法审计；完整性类缺口**不得**被改写成「科学上支持不足」（C-04 §6 第 2 行）。",
    "required integrity/authority facts are not auditable, so no valid release or lane conclusion exists (C-04 §6 row 1)": "必需的完整性／权威事实无法审计，因此不存在有效的发布或使用上限结论（C-04 §6 第 1 行）。",
    "the lane predicate rests on a version binding that no longer matches the current runtime, so no promotion is supportable (C-04 §9, stale dependency)": "该使用上限所依据的版本绑定**已与当前运行时不符**，因此不支持任何上调（C-04 §9：依赖已过期）。",
    "A lane is a governed use/release ceiling for the named scope. It is not a scientific result, not a score, not a ranking, and not authority for any use beyond the stated scope and restrictions.": "使用上限（lane）是**受治理的使用／发布封顶**，只对声明的范围有效。它**不是**科学结论、不是分数、不是排名，也不构成超出声明范围与限制的任何使用授权。",
    "Recording a lane decision is not a release, not a deployment, not a promotion, and not a Package A closure.": "记录一条使用上限决定**不是**发布、**不是**部署、**不是**晋升、也**不是** Package A 收尾。",
    "The time shown against a decision is when its line was written to the ledger file. It is NOT a decision field: a copied or restored ledger carries the copy time, and the record itself states no decision time.": "决定上显示的时间，是**这一行写进账本文件的时间**。它**不是**决定自身的字段：账本被复制或恢复后，显示的就是复制时间；记录本身并不声明决定时间。",
    "`validated` and `production` are not reachable for this account: they require an authorised cross-axis ordering, and this workline authorises no cross-axis ordering (P-02 §5.2, C-04 §6). Absence of those lanes is NOT a statement that the account is weak.": "本账户**够不着**「已验证」与「生产」两档：它们要求**经授权的跨轴定序**，而本工作线不授权任何跨轴定序（P-02 §5.2、C-04 §6）。**这两档不存在，不等于结果弱。**",
    "DP-01..DP-05 filtering slice / research use within this project": "DP-01…DP-05 过滤切片 ／ 本项目内的研究用途",
    "PRJ-Alpha / AN-001 filtering step": "PRJ-Alpha / AN-001 的过滤步骤（标识照录）",
    "PRJ-Alpha / AN-001 normalization step": "PRJ-Alpha / AN-001 的标准化步骤（标识照录）",
    "PRJ-Alpha / AN-001 differential analysis": "PRJ-Alpha / AN-001 的差异分析（标识照录）",
    "PRJ-Alpha / AN-001 correction step": "PRJ-Alpha / AN-001 的校正步骤（标识照录）",
    "PRJ-Alpha / AN-001 threshold": "PRJ-Alpha / AN-001 的阈值（标识照录）",
    "a threshold stated in the write-up, with no execution record": "阈值只在分析报告里写过，**没有留下执行记录**",
    "clinical or diagnostic use": "临床或诊断用途",
    "any use outside the named project": "本项目之外的任何用途",
    "batch 3 excluded (instrument drift); no external release intended": "第 3 批不包含在内（仪器漂移）；不打算对外发布",
    "normalization decision is supportable for the declared context": "在声明的上下文里，标准化这一步的决定站得住",
    "differential-analysis method is supportable for the declared design and comparison": "在声明的设计与比较下，差异分析方法的选择站得住",
    "multiple-testing correction choice is supportable for the declared context": "在声明的上下文里，多重检验校正的选择站得住",
    "no release machinery / no lane grant": "没有发布机制、也不授予 lane",
    "normalization only / teaching use": "仅限标准化步骤 ／ 教学用途",
    "nothing outstanding was observed": "没有观察到需要跟进的未决事项",
    "partially implemented: no lane is granted and no release authority exists, but a use/release ceiling can be recorded and rendered with its evidence, restrictions and required next evidence (see lane_and_release). This is not release machinery: no automatic promotion, transition, rollback or deployment; the decision caps use and never authorises it by itself": "**部分实现**：本系统不授予任何使用档位、也没有发布权威；但可以记录并呈现「使用／发布封顶」，连同它的证据、限制与「还需要什么证据」。这**不是发布机制**——不会自动晋升、不会迁移、不会回滚、不会部署；该决定只**封顶**使用，本身从不授予使用。",
    "partially implemented: every evidence item now carries its observation time, the validity window its submitter declared, an optional provenance and subject, the claim it is offered for, and its role; the report derives and shows whether each item is still current. NOT implemented: per-item authority permission (who may rely on it), and any judgement of whether the declared provenance is genuine — the system records the declaration and does not adjudicate it": "**部分实现**：每条证据现在都带**观测时间**、**声明方给出的有效期**、可选的**来源与主体**、它**为哪条主张**提供支持、以及它的**角色**；报告会算出并显示每条是否仍在有效期内。**尚未实现**：逐项的权限（谁可以依赖它），以及判断所声明的来源是否为真——系统只**记录声明**，不替它作真伪裁定。",
    "partially implemented: P-02 §5 lists 13 state and consequence meanings; auditable, scientifically_limited, conflicted, not_auditable and integrity_failed are expressible as point judgements, historical_only on the history side, and reviewed, blocked, limited-use, stale, scoped and planned are attested from facts this report holds — 12 of 13 in total, and external is not expressible, because no input carries an outside-the-artifact physical observation; see `state_meanings.unexpressed_states` for its reason and what would have to change for it.": "**部分实现**：P-02 §5 列出 13 个状态与后果含义。其中 5 个能作为判断值表达（可审计、科学上支持不足、冲突未解、无法评估、完整性失败），历史侧另有「仅历史」；另有 6 个由本报告依据自己掌握的事实认证（已复核、已阻断、限定使用、过期、范围受限、计划中）——合计 13 个中的 12 个。只有「外部观测」无法表达，因为没有任何输入携带**工件之外的物理观测**；原因与「要改变它需要什么」见 `state_meanings.unexpressed_states`。",
    "support execution observation": "支持「执行过程被观测到」这一点",
    "that a future block cannot arise": "不能推断「将来不会出现阻断」",
    "the analysis script parameters recorded for this run": "本次运行的**分析脚本参数记录**",
    "the declared BH correction was applied": "所声明的 BH 校正确实被应用了",
    "the declared DESeq2 method": "所声明的 DESeq2 方法",
    "the declared TMM normalization was what ran": "所声明的 TMM 标准化确实被执行了",
    "the declared low-count filtering method was the executed one": "所声明的低计数过滤方法确实是被执行的那一个",
    "the declared threshold choice": "所声明的阈值选择",
    "the executed differential method": "实际执行的差异分析方法",
    "the executed workflow log": "实际执行的工作流日志",
    "the methods section of the submitted write-up": "提交报告里的方法一节",
    "the pipeline log for this run (rule match + executed method)": "本次运行的流程日志（规则匹配 + 实际执行的方法）",
    "this account's stated conclusions within their scope": "本账户在其声明范围内给出的结论",
    "this observation is within the validity window the submitter declared, so it can actively support the claim it was offered for": "这条观测仍在提交方声明的有效期内，因此**可以**为它所支持的那条主张提供积极支撑。",
    "this observation is past the validity window the submitter declared: it no longer actively supports the claim it was offered for, and what it can still establish is historical meaning only": "这条观测**已超出**提交方声明的有效期：它不再为所支持的主张积极作证，现在只能提供**历史含义**。",
    "the evidence offered to support this decision is past the validity window its submitter declared, so nothing here actively supports a promotion; supply current supporting evidence, or record the honest outcome (C-04 §6 item 5: freshness facts are required dependencies)": "为这条决定提供**支撑**的证据已超出提交方声明的有效期，因此这里没有任何材料能积极支持上调；要么补一份当前的支撑证据，要么如实记录结论（C-04 §6 第 5 项：时效事实是必须覆盖的依赖）。",
    "deployment and Package A closure": "部署与 Package A 收尾",
    "runtime proof / execution evidence": "运行证明／执行证据",
    "independent reviewer approval": "独立复核人批准",
    "additional decision points beyond the five": "五点之外的决策点",
    "other analysis types beyond bulk RNA-seq": "bulk RNA-seq 之外的分析类型",
    "evidence provenance / freshness fields": "证据来源链／时效字段",
    "a formal report for showing to others": "给他人看的正式报告",
    "deseq2": "DESeq2（差异分析软件）",
    "edgeR": "edgeR（差异分析软件）",
    "tmm": "TMM（标准化方法）",
    "BH": "Benjamini–Hochberg（多重检验校正法）",
    "low_count_filter": "低计数过滤法",
    "padj_0.05": "校正后 p 值 < 0.05 的阈值",
    "bulk_rnaseq": "bulk RNA-seq（批量转录组测序）",
    "method": "方法",
    "filtering_support": "过滤步骤的支持度",
    "declared_observed_conflict": "声明与观测冲突",
    "evidence_observations": "证据观测",
    "decision_declaration": "决策声明",
    "five-point MVP slice": "五点 MVP 切片",
    "internal scientific review": "内部科学复核",
    "project_id=PRJ-Alpha": "project_id=PRJ-Alpha（项目编号）",
    "analysis_id=AN-001": "analysis_id=AN-001（分析编号）",
    "comparison=treated_vs_control": "comparison=treated_vs_control（处理组 vs 对照组）",
    "audit_scope=five-point MVP slice": "audit_scope=五点 MVP 切片（审计范围）",
    "No total scientific score.": "不得有科学总分。",
    "No confidence score standing in for the result vector.": "不得用置信度替代结果向量。",
    "No universal ranking.": "不得有通用排名。",
    "No single quality number.": "不得有单一质量数字。",
    "No lane-as-science interpretation.": "不得把 lane 当作科学强度。",
    "No local summary that substitutes for the embedded result authority.":
        "不得用局部摘要替代内嵌的结果权威。",
}


def _normalise(text: str) -> str:
    """Collapse whitespace so a re-wrapped report string still matches a key."""
    return " ".join(text.split())


_NORMALISED_SENTENCES = {_normalise(k): v for k, v in SENTENCES.items()}
_NORMALISED_PHRASES = {_normalise(k): v for k, v in PHRASES.items()}


def zh_for(text: Any) -> str:
    """Chinese rendering of one English string ('' when not covered).

    A string that is already Chinese (legacy engine messages are) needs no
    rendering: '' tells the caller to show it as-is.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        return ""
    stripped = _normalise(text)
    if stripped in _NORMALISED_SENTENCES:
        return _NORMALISED_SENTENCES[stripped]
    if stripped in _NORMALISED_PHRASES:
        return _NORMALISED_PHRASES[stripped]
    for prefix, zh in PREFIX_FALLBACK:
        if stripped.startswith(_normalise(prefix)):
            return zh
    for pattern, template in SENTENCE_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return template.format(*match.groups())
    return ""


def coverage(strings: list[str]) -> dict[str, Any]:
    """How many of the given English strings have a Chinese rendering."""
    checked = [s for s in strings if isinstance(s, str) and re.search(r"[A-Za-z]", s)]
    missing = sorted({s for s in checked if not zh_for(s)})
    return {"total": len(checked), "translated": len(checked) - len(missing),
            "missing": missing}
