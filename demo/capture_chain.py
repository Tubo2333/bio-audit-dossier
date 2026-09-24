"""63.7 复现链路共享模块（采集演示 · 机制层；工坊页现象层共用）。

设计依据：深色审计台设计规范 §6（63.7 复现技术说明）。
**单一事实源**：工坊页 `01_workshop._golden_b_chain`（现象层）与采集页
`02_capture` 的勾选实时重算（机制层）共用本模块——两处数字必须一致
（验收清单-2，验收会独立重算核对）。

输入全部为 demo/data 提炼副本（自包含性硬约束，禁止读仓库外
cellvoyager-outputs）：
- ``verdicts_10X_B.jsonl``：M1 声明重建（``provenance_source == "M1声明"``，
  按 verdict_id 去重取末条 → 11 条，含 skip_doublet——它会被交叉验证判虚报
  撤销，随后按 expected 补入）；
- ``golden_agent_10X_B_executed.py``：M3 解析输入（解析专用副本，不执行）；
- ``windowL_10X_B_expected.json``：63.7 断言基准（provenance 保留
  source=windowL_10X_B_expected.json）。

链路（设计 §6）：M1 重建 → M3 解析（79 候选）→ ``expected_types_for``
（11 决策）→ ``CrossValidator.validate(..., expected_types=...)`` →
stats{consistent 10, false_positive 1, expected_added 1} → final 11 决策
→ ``run_audit`` → **63.7 · blocked**（与断言基准实时比对）。

外围层纪律：只调 bioaudit.capture 公共类 + bioaudit.api.run_audit，
零评分路径改动（引擎/规则/本体/黄金资产零改动，golden 0 差异硬验收）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bioaudit.api.audit import run_audit
from bioaudit.capture.cross_validator import CrossValidator
from bioaudit.capture.expected_types import expected_types_for
from bioaudit.capture.m3_parser import M3Parser
from bioaudit.capture.models import PROVENANCE_SOURCE_M1

# demo/ 同目录模块（case_registry）在本模块的两种加载方式下都必须可解析：
# ① 常规运行（app.py / 页面已把 demo/ 放入 sys.path）；
# ② 测试按文件路径加载本模块（tests/test_demo_smoke.py 刻意不污染 sys.path）。
# 因此在此自包含保障——不依赖调用方设置路径（否则测试/独立加载会 ImportError）。
_DEMO_DIR = Path(__file__).resolve().parent
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

import case_registry  # noqa: E402  # 采集案例注册表（多案例单一事实源）

#: demo/data 提炼副本路径（模块自包含：不依赖 sys.path / cwd；启动校验
#: 由 app.py 的 verify_data_ready 统一把关）。
_DATA_DIR = Path(__file__).resolve().parent / "data"


def case_paths(case_id: str | None = None) -> tuple[Path, Path, Path]:
    """(verdicts, executed, benchmark) 三条提炼副本路径（按案例取）。

    案例元数据来自 ``case_registry``（单一事实源）；``None`` → 默认案例。
    """
    case = case_registry.get_case(case_id)
    return (
        _DATA_DIR / case.verdicts_file,
        _DATA_DIR / case.executed_file,
        _DATA_DIR / case.benchmark_file,
    )


#: 默认案例（= 现行演示案例；保持既有调用面行为不变）
DEFAULT_CASE = case_registry.get_case(None)

#: 兼容常量（默认案例的三条路径 / declared；既有引用不动）
VERDICTS_PATH = _DATA_DIR / DEFAULT_CASE.verdicts_file
EXECUTED_PATH = _DATA_DIR / DEFAULT_CASE.executed_file
BENCHMARK_PATH = _DATA_DIR / DEFAULT_CASE.benchmark_file
DECLARED: dict[str, str] = dict(DEFAULT_CASE.declared)


def load_m1_declarations(case_id: str | None = None) -> list[dict]:
    """从 verdicts 提炼副本重建 M1 声明（provenance_source == M1声明，
    按 verdict_id 去重取末条）。``case_id=None`` → 默认案例。"""
    verdicts_path = case_paths(case_id)[0]
    records: dict[str, dict] = {}
    for line in verdicts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("provenance_source") != PROVENANCE_SOURCE_M1:
            continue
        snap = rec.get("score_snapshot") or {}
        records[rec["verdict_id"]] = {
            "step_id": rec.get("step_id"),
            "decision_type": rec.get("decision_type"),
            "choice": rec.get("choice"),
            "rationale": snap.get("agent_rationale", ""),
            "context": snap.get("context", {}),
            "verdict_id": rec.get("verdict_id"),
        }
    return list(records.values())


def default_expected(case_id: str | None = None) -> list[str]:
    """该案例配置下的预期决策点清单（按范式 + declared 平台键求取）。"""
    case = case_registry.get_case(case_id)
    return expected_types_for(case.paradigm, dict(case.declared))


def run_chain(
    expected: Optional[list[str]] = None,
    case_id: str | None = None,
) -> dict:
    """完整采集链路重放（``expected`` 可注入——采集页勾选交互传子集）。

    Parameters
    ----------
    expected : list[str] | None
        预期决策点清单；``None`` → 该案例配置下的默认清单。
    case_id : str | None
        案例 id（见 ``case_registry``）；``None`` → 默认案例（行为与历史一致）。

    Returns
    -------
    dict
        n_m1 / m3_n_candidates / stats / alignments（序列化）/
        added / final_n / state（run_audit 结果）/ had_error /
        generated_at / benchmark（断言基准）/ case_id —— 键名与工坊页
        ``_golden_b_chain`` 返回一致（现象/机制共用同一形状）。
    """
    case = case_registry.get_case(case_id)
    _, executed_path, _ = case_paths(case.case_id)
    declared = dict(case.declared)

    m1 = load_m1_declarations(case.case_id)
    parser = M3Parser(act=case.paradigm, metadata=None, declared=declared)
    m3 = parser.parse_code(
        executed_path.read_text(encoding="utf-8"),
        source=case.executed_file,
    )
    expected_list = (
        list(expected) if expected is not None else default_expected(case.case_id)
    )

    result = CrossValidator(act=case.paradigm).validate(
        m1, m3,
        session_id=f"demo_capture_{case.case_id}",
        expected_types=expected_list,
        expected_context=declared,
    )

    # final 轨迹（final-only 消费纪律 B4）：一致声明 + 补入决策
    consistent_keys = {
        (a.m1["step_id"], a.m1["decision_type"])
        for a in result.alignments if a.status == "consistent" and a.m1
    }
    final_decisions = [
        {k: d[k] for k in ("step_id", "decision_type", "choice", "rationale", "context")}
        for d in m1 if (d["step_id"], d["decision_type"]) in consistent_keys
    ]
    final_decisions += [
        {k: d[k] for k in ("step_id", "decision_type", "choice", "rationale", "context")}
        for d in result.added_decisions
    ]
    state = run_audit(final_decisions, act=case.paradigm)
    had_error = bool(state.get("error"))
    if had_error:
        state.setdefault("trajectory_score", 0.0)
        state.setdefault("eval_verdict", "error")
        state.setdefault("dimension_scores", {})
        state.setdefault("step_scores", [])
        state.setdefault("critical_issues", [])

    return {
        "n_m1": len(m1),
        "m3_n_candidates": len(m3.candidates),
        "m3_n_uncertain": len(m3.uncertain),
        "stats": dict(result.stats),
        "alignments": [
            {
                "decision_type": a.decision_type,
                "status": a.status,
                "expected_added": a.expected_added,
                "auto_added": a.auto_added,
                "m1_choice": a.m1["choice"] if a.m1 else None,
                "m3_tool": a.m3["tool_call"] if a.m3 else None,
                "m3_choice": a.m3["choice"] if a.m3 else None,
                "n_instances": len(a.instances),
                "detail": a.detail,
            }
            for a in result.alignments
        ],
        "added": [
            {"decision_type": d["decision_type"], "choice": d["choice"],
             "context": d.get("context", {})}
            for d in result.added_decisions
        ],
        "final_n": len(final_decisions),
        "state": state,
        "had_error": had_error,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": load_benchmark(case.case_id),
        "case_id": case.case_id,
    }


def load_benchmark(case_id: str | None = None) -> dict:
    """该案例的断言基准（demo/data 提炼副本，provenance 保留 source）。

    结构兼容：10X-B 基准含 `expected_types` 块；黄金 A 等常规报告无该块
    （其 m3/m1/交叉验证结果内嵌）——缺失时 `expected_effective` 取 None，
    由调用方按 case 注册表的默认清单解释，不伪造数值。
    """
    case = case_registry.get_case(case_id)
    _, _, benchmark_path = case_paths(case.case_id)
    bench = json.loads(benchmark_path.read_text(encoding="utf-8"))
    expected_block = bench.get("expected_types") or {}
    return {
        # 源报告事实（UI 展示 + provenance；**不是**重放核验口径）
        "trajectory_score": bench["audit"]["trajectory_score"],
        "eval_verdict": bench["audit"]["eval_verdict"],
        "n_decisions": bench["final_trajectory"]["n_decisions"],
        "expected_effective": expected_block.get("effective"),
        "provenance": bench.get("provenance", {}),
        # 重放核验口径（来自案例注册表；与源报告不同 = 存在口径演进，
        # 如 windowI_B：源报告 63.0·blocked → J 后重评 69.0·needs_correction）
        "replay_score": case.expected_score,
        "replay_verdict": case.expected_verdict,
        "replay_n_decisions": case.expected_n_decisions,
        "replay_added_types": list(case.expected_added_types),
        "caliber_note": case.caliber_note,
    }


def chain_matches_benchmark(
    chain: dict,
    bench: dict,
    expected_added_types: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """实时重算 vs 核验基准（分数 + verdict + 决策数 + 补入类型）。

    基准口径：``bench["replay_*"]``（案例注册表的重放期望）优先；缺失时回落
    ``trajectory_score`` 等源报告字段（兼容：旧测试手工构造的 bench 字典）。
    ``expected_added_types`` 缺省从 ``chain["case_id"]`` 对应案例注册表读取；
    显式传入则以其为准。
    """
    state = chain["state"]
    mismatches: list[str] = []
    want_score = bench.get("replay_score", bench["trajectory_score"])
    want_verdict = bench.get("replay_verdict", bench["eval_verdict"])
    want_n = bench.get("replay_n_decisions", bench["n_decisions"])
    if abs(state["trajectory_score"] - want_score) >= 1e-9:
        mismatches.append(
            f"分数 {state['trajectory_score']:.1f} != 基准 {want_score:.1f}"
        )
    if state["eval_verdict"] != want_verdict:
        mismatches.append(
            f"verdict {state['eval_verdict']} != 基准 {want_verdict}"
        )
    if chain["final_n"] != want_n:
        mismatches.append(f"决策数 {chain['final_n']} != 基准 {want_n}")
    if expected_added_types is None:
        expected_added_types = bench.get("replay_added_types")
    if expected_added_types is None:
        expected_added_types = case_registry.get_case(
            chain.get("case_id")).expected_added_types
    added_types = [a["decision_type"] for a in chain["added"]]
    if added_types != list(expected_added_types):
        mismatches.append(
            f"补入类型 {added_types} != 预期 {list(expected_added_types)}"
        )
    return not mismatches, mismatches
