"""采集层数据模型（窗口 C：M1/M3 采集与交叉验证）。

设计依据：docs/specs/2026-08-13-trajectory-capture-design-v1.md
- :class:`DecisionProvenance`：逐决策来源（M1声明/M3解析）+ 时间戳 + 证据
- :class:`CapturedDecision`：M3 解析产物（signatures 驱动；context 键带三级可信源）
- :class:`UncertainCandidate`：choice 无法确定性判定/context 缺键的候选
  （宁可标 unverified 也不伪造——F6 禁猜规则）
- :class:`ParseResult`：一次解析的全部输出（可审计决策 + 未定候选 + 元信息）

上下文四级可信源（设计 §四 + 窗口 G-2 定稿，严格排序，禁止跳级猜测）：
    call_arg（调用参数） > data_metadata（数据元数据） > declared（评测者/数据事实声明）
    > unverified（任一级都提取不到 → 显式标记，绝不从正则猜数字）。

**G-2 定稿：declared 的边界（关键纪律，execution-plan §六.十二 G2-a.1）**
- declared 只允许来自**评测者 / 数据事实**——运行宪法或评测配置注入的键值
  （如数据集平台 GSE115978 = smartseq2）；
- **与 Agent claim（M1 声明）严格区分**：Agent 上报的键**永远不进 declared**
  （那是 M1/M3 交叉验证的职责）；declared 提供键 → 该键不再 unverified。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# ── 来源常量（provenance，设计 §四/§七）──
PROVENANCE_SOURCE_M1 = "M1声明"     # Agent 主动上报（self-declared）
PROVENANCE_SOURCE_M3 = "M3解析"     # 产物解析（artifact trace）
# M1.1（窗口 M，2026-08-16）：预期决策缺失补入（expected_types 强制检查；
# choice 优先取 Agent 已声明值（如 skip_doublet），无声明 → not_performed）
PROVENANCE_SOURCE_EXPECTED = "expected"
PROVENANCE_SOURCE_UNKNOWN = "unknown"

# ── 上下文可信源（设计 §四 + G-2 定稿：四级，declared 仅评测者/数据事实）──
TRUST_CALL_ARG = "call_arg"            # 调用参数（最可信）
TRUST_DATA_METADATA = "data_metadata"  # 数据元数据（次可信）
TRUST_DECLARED = "declared"            # 评测者/数据事实声明（运行宪法/评测配置注入；
                                       # 与 Agent claim（M1 声明）严格区分——
                                       # Agent 上报的键永远不进 declared）
TRUST_UNVERIFIED = "unverified"        # 任一级都提取不到 → 显式标记，不猜测

#: 全部合法可信源
TRUST_LEVELS: frozenset[str] = frozenset({
    TRUST_CALL_ARG, TRUST_DATA_METADATA, TRUST_DECLARED, TRUST_UNVERIFIED,
})


class DecisionProvenance(BaseModel):
    """逐决策 provenance（设计 §四：{来源: M1声明/M3解析, 时间戳, 证据}）。"""

    source: str  # PROVENANCE_SOURCE_M1 / PROVENANCE_SOURCE_M3
    timestamp: str  # ISO 时间戳
    evidence: str  # 证据：代码行/调用签名
    # 如 "notebook cell #3: sc.pp.filter_cells(min_genes=200)"
    detail: dict = Field(default_factory=dict)  # 补充细节（cell 序号、工具名、签名 id）


class CapturedDecision(BaseModel):
    """M3 解析出的一条候选决策（signatures 命中 + choice 确定性可判定）。

    - ``context``：可审计上下文（仅含三级可信源提取到的键；提取不到不出现）
    - ``context_trust``：每个 context 键的可信源（call_arg/data_metadata/declared）
    - ``unverified_keys``：决策类型 context_schema 中三级都提取不到的键
      （显式标 unverified，绝不伪造数值）
    - ``instance_index``：同类型多实例建模（迭代调参，v1.1 B5）
    """

    step_id: str
    decision_type: str  # 本体规范 ID（signatures 表 key；解析前经 input_synonyms 归一化）
    choice: str
    rationale: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    context_trust: dict[str, str] = Field(default_factory=dict)
    unverified_keys: list[str] = Field(default_factory=list)
    tool_call: Optional[str] = None
    code_snippet: Optional[str] = None
    provenance: DecisionProvenance
    instance_index: int = 1  # 同类型多实例（迭代调参）：第几次出现
    paradigm: Optional[str] = None  # 解析时使用的范式（None = 未限定）

    def to_decision(self) -> dict:
        """转 ``audit_decision`` 契约决策负载（Decision schema：step_id/decision_type/choice…）。"""
        return {
            "step_id": self.step_id,
            "decision_type": self.decision_type,
            "choice": self.choice,
            "rationale": self.rationale,
            "context": dict(self.context),
            "tool_call": self.tool_call,
            "code_snippet": self.code_snippet,
        }


class UncertainCandidate(BaseModel):
    """choice 无法确定性判定（或上下文严重缺失）的候选——**不**进入可审计决策集。

    禁猜规则（F6）：无法从调用结构确定性推导 choice/数值时，宁可标 unverified，
    绝不正则猜数字（旧 trajectory_capture 的 n_patients=11/n_cells=50000 默认值
    与 UMAP→PCA 张冠李戴即反面教材）。
    """

    decision_type: str
    step_id: str
    evidence: str
    reason: str  # 为何未定（如 "choice 需 elbow 证据，代码仅含 sc.pp.pca 调用"）
    partial_context: dict = Field(default_factory=dict)  # 已提取到的键（call_arg 级）
    tool_call: Optional[str] = None
    code_snippet: Optional[str] = None


class ParseResult(BaseModel):
    """一次 M3 解析的全部输出。"""

    candidates: list[CapturedDecision] = Field(default_factory=list)
    uncertain: list[UncertainCandidate] = Field(default_factory=list)
    n_code_cells: int = 0
    parser_version: str = "1.0"
    paradigm: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

    def decisions(self) -> list[dict]:
        """可审计决策负载列表（供 run_audit / 交叉验证器消费）。"""
        return [c.to_decision() for c in self.candidates]


__all__ = [
    "PROVENANCE_SOURCE_M1",
    "PROVENANCE_SOURCE_M3",
    "PROVENANCE_SOURCE_EXPECTED",
    "PROVENANCE_SOURCE_UNKNOWN",
    "TRUST_CALL_ARG",
    "TRUST_DATA_METADATA",
    "TRUST_DECLARED",
    "TRUST_UNVERIFIED",
    "TRUST_LEVELS",
    "DecisionProvenance",
    "CapturedDecision",
    "UncertainCandidate",
    "ParseResult",
]
