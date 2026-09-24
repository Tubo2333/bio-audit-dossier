"""benchmark 任务集数据模型（窗口 D / 阶段 3）。

设计依据（refactor-plan-v1.1 E1-E8 + audit-report E 组 + execution-plan §六.七 D1-D6）：
- 任务 = v2 轨迹 + 难度（E4 独立量化）+ gold 标注（E3 双标注/共识强度）；
- 任务集 manifest 带 semver（E8）+ 快照三元组（C1/P2）+ 模型信息（E6）；
- 关键纪律：**难度标签不得由审计分数定义**（E4）；gold 由标注管线产出
  （生成器不写 gold）；元数据不参与评分（golden 0 差异不变量）。

Schema 版本：benchmark.task.v1（taskset.json 记录）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from bioaudit.models.decision import Decision
from bioaudit.models.trajectory import VALID_PARADIGMS, TrajectoryProvenance

# ── 常量 ────────────────────────────────────────────────────────────────────────
TASKSET_SCHEMA_VERSION = "benchmark.task.v1"
BENCHMARK_SOURCE = "benchmark"          # provenance.source 标记（v2 合法来源之一）

#: gold 标签（标注 rubric 定义，见 benchmark/annotation_rubric.md）
GOLD_LABELS: tuple[str, ...] = ("correct", "error", "edge")

#: 共识强度（E3：强=双标一致 / 中=仲裁 2:1 / 弱=仲裁与双方均不一致）
CONSENSUS_STRENGTHS: tuple[str, ...] = ("strong", "medium", "weak")

#: 难度标签（E4 预注册 rubric，difficulty.py；1=易 2=中 3=难）
DIFFICULTY_LABELS: tuple[int, ...] = (1, 2, 3)

#: 一致性族类型（G1/audit-report E3：跨模块一致性判定；用于难度"微妙错误"特征）
CONSISTENCY_FAMILY: frozenset[str] = frozenset({
    "cluster_annotation_consistency",
    "annotation_deg_consistency",
    "annotation_validation",
    "trajectory_annotation_consistency",
    "trajectory_validation",
    "expression_survival_consistency",
    "immune_expression_consistency",
})

#: 微妙错误类型（错误隐蔽性：方法学细节/一致性，非直观错误）
SUBTLE_ERROR_TYPES: frozenset[str] = CONSISTENCY_FAMILY | frozenset({
    "clustering_resolution",
    "pca_dimension",
    "significance_threshold",
    "events_per_variable",
    "ic50_sample_size",
    "multiple_testing_correction",
})


class BenchmarkGeneratorInfo(BaseModel):
    """生成器信息（E6：生成器与评测 Agent 不同模型；模型信息进任务集元数据）。"""

    model: str                      # 生成用 LLM 标识（如 "deepseek-v4-flash"）
    prompt_version: str             # generator_prompt.md 的 sha256（前 12 位）
    transform_version: str          # 确定性变换脚本版本（generator.transform.v1）
    reviewed_by: str                # 人工审核署名
    reviewed_at: str                # 审核日期


class BenchmarkProvenance(TrajectoryProvenance):
    """任务 provenance（v2 轨迹 provenance 的 benchmark 扩展，不参与评分）。"""

    source: str = BENCHMARK_SOURCE
    base_trajectories: list[str] = Field(default_factory=list)
    """语料基础轨迹（错误注入素材的真实来源，E6：来自真实 Agent 运行语料）。"""

    error_pattern_sources: list[str] = Field(default_factory=list)
    """错误模式来源（语料轨迹 id 清单；错误注入不来自规则反推）。"""

    generator: Optional[BenchmarkGeneratorInfo] = None


class GoldLabel(BaseModel):
    """单条决策的 gold 标注（E3：共识强度记录）。"""

    step_id: str
    label: str                      # correct | error | edge
    consensus: str                  # strong | medium | weak

    @field_validator("label")
    @classmethod
    def _label_valid(cls, v: str) -> str:
        if v not in GOLD_LABELS:
            raise ValueError(f"gold label 必须为 {GOLD_LABELS}，收到 {v!r}")
        return v

    @field_validator("consensus")
    @classmethod
    def _consensus_valid(cls, v: str) -> str:
        if v not in CONSENSUS_STRENGTHS:
            raise ValueError(f"consensus 必须为 {CONSENSUS_STRENGTHS}，收到 {v!r}")
        return v


class GoldAnnotation(BaseModel):
    """任务级 gold（D1.1：每条任务带 gold 标注字段；D3.8：与三元组快照绑定）。"""

    version: str                    # 标注管线版本（annotation.v1）
    annotated_at: str               # ISO 时间戳
    irr: dict = Field(default_factory=dict)
    """标注批次 IRR（cohen_kappa / krippendorff_alpha / n_items / n_agreed）。"""

    labels: list[GoldLabel] = Field(min_length=1)


class DifficultyFeatures(BaseModel):
    """难度特征（E4：独立指标——决策数/隐藏错误数/一致性类型数；**不含审计分数**）。"""

    n_decisions: int
    n_errors: int                   # gold error 数
    n_edge: int                     # gold edge 数
    n_subtle_errors: int            # 微妙错误数（SUBTLE_ERROR_TYPES ∩ error）
    n_consistency_family: int       # 一致性族类型出现次数


class Difficulty(BaseModel):
    """难度标签（E4：预注册 rubric 的确定性输出；与审计分数无推导关系）。"""

    label: int                      # 1 | 2 | 3
    rubric_version: str             # difficulty.v1
    features: DifficultyFeatures
    note: str = "按预注册 rubric 由 gold 特征计算（E4：不使用审计分数定义难度）"

    @field_validator("label")
    @classmethod
    def _label_valid(cls, v: int) -> int:
        if v not in DIFFICULTY_LABELS:
            raise ValueError(f"difficulty label 必须为 {DIFFICULTY_LABELS}，收到 {v!r}")
        return v


class Task(BaseModel):
    """benchmark 任务：v2 轨迹 + 难度 + gold（D1.1 结构）。

    评分不变量：引擎只消费 ``decisions``（gold/difficulty/provenance 为元数据）。
    """

    version: int                    # 2（轨迹 schema；与 v2 对齐）
    trajectory_id: str
    act: str                        # deg | pan | scrna
    provenance: BenchmarkProvenance
    difficulty: Difficulty
    gold: GoldAnnotation
    decisions: list[Decision] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _version_supported(cls, v: int) -> int:
        if v != 2:
            raise ValueError(f"任务轨迹 version 必须为 2，收到 {v!r}")
        return v

    @field_validator("act")
    @classmethod
    def _act_valid(cls, v: str) -> str:
        if v not in VALID_PARADIGMS:
            raise ValueError(f"act 必须为 {sorted(VALID_PARADIGMS)} 之一，收到 {v!r}")
        return v


__all__ = [
    "TASKSET_SCHEMA_VERSION",
    "BENCHMARK_SOURCE",
    "GOLD_LABELS",
    "CONSENSUS_STRENGTHS",
    "DIFFICULTY_LABELS",
    "CONSISTENCY_FAMILY",
    "SUBTLE_ERROR_TYPES",
    "BenchmarkGeneratorInfo",
    "BenchmarkProvenance",
    "GoldLabel",
    "GoldAnnotation",
    "DifficultyFeatures",
    "Difficulty",
    "Task",
]
