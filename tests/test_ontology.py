"""本体测试套件（B2 验收核心）。

覆盖：
1. 34 定义文件完整可加载（必填键 / 唯一 ID / context_schema 结构）
2. ★ 聚合维度等价性守卫：本体 dimension == 旧 TYPE_TO_DIMENSION 硬编码（34 类型逐一）
   ——这是"本体化不漂移"的静态守卫；动态守卫是 tests/test_golden.py 0 差异
3. depends_on ⊇ 旧 dependency_graph.yaml（DEG 3 条边逐条保留）
4. aliases 3 组（对称性 + deg_method 仅同源注释）
5. A4 design→fail-closed / G3 unit / G4 confound / G5 when_not_applicable
6. P1 校验器三职责：覆盖报告 / 语义边界（含合成违例检出）/ 冲突完整性（真实冲突检出）
7. 引擎接线：matcher 读本体 input_synonyms（行为不变）+ 同源注释 + unclassified；
   error_tracer 读 depends_on（含 scRNA 新增边）；aggregator 读 dimension
"""

import json
from pathlib import Path

import pytest
import yaml

from bioaudit.engine.aggregator import ScoreAggregator
from bioaudit.engine.error_tracer import ErrorPropagationTracer
from bioaudit.engine.matcher import RuleMatcher
from bioaudit.models.decision import Decision
from bioaudit.models.score import DecisionScore
from bioaudit.ontology.loader import Ontology, OntologyError, get_ontology
from bioaudit.ontology.validator import validate
from bioaudit.paths import MAPPINGS_DIR, ONTOLOGY_DIR, RULES_DIR
from bioaudit.storage.rule_registry import RuleRegistry


# ── 冻结参考：B2 之前的 TYPE_TO_DIMENSION 硬编码（聚合行为等价性基准）──
LEGACY_TYPE_TO_DIMENSION = {
    "filtering": "data_handling", "normalization": "data_handling",
    "deg_method": "method_selection", "multiple_testing_correction": "statistical_rigor",
    "significance_threshold": "statistical_rigor",
    "cox_ph_assumption": "statistical_rigor", "independent_prognostic_claim": "statistical_rigor",
    "events_per_variable": "statistical_rigor",
    "cbioportal_projection": "data_handling", "immune_correlation_method": "method_selection",
    "purity_confounding": "data_handling", "gsea_background": "data_handling",
    "enrichment_correction": "statistical_rigor", "ic50_sample_size": "statistical_rigor",
    "expression_survival_consistency": "statistical_rigor",
    "immune_expression_consistency": "statistical_rigor",
    "api_data_integrity": "data_handling", "qc_filtering": "data_handling",
    "qc_mito_threshold": "data_handling", "doublet_detection": "data_handling",
    "scRNA_normalization": "data_handling", "hv_gene_selection": "method_selection",
    "batch_correction": "data_handling", "dim_reduction": "method_selection",
    "pca_dimension": "method_selection", "clustering_method": "method_selection",
    "clustering_resolution": "method_selection", "annotation_method": "method_selection",
    "annotation_validation": "method_selection", "trajectory_inference": "method_selection",
    "trajectory_validation": "method_selection",
    "cluster_annotation_consistency": "method_selection",
    "annotation_deg_consistency": "method_selection",
    "trajectory_annotation_consistency": "method_selection",
}

LEGACY_DEP_GRAPH = {
    "deg_method": ["normalization", "filtering"],
    "multiple_testing_correction": ["deg_method"],
    "significance_threshold": ["deg_method", "multiple_testing_correction"],
}


@pytest.fixture(scope="module")
def ont() -> Ontology:
    return get_ontology()


# ══ 1. 34 定义文件完整 ══

def test_ontology_loads_34_types(ont):
    assert len(ont.types) == 34
    assert len(set(ont.types)) == 34


def test_all_types_have_required_keys(ont):
    for tid, t in ont.types.items():
        for key in ("decision_type", "display", "stage", "paradigms",
                    "dimension", "optional", "depends_on", "context_schema"):
            assert key in t, f"{tid} 缺 {key}"
        assert t["display"]["cn"] and t["display"]["en"], f"{tid} display 不完整"
        assert t["context_schema"], f"{tid} context_schema 为空"


def test_context_schema_keys_valid(ont):
    for tid, t in ont.types.items():
        seen = set()
        for item in t["context_schema"]:
            assert item["missing"] in {"fail-closed", "skip", "fail-open"}, \
                f"{tid}/{item['key']} missing 档位非法"
            assert item["key"] not in seen, f"{tid} context_schema 键重复 {item['key']}"
            seen.add(item["key"])
            if item["type"] == "enum":
                assert item["values"], f"{tid}/{item['key']} enum values 为空"
            if item["type"] == "int":
                assert "min" in item, f"{tid}/{item['key']} int 缺 min"


def test_three_missing_tiers_all_used(ont):
    tiers = [i["missing"] for t in ont.types.values() for i in t["context_schema"]]
    assert set(tiers) == {"fail-closed", "skip", "fail-open"}, \
        f"missing 三档应全部使用，实际 {set(tiers)}"


# ══ 2. ★ 聚合维度等价性（本体化不漂移的静态守卫）══

def test_dimension_matches_legacy_hardcode(ont):
    assert set(ont.types) == set(LEGACY_TYPE_TO_DIMENSION)
    for tid, legacy_dim in LEGACY_TYPE_TO_DIMENSION.items():
        assert ont.dimension(tid) == legacy_dim, \
            f"{tid}: 本体 {ont.dimension(tid)!r} != 旧硬编码 {legacy_dim!r}"


def test_aggregator_uses_ontology_dimension(ont):
    """aggregator 不再持有 TYPE_TO_DIMENSION 硬编码，dimension 全部来自本体。"""
    import inspect
    src = inspect.getsource(ScoreAggregator)
    # 硬编码映射的特征字面量（"filtering": "data_handling" 等）不得出现在代码中
    for literal in ('"filtering": "data_handling"',
                    "'filtering': 'data_handling'",
                    "TYPE_TO_DIMENSION ="):
        assert literal not in src, f"聚合器仍含硬编码维度映射: {literal!r}"

    agg = ScoreAggregator(ontology=ont)
    assert not hasattr(agg, "TYPE_TO_DIMENSION")
    scores = [
        DecisionScore(step_id="s1", decision_type="filtering", agent_choice="x",
                      agent_rationale="", level=3, numeric_score=0.85,
                      explanation="", reward_signal=0.85),
        DecisionScore(step_id="s2", decision_type="deg_method", agent_choice="x",
                      agent_rationale="", level=2, numeric_score=0.6,
                      explanation="", reward_signal=0.6),
    ]
    result = agg.aggregate(scores)
    assert result.dimension_scores == {
        "data_handling": 0.85, "method_selection": 0.6,
    }


# ══ 3. depends_on ⊇ 旧依赖图 ══

def test_depends_on_superset_of_legacy_graph(ont):
    new_graph = ont.dep_graph()
    for downstream, upstreams in LEGACY_DEP_GRAPH.items():
        assert downstream in new_graph, f"本体缺类型 {downstream}"
        for up in upstreams:
            assert up in new_graph[downstream], \
                f"旧依赖边丢失: {downstream} -> {up}"


def test_error_tracer_reads_ontology_depends_on(ont):
    tracer = ErrorPropagationTracer()  # 默认 = 本体 dep_graph
    assert tracer.dep_graph["deg_method"] == \
        ont.depends_on("deg_method")
    # 旧文件仍可显式加载（兼容路径）
    legacy = ErrorPropagationTracer(MAPPINGS_DIR / "dependency_graph.yaml")
    assert legacy.dep_graph == LEGACY_DEP_GRAPH

    # scRNA 新增边：scRNA_normalization 错误 → 影响 deg_method（旧图无此边）
    scores = [
        DecisionScore(step_id="q1", decision_type="scRNA_normalization",
                      agent_choice="no_normalization", agent_rationale="",
                      level=0, numeric_score=0.0, explanation="危险", reward_signal=0.0),
        DecisionScore(step_id="d1", decision_type="deg_method",
                      agent_choice="MAST", agent_rationale="",
                      level=3, numeric_score=0.85, explanation="正确", reward_signal=0.85),
    ]
    chains = tracer.trace(scores)
    assert any(c.source_step == "q1" and "d1" in c.affected_steps for c in chains)


# ══ 4. aliases 3 组 ══

def test_alias_homology_groups(ont):
    assert ont.aliases_for("filtering") == ["qc_filtering"]
    assert ont.aliases_for("qc_filtering") == ["filtering"]
    assert ont.aliases_for("normalization") == ["scRNA_normalization"]
    assert ont.aliases_for("scRNA_normalization") == ["normalization"]
    assert ont.aliases_for("deg_method") == []
    # 对称性：双向声明
    for tid, hom in ((t, ont.aliases_for(t)) for t in ont.types):
        for other in hom:
            assert tid in ont.aliases_for(other), f"同源不对称: {tid}<->{other}"


def test_deg_method_homology_note(ont):
    t = ont.types["deg_method"]
    assert "homology_note" in t
    assert "不声明 aliases" in t["homology_note"]
    assert ont.aliases["deg_method"]["homologous"] is False
    groups = {g["group_id"] for g in ont.aliases["homology_groups"]}
    assert groups == {"filtering-qc_filtering", "normalization-scRNA_normalization"}


def test_aliases_not_matching_channel(ont):
    """同源声明不是匹配通道：qc_filtering 不会归一化为 filtering（防 scRNA 规则失配）。"""
    matcher = RuleMatcher(RuleRegistry(RULES_DIR / "scRNA"))
    parsed, _ = matcher.match(Decision(
        step_id="s1", decision_type="qc_filtering", choice="hard_threshold",
        context={"sequencing": "10X_scRNA_seq"},
    ))
    assert parsed.decision_type == "qc_filtering"


# ══ 5. A4 / G3 / G4 / G5 ══

def test_a4_design_fail_closed(ont):
    deg = ont.types["deg_method"]
    design = next(i for i in deg["context_schema"] if i["key"] == "design")
    assert design["missing"] == "fail-closed", "A4 裁决：design 键必须 fail-closed"


def test_g3_unit_key(ont):
    for tid in ("deg_method", "scRNA_normalization", "batch_correction", "doublet_detection"):
        t = ont.types[tid]
        unit = next((i for i in t["context_schema"] if i["key"] == "unit"), None)
        assert unit is not None, f"G3: {tid} 缺 unit 键"
        assert unit["missing"] != "fail-open", f"G3: {tid} unit 不能 fail-open"


def test_g4_confound_key(ont):
    bc = ont.types["batch_correction"]
    confound = next((i for i in bc["context_schema"] if i["key"] == "confound"), None)
    assert confound is not None, "G4: batch_correction 缺 confound 键"


def test_g5_optional_requires_when_not_applicable(ont):
    for tid, t in ont.types.items():
        if t.get("optional"):
            assert t.get("when_not_applicable"), f"G5: {tid} optional 但缺 when_not_applicable"
    # 无 optional 却声明谓词 → 校验器给警告
    report = validate(ont)
    g5_warnings = [w for w in report["warnings"]
                   if w["kind"] == "g5_when_not_applicable_without_optional"]
    assert g5_warnings == []


def test_optional_types_include_batch_correction_and_consistency(ont):
    assert ont.optional_of("batch_correction") is True
    for tid in ("expression_survival_consistency", "immune_expression_consistency",
                "cluster_annotation_consistency", "annotation_deg_consistency",
                "trajectory_annotation_consistency"):
        assert ont.optional_of(tid) is True
        assert ont.when_not_applicable_of(tid) == "claim_not_made"


def test_internal_ref_g1(ont):
    """G1：一致性族 5 成员全部带 internal_ref，且引用存在。"""
    for tid in ("expression_survival_consistency", "immune_expression_consistency",
                "cluster_annotation_consistency", "annotation_deg_consistency",
                "trajectory_annotation_consistency"):
        refs = ont.types[tid].get("internal_ref", [])
        assert refs, f"G1: {tid} 缺 internal_ref"
        for r in refs:
            assert r in ont.types, f"G1: {tid} internal_ref 悬空 {r}"


# ══ 6. P1 校验器三职责 ══

def test_validator_coverage_report(ont):
    report = validate(ont)
    cov = report["coverage"]
    assert cov["n_types"] == 34
    assert cov["rule_types_all_known"] is True
    assert cov["types_without_rules"] == []
    assert len(cov["paradigm_matrix"]) == 3
    # bulk-DEG 骨架待补全 → 警告而非错误
    skeleton = [w for w in report["warnings"] if w["kind"] == "skeleton_paradigm"]
    assert skeleton and skeleton[0]["paradigm"] == "bulk-DEG"
    # 待补清单（G2/G6）
    assert any(b["id"].startswith("G6-") for b in cov["backlog"])


def test_validator_semantic_boundaries(ont):
    report = validate(ont)
    sem = report["semantic_boundaries"]
    usage = sem["missing_tier_usage"]
    assert usage["fail-closed"] > 0 and usage["skip"] > 0 and usage["fail-open"] > 0
    # 真实规则集无 A2 违例（fail-open 键未被罚分规则引用）
    assert sem["a2_violations"] == []
    assert report["ok"] is True


def test_validator_conflict_completeness(ont):
    """D2 冲突完整性（B5 裁决后）：现存 2 处冲突已裁决归零。

    - deg_method/MAST：G1.3 已修订（裸 MAST 移至 L1 与 G1.1 对齐）→ 同规则集内无冲突
    - multiple_testing_correction/bonferroni：范式感知检测（G1.2 scRNA vs
      M1.2 bulk 分属不同规则集，运行时互不命中）→ 不再报告
    裁决文档：docs/specs/2026-08-14-d2-adjudication.md
    """
    report = validate(ont)
    assert report["conflicts"]["n_conflicts"] == 0
    assert report["conflicts"]["scope"] == "same-rule-set"


def _make_rule(rule_id: str, decision_type: str, choice: str, level: int) -> dict:
    """构造一条最小规则（choice 落在指定 level）。"""
    levels = {4: [], 3: [], 2: [], 1: [], 0: []}
    levels[level] = [choice]
    return {
        "rule_id": rule_id, "domain": "test", "status": "active",
        "title": "t", "description": "t",
        "condition": {"decision_type": decision_type, "required_context": {}},
        "scoring": {f"level_{k}": {"methods": v} for k, v in levels.items()},
    }


def test_validator_detects_same_rule_set_conflict(tmp_path):
    """合成用例：同一规则集内同 decision_type + choice 不同 level → 必须检出。"""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "a.yaml").write_text(
        yaml.safe_dump(_make_rule("TST-A-001", "deg_method", "MAST", 1)),
        encoding="utf-8",
    )
    (rules_dir / "b.yaml").write_text(
        yaml.safe_dump(_make_rule("TST-B-001", "deg_method", "MAST", 2)),
        encoding="utf-8",
    )
    report = validate(get_ontology(), rules_dir=rules_dir)
    conflicts = report["conflicts"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["decision_type"] == "deg_method"
    assert conflicts[0]["choice"] == "mast"
    assert {e["level"] for e in conflicts[0]["entries"]} == {1, 2}
    assert conflicts[0]["rule_set"] == "."  # 平铺目录 = 同一规则集


def test_validator_ignores_cross_rule_set_conflict(tmp_path):
    """合成用例：不同规则集（子目录 A/B）同 choice 不同 level → 不报冲突（范式隔离）。"""
    rules_dir = tmp_path / "rules"
    (rules_dir / "A").mkdir(parents=True)
    (rules_dir / "B").mkdir()
    (rules_dir / "A" / "a.yaml").write_text(
        yaml.safe_dump(_make_rule("TST-A-001", "multiple_testing_correction",
                                  "bonferroni", 2)),
        encoding="utf-8",
    )
    (rules_dir / "B" / "b.yaml").write_text(
        yaml.safe_dump(_make_rule("TST-B-001", "multiple_testing_correction",
                                  "bonferroni", 1)),
        encoding="utf-8",
    )
    report = validate(get_ontology(), rules_dir=rules_dir)
    assert report["conflicts"]["n_conflicts"] == 0


def test_validator_detects_synthetic_a2_violation(tmp_path, ont):
    """A2 合成违例：把某类型某键改为 fail-open 且被罚分规则引用 → 必须报错。"""
    # 用最小规则触发：罚分规则引用 fail-open 键
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    rule = {
        "rule_id": "TST-A2-001", "domain": "test", "status": "active",
        "title": "t", "description": "t",
        "condition": {"decision_type": "filtering",
                      "required_context": {"sequencing": "bulk_RNA_seq"}},
        "scoring": {
            "level_4": {"methods": []}, "level_3": {"methods": []},
            "level_2": {"methods": []},
            "level_1": {"methods": ["no_filtering"]},
            "level_0": {"methods": []},
        },
    }
    (rules_dir / "tst.yaml").write_text(yaml.safe_dump(rule), encoding="utf-8")

    # 本体副本：filtering 的 sequencing 键改为 fail-open
    ont_dir = tmp_path / "ontology"
    (ont_dir / "decision_types").mkdir(parents=True)
    for f in (ONTOLOGY_DIR / "decision_types").glob("*.yaml"):
        (ont_dir / "decision_types" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    filtering = ont_dir / "decision_types" / "filtering.yaml"
    data = yaml.safe_load(filtering.read_text(encoding="utf-8"))
    for item in data["context_schema"]:
        if item["key"] == "sequencing":
            item["missing"] = "fail-open"
    filtering.write_text(yaml.safe_dump(data), encoding="utf-8")
    for name in ("paradigms.yaml", "stages.yaml", "aliases.yaml",
                 "input_synonyms.yaml", "topics.yaml", "backlog.yaml"):
        src = ONTOLOGY_DIR / name
        (ont_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    report = validate(Ontology(ont_dir), rules_dir=rules_dir)
    a2 = [e for e in report["errors"] if e["kind"] == "a2_penalty_rule_fail_open_key"]
    assert a2, "A2 违例未被检出"
    assert a2[0]["violations"][0]["key"] == "sequencing"


def test_validator_detects_synthetic_g5_violation(ont):
    """G5 合成违例：构造 optional 而无 when_not_applicable 的类型文件 → loader/校验报错。"""
    from copy import deepcopy
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ont_dir = Path(tmp) / "ontology"
        (ont_dir / "decision_types").mkdir(parents=True)
        for f in (ONTOLOGY_DIR / "decision_types").glob("*.yaml"):
            (ont_dir / "decision_types" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        for name in ("paradigms.yaml", "stages.yaml", "aliases.yaml",
                     "input_synonyms.yaml", "topics.yaml", "backlog.yaml"):
            src = ONTOLOGY_DIR / name
            (ont_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        # 把 batch_correction 的 when_not_applicable 去掉
        bc = ont_dir / "decision_types" / "batch_correction.yaml"
        data = yaml.safe_load(bc.read_text(encoding="utf-8"))
        del data["when_not_applicable"]
        bc.write_text(yaml.safe_dump(data), encoding="utf-8")

        report = validate(Ontology(ont_dir))
        g5 = [e for e in report["errors"]
              if e["kind"] == "g5_optional_requires_when_not_applicable"]
        assert g5 and g5[0]["type"] == "batch_correction"


def test_validator_rejects_bad_missing_tier():
    """非法 missing 档位 → 加载期硬错误（语义边界第一道闸）。"""
    from copy import deepcopy
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ont_dir = Path(tmp) / "ontology"
        (ont_dir / "decision_types").mkdir(parents=True)
        for f in (ONTOLOGY_DIR / "decision_types").glob("*.yaml"):
            (ont_dir / "decision_types" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        for name in ("paradigms.yaml", "stages.yaml", "aliases.yaml",
                     "input_synonyms.yaml", "topics.yaml", "backlog.yaml"):
            src = ONTOLOGY_DIR / name
            (ont_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        f = ont_dir / "decision_types" / "filtering.yaml"
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        data["context_schema"][0]["missing"] = "fail-openly"
        f.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(OntologyError):
            Ontology(ont_dir).types  # 触发加载


# ══ 7. 引擎接线 ══

def test_matcher_reads_ontology_input_synonyms():
    """matcher 默认从本体读同义映射（与旧 type_aliases.yaml 条目一致，行为不变）。"""
    matcher = RuleMatcher(RuleRegistry(RULES_DIR / "DEG"))
    for raw, canonical in {
        "filter": "filtering", "norm": "normalization", "deg": "deg_method",
        "padj": "multiple_testing_correction", "threshold": "significance_threshold",
    }.items():
        parsed, _ = matcher.match(Decision(
            step_id="s1", decision_type=raw, choice="x", context={},
        ))
        assert parsed.decision_type == canonical, f"{raw} → {canonical}"


def test_matcher_homology_annotation_and_unclassified():
    matcher = RuleMatcher(RuleRegistry(RULES_DIR / "DEG"))
    parsed, _ = matcher.match(Decision(
        step_id="s1", decision_type="filtering", choice="filterByExpr", context={},
    ))
    assert parsed.homologous_types == ["qc_filtering"]
    assert parsed.unclassified is False

    unknown, _ = matcher.match(Decision(
        step_id="s2", decision_type="no_such_type", choice="x", context={},
    ))
    assert unknown.unclassified is True
    assert unknown.homologous_types == []


def test_golden_still_zero_diff():
    """B2 动态回归守卫：本体化后 137 决策仍 0 差异（与 tests/test_golden.py 一致）。"""
    from bioaudit.regression import replay_all
    expected = json.loads(
        (Path(__file__).resolve().parent / "golden" / "golden_expected_output_after.json")
        .read_text(encoding="utf-8")
    )
    actual = replay_all()
    assert actual["n_decisions"] == expected["n_decisions"] == 137
    exp_steps = {(t["trajectory"], s["step_id"]): s
                 for t in expected["trajectories"] for s in t["step_scores"]}
    act_steps = {(t["trajectory"], s["step_id"]): s
                 for t in actual["trajectories"] for s in t["step_scores"]}
    assert exp_steps == act_steps, "137 决策中存在评分漂移"
