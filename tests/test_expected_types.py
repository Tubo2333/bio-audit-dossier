"""窗口 M / M1.1：expected_types 强制预期决策点检查测试。

覆盖（execution-plan §六.十七 M1.1/M1.2）：
- 配置加载 + 范式×平台解析（10X 12 决策 / Smart-seq2 11 决策无 doublet）
- B7/G5 豁免：optional + when_not_applicable 谓词满足 → 不补入；事实缺失 → 不豁免
- 补入语义：provenance=expected、choice 取 M1 已撤销声明（否则 not_performed）、
  verdict final（来源 expected）、参与评分（10X-B 闭环：skip_doublet → D1.1 L0 → blocked）
"""

import json
import tempfile

from bioaudit.capture.cross_validator import (
    STATUS_EXPECTED_ADDED,
    STATUS_FALSE_NEGATIVE,
    STATUS_FALSE_POSITIVE,
    STATUS_UNVERIFIED,
    CrossValidator,
    final_trajectory,
)
from bioaudit.capture.expected_types import (
    expected_types_for,
    is_exempt,
    load_expected_types,
    platform_for,
)
from bioaudit.capture.models import (
    PROVENANCE_SOURCE_EXPECTED,
    ParseResult,
)
from bioaudit.capture.verdict import VerdictStatus, VerdictStore


def _m1(step, dtype, choice, context=None, verdict_id=None):
    d = {
        "step_id": step, "decision_type": dtype, "choice": choice,
        "context": context or {},
    }
    if verdict_id is not None:
        d["verdict_id"] = verdict_id
    return d


# ── 1. 配置与平台解析 ──


def test_config_load_and_platform():
    cfg = load_expected_types()
    assert cfg["version"] == 1
    assert "scrna_10x" in cfg["defaults"]
    assert "scrna_smartseq2" in cfg["defaults"]
    assert platform_for("scrna", {"sequencing": "10X_scRNA_seq"}) == "scrna_10x"
    assert platform_for("scrna", {"sequencing": "smartseq2"}) == "scrna_smartseq2"
    assert platform_for("deg", {}) == "deg"
    assert platform_for("pan", {}) == "pan"


def test_expected_types_for_10x_and_smartseq2():
    tenx = expected_types_for("scrna", {"sequencing": "10X_scRNA_seq"})
    smart = expected_types_for("scrna", {"sequencing": "smartseq2"})
    # 10X 标准管线 11 决策（L 窗口 A 版最终轨迹实证；api_data_integrity
    # 无 M3 确定性签名 → 不进默认清单）
    assert len(tenx) == 11
    assert "doublet_detection" in tenx
    assert "qc_filtering" in tenx
    assert "deg_method" in tenx
    assert "api_data_integrity" not in tenx
    # Smart-seq2 无双联体前提（平台查证实证）
    assert len(smart) == 10
    assert "doublet_detection" not in smart
    # deg/pan 骨架未启用强制
    assert expected_types_for("deg", {}) == []


# ── 2. B7/G5 豁免 ──


def test_exemption_requires_optional_and_predicate():
    # trajectory_inference = optional + study_not_trajectory_focused
    assert is_exempt("trajectory_inference", {"trajectory_focused": False}) is True
    assert is_exempt("trajectory_inference", {"trajectory_focused": True}) is False
    # 事实缺失 → 不豁免（保守：引擎不猜研究范围）
    assert is_exempt("trajectory_inference", {}) is False
    # 非 optional 类型永不豁免
    assert is_exempt("doublet_detection", {"trajectory_focused": False}) is False
    assert is_exempt("qc_filtering", {}) is False


def test_exemption_claims_and_batch():
    assert is_exempt("batch_correction", {"has_batch": False}) is True
    assert is_exempt("batch_correction", {"has_batch": True}) is False
    assert is_exempt("batch_correction", {"n_samples": 1}) is True
    assert is_exempt("batch_correction", {"n_samples": 5, "has_batch": True}) is False
    assert is_exempt("annotation_deg_consistency", {"claims": ["deg_method"]}) is True
    assert is_exempt(
        "annotation_deg_consistency",
        {"claims": ["annotation_deg_consistency"]},
    ) is False


# ── 3. 补入语义 ──


def test_expected_added_when_no_evidence():
    """预期决策点双方都无证据 → 补入 provenance=expected（choice=not_performed）。"""
    store = VerdictStore(tempfile.mkdtemp())
    result = CrossValidator(act="scrna").validate(
        [], ParseResult(), session_id="sess-exp1",
        expected_types=["qc_filtering"], verdict_store=store,
    )
    assert result.stats[STATUS_EXPECTED_ADDED] == 1
    assert result.stats[STATUS_UNVERIFIED] == 1
    added = [d for d in result.added_decisions if d["decision_type"] == "qc_filtering"]
    assert len(added) == 1
    assert added[0]["choice"] == "not_performed"
    assert added[0]["verdict_id"]
    # verdict final + 来源 expected → final 轨迹含该决策
    final = store.final_verdicts("sess-exp1")
    assert len(final) == 1
    assert final[0].provenance_source == PROVENANCE_SOURCE_EXPECTED
    assert final[0].status == VerdictStatus.FINAL
    traj = final_trajectory(store, "sess-exp1", act="scrna")
    types = [d["decision_type"] for d in traj["decisions"]]
    assert "qc_filtering" in types


def test_expected_added_uses_revoked_m1_choice():
    """M1 声明虚报撤销后：补入 choice 取 Agent 已声明值（skip_doublet 闭环）。"""
    store = VerdictStore(tempfile.mkdtemp())
    v = store.create("sess-exp2", "S3", "doublet_detection", "skip_doublet",
                     "scrna", "M1声明", status=VerdictStatus.PROVISIONAL,
                     score_snapshot={"context": {"sequencing": "10X_scRNA_seq",
                                                "n_cells": 59399}})
    m1 = [_m1("S3", "doublet_detection", "skip_doublet",
              {"sequencing": "10X_scRNA_seq", "n_cells": 59399},
              verdict_id=v.verdict_id)]
    result = CrossValidator(act="scrna").validate(
        m1, ParseResult(), session_id="sess-exp2",
        expected_types=["doublet_detection"], verdict_store=store,
    )
    # 虚报撤销 + 预期补入并存
    assert result.stats[STATUS_FALSE_POSITIVE] == 1
    assert result.stats[STATUS_EXPECTED_ADDED] == 1
    added = [d for d in result.added_decisions if d["decision_type"] == "doublet_detection"]
    assert len(added) == 1
    assert added[0]["choice"] == "skip_doublet"  # Agent 自己的声明，不伪造
    assert added[0]["context"] == {"sequencing": "10X_scRNA_seq", "n_cells": 59399}
    # 原 provisional verdict 被撤销；补入 verdict 为 final
    orig = store.get("sess-exp2")
    revoked = [r for r in orig if r.verdict_id == v.verdict_id]
    assert revoked[0].status == VerdictStatus.REVOKED
    finals = [r for r in orig if r.status == VerdictStatus.FINAL]
    assert len(finals) == 1
    assert finals[0].provenance_source == PROVENANCE_SOURCE_EXPECTED


def test_expected_not_added_when_evidence_exists():
    """预期决策点有真实证据（一致/漏报）→ 不补入。"""
    store = VerdictStore(tempfile.mkdtemp())
    m3 = ParseResult()  # 空
    # 漏报路径：M3 有执行证据
    from bioaudit.capture.m3_parser import M3Parser

    m3 = M3Parser(act="scrna").parse_code("sc.pp.filter_cells(adata, min_genes=200)")
    result = CrossValidator(act="scrna").validate(
        [], m3, session_id="sess-exp3",
        expected_types=["qc_filtering", "doublet_detection"], verdict_store=store,
    )
    assert result.stats[STATUS_EXPECTED_ADDED] == 1  # 仅 doublet_detection 补入
    assert result.stats[STATUS_FALSE_NEGATIVE] == 1  # qc_filtering 漏报补入（不重复）
    types_added = [d["decision_type"] for d in result.added_decisions]
    assert types_added.count("qc_filtering") == 1


# ── 4. 10X-B 闭环（引擎级端到端：补入 → D1.1 L0 → blocked）──


def test_10x_b_closed_loop_engine():
    """B 版带 expected_types：doublet_detection 补入 → D1.1 L0 → blocked（63.7）。"""
    from bioaudit.api.audit import run_audit

    store = VerdictStore(tempfile.mkdtemp())
    # 10 条一致声明（黄金 A 版同款）——用 M3 解析构造（简化：qc_filtering 一条一致
    # + doublet 预期补入即可验证 D1.1 L0 链路；63.7 全量数字由 windowL 真实重跑验证）
    v = store.create("sess-exp4", "S3", "doublet_detection", "skip_doublet",
                     "scrna", "M1声明", status=VerdictStatus.PROVISIONAL)
    m1 = [_m1("S3", "doublet_detection", "skip_doublet",
              {"sequencing": "10X_scRNA_seq", "n_cells": 59399,
               "data_category": "umi_counts", "n_patients": 23},
              verdict_id=v.verdict_id)]
    CrossValidator(act="scrna").validate(
        m1, ParseResult(), session_id="sess-exp4",
        expected_types=["doublet_detection"], verdict_store=store,
    )
    traj = final_trajectory(store, "sess-exp4", act="scrna")
    doublet = [d for d in traj["decisions"] if d["decision_type"] == "doublet_detection"]
    assert len(doublet) == 1
    # 补入决策走真实引擎评分 → D1.1 → skip_doublet → L0
    audit = run_audit(traj, act="scrna")
    scores = {s["decision_type"]: s for s in audit["step_scores"]}
    assert scores["doublet_detection"]["level"] == 0
    assert scores["doublet_detection"]["matched_rules"] == ["D1.1-DOUB-001"]
    # 单决策 0 分 → 该维度被拉低 → blocked（verdict 由 L0 主导）
    assert audit["eval_verdict"] == "blocked"


# ── 5. CLI --expected 支持 YAML 配置 ──


def test_cli_expected_yaml_config(tmp_path):
    from bioaudit.cli import main

    yaml_path = tmp_path / "expected.yaml"
    yaml_path.write_text(
        "version: 1\ndefaults:\n  scrna_10x: [qc_filtering, doublet_detection]\n",
        encoding="utf-8",
    )
    m1_path = tmp_path / "m1.json"
    m1_path.write_text(json.dumps([]), encoding="utf-8")
    m3_path = tmp_path / "m3.json"
    m3_path.write_text(json.dumps([]), encoding="utf-8")
    rc = main([
        "cross-validate", "--m1", str(m1_path), "--m3-json", str(m3_path),
        "--act", "scrna", "--expected", str(yaml_path), "--no-verdicts",
    ])
    assert rc == 0
