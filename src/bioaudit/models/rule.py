"""Rule 数据模型（自 fullflow-demo 迁移，未改动）。"""

from pydantic import BaseModel, Field
from typing import Optional


class ScoringLevel(BaseModel):
    """Single scoring level with methods and metadata."""
    methods: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    rationale: Optional[str] = None
    note: Optional[str] = None
    conditions_when_acceptable: Optional[str] = None


class RuleScoring(BaseModel):
    """5-level scoring rubric using ScoringLevel sub-models."""
    level_4: ScoringLevel = Field(default_factory=ScoringLevel)
    level_3: ScoringLevel = Field(default_factory=ScoringLevel)
    level_2: ScoringLevel = Field(default_factory=ScoringLevel)
    level_1: ScoringLevel = Field(default_factory=ScoringLevel)
    level_0: ScoringLevel = Field(default_factory=ScoringLevel)
    # Special overrides (e.g., n=2)
    override_n2: Optional[dict] = None


class RuleCondition(BaseModel):
    """Declarative rule trigger condition."""
    decision_type: str
    required_context: dict = Field(default_factory=dict)
    forbidden_context: dict[str, list] = Field(default_factory=dict)
    context_constraints: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    """Evidence citation anchoring a rule."""
    source_type: str  # "benchmark_paper" | "method_paper" | "consensus_guideline" | "math_theorem"
    doi: Optional[str] = None
    pmid: Optional[str] = None  # PubMed ID — 国内可直接访问
    url: Optional[str] = None   # 任意直链 (如 JSTOR) — 优先级: pmid > url > doi
    title: str
    confidence: str  # "L-Confirmed" | "L-Consensus" | "L-Evidenced" | "L-Emerging" | "L-Anecdotal"
    excerpt: str
    supports_levels: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    """A complete scientific decision rule."""
    rule_id: str
    domain: str
    status: str = "active"
    version: int = 1
    title: str = ""
    description: str = ""
    condition: RuleCondition
    scoring: RuleScoring
    evidence: list[EvidenceRef] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    superseded_by: Optional[str] = None
