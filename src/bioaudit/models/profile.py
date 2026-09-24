"""ScenarioProfile 模型（自 fullflow-demo 迁移，未改动）。"""

from pydantic import BaseModel, Field


class RuleOverride(BaseModel):
    rule_pattern: str  # "M1.1-DEG-*"
    weight_multiplier: float = 1.0
    min_level: int | None = None


class ScenarioProfile(BaseModel):
    profile_id: str
    description: str = ""
    rule_overrides: list[RuleOverride] = Field(default_factory=list)
    dimension_weights: dict[str, float] = Field(default_factory=dict)
