"""benchmark 包测试（窗口 D：阶段 3 基准；D6.15 新增测试）。

覆盖：任务 schema / E4 难度独立性 / E1 预注册协议 / E2 污染扫描 /
E5 覆盖审计 / D4 运行器与功效 / E8 任务集清单 / E6 生成器纪律。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_benchmark_tasks import SPECS  # noqa: E402

from bioaudit.benchmark.annotation import merge_annotations  # noqa: E402
from bioaudit.benchmark.contamination import (  # noqa: E402
    collect_rule_fragments,
    scan_dir,
    scan_file,
    scan_text,
)
from bioaudit.benchmark.coverage import audit as coverage_audit  # noqa: E402
from bioaudit.benchmark.difficulty import (  # noqa: E402
    assign_difficulty,
    difficulty_from_features,
)
from bioaudit.benchmark.generator import apply_spec, load_corpus, prompt_hash  # noqa: E402
from bioaudit.benchmark.manifest import (  # noqa: E402
    TasksetError,
    load_tasks,
    load_taskset,
    validate_taskset,
)
from bioaudit.benchmark.models import (  # noqa: E402
    CONSISTENCY_FAMILY,
    DifficultyFeatures,
    Task,
)
from bioaudit.benchmark.paths import GENERATOR_PROMPT, TASKS_DIR  # noqa: E402
from bioaudit.benchmark.protocol import (  # noqa: E402
    PRE_REGISTRATION,
    assign_split,
    check_gap,
)
from bioaudit.benchmark.runner import (  # noqa: E402
    aggregate,
    audit_task,
    holm_bonferroni,
    run_benchmark,
)

# ── 任务 schema（D1.1：v2 轨迹 + gold 标注字段）───────────────────────────────


def test_all_shipped_tasks_parse_and_have_gold(tmp_path):
    """任务集全部通过 Task schema（gold+difficulty+benchmark provenance 在场）。"""
    tasks = load_tasks(TASKS_DIR)
    assert len(tasks) >= 60, "任务集 ≥60 条（F1.1：批 1 30 + 批 2 30）"
    for t in tasks:
        task = Task(**t)
        assert task.provenance.source == "benchmark"
        assert task.provenance.generator is not None, "E6：生成器模型信息必须记录"
        assert task.difficulty.label in (1, 2, 3)
        assert len(task.gold.labels) == len(task.decisions), "gold 覆盖全部决策"


def test_task_extra_meta_does_not_break_engine(tmp_path):
    """gold/difficulty 是元数据：run_audit 只消费 decisions（评分路径保护）。"""
    from bioaudit.api import run_audit

    tasks = load_tasks(TASKS_DIR)
    r = run_audit(tasks[0], act=tasks[0]["act"])
    assert r.get("error") is None


# ── E4：难度独立量化（不用审计分数定义难度）──────────────────────────────────


def test_difficulty_rubric_deterministic():
    f = DifficultyFeatures(n_decisions=12, n_errors=2, n_edge=0,
                           n_subtle_errors=0, n_consistency_family=0)
    assert difficulty_from_features(f) == 2
    f_easy = DifficultyFeatures(n_decisions=8, n_errors=0, n_edge=1,
                                n_subtle_errors=0, n_consistency_family=0)
    assert difficulty_from_features(f_easy) == 1
    f_hard = DifficultyFeatures(n_decisions=17, n_errors=1, n_edge=0,
                                n_subtle_errors=0, n_consistency_family=0)
    assert difficulty_from_features(f_hard) == 3
    f_hard2 = DifficultyFeatures(n_decisions=5, n_errors=3, n_edge=0,
                                 n_subtle_errors=0, n_consistency_family=0)
    assert difficulty_from_features(f_hard2) == 3
    f_hard3 = DifficultyFeatures(n_decisions=10, n_errors=2, n_edge=0,
                                 n_subtle_errors=2, n_consistency_family=2)
    assert difficulty_from_features(f_hard3) == 3


def test_difficulty_independent_of_audit_score():
    """E4 守卫：难度只由 gold 特征 + 类型映射计算；不得引用引擎分数。"""
    import inspect

    src = inspect.getsource(assign_difficulty)
    for forbidden in ("run_audit", "trajectory_score", "audit_decision",
                      "RuleEvaluator", "LEVEL_TO_SCORE"):
        assert forbidden not in src, f"难度计算引用审计分数路径: {forbidden}"


def test_consistency_family_types_exist():
    for t in CONSISTENCY_FAMILY:
        assert (TASKS_DIR.parent.parent / "ontology" / "decision_types" /
                f"{t}.yaml").exists(), f"一致性族类型不在本体: {t}"


# ── E1：预注册协议（split + gap 容忍区间）─────────────────────────────────────


def test_pre_registration_record_frozen():
    rec = PRE_REGISTRATION
    assert rec["record_id"] == "benchmark-pr-2026-08-16-02"
    assert rec["supersedes"] == "benchmark-pr-2026-08-16-01"
    assert rec["gap"]["tolerance_interval"] == [-0.10, 0.10]
    assert rec["irr_gate"]["primary"] == "cohen_kappa_3class >= 0.8"
    assert rec["split"]["seed"] == 42
    assert rec["annotation_rubric_version"] == "annotation.v1.1"


def test_pre_registration_v1_archived():
    """批 1 预注册旧值留档（E1：不覆盖旧记录，v1 常量 + 磁盘副本）。"""
    from bioaudit.benchmark.protocol import PRE_REGISTRATION_V1

    assert PRE_REGISTRATION_V1["record_id"] == "benchmark-pr-2026-08-16-01"
    assert PRE_REGISTRATION_V1["gap"]["tolerance_interval"] == [-0.10, 0.10]
    from bioaudit.benchmark.protocol import pre_registration_v1_archive_path

    p = pre_registration_v1_archive_path()
    assert p.exists(), "批 1 预注册留档文件必须存在"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["record_id"] == "benchmark-pr-2026-08-16-01"


def test_check_gap_alarm_logic():
    # 超出区间 → 负向告警
    r = check_gap([0.9, 0.85, 0.88], [0.3, 0.4, 0.35])
    assert r["alarm"] is True
    assert r["delta"] > 0.10
    # 区间内 → 不告警
    r2 = check_gap([0.8, 0.82], [0.78, 0.79])
    assert r2["alarm"] is False
    # 空侧 → 数据缺口，不告警
    r3 = check_gap([], [0.8])
    assert r3["alarm"] is False


def test_assign_split_deterministic_and_complete():
    tasks = [{"trajectory_id": f"t{i}", "act": a, "difficulty": {"label": d}}
             for i, (a, d) in enumerate([
                 ("scrna", 1), ("scrna", 1), ("scrna", 2), ("scrna", 2), ("scrna", 3),
                 ("scrna", 3),
                 ("pan", 1), ("pan", 1), ("pan", 2), ("pan", 2), ("pan", 3), ("pan", 3),
                 ("deg", 1), ("deg", 1), ("deg", 2), ("deg", 2), ("deg", 3), ("deg", 3)])]
    s1 = assign_split(tasks)
    s2 = assign_split(tasks)
    assert s1 == s2, "同输入必须同划分（可复现）"
    assert set(s1) == {t["trajectory_id"] for t in tasks}
    assert len(set(s1.values())) == 2
    pub = [k for k, v in s1.items() if v == "public"]
    hid = [k for k, v in s1.items() if v == "hidden"]
    assert hid, "隐藏集不可为空"
    assert pub, "公开集不可为空"
    # 每层（act, difficulty）两侧都有（n≥2 的层保证）
    for (a, d), ids in sorted({(t["act"], t["difficulty"]["label"]): [] for t in tasks}.items()):
        layer = [t for t in tasks if t["act"] == a and t["difficulty"]["label"] == d]
        assert len(layer) == 2
        layer_split = {s1[t["trajectory_id"]] for t in layer}
        assert layer_split == {"public", "hidden"}, "n≥2 层必须两侧都有"


# ── E2：规则字符串污染扫描（黑盒）────────────────────────────────────────────


def test_contamination_detects_rule_content():
    frag = collect_rule_fragments()
    r = scan_text("本任务无关内容 A1.2-ANNO-002_marker_validation 混入",
                  frag)
    assert r["n_rule_id_hits"] == 1
    assert not r["ok"]


def test_taskset_and_prompt_contamination_free():
    """E2/E6：任务文件与生成器提示词不得含规则标识/标题（命中即污染）。"""
    frag = collect_rule_fragments()
    tasks_rep = scan_dir(TASKS_DIR, frag)
    assert tasks_rep["ok"], tasks_rep["files_with_rule_hits"]
    prompt_rep = scan_file(GENERATOR_PROMPT, frag)
    assert prompt_rep["ok"], "生成器提示词含规则内容（E6 违规）"


def test_generator_specs_rule_id_free():
    """E6：生成规格表（SPECS）不得含规则标识（防泄漏的第一道闸）。"""
    import re

    rule_id_re = re.compile(r"\b[A-Z]\d(?:\.\d)?-[A-Z]{2,6}-\d{3}(?:_[A-Za-z0-9_]+)?\b")
    text = json.dumps(SPECS, ensure_ascii=False)
    assert not rule_id_re.findall(text), "规格表含规则标识（E6 违规）"


# ── E5：覆盖审计（34 类型 + 38 规则）─────────────────────────────────────────


def test_coverage_all_types_and_rules():
    """任务集准入：34 决策类型 + 规则全覆盖（D5.12，零触发 = 0 或显式豁免）。"""
    cov = coverage_audit(TASKS_DIR)
    assert cov["ok"], cov["missing_types"] or cov["remaining_missing_rules"]
    assert cov["n_types_covered"] == 34
    assert cov["n_rules_matched"] == 38  # G1.4（J2）+ I4.1 scRNA（K1）由 DEFAULT_EXEMPTIONS 豁免
    assert cov["missing_rules"] == [
        "G1.4-DEG-004_significance_threshold",
        "I4.1-IMMU-001_scRNA_correlation_method",
    ]
    assert cov["remaining_missing_rules"] == []


# ── D4：运行器 + 功效分析 ────────────────────────────────────────────────────


def _synthetic_task(tid="t1", act="deg", labels=("correct", "error")) -> dict:
    return {
        "version": 2, "trajectory_id": tid, "act": act,
        "provenance": {"source": "benchmark"},
        "difficulty": {"label": 2, "rubric_version": "difficulty.v1",
                       "features": {"n_decisions": 2, "n_errors": 1, "n_edge": 0,
                                    "n_subtle_errors": 0, "n_consistency_family": 0}},
        "gold": {"version": "annotation.v1", "annotated_at": "2026-08-16",
                 "irr": {}, "labels": [
                     {"step_id": "s1", "label": labels[0], "consensus": "strong"},
                     {"step_id": "s2", "label": labels[1], "consensus": "strong"}]},
        "decisions": [
            {"step_id": "s1", "decision_type": "filtering",
             "choice": "filterByExpr", "rationale": "ok",
             "context": {"sequencing": "bulk_RNA_seq", "n_replicates": 6}},
            {"step_id": "s2", "decision_type": "multiple_testing_correction",
             "choice": "no_correction", "rationale": "top genes only",
             "context": {"analysis_type": "differential_expression"}},
        ],
    }


def test_runner_metrics_and_determinism():
    tasks = [_synthetic_task("t1"), _synthetic_task("t2")]
    audits = [audit_task(t) for t in tasks]
    agg1 = aggregate(tasks, audits, seed=42, n_boot=200)
    agg2 = aggregate(tasks, audits, seed=42, n_boot=200)
    assert agg1 == agg2, "同种子必须同结果（可重复运行）"
    d = agg1["overall"]["detection"]
    # t1: s1 correct→? filterByExpr→L3 未检出；s2 error(no_correction)→L0 检出 → TP=1,FP=0
    assert d["recall"] == 1.0
    assert d["precision"] == 1.0
    assert d["f1"] == 1.0
    assert agg1["overall"]["mean_score"]["n"] == 2
    assert agg1["method"]["bootstrap"]["seed"] == 42


def test_runner_gap_report_integration(tmp_path, monkeypatch):
    """run_benchmark 输出 gap 检查（E1：负向告警协议在场）。"""
    tasks = load_tasks(TASKS_DIR)
    # 用真实任务集跑（确定性，bootstrap 数量调小以省时间）
    report = run_benchmark(tasks_dir=TASKS_DIR, seed=42, n_boot=100)
    assert report["n_tasks_run"] == len(tasks)
    assert report["gap"] is not None
    assert "alarm" in report["gap"]
    assert report["aggregate"]["overall"]["n_tasks"] == len(tasks)


def test_holm_bonferroni_monotone():
    ps = [0.001, 0.02, 0.5]
    adj = holm_bonferroni(ps)
    assert adj[0] <= adj[1] <= adj[2]
    assert adj[0] == pytest.approx(0.003, abs=1e-9)
    assert adj[2] == pytest.approx(0.5, abs=1e-9)


# ── E3：标注合并（共识强度）──────────────────────────────────────────────────


def test_merge_annotations_consensus():
    a = [{"step_id": "s1", "label": "correct"}, {"step_id": "s2", "label": "error"}]
    b = [{"step_id": "s1", "label": "correct"}, {"step_id": "s2", "label": "edge"}]
    arb = [{"step_id": "s2", "label": "error"}]
    merged = merge_annotations(a, b, arb)
    m = {x["step_id"]: x for x in merged}
    assert m["s1"]["consensus"] == "strong"
    assert m["s2"]["label"] == "error" and m["s2"]["consensus"] == "medium"
    # 仲裁与双方都不一致 → weak
    arb2 = [{"step_id": "s2", "label": "correct"}]
    m2 = {x["step_id"]: x for x in merge_annotations(a, b, arb2)}
    assert m2["s2"]["consensus"] == "weak"
    # 无仲裁 → disputed
    m3 = {x["step_id"]: x for x in merge_annotations(a, b)}
    assert m3["s2"]["consensus"] == "disputed"


# ── E6：生成器（语料变换 + 零规则内容提示词）─────────────────────────────────


def test_generator_prompt_hash_stable_and_recorded():
    h = prompt_hash()
    assert len(h) == 12
    tasks = load_tasks(TASKS_DIR)
    for t in tasks[:5]:
        assert t["provenance"]["generator"]["prompt_version"] == h


def test_apply_spec_deterministic():
    corpus = load_corpus()
    spec = {
        "trajectory_id": "bmd_test_x1", "act": "deg",
        "base": "deg_correct",
        "choice_replacements": [{"step": "s4", "choice": "no_correction",
                                 "rationale": "r"}],
        "context_overrides": {"s1": {"n_replicates": 4}},
        "error_pattern_sources": ["deg_error"],
    }
    d1 = apply_spec(spec, corpus)
    d2 = apply_spec(spec, corpus)
    assert d1 == d2
    assert d1["provenance"]["source"] == "benchmark"
    assert d1["provenance"]["error_pattern_sources"] == ["deg_error"]
    assert "gold" not in d1, "生成器不写 gold（标注管线独立）"


# ── E8：任务集 manifest ──────────────────────────────────────────────────────


def test_taskset_manifest_valid_and_versioned():
    manifest = load_taskset(TASKS_DIR)
    assert manifest["taskset_version"] == "1.1.1"
    assert manifest["n_tasks"] == len(load_tasks(TASKS_DIR)) == 60
    assert isinstance(manifest["snapshot"], dict) and manifest["snapshot"]["ruleset"]
    assert isinstance(manifest["model_info"], dict)
    assert manifest["split"]["public"] and manifest["split"]["hidden"]
    assert manifest["irr"]["calibration_batch2"]["gate_pass"] is True
    assert manifest["irr"]["calibration_batch1"]["cohen_kappa"] == 0.8087  # 旧值留档
    report = validate_taskset(TASKS_DIR)
    assert report["ok"], report["errors"]


def test_taskset_bad_version_rejected(tmp_path):
    (tmp_path / "taskset.json").write_text(
        json.dumps({"taskset_version": "not-semver", "files": []}),
        encoding="utf-8")
    with pytest.raises(TasksetError):
        load_taskset(tmp_path)
