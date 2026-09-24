"""C2 M3 产物解析器测试（窗口 C 验收项 5/6/7/8）。

- 5. signatures 驱动：notebook/代码 → 候选决策点（capture/signatures.yaml 映射表）
- 6. 上下文三级可信源（调用参数 > 数据元数据 > 环境声明）；缺失 → unverified，
      绝不正则猜数字（F6 禁猜）
- 7. provenance 逐决策记录：{来源: M3解析, 时间戳, 证据}
- 8. 旧 trajectory_capture 缺陷不复现：UMAP≠PCA 张冠李戴 / 分辨率数值不当方法名 /
      不伪造 n_patients/n_cells
- 补充：LLM 提名校验（设计 §五.2：只提名，不过本体校验即丢弃）
"""

import json
from pathlib import Path

from bioaudit.capture.m3_parser import M3Parser
from bioaudit.capture.models import (
    PROVENANCE_SOURCE_M3,
    TRUST_CALL_ARG,
    TRUST_DATA_METADATA,
    TRUST_DECLARED,
)
from bioaudit.capture.signatures import SignatureTable, validate_table

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_scrna_notebook.ipynb"
METADATA = {
    "n_cells": 63689,
    "n_patients": 10,
    "sequencing": "10X_scRNA_seq",
    "n_genes": 25655,
}


# ── 5. signatures 驱动 ──


def test_parse_notebook_signature_driven_candidates():
    """样例 notebook → 确定性候选（signatures 命中，禁猜）。"""
    result = M3Parser(act="scrna", metadata=METADATA).parse_notebook(FIXTURE)
    assert result.n_code_cells == 7
    by_type = {c.decision_type: c for c in result.candidates}
    assert by_type["qc_filtering"].choice == "hard_threshold"
    assert by_type["qc_filtering"].context["min_genes"] == 200  # 调用参数
    assert by_type["qc_filtering"].context["min_counts"] == 500
    assert by_type["scRNA_normalization"].choice == "LogNormalize"
    assert by_type["hv_gene_selection"].choice == "vst"
    assert by_type["hv_gene_selection"].context["n_top_genes"] == 2000
    assert by_type["pca_dimension"].choice == "heuristic_30_50"
    assert by_type["clustering_method"].choice == "Leiden"
    assert by_type["clustering_resolution"].choice == "default_0_8"
    assert by_type["clustering_resolution"].context["resolution"] == 0.8
    assert by_type["deg_method"].choice == "wilcoxon_rank_sum"


def test_parse_uncertain_never_guesses():
    """choice 无法确定性判定 → uncertain 列表（绝不猜）。"""
    result = M3Parser(act="scrna", metadata=METADATA).parse_notebook(FIXTURE)
    uncertain_types = {u.decision_type for u in result.uncertain}
    assert "dim_reduction" in uncertain_types  # pca 无 elbow 证据 + umap 投影
    assert "api_data_integrity" in uncertain_types  # 校验方式需声明
    # 所有候选都有确定性 choice（候选集不含猜测）
    for c in result.candidates:
        assert c.choice


def test_signatures_table_validates_against_ontology():
    """签名表校验：决策类型 ∈ 本体 34 类型；choice 提取方式互斥；模式可编译。"""
    report = validate_table(SignatureTable())
    assert report["ok"], report["errors"]
    assert report["n_types"] > 0
    assert report["n_types_with_signatures"] >= 15  # scRNA 主线 + bulk/pan 主要调用


# ── 6. 上下文三级可信源 + 禁猜 ──


def test_context_three_level_trust_sources():
    """调用参数 > 数据元数据 > 环境声明，逐键记录可信源。"""
    parser = M3Parser(
        act="scrna",
        metadata={"n_cells": 63689, "sequencing": "10X_scRNA_seq"},
        declared={"n_patients": 7},
    )
    result = parser.parse_code("sc.pp.filter_cells(adata, min_genes=200)")
    cand = result.candidates[0]
    assert cand.context_trust["min_genes"] == TRUST_CALL_ARG       # 一级
    assert cand.context_trust["n_cells"] == TRUST_DATA_METADATA    # 二级
    assert "n_patients" not in cand.context                         # 不在该类型 schema
    assert cand.context_trust["sequencing"] == TRUST_DATA_METADATA

    # 三级：deg_method 的 n_patients 来自 declared
    r2 = M3Parser(act="scrna", metadata={}, declared={"n_patients": 7}).parse_code(
        "sc.tl.rank_genes_groups(adata, method='wilcoxon')"
    )
    cand2 = r2.candidates[0]
    assert cand2.context["n_patients"] == 7
    assert cand2.context_trust["n_patients"] == TRUST_DECLARED


def test_missing_context_marked_unverified_not_fabricated():
    """任一级都提取不到 → 键标 unverified，绝不补默认数值（F6）。"""
    result = M3Parser(act="scrna").parse_code(
        "sc.pp.filter_cells(adata, min_genes=200)"  # 无任何元数据/声明
    )
    cand = result.candidates[0]
    assert cand.context["min_genes"] == 200  # 调用参数仍可信
    assert "sequencing" in cand.unverified_keys
    assert "n_cells" in cand.unverified_keys
    assert "min_cells" in cand.unverified_keys
    assert "sequencing" not in cand.context  # 未验证键不进 context
    assert "n_cells" not in cand.context


def test_no_fabricated_patient_cell_counts():
    """代码里提到患者数但无元数据 → 不伪造 n_patients（旧 bug: 默认 11）。"""
    code = (
        "# cohort: 11 patients, ~50k cells\n"
        "sc.tl.rank_genes_groups(adata, groupby='cell_type', method='wilcoxon')"
    )
    result = M3Parser(act="scrna").parse_code(code)
    cand = result.candidates[0]
    assert cand.decision_type == "deg_method"
    assert "n_patients" in cand.unverified_keys  # 显式 unverified
    assert cand.context.get("n_patients") is None  # 不猜 11
    assert "11" not in json.dumps(cand.context)


def test_metadata_is_legit_source_for_numbers():
    """数据元数据是数字的合法来源（与禁猜不冲突）。"""
    result = M3Parser(act="scrna", metadata={"n_patients": 11}).parse_code(
        "sc.tl.rank_genes_groups(adata, method='wilcoxon')"
    )
    cand = result.candidates[0]
    assert cand.context["n_patients"] == 11
    assert cand.context_trust["n_patients"] == TRUST_DATA_METADATA


# ── 7. provenance 逐决策记录 ──


def test_provenance_per_decision():
    result = M3Parser(act="scrna", metadata=METADATA).parse_notebook(FIXTURE)
    for cand in result.candidates:
        assert cand.provenance.source == PROVENANCE_SOURCE_M3
        assert cand.provenance.timestamp
        assert "notebook cell #" in cand.provenance.evidence
        assert cand.provenance.detail["cell_index"] >= 0
    assert any(c.provenance.evidence.startswith("notebook cell #3") for c in result.candidates)


# ── 8. 旧缺陷不复现 ──


def test_umap_not_mapped_to_pca():
    """旧 bug：sc.tl.umap → choice 'PCA'（张冠李戴）。现在 umap 是投影，choice 未定。"""
    result = M3Parser(act="scrna").parse_code("sc.tl.umap(adata)")
    assert not result.candidates  # umap 不产生可审计候选
    assert any(
        u.decision_type == "dim_reduction" and "UMAP" in u.reason
        for u in result.uncertain
    )
    # 全量断言：任何候选的 choice 都不得是 'PCA'（除非来自 pca 调用上下文）
    for c in result.candidates:
        assert not (c.decision_type == "dim_reduction" and c.choice == "PCA")


def test_resolution_value_not_used_as_method_name():
    """旧 bug：resolution 数值被当方法名。现在数值进 context，choice 走词表映射。"""
    result = M3Parser(act="scrna").parse_code(
        "sc.tl.leiden(adata, resolution=1.0)"
    )
    # clustering_method 仍正常产出 Leiden（方法 ≠ 分辨率）；分辨率决策 1.0 不在
    # 词表（0.8 → default_0_8）→ 未定候选，但数值保留在 partial_context
    methods = [c for c in result.candidates if c.decision_type == "clustering_method"]
    assert [c.choice for c in methods] == ["Leiden"]
    resolutions = [c for c in result.candidates if c.decision_type == "clustering_resolution"]
    assert resolutions == []
    u = next(u for u in result.uncertain if u.decision_type == "clustering_resolution")
    assert u.partial_context.get("resolution") == 1.0
    # 0.8 → 词表映射 default_0_8；数值仍是 context 而非 choice
    r2 = M3Parser(act="scrna").parse_code("sc.tl.leiden(adata, resolution=0.8)")
    cand = next(
        c for c in r2.candidates if c.decision_type == "clustering_resolution"
    )
    assert cand.choice == "default_0_8"
    assert cand.context["resolution"] == 0.8
    assert not cand.choice.isdigit()  # choice 不是裸数值


def test_non_literal_kwarg_choice_ranges_uncertain_not_crash():
    """窗口 I 实测缺陷（2026-08-16）：choice_ranges 收到非字面量 kwarg
    （如 n_comps=n_comps 变量间接）时此前直接 TypeError 崩溃——
    违反禁猜（F6）设计（无法确定性取值 → uncertain，绝不崩溃）。"""
    code = (
        "n_comps = 20\n"
        "sc.pp.pca(adata, n_comps=n_comps, random_state=42)\n"
        "sc.tl.leiden(adata, resolution=res, flavor='igraph')\n"
    )
    result = M3Parser(act="scrna").parse_code(code)
    # 不崩溃；pca_dimension 无法确定性判定 → uncertain（不猜、不产出候选）
    assert not [c for c in result.candidates if c.decision_type == "pca_dimension"]
    assert any(
        u.decision_type == "pca_dimension"
        for u in result.uncertain
    )
    # 字面量路径行为不变（0.8 → default_0_8）
    r2 = M3Parser(act="scrna").parse_code("sc.tl.leiden(adata, resolution=0.8)")
    cand = next(
        c for c in r2.candidates if c.decision_type == "clustering_resolution"
    )
    assert cand.choice == "default_0_8"
    # 变量分辨率 → clustering_resolution uncertain（不崩溃）
    r3 = M3Parser(act="scrna").parse_code("res = 1.0\nsc.tl.leiden(adata, resolution=res)")
    assert not [c for c in r3.candidates if c.decision_type == "clustering_resolution"]
    assert any(
        u.decision_type == "clustering_resolution"
        for u in r3.uncertain
    )


def test_choice_table_maps_threshold_vocabulary():
    """significance_threshold：padj+logFC 组合 → 规则词表 choice（M1.3）。"""
    code = "deg = df[(df.padj < 0.05) & (df.logFC.abs() > 1.0)]"
    result = M3Parser(act="deg").parse_code(code)
    cand = result.candidates[0]
    assert cand.decision_type == "significance_threshold"
    assert cand.choice == "padj <= 0.05, |logFC| >= 1.0"
    assert cand.context["padj_cutoff"] == 0.05
    assert cand.context["logfc_cutoff"] == 1.0


def test_choice_table_out_of_vocabulary_uncertain():
    code = "deg = df[(df.padj < 0.2) & (df.logFC.abs() > 1.0)]"
    result = M3Parser(act="deg").parse_code(code)
    assert result.candidates == []  # 0.2 组合不在词表 → 不猜测
    assert result.uncertain


# ── LLM 辅助模糊发现（设计 §五.2：只提名，本体校验才算数）──


def test_llm_nominations_validated_against_ontology_schema():
    parser = M3Parser(act="scrna")
    nominations = [
        {"step_id": "s1", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"sequencing": "10X_scRNA_seq", "n_patients": 3}},
        {"step_id": "s2", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"sequencing": "banana_sequencing"}},        # 枚举外值 → 拒绝
        {"step_id": "s3", "decision_type": "not_a_real_type",
         "choice": "x", "context": {}},                          # 未知类型 → 拒绝
        {"step_id": "s4", "decision_type": "deg_method", "choice": "DESeq2",
         "context": {"n_replicates": "six"}},                    # 类型错误 → 拒绝
    ]
    out = parser.validate_nominations(nominations)
    assert len(out["accepted"]) == 1
    assert out["accepted"][0]["step_id"] == "s1"
    assert len(out["rejected"]) == 3
    reasons = " ".join(r["reason"] for r in out["rejected"])
    assert "枚举" in reasons and "34 类型" in reasons and "int" in reasons


def test_llm_nominations_do_not_shortcut_engine():
    """提名只校验 schema，不产生评分（LLM 不定案）。"""
    out = M3Parser(act="scrna").validate_nominations([
        {"step_id": "s1", "decision_type": "qc_filtering",
         "choice": "MAD5_adaptive_threshold", "context": {"sequencing": "10X_scRNA_seq"}},
    ])
    assert out["accepted"][0]["choice"] == "MAD5_adaptive_threshold"
    assert "score" not in out["accepted"][0]
    assert "level" not in out["accepted"][0]
