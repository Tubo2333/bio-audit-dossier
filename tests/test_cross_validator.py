"""C3 交叉验证器 + verdict 状态位测试（窗口 C 验收项 9/10/11）。

- 9. 四类判定：一致 / 虚报（声明未执行）/ 漏报（执行未声明→自动补入）/ 未验证
- 10. verdict 状态位：provisional → final / revoked（v1.1 B4）；
      报告与 reward 只消费 final（final_trajectory）
- 11. M1/M3 对齐：按 decision_type 对齐；同类型多实例（迭代调参）建模（B5）
"""


import pytest

from bioaudit.capture.cross_validator import (
    STATUS_CONSISTENT,
    STATUS_EXPECTED_ADDED,
    STATUS_FALSE_NEGATIVE,
    STATUS_FALSE_POSITIVE,
    STATUS_UNVERIFIED,
    CrossValidator,
    final_trajectory,
)
from bioaudit.capture.m3_parser import M3Parser
from bioaudit.capture.verdict import (
    VERDICT_TRANSITIONS,
    VerdictStatus,
    VerdictStore,
    VerdictTransitionError,
)

METADATA = {
    "n_cells": 63689,
    "n_patients": 10,
    "sequencing": "10X_scRNA_seq",
    "n_genes": 25655,
}


def _m1(step, dtype, choice, context=None, verdict_id=None):
    d = {
        "step_id": step, "decision_type": dtype, "choice": choice,
        "context": context or {},
    }
    if verdict_id is not None:
        d["verdict_id"] = verdict_id
    return d


# ── 9. 四类判定 ──


def test_four_categories():
    m1 = [
        _m1("s1", "qc_filtering", "hard_threshold"),          # M3 有 → 一致
        _m1("s2", "dim_reduction", "PCA_elbow_selection"),    # M3 无 → 虚报
    ]
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.filter_cells(adata, min_genes=200)"
    )
    result = CrossValidator(act="scrna").validate(
        m1, m3, session_id="s",
        expected_types=["qc_filtering", "dim_reduction", "clustering_method"],
    )
    by_type = {a.decision_type: a for a in result.alignments}
    assert by_type["qc_filtering"].status == STATUS_CONSISTENT
    # M1.1（窗口 M）：dim_reduction/clustering_method 为预期决策点且无 M3 执行
    # 证据 → 虚报/未验证 + 补入 provenance=expected（该做没做）
    assert by_type["dim_reduction"].status == STATUS_FALSE_POSITIVE
    assert by_type["dim_reduction"].expected_added is True
    assert by_type["clustering_method"].status == STATUS_UNVERIFIED
    assert by_type["clustering_method"].expected_added is True
    added = {d["decision_type"]: d for d in result.added_decisions}
    assert added["dim_reduction"]["choice"] == "PCA_elbow_selection"  # Agent 声明值
    assert added["clustering_method"]["choice"] == "not_performed"
    assert result.stats == {
        STATUS_CONSISTENT: 1, STATUS_FALSE_POSITIVE: 1,
        STATUS_FALSE_NEGATIVE: 0, STATUS_UNVERIFIED: 1,
        STATUS_EXPECTED_ADDED: 2,
    }


def test_false_negative_auto_added():
    """漏报：M3 执行未声明 → 自动补入 + verdict final（事实证据链）。"""
    store = VerdictStore(__import__("tempfile").mkdtemp())
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.filter_cells(adata, min_genes=200)"
    )
    result = CrossValidator(act="scrna").validate(
        [], m3, session_id="sess-fn", verdict_store=store
    )
    assert result.stats[STATUS_FALSE_NEGATIVE] == 1
    assert len(result.added_decisions) == 1
    assert result.added_decisions[0]["decision_type"] == "qc_filtering"
    assert result.added_decisions[0]["verdict_id"]
    final = store.final_verdicts("sess-fn")
    assert len(final) == 1
    assert final[0].provenance_source == "M3解析"
    assert final[0].status == VerdictStatus.FINAL


def test_false_positive_revokes_verdict():
    """虚报：声明未执行 → provisional verdict 被 revoked（M3 判虚报后分数被推翻）。"""
    store = VerdictStore(__import__("tempfile").mkdtemp())
    v = store.create("sess-fp", "s1", "dim_reduction", "PCA_elbow_selection",
                     "scrna", "M1声明", status=VerdictStatus.PROVISIONAL)
    m1 = [_m1("s1", "dim_reduction", "PCA_elbow_selection", verdict_id=v.verdict_id)]
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.filter_cells(adata, min_genes=200)"  # M3 无 dim_reduction 证据
    )
    result = CrossValidator(act="scrna").validate(
        m1, m3, session_id="sess-fp", verdict_store=store
    )
    assert result.stats[STATUS_FALSE_POSITIVE] == 1
    assert result.verdict_updates[0]["status"] == "revoked"
    record = store.get("sess-fp")[0]
    assert record.status == VerdictStatus.REVOKED
    # 分数被推翻：final 集合里不再有 dim_reduction（漏报补入的 qc_filtering 除外）
    final_types = [v.decision_type for v in store.final_verdicts("sess-fp")]
    assert "dim_reduction" not in final_types


def test_consistent_finalizes_verdict():
    store = VerdictStore(__import__("tempfile").mkdtemp())
    v = store.create("sess-c", "s1", "qc_filtering", "hard_threshold",
                     "scrna", "M1声明", status=VerdictStatus.PROVISIONAL)
    m1 = [_m1("s1", "qc_filtering", "hard_threshold", verdict_id=v.verdict_id)]
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.filter_cells(adata, min_genes=200)"
    )
    result = CrossValidator(act="scrna").validate(
        m1, m3, session_id="sess-c", verdict_store=store
    )
    assert result.stats[STATUS_CONSISTENT] == 1
    assert result.verdict_updates[0]["status"] == "final"
    assert len(store.final_verdicts("sess-c")) == 1


# ── 10. verdict 状态位（B4/P3）──


def test_verdict_lifecycle_transitions():
    assert VERDICT_TRANSITIONS[VerdictStatus.PROVISIONAL] == frozenset(
        {VerdictStatus.FINAL, VerdictStatus.REVOKED}
    )
    assert VERDICT_TRANSITIONS[VerdictStatus.FINAL] == frozenset(
        {VerdictStatus.REVOKED}
    )
    assert VERDICT_TRANSITIONS[VerdictStatus.REVOKED] == frozenset()


def test_illegal_transition_rejected():
    store = VerdictStore(__import__("tempfile").mkdtemp())
    v = store.create("s", "s1", "qc_filtering", "hard_threshold", "scrna",
                     "M1声明", status=VerdictStatus.REVOKED)
    with pytest.raises(VerdictTransitionError):
        store.transition(v.verdict_id, VerdictStatus.FINAL, "重新定案")


def test_report_reward_consume_final_only():
    """B4 不变量：报告与 reward 只消费 final——revoked 分数不得进入 final_trajectory。"""
    store = VerdictStore(__import__("tempfile").mkdtemp())
    store.create("sess-r", "s1", "qc_filtering", "hard_threshold", "scrna",
                 "M1声明", score_snapshot={"level": 1},
                 status=VerdictStatus.FINAL)
    store.create("sess-r", "s2", "dim_reduction", "PCA_elbow_selection", "scrna",
                 "M1声明", score_snapshot={"level": 3},
                 status=VerdictStatus.PROVISIONAL)
    v3 = store.create("sess-r", "s3", "batch_correction", "Harmony", "scrna",
                      "M1声明", score_snapshot={"level": 3},
                      status=VerdictStatus.FINAL)
    store.transition(v3.verdict_id, VerdictStatus.REVOKED, "虚报")

    traj = final_trajectory(store, "sess-r", act="scrna")
    decisions = traj["decisions"]
    assert len(decisions) == 1  # 只有 final 的 qc_filtering
    assert decisions[0]["decision_type"] == "qc_filtering"
    assert traj["version"] == 2
    assert traj["provenance"]["source"] == "capture"


def test_verdict_store_persistence_replay():
    """JSONL 持久化：新 store 实例重放同一会话（崩溃恢复路径）。"""
    store_dir = __import__("tempfile").mkdtemp()
    store1 = VerdictStore(store_dir)
    v = store1.create("sess-p", "s1", "qc_filtering", "hard_threshold", "scrna",
                      "M1声明", status=VerdictStatus.PROVISIONAL)
    store1.transition(v.verdict_id, VerdictStatus.FINAL, "交叉验证一致")
    store2 = VerdictStore(store_dir)  # 全新实例（无内存缓存）
    records = store2.get("sess-p")
    assert len(records) == 1
    assert records[0].status == VerdictStatus.FINAL
    assert records[0].history[-1]["reason"] == "交叉验证一致"


# ── 11. 对齐键 + 多实例建模（B5）──


def test_multi_instance_iteration_modeling():
    """迭代调参：同类型多实例；声明命中被取代实例 → 一致但标注被取代。"""
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.tl.leiden(adata, resolution=0.8)\nsc.tl.louvain(adata)"
    )
    clustering = [c for c in m3.candidates if c.decision_type == "clustering_method"]
    assert [c.choice for c in clustering] == ["Leiden", "Louvain"]
    assert [c.instance_index for c in clustering] == [1, 2]
    m1 = [_m1("s1", "clustering_method", "Leiden")]
    result = CrossValidator(act="scrna").validate(m1, m3, session_id="sess-mi")
    # Louvain 未声明 → 漏报补入；Leiden 声明且执行 → 一致但被后续迭代取代
    fn = next(a for a in result.alignments
              if a.decision_type == "clustering_method" and a.auto_added)
    assert fn.status == STATUS_FALSE_NEGATIVE
    a = next(a for a in result.alignments
             if a.decision_type == "clustering_method" and a.m1 is not None)
    assert a.status == STATUS_CONSISTENT
    assert "被后续迭代取代" in a.detail
    assert a.instances[-1]["choice"] == "Louvain"  # operative = 最后实例


def test_multi_instance_claim_of_superseded_choice_rejected():
    """声明的是被取代实例的 choice → 与 operative 不符 → 虚报。"""
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.tl.leiden(adata, resolution=0.8)\nsc.tl.louvain(adata)"
    )
    m1 = [_m1("s1", "clustering_method", "Walktrap")]  # 从未执行
    result = CrossValidator(act="scrna").validate(m1, m3, session_id="sess-mi2")
    a = next(a for a in result.alignments
             if a.decision_type == "clustering_method" and a.m1 is not None)
    assert a.status == STATUS_FALSE_POSITIVE
    assert "Louvain" in a.detail


def test_parameter_level_alignment():
    """B5 参数级粒度：choice 一致但参数不同 → 一致 + 参数差异明细。"""
    m1 = [_m1("s1", "qc_filtering", "hard_threshold",
              {"min_genes": 200, "sequencing": "10X_scRNA_seq"})]
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.filter_cells(adata, min_genes=500)"  # 参数不同
    )
    result = CrossValidator(act="scrna").validate(m1, m3, session_id="sess-pa")
    a = result.alignments[0]
    assert a.status == STATUS_CONSISTENT
    assert "参数级差异" in a.detail
    assert "min_genes" in a.detail


def test_uncertain_evidence_only_declaration_is_false_positive():
    """M3 仅有未定证据（如 umap 投影）→ 声明无法证实 → 虚报（不猜）。"""
    m1 = [_m1("s1", "dim_reduction", "PCA_elbow_selection",
              {"sequencing": "10X_scRNA_seq"})]
    m3 = M3Parser(act="scrna", metadata=METADATA).parse_code(
        "sc.pp.pca(adata, n_comps=50)\nsc.tl.umap(adata)"
    )
    result = CrossValidator(act="scrna").validate(m1, m3, session_id="sess-u")
    a = next(a for a in result.alignments if a.decision_type == "dim_reduction")
    assert a.status == STATUS_FALSE_POSITIVE
    assert "未定证据" in a.detail
