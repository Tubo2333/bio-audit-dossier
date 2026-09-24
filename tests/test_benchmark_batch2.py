"""批 2 任务集测试（窗口 F：30 → 60；F1.1-F4.12 相关守卫）。

覆盖：批 2 规格 E6 / 生成器 provenance / 预注册 v2 / 校准批 IRR 门槛 /
批 2 gold 版本（annotation.v1.1）/ 全量 60 覆盖 / 难度分布三范式三梯度 /
60 条 split / rubric v1.1 六条澄清点在场。
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_benchmark_tasks_batch2 import SPECS as SPECS_BATCH2  # noqa: E402

from bioaudit.benchmark.annotation import ANNOTATION_VERSION  # noqa: E402
from bioaudit.benchmark.contamination import (  # noqa: E402
    collect_rule_fragments,
    scan_dir,
    scan_file,
)
from bioaudit.benchmark.coverage import audit as coverage_audit  # noqa: E402
from bioaudit.benchmark.manifest import load_tasks, load_taskset  # noqa: E402
from bioaudit.benchmark.paths import (  # noqa: E402
    ANNOTATION_RUBRIC,
    GENERATOR_PROMPT,
    TASKS_DIR,
)
from bioaudit.benchmark.protocol import PRE_REGISTRATION  # noqa: E402

BATCH2_IDS = {s["trajectory_id"] for s in SPECS_BATCH2}


# ── F1：批 2 生成纪律（E6 防泄漏）─────────────────────────────────────────────


def test_batch2_specs_rule_id_free():
    """批 2 规格表不得含规则标识（E6 第一道闸，与批 1 同规）。"""
    rule_id_re = re.compile(r"\b[A-Z]\d(?:\.\d)?-[A-Z]{2,6}-\d{3}(?:_[A-Za-z0-9_]+)?\b")
    text = json.dumps(SPECS_BATCH2, ensure_ascii=False)
    assert not rule_id_re.findall(text), "批 2 规格表含规则标识（E6 违规）"


def test_batch2_specs_count_and_balance():
    """批 2 = 30 条（scrna 10 / pan 10 / deg 10），与批 1 合计 60。"""
    acts = [s["act"] for s in SPECS_BATCH2]
    assert len(SPECS_BATCH2) == 30
    assert acts.count("scrna") == 10 and acts.count("pan") == 10 and acts.count("deg") == 10
    tasks = load_tasks(TASKS_DIR)
    assert len(tasks) == 60, "任务集合计 60 条（F1.1）"


def test_batch2_specs_error_sources_in_corpus():
    """错误注入素材必须来自语料轨迹 id（E6：不来自规则反推）。"""
    from bioaudit.benchmark.generator import load_corpus

    corpus = load_corpus()
    for s in SPECS_BATCH2:
        assert s["base"] in corpus, f"{s['trajectory_id']} base 不在语料"
        for src in s.get("error_pattern_sources", []):
            assert src in corpus, f"{s['trajectory_id']} 错误模式来源不在语料: {src}"


def test_batch2_tasks_contamination_free():
    """全量 60 任务 + 生成器提示词污染扫描 0 命中（E2/E6）。"""
    frag = collect_rule_fragments()
    rep = scan_dir(TASKS_DIR, frag)
    assert rep["ok"], rep["files_with_rule_hits"]
    prompt_rep = scan_file(GENERATOR_PROMPT, frag)
    assert prompt_rep["ok"]


def test_batch2_provenance_generator_info():
    """批 2 任务 provenance：source=benchmark + 生成器信息 + window-F 审核。"""
    tasks = load_tasks(TASKS_DIR)
    batch2 = [t for t in tasks if t["trajectory_id"] in BATCH2_IDS]
    assert len(batch2) == 30
    for t in batch2:
        assert t["provenance"]["source"] == "benchmark"
        gen = t["provenance"]["generator"]
        assert gen is not None
        assert gen["reviewed_by"] == "window-F review"
        assert gen["prompt_version"], "E6：提示词版本必须记录"


# ── F1.3：预注册 v2 ───────────────────────────────────────────────────────────


def test_pre_registration_v2_fields():
    rec = PRE_REGISTRATION
    assert rec["record_id"] == "benchmark-pr-2026-08-16-02"
    assert "re_evaluation" in rec["gap"], "gap 区间重评估说明必须预注册"
    assert "批 1 实测 Δ=-0.1864" in rec["gap"]["re_evaluation"]
    assert rec["annotation_rubric_version"] == "annotation.v1.1"
    assert rec["irr_gate"]["calibration_batch_size"] == 10


# ── F2：批 2 标注 ─────────────────────────────────────────────────────────────


def test_batch2_calibration_irr_gate_pass():
    """批 2 校准批 IRR 门槛（κ/α ≥ 0.8）达标后才放量（E3，taskset 记录）。"""
    manifest = load_taskset(TASKS_DIR)
    calib = manifest["irr"]["calibration_batch2"]
    assert calib["gate_pass"] is True
    assert calib["cohen_kappa"] >= 0.8
    assert calib["krippendorff_alpha"] >= 0.8


def test_batch2_gold_version_v11_batch1_kept():
    """批 2 gold = annotation.v1.1；批 1 旧 gold 不追溯重判（保持 v1）。"""
    tasks = load_tasks(TASKS_DIR)
    assert ANNOTATION_VERSION == "annotation.v1.1"
    for t in tasks:
        if t["trajectory_id"] in BATCH2_IDS:
            assert t["gold"]["version"] == "annotation.v1.1", t["trajectory_id"]
        else:
            assert t["gold"]["version"] == "annotation.v1", t["trajectory_id"]
        assert len(t["gold"]["labels"]) == len(t["decisions"]), \
            f"{t['trajectory_id']} gold 未覆盖全部决策"


def test_batch2_merged_annotations_complete():
    """批 2 合并标注产物在场且逐任务覆盖。"""
    from bioaudit.benchmark.paths import ANNOTATION_DIR

    merged = json.loads((ANNOTATION_DIR / "merged_annotations_batch2.json")
                        .read_text(encoding="utf-8"))
    tasks = load_tasks(TASKS_DIR)
    batch2 = [t for t in tasks if t["trajectory_id"] in BATCH2_IDS]
    by_task: dict[str, int] = {}
    for m in merged:
        by_task[m["task_id"]] = by_task.get(m["task_id"], 0) + 1
    for t in batch2:
        assert by_task.get(t["trajectory_id"], 0) == len(t["decisions"]), \
            f"{t['trajectory_id']} 合并标注不完整"
    consensus = {m["consensus"] for m in merged}
    assert consensus <= {"strong", "medium", "weak"}, \
        f"存在未仲裁分歧: {consensus - {'strong', 'medium', 'weak'}}"


# ── F2.6：rubric v1.1 六条澄清点 ──────────────────────────────────────────────


def test_rubric_v11_six_clarifications_present():
    text = ANNOTATION_RUBRIC.read_text(encoding="utf-8")
    assert "annotation.v1.1" in text
    for section in ("4.1 TMM", "4.2", "4.3", "4.4", "4.5", "4.6"):
        assert section in text, f"rubric v1.1 缺少澄清点: {section}"


# ── F3：覆盖与难度 ────────────────────────────────────────────────────────────


def test_coverage_60_tasks_still_34_38():
    """60 条仍覆盖 34/34 类型 + 38/38 规则（零触发 = 0 或显式豁免，F3.7）。"""
    cov = coverage_audit(TASKS_DIR)
    assert cov["ok"], cov["missing_types"] or cov["remaining_missing_rules"]
    assert cov["n_types_covered"] == 34
    assert cov["n_rules_matched"] == 38  # G1.4（J2 新增）由 DEFAULT_EXEMPTIONS 豁免
    assert cov["remaining_missing_rules"] == []


def test_difficulty_all_paradigm_cells_nonempty():
    """3 范式 × 3 梯度全非空（F3.8：批 1 scrna 无 easy，批 2 补齐）。"""
    tasks = load_tasks(TASKS_DIR)
    cells: dict[tuple[str, int], int] = {}
    for t in tasks:
        cells[(t["act"], t["difficulty"]["label"])] = \
            cells.get((t["act"], t["difficulty"]["label"]), 0) + 1
    for act in ("scrna", "pan", "deg"):
        for d in (1, 2, 3):
            assert cells.get((act, d), 0) >= 1, f"{act} 难度 {d} 无任务"
    # 批 2 scrna easy ≥ 1（批 1 缺口修复）
    scrna_easy_batch2 = sum(1 for t in tasks
                            if t["act"] == "scrna" and t["difficulty"]["label"] == 1
                            and t["trajectory_id"] in BATCH2_IDS)
    assert scrna_easy_batch2 >= 1


# ── F4：60 条 split ──────────────────────────────────────────────────────────


def test_split_60_tasks_complete():
    """60 条全量重新划分（预注册 v2：seed=42，70/30；hidden n≈18）。"""
    manifest = load_taskset(TASKS_DIR)
    tasks = load_tasks(TASKS_DIR)
    pub = set(manifest["split"]["public"])
    hid = set(manifest["split"]["hidden"])
    ids = {t["trajectory_id"] for t in tasks}
    assert len(ids) == 60
    assert pub | hid == ids
    assert not (pub & hid)
    assert len(hid) >= 10, "隐藏集不可过小（预注册 70/30，hidden≈18）"
