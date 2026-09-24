"""窗口 G-2 测试：declared 上下文注入（G2-a）+ 规则平台键放宽（G2-b）。

验收对应（execution-plan-v1 §六.十二）：
- G2-a.1 四级可信源：call_arg > data_metadata > declared（评测者/数据事实声明）
  > unverified；declared 只能由评测者注入，**与 Agent claim（M1 声明）严格区分**
- G2-a.2 M1/M3/交叉验证链路支持 declared 键注入；declared 提供键 → 不再 unverified
- G2-a.3 declared 注入后规则匹配成功（sequencing declared → Q1.1 匹配出 level）
- G2-b 平台键审查：只放宽确实过强的依赖（sequencing 接受 smartseq2 等）；
  10X 专属规则（D1.1 双联体检测）保留 10X_scRNA_seq 硬依赖

关键纪律守卫：declared 不是 Agent 自证通道——解析器只从构造参数读 declared，
M1 上报的键永远不会进入 declared（G2-a.1）。
"""

import json
from pathlib import Path

from bioaudit.api import audit_decision
from bioaudit.capture.m3_parser import M3Parser
from bioaudit.capture.models import (
    TRUST_CALL_ARG,
    TRUST_DATA_METADATA,
    TRUST_DECLARED,
)
from bioaudit.paths import RULES_DIR
from bioaudit.storage.rule_registry import RuleRegistry

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_scrna_notebook.ipynb"


# ── G2-a.3：declared 注入后规则匹配成功（验收项 3 的直接测试）──


def test_declared_sequencing_matches_q1_1():
    """declared sequencing=smartseq2 → Q1.1-QC-001 匹配出 level（不再 L-1）。

    场景还原（窗口 G 重评）：GSE115978 为 Smart-seq2 平台（GEO 官方记录），
    评测者以 declared 注入 sequencing=smartseq2（数据事实，非 Agent 自证）。
    """
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "qc_filtering",
        "choice": "hard_threshold",
        "context": {"sequencing": "smartseq2", "min_genes": 1000, "n_cells": 7186},
    }, paradigm="scrna")
    assert d["level"] == 1  # hard_threshold → L1（有风险）
    assert d["matched_rules"] == ["Q1.1-QC-001"]


def test_q1_1_without_sequencing_still_unevaluable():
    """回归守卫：declared 未注入时缺键仍 fail-closed。

    M2.4（窗口 M）：missing 三档运行时强制后，fail-closed 键（sequencing）缺失
    且被候选规则（Q1.1 required_context）引用 → **未验证（-2）**——从 G2 时代
    的 -1（规则不匹配）升级为显式"评估前提不成立"状态（与 -1 区分并在报告呈现）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "qc_filtering",
        "choice": "hard_threshold",
        "context": {"min_genes": 1000},
    }, paradigm="scrna")
    assert d["level"] == -2  # 未验证（关键上下文缺失）
    assert "sequencing" in d["missing_keys"]
    assert d["matched_rules"] == []


def test_q1_1_still_accepts_10x():
    """放宽是加性的：10X_scRNA_seq 仍命中（golden 0 差异的基础）。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "qc_filtering",
        "choice": "hard_threshold",
        "context": {"sequencing": "10X_scRNA_seq"},
    }, paradigm="scrna")
    assert d["level"] == 1
    assert "Q1.1-QC-001" in d["matched_rules"]


# ── G2-b 平台键审查守卫：只放宽确实过强的，保留 10X 专属 ──


def test_doublet_rule_remains_10x_specific():
    """D1.1 双联体检测是 10X 专属规则（液滴平台双联体问题；板式/Smart-seq2
    FACS 分选基本无双联体）——smartseq2 不命中（L-1），10X 命中（L0）。"""
    d_ss2 = audit_decision({
        "step_id": "s1",
        "decision_type": "doublet_detection",
        "choice": "no_doublet_detection",
        "context": {"sequencing": "smartseq2", "n_cells": 7186},
    }, paradigm="scrna")
    assert d_ss2["level"] == -1
    assert d_ss2["matched_rules"] == []

    d_10x = audit_decision({
        "step_id": "s1",
        "decision_type": "doublet_detection",
        "choice": "no_doublet_detection",
        "context": {"sequencing": "10X_scRNA_seq", "n_cells": 7186},
    }, paradigm="scrna")
    assert d_10x["level"] == 0
    assert "D1.1-DOUB-001" in d_10x["matched_rules"]


def test_normalization_accepts_smartseq2_raw_counts():
    """N1.1 归一化：Smart-seq2 全转录组为 raw_counts（非 UMI），
    G-2 放宽 data_category ∈ [umi_counts, raw_counts] + sequencing ∈ [10X, smartseq2]。"""
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "scRNA_normalization",
        "choice": "LogNormalize",
        "context": {"sequencing": "smartseq2", "data_category": "raw_counts"},
    }, paradigm="scrna")
    assert d["level"] == 1  # LogNormalize → L1（经典但有已知缺陷）
    assert d["matched_rules"] == ["N1.1-NORM-001"]


def test_clustering_rule_keeps_snn_graph_precondition():
    """C1.1 聚类：graph_type=SNN 是真实的图聚类前置条件（Leiden 运行在
    neighbors SNN 图上），保留硬依赖；缺 graph_type 仍不匹配。"""
    d_no = audit_decision({
        "step_id": "s1",
        "decision_type": "clustering_method",
        "choice": "Leiden",
        "context": {"sequencing": "smartseq2"},
    }, paradigm="scrna")
    assert d_no["level"] == -1

    d_yes = audit_decision({
        "step_id": "s1",
        "decision_type": "clustering_method",
        "choice": "Leiden",
        "context": {"sequencing": "smartseq2", "graph_type": "SNN"},
    }, paradigm="scrna")
    assert d_yes["level"] == 3
    assert "C1.1-CLUS-001_method" in d_yes["matched_rules"]


# ── G2-a.1/.2：采集链路 declared 注入 ──


def test_m3_declared_fills_schema_key_and_clears_unverified():
    """declared 提供 schema 键 → 该键进入 context（trust=declared），
    不再出现在 unverified_keys（G2-a.2 语义）。"""
    result = M3Parser(
        act="scrna", declared={"sequencing": "smartseq2"}
    ).parse_code("sc.pp.filter_cells(adata, min_genes=200)")
    cand = result.candidates[0]
    assert cand.context["sequencing"] == "smartseq2"
    assert cand.context_trust["sequencing"] == TRUST_DECLARED
    assert "sequencing" not in cand.unverified_keys
    # 仍缺的键（无元数据/声明）继续标 unverified，绝不猜
    assert "n_cells" in cand.unverified_keys


def test_trust_order_declared_never_overrides_higher_sources():
    """严格排序守卫：call_arg > data_metadata > declared——declared 不能覆盖
    更高可信源（禁止跳级/覆盖猜测）。"""
    # metadata > declared
    parser = M3Parser(
        act="scrna",
        metadata={"sequencing": "10X_scRNA_seq"},
        declared={"sequencing": "smartseq2"},
    )
    cand = parser.parse_code("sc.pp.filter_cells(adata, min_genes=200)").candidates[0]
    assert cand.context["sequencing"] == "10X_scRNA_seq"
    assert cand.context_trust["sequencing"] == TRUST_DATA_METADATA

    # call_arg > declared（leiden 的 graph_type=SNN 为工具语义 call_arg 级）
    r = M3Parser(
        act="scrna", declared={"graph_type": "KNN"}
    ).parse_code("sc.tl.leiden(adata, resolution=1.0)")
    for c in r.candidates:
        if c.decision_type == "clustering_method":
            assert c.context["graph_type"] == "SNN"  # context_fixed 不被 declared 覆盖
            assert c.context_trust["graph_type"] == TRUST_CALL_ARG


def test_leiden_signature_provides_snn_graph_type():
    """采集修复（G2-b 配套）：sc.tl.leiden 的 graph_type=SNN 是工具定义语义
    （scanpy 图聚类运行在 neighbors SNN 图上）→ context_fixed（call_arg 级）。"""
    result = M3Parser(act="scrna").parse_code("sc.tl.leiden(adata, resolution=1.0)")
    for cand in result.candidates:
        if cand.decision_type == "clustering_method":
            assert cand.context["graph_type"] == "SNN"
            assert cand.context_trust["graph_type"] == TRUST_CALL_ARG
            return
    raise AssertionError("leiden 签名未产出 clustering_method 候选")


def test_m3_declared_flows_to_notebook_parse_and_hook_parser():
    """declared 注入在 parse_notebook 与 hook 同源（make_cellvoyager_hook 的
    parser 即 M3Parser(declared=...)）——fixture notebook 全链路验证。"""
    result = M3Parser(
        act="scrna", declared={"sequencing": "smartseq2"}
    ).parse_notebook(FIXTURE)
    assert result.candidates
    # qc_filtering 候选获得 declared sequencing
    qc = [c for c in result.candidates if c.decision_type == "qc_filtering"][0]
    assert qc.context.get("sequencing") == "smartseq2"
    assert qc.context_trust.get("sequencing") == TRUST_DECLARED


# ── 引擎层：required_context 列表 any-of 语义（G2-b 修订落地）──


def test_required_context_list_any_of_engine_semantics():
    """required_context 值为列表 = any-of：任一命中即通过；缺失/非命中拒绝。"""
    reg = RuleRegistry(RULES_DIR)
    reg.load_all()
    rule = reg.get_rule("Q1.1-QC-001")
    assert rule is not None
    cond = rule.condition
    assert cond.required_context["sequencing"] == ["10X_scRNA_seq", "smartseq2"]

    assert reg._condition_matches(cond, {"sequencing": "smartseq2"}) is True
    assert reg._condition_matches(cond, {"sequencing": "10X_scRNA_seq"}) is True
    assert reg._condition_matches(cond, {"sequencing": "bulk_RNA_seq"}) is False
    assert reg._condition_matches(cond, {}) is False

    # 透明匹配明细（match_details）同样展示 any-of 判定
    details = reg.match_with_details("qc_filtering", {"sequencing": "smartseq2"})
    q1 = [d for d in details if d["rule_id"] == "Q1.1-QC-001"][0]
    assert q1["matched"] is True
    seq_check = [c for c in q1["checks"] if c["type"] == "required"][0]
    assert "∈" in seq_check["expr"]


# ── G2-a.2：cross-validate CLI 支持 --declared ──


def test_cross_validate_cli_accepts_declared(tmp_path, capsys):
    """cross-validate --declared 接线：declared 键进入 M3 解析候选 context。"""
    from bioaudit.cli import main

    m1 = tmp_path / "m1.jsonl"
    m1.write_text(json.dumps({
        "step_id": "nb1-qc_filtering",
        "decision_type": "qc_filtering",
        "choice": "hard_threshold",
    }) + "\n", encoding="utf-8")
    declared = tmp_path / "declared.json"
    declared.write_text(json.dumps({"sequencing": "smartseq2"}), encoding="utf-8")

    code = main([
        "cross-validate",
        "--m1", str(m1),
        "--m3", str(FIXTURE),
        "--declared", str(declared),
        "--act", "scrna",
        "--session", "g2_cli_test",
        "--no-verdicts",
    ])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stats"]["consistent"] >= 0  # 链路正常
    # M3 候选（含自动补入/一致实例）携带 declared sequencing
    declared_hits = [
        a for a in out["alignments"]
        if a.get("m3") and a["m3"].get("context", {}).get("sequencing") == "smartseq2"
    ]
    assert declared_hits, "declared sequencing 未进入 M3 候选 context"
