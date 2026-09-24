"""N-c 采集演示 smoke 测试（台账 §10.1-4 / demo-redesign-design v0.3 §5）。

组成（验收清单 §10.1 第 4 项）：
1. **AppTest 页面冒烟**：四页路由（审计工坊 / 采集演示 / 评测与奖励 / 关于）
   + 采集页 expected_types 勾选交互（取消勾选 doublet_detection → 80.0 pass
   「静默跳过不可见」；勾回 → 63.7 blocked 与断言基准一致）。
   streamlit 未安装时该组测试跳过（CI 已加装 demo extra——台账 §2.2 收口，
   CI 云上真跑）；
2. **golden 0 差异守卫**：20 轨迹分数 + verdict vs 冻结基线（紧凑版；
   全量逐决策明细守卫见 test_golden.py，不重复）；
3. **63.7 断言读 demo/data 提炼副本**（provenance 保留
   source=windowL_10X_B_expected.json）：M1 重建 11 条（含 skip_doublet）/
   M3 解析 executed.py 副本 79 候选 / expected_types_for 11 决策 /
   CrossValidator stats{consistent 10, false_positive 1, false_negative 0,
   unverified 0, expected_added 1} / final 11 决策 / run_audit →
   **63.7 · blocked** == 断言基准（与工坊页现象层共用 capture_chain 单一
   事实源，两处数字一致）。

外围层纪律：只调 bioaudit.api + capture 公共类 + demo/data 副本，
零评分路径改动（引擎/规则/本体/黄金资产零改动）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from bioaudit.regression import REPO_GOLDEN, replay_all

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"


def _load_capture_chain():
    """按文件路径加载 demo/capture_chain.py（不污染 sys.path；bioaudit 已
    editable 安装，capture_chain 的包内导入可直接解析）。"""
    spec = importlib.util.spec_from_file_location(
        "capture_chain", DEMO_DIR / "capture_chain.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── golden 0 差异守卫（紧凑版）────────────────────────────────────────

def test_golden_zero_diff_guard():
    expected = json.loads(Path(REPO_GOLDEN).read_text(encoding="utf-8"))
    actual = replay_all()

    assert actual["n_trajectories"] == expected["n_trajectories"] == 20
    assert actual["n_decisions"] == expected["n_decisions"] == 137

    exp_by_traj = {t["trajectory"]: t for t in expected["trajectories"]}
    act_by_traj = {t["trajectory"]: t for t in actual["trajectories"]}
    assert set(exp_by_traj) == set(act_by_traj), "轨迹集合不一致"

    for name, e in exp_by_traj.items():
        a = act_by_traj[name]
        assert a["trajectory_score"] == e["trajectory_score"], f"{name} 分数漂移"
        assert a["verdict"] == e["verdict"], f"{name} verdict 漂移"
        assert a["n_decisions"] == e["n_decisions"], f"{name} 决策数漂移"


# ── 63.7 断言（demo/data 提炼副本，独立重算链路）──────────────────────

def test_63_7_chain_matches_benchmark():
    """完整复现链路 vs 断言基准（台账 §10.1-4：63.7 断言读 demo/data 副本）。"""
    cc = _load_capture_chain()
    chain = cc.run_chain()
    bench = chain["benchmark"]

    # provenance 保留 source=windowL_10X_B_expected.json（提炼副本语义）
    assert bench["provenance"].get("source") == "windowL_10X_B_expected.json"

    # ① M1 声明重建（verdict_id 去重取末条 → 11 条，含 skip_doublet）
    m1 = cc.load_m1_declarations()
    assert len(m1) == 11
    m1_types = {d["decision_type"] for d in m1}
    assert "doublet_detection" in m1_types
    skip = next(d for d in m1 if d["decision_type"] == "doublet_detection")
    assert skip["choice"] == "skip_doublet"

    # ② M3 解析 executed.py 提炼副本（79 候选）
    assert chain["m3_n_candidates"] == 79

    # ③ expected_types_for("scrna", {"sequencing": "10X_scRNA_seq"}) → 11 决策
    expected = cc.default_expected()
    assert len(expected) == 11
    assert "doublet_detection" in expected

    # ④ 交叉验证 stats{consistent 10, false_positive 1, expected_added 1}
    assert chain["stats"] == {
        "consistent": 10,
        "false_positive": 1,
        "false_negative": 0,
        "unverified": 0,
        "expected_added": 1,
    }

    # ⑤ 补入 doublet_detection（choice 取 Agent 已撤销声明，不伪造）
    assert [a["decision_type"] for a in chain["added"]] == ["doublet_detection"]
    assert chain["added"][0]["choice"] == "skip_doublet"

    # ⑥ final 11 决策 → run_audit → 63.7 · blocked（与断言基准一致）
    state = chain["state"]
    assert chain["final_n"] == 11
    assert state["trajectory_score"] == pytest.approx(63.7)
    assert state["eval_verdict"] == "blocked"
    assert state["dimension_scores"] == {
        "data_handling": 0.6375,
        "method_selection": 0.8,
        "statistical_rigor": 0.85,
    }
    assert bench["trajectory_score"] == pytest.approx(63.7)
    assert bench["eval_verdict"] == "blocked"
    assert bench["n_decisions"] == 11
    ok, mismatches = cc.chain_matches_benchmark(chain, bench)
    assert ok, mismatches


def test_63_7_interaction_contrast_unchecked_doublet():
    """机制交互对比：取消勾选 doublet_detection → 跳过不可见 → 80.0 · pass。

    （勾选/取消即预期清单增删；本案例唯一缺失的预期决策点为
    doublet_detection——静默跳过在预期清单外不可见。）
    """
    cc = _load_capture_chain()
    expected = cc.default_expected()
    chain = cc.run_chain([t for t in expected if t != "doublet_detection"])
    state = chain["state"]
    assert chain["final_n"] == 10
    assert chain["stats"]["expected_added"] == 0
    assert [a["decision_type"] for a in chain["added"]] == []
    assert state["trajectory_score"] == pytest.approx(80.0)
    assert state["eval_verdict"] == "pass"


# ── AppTest 页面冒烟（需要 streamlit；CI 已加装 demo extra）────────────

def _apptest():
    pytest.importorskip("streamlit", reason="需要 streamlit（pip install -e '.[demo]'）")
    from streamlit.testing.v1 import AppTest
    return AppTest


def _all_markdown(app) -> str:
    return "".join(m.value for m in app.markdown)


def test_apptest_four_pages_smoke():
    """四页路由冒烟：默认页 + 逐页切换无异常，采集页机制结果渲染。"""
    AppTest = _apptest()
    app = AppTest.from_file(str(DEMO_DIR / "app.py"), default_timeout=120)
    app.run(timeout=120)
    assert not app.exception, f"默认页异常: {app.exception}"

    for pid, marker in [
        ("capture", "声明 vs 事实对齐表"),
        ("benchmark", "评测与奖励"),
        ("about", "关于"),
        ("workshop", "审计工坊"),
    ]:
        app.sidebar.radio[0].set_value(pid)
        app.run(timeout=120)
        assert not app.exception, f"{pid} 页面异常: {app.exception}"
        assert marker in _all_markdown(app), f"{pid} 页面缺标记 {marker!r}"
        if pid == "capture":
            # 采集页默认态（全选）应已完成机制重算并与断言基准一致
            assert "与断言基准一致" in _all_markdown(app), "采集页默认态应一致"
            assert "63.7" in _all_markdown(app), "采集页默认态应显示 63.7"


def test_apptest_capture_interaction():
    """采集页 expected_types 勾选交互：取消 doublet → 80.0 pass；勾回 → 63.7。"""
    AppTest = _apptest()
    app = AppTest.from_file(str(DEMO_DIR / "app.py"), default_timeout=120)
    app.run(timeout=120)
    app.sidebar.radio[0].set_value("capture")
    app.run(timeout=120)
    assert not app.exception

    checkbox = next(
        (c for c in app.checkbox if "doublet_detection" in c.label), None
    )
    assert checkbox is not None, "采集页缺 doublet_detection 勾选框"
    assert checkbox.value is True, "默认应全选"

    # 取消勾选 → 静默跳过不可见 → 80.0 · pass + 与断言基准不一致
    checkbox.set_value(False)
    app.run(timeout=120)
    assert not app.exception
    md = _all_markdown(app)
    assert "与断言基准不一致" in md, "取消勾选后应提示与断言基准不一致"
    assert "80.0" in md, "取消勾选后应显示 80.0"

    # 勾回 → 补入 → 63.7 · blocked == 断言基准
    next(c for c in app.checkbox if "doublet_detection" in c.label).set_value(True)
    app.run(timeout=120)
    assert not app.exception
    md = _all_markdown(app)
    assert "与断言基准一致" in md, "勾回后应与断言基准一致"
    assert "63.7" in md and "blocked" in md
