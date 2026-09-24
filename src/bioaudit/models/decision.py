"""Decision 与 ParsedStep 数据模型（自 fullflow-demo 迁移；B2 加本体注释字段）。

B3 变更（audit-report A15）：``Decision`` 增加 ``extra="forbid"``——
decision_type 拼错（如 "decisionType"）不再被静默忽略：必填字段缺失 →
pydantic ValidationError → API 层包装为 validation-error 显式报错。
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class Decision(BaseModel):
    """A single decision made by an AI Agent during bioinformatics analysis."""
    model_config = ConfigDict(extra="forbid")  # A15: 未知字段显式报错，不静默吞掉

    step_id: str
    decision_type: str  # e.g. "deg_method", "normalization"
    choice: str         # e.g. "DESeq2", "TMM"
    rationale: str = ""  # Agent's stated reason
    context: dict = Field(default_factory=dict)
    tool_call: Optional[str] = None
    code_snippet: Optional[str] = None


class ParsedStep(BaseModel):
    """Normalized step after parsing.

    B2 新增（本体接线，不改评分路径）：
    - homologous_types: 跨范式同源声明（本体 aliases，非匹配通道，ontology-design-v1 §二.1）
    - unclassified: 决策类型不在本体 34 类型内（§五：标 unclassified + 待补清单）
    """
    step_id: str
    original: Decision
    decision_type: str
    normalized_context: dict
    homologous_types: list[str] = Field(default_factory=list)
    unclassified: bool = False
