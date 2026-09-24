"""demo 数据提炼脚本：cellvoyager-outputs → demo/data/。

设计依据：深色审计台设计规范 §5（数据组织）/§6（63.7 复现）+ 验收 §2.1
（数据源钉死表）。

纪律：
- 全部分数从源产物**读取提炼**，零硬编码；数字锚点断言仅作防漂移护栏
  （源值意外变化 → 脚本报错退出，不静默写坏数据）；
- 每个摘要带 provenance（source / source_sha256 / generated_at / exported_at）；
- **剥离 Windows 绝对路径**（输出文件全库扫描，含 `D:\\`/`C:\\` 即报错）；
- 输出只写 demo/data/；源读取仅 cellvoyager-outputs（--sources 可覆盖）与
  本仓库内资产（trajectories v2 / golden 基线 / scrna_r0.json）。

用法（源根必给，脚本内不写死本机路径）：
    python demo/scripts/export_demo_data.py --sources <cellvoyager-outputs 根>
    # 或先设环境变量 BIOAUDIT_DEMO_SOURCES，再不带参数运行

**不可独立运行**：源数据在仓库外，且数处数字锚点取自未随本仓发布的内部
评测记录。本脚本在此的用途是**登记提炼口径**（每个数字的来源与护栏值），
不是提供可直接复跑的工具。

输出清单（demo/data/）：
    manifest.json                   指纹 + 来源登记（启动校验依据）
    golden_summary.json             黄金对照 ×5（80.0/69.0/66.7/80.0/63.7）
    eval_summary.json               真实评测 ×2（30.0×2，G/L-b）
    benchmark_summary.json          benchmark 摘要（recall/precision/F1/gap/IRR）
    r0_summary.json                 R0 锚定（ρ=0.9747）
    reward_summary.json             reward 校准摘要（映射 + mask + spike-in）
    engineering_summary.json        工程数字摘要（测试数/CI/golden 口径）
    trajectories_index.json         20 条轨迹索引（含 golden 基线分数）
    verdicts 与 executed.py、断言基准按**案例注册表**（demo/case_registry.py）
    逐案例提炼（多案例共用同一套逻辑；新增案例 = 注册表加条目 + CAPTURE_SOURCES
    补源路径）：
      windowL_10X_B_expected        黄金对照 B（10X，63.7 断言基准）
      windowI_A                     黄金对照 A（Smart-seq2，80.0 断言基准）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "demo" / "data"
#: 源根**不写死在本脚本里**（源数据在仓库外，不入 git，公开仓不持有）。
#: 取值顺序：``--sources`` 参数 → 环境变量 ``BIOAUDIT_DEMO_SOURCES`` → 报错退出。
SOURCES_ENV = "BIOAUDIT_DEMO_SOURCES"


def _resolve_sources(cli_value: Path | None,
                     parser: argparse.ArgumentParser) -> Path:
    """解析源根：``--sources`` → 环境变量 → 报错退出。不内置任何本机路径。"""
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get(SOURCES_ENV, "").strip()
    if env_value:
        return Path(env_value)
    parser.error(
        f"必须给出 --sources（或设置环境变量 {SOURCES_ENV}）："
        "源数据在仓库外，不随本仓库分发")

# 必需输出文件清单与 data_index.REQUIRED_FILES 共享（单一事实源防漂移）；
# manifest.json 自身单独追加（它登记所有文件指纹）
sys.path.insert(0, str(REPO_ROOT / "demo"))
from data_index import REQUIRED_FILES  # noqa: E402

import case_registry  # noqa: E402  # 采集案例注册表（目标命名/语义/断言的事实源）

OUT_FILES: tuple[str, ...] = REQUIRED_FILES + ("manifest.json",)

#: Windows 绝对路径模式（剥离目标：输出文件不得含这些）。
#: 反斜杠形态要求 ≥2 级路径（盘符 + 至少两个分隔符），避免把普通文本里的
#: ``d:\\n``（如 "failed:\\n" 转义）误判为盘符路径；另覆盖 UNC。
#: 注意：**不**支持正斜杠盘符形态（``C:/dir``）——它无法与 URL 的
#: ``https://``（``s://``）区分，实测源产物无此形态。
_WIN_PATH = re.compile(
    r"(?:[A-Za-z]:\\(?:[^\"'\n]*\\)+[^\"'\n]*"  # C:\dir\file（≥2 级）
    r"|\\\\[^\"'\n]+)"                            # \\server\share（UNC）
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def provenance(source: str, source_sha256: str, generated_at: str | None,
               note: str = "") -> dict:
    """统一 provenance 块（source / source_sha256 / generated_at / exported_at）。"""
    p = {
        "source": source,
        "source_sha256": source_sha256,
        "generated_at": generated_at or "",
        "exported_at": _now(),
    }
    if note:
        p["note"] = note
    return p


def _strip_win_paths(text: str) -> str:
    return _WIN_PATH.sub("<absolute-path-stripped>", text)


def sanitize_obj(obj):
    """递归剥离 dict/list 中的 Windows 绝对路径字符串。"""
    if isinstance(obj, dict):
        return {k: sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_obj(v) for v in obj]
    if isinstance(obj, str):
        return _WIN_PATH.sub("<absolute-path-stripped>", obj)
    return obj


def _read_json(path: Path, *, bom: bool = False) -> dict:
    enc = "utf-8-sig" if bom else "utf-8"
    return json.loads(path.read_text(encoding=enc))


# ═══════════════════════════════════════════════════════════════
# 黄金对照摘要（windowI A/B/C + windowL 10X A/B_expected）
# ═══════════════════════════════════════════════════════════════
def extract_golden(src: Path) -> dict:
    reports = {
        "windowI_A": src / "windowI" / "reports" / "windowI_A.json",
        "windowI_B": src / "windowI" / "reports" / "windowI_B.json",
        "windowI_C": src / "windowI" / "reports" / "windowI_C.json",
        "windowL_10X_A": src / "windowL" / "reports" / "windowL_10X_A.json",
        "windowL_10X_B_expected": src / "windowL" / "reports" / "windowL_10X_B_expected.json",
    }
    entries: list[dict] = []
    for key, path in reports.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少黄金对照源文件: {path}")
        raw = _read_json(path)
        audit = raw["audit"]
        source_sha = _sha256_file(path)
        common = {
            "id": key,
            "paradigm": "scrna",
            "window": "I" if key.startswith("windowI") else "L",
            "platform": "smartseq2" if key.startswith("windowI") else "10X_scRNA_seq",
            "trajectory_score": audit["trajectory_score"],
            "eval_verdict": audit["eval_verdict"],
            "n_decisions": len(audit.get("per_decision", [])),
            "dimension_scores": audit.get("dimension_scores", {}),
            "critical_issues": audit.get("critical_issues", []),
            "provenance": provenance(path.name, source_sha,
                                     raw.get("generated_at")),
        }
        if key == "windowI_A":
            common["note"] = "黄金对照 A（Smart-seq2）· 10 决策"
        elif key == "windowI_B":
            # 原始 63.0 blocked（源报告实测）→ 复核后重评 69.0 needs_correction
            # （复核记录锚点；二次复核不变 80.0/69.0/66.7）
            j1 = (REPO_ROOT / "docs" / "migration" / "J1-rule-quality-report.md")
            m = re.search(r"\*\*69\.0 · needs_correction", j1.read_text(encoding="utf-8"))
            if not m:
                raise AssertionError("J1 报告未找到 69.0 · needs_correction 锚点")
            common["trajectory_score"] = 69.0
            common["eval_verdict"] = "needs_correction"
            common["score_original"] = audit["trajectory_score"]  # 63.0
            common["verdict_original"] = audit["eval_verdict"]  # blocked
            common["provenance"]["note"] = (
                "原始 63.0·blocked 实测于 windowI_B.json；"
                "69.0·needs_correction 为复核后重评值（"
                "二次复核不变）"
            )
            common["note"] = ("黄金对照 B（Smart-seq2；复核后重评）"
                              "63.0 → 69.0 · needs_correction")
        elif key == "windowI_C":
            common["note"] = ("黄金对照 C（Smart-seq2）· 66.7 仅限 "
                              "Smart-seq2-C 口径（QC 硬阈值），禁止与 63.7 混写")
        elif key == "windowL_10X_A":
            common["note"] = "黄金对照 A（10X）· 11 决策含双联体检测"
        else:  # windowL_10X_B_expected
            common["note"] = ("黄金对照 B（10X，expected_types 后）· "
                              "63.7 仅限 10X-B expected 口径（静默跳过双联体被补入），"
                              "禁止与 66.7 混写")
            common["expected_types_n_added"] = raw.get("cross_validate", {}).get("n_added")
        entries.append(common)

    # ── 数字锚点断言（防漂移护栏，非数据源）────────────────
    by_id = {e["id"]: e for e in entries}
    assert by_id["windowI_A"]["trajectory_score"] == 80.0, "windowI_A 分数漂移"
    assert by_id["windowI_A"]["eval_verdict"] == "pass"
    assert by_id["windowI_B"]["score_original"] == 63.0, "windowI_B 原始分漂移"
    assert by_id["windowI_B"]["trajectory_score"] == 69.0, "windowI_B 重评分漂移"
    assert by_id["windowI_B"]["eval_verdict"] == "needs_correction"
    assert by_id["windowI_C"]["trajectory_score"] == 66.7, "windowI_C 分数漂移"
    assert by_id["windowI_C"]["eval_verdict"] == "needs_correction"
    assert by_id["windowL_10X_A"]["trajectory_score"] == 80.0, "10X_A 分数漂移"
    assert by_id["windowL_10X_A"]["n_decisions"] == 11, "10X_A 决策数漂移"
    assert by_id["windowL_10X_B_expected"]["trajectory_score"] == 63.7, "63.7 漂移"
    assert by_id["windowL_10X_B_expected"]["eval_verdict"] == "blocked"
    return {"entries": entries,
            "provenance": provenance("windowI_A/B/C + windowL_10X_A/B_expected.json",
                                     _sha256_file(reports["windowI_A"]),
                                     None,
                                     "五份黄金对照报告提炼；口径分列见各条目 note；"
                                     "source_sha256 为聚合块引用值，各条目含独立指纹")}


# ═══════════════════════════════════════════════════════════════
# 真实评测摘要（CellVoyager G / L-b）
# ═══════════════════════════════════════════════════════════════
def extract_eval(src: Path) -> dict:
    g_reeval = src / "reports" / "windowG_reeval.json"
    k1_reeval = src / "reports" / "windowK1_reeval.json"
    lb = src / "windowL" / "reports" / "windowLb_analysis.json"
    for p in (g_reeval, k1_reeval, lb):
        if not p.exists():
            raise FileNotFoundError(f"缺少真实评测源文件: {p}")

    g = _read_json(g_reeval)
    k1 = _read_json(k1_reeval)  # 顶层即结果（无 audit 键，结构异于 g/lb）
    lbr = _read_json(lb)

    g_audit = g["audit"]
    assert g_audit["trajectory_score"] == 30.0, "windowG_reeval 分数漂移"
    assert k1["trajectory_score"] == 30.0, "windowK1_reeval 分数漂移"
    assert k1["eval_verdict"] == "needs_correction", "复核后重评 verdict 漂移"
    assert k1["level_counts"].get("1") == 19, "复核后重评 L1 计数漂移"
    assert lbr["audit"]["trajectory_score"] == 30.0, "windowLb 分数漂移"
    # level 计数统一字符串键（JSON 键序列化一致性；重评报告源为字符串键、lb 源为 int 键）
    lb_levels = {str(k): v for k, v in sorted(
        Counter(d.get("level") for d in lbr["audit"]["per_decision"]).items())}

    # ── 成本（出处 = 评测报告权威口径，与导出数据同源链）────
    # 长评测：评测报告 §2（总花费 ¥2.55，平台余额 39.53 → 36.98 权威口径）+
    #     其补充报告 §1（成本 ¥2.55）；短评测：评测报告 §7.2
    #     （平台余额差权威口径 ¥0.43，22.53 → 22.10；usage 换算口径 ¥0.90 留档）
    g_report = REPO_ROOT / "docs" / "migration" / "agent-eval-report.md"
    g2_report = REPO_ROOT / "docs" / "migration" / "agent-eval-report-g2.md"
    l1_report = REPO_ROOT / "docs" / "migration" / "L1-broader-eval-report.md"
    g_text = g_report.read_text(encoding="utf-8")
    g2_text = g2_report.read_text(encoding="utf-8")
    l1_text = l1_report.read_text(encoding="utf-8")
    m_cost_g = re.search(r"总花费（窗口 G）.*?¥([\d.]+)", g_text)
    if not m_cost_g:
        raise AssertionError("评测报告未找到总花费 ¥2.55 锚点")
    cost_g = float(m_cost_g.group(1))
    assert abs(cost_g - 2.55) < 1e-9, f"长评测成本锚点漂移: {cost_g}"
    assert "成本 ¥2.55" in g2_text, "补充报告未找到成本 ¥2.55 锚点"
    m_cost_lb = re.search(r"成本（平台余额差，权威口径）.*?¥([\d.]+)", l1_text)
    if not m_cost_lb:
        raise AssertionError("L1 报告未找到成本 ¥0.43 锚点")
    cost_lb = float(m_cost_lb.group(1))
    assert abs(cost_lb - 0.43) < 1e-9, f"L-b 成本锚点漂移: {cost_lb}"

    return {
        "runs": [
            {
                "id": "cellvoyager_g",
                "label": "CellVoyager 真实评测（复核后重评）",
                "trajectory_score": 30.0,
                "eval_verdict": "needs_correction",
                "n_decisions": len(k1["per_decision"]),
                "level_counts": k1["level_counts"],
                "note": ("30.0 · needs_correction（L1×19/L3×1）——复核后重评口径"
                         "（windowK1_reeval.json，ruleset "
                         "1.5.0）；更早一次重评同分 30.0 但 L1×7/L3×1/L-1×12"
                         "（windowG_reeval.json，ruleset 1.2.0）"),
                "cost": {
                    "amount": cost_g,
                    "currency": "CNY",
                    "caliber": "平台余额差（权威口径），预算 ¥5 内未超支",
                    "source": ("评测报告 §2（余额 39.53 → 36.98）+ "
                               "其补充报告 §1"),
                },
                "provenance": provenance(
                    "windowG_reeval.json + windowK1_reeval.json",
                    _sha256_file(k1_reeval),
                    k1.get("generated_at"),
                    "分数两版同 30.0；level 分布以复核后重评为准",
                ),
            },
            {
                "id": "cellvoyager_lb",
                "label": "CellVoyager 短评测",
                "trajectory_score": 30.0,
                "eval_verdict": "needs_correction",
                "n_decisions": len(lbr["audit"]["per_decision"]),
                "level_counts": lb_levels,
                "note": "30.0 · needs_correction（L1×4/L2×1，5 决策）",
                "cost": {
                    "amount": cost_lb,
                    "currency": "CNY",
                    "caliber": "平台余额差（权威口径），预算 ¥5 内未超支",
                    "source": ("评测报告 §7.2（余额 22.53 → 22.10；"
                               "usage 换算口径 ¥0.90 留档）"),
                },
                "provenance": provenance("windowLb_analysis.json",
                                         _sha256_file(lb),
                                         lbr.get("generated_at")),
            },
        ],
        "provenance": provenance("windowG_reeval + windowK1_reeval + windowLb_analysis",
                                 _sha256_file(lb), None,
                                 "真实评测两轮 30.0 均 needs_correction；29/30 双口径"
                                 "页内注释见审计工坊页"),
    }


# ═══════════════════════════════════════════════════════════════
# benchmark 摘要（60 任务 aggregate + gap + IRR）
# ═══════════════════════════════════════════════════════════════
def extract_benchmark(src: Path) -> dict:
    base = src / "reports" / "benchmark_run_baseline.json"
    if not base.exists():
        raise FileNotFoundError(f"缺少 benchmark 源文件: {base}")
    raw = _read_json(base, bom=True)  # 源文件带 UTF-8 BOM

    f1 = (REPO_ROOT / "docs" / "migration" / "F1-phase3-benchmark-batch2-report.md")
    m1 = (REPO_ROOT / "docs" / "migration" / "M1-capture-integrity-report.md")
    f1_text = f1.read_text(encoding="utf-8")
    m1_text = m1.read_text(encoding="utf-8")

    irr = re.search(r"全量 60 合并 IRR κ=([\d.]+)\s*/\s*α=([\d.]+)", f1_text)
    if not irr:
        raise AssertionError("评测报告未找到 IRR κ 锚点")
    kappa = float(irr.group(1))
    assert abs(kappa - 0.8336) < 1e-9, f"IRR κ 锚点漂移: {kappa}"
    # 决策数/一致率同样从评测报告解析（防硬编码无锚点漂移）；跨行匹配
    # "全量 60 合并 IRR κ=…**（623 决策，\n一致率 93.58%）"
    m60 = re.search(r"全量 60 合并[^\n]*?（(\d+) 决策，\s*一致率 ([\d.]+)%",
                    f1_text, re.DOTALL)
    if not m60:
        raise AssertionError("评测报告未找到 623 决策/一致率锚点")
    n_decisions = int(m60.group(1))
    agreement = float(m60.group(2)) / 100.0
    assert n_decisions == 623, f"F1 决策数锚点漂移: {n_decisions}"
    assert abs(agreement - 0.9358) < 1e-9, f"F1 一致率锚点漂移: {agreement}"

    gap_after = re.search(r"\*\*\+0\.0449\*\*", m1_text)
    if not gap_after:
        raise AssertionError("M1 报告未找到 gap +0.0449 锚点")

    agg = raw["aggregate"]["overall"]
    det = agg["detection"]
    assert det["recall"] == 0.82, "recall 漂移"
    assert abs(det["precision"] - 0.7454545454545455) < 1e-9, "precision 漂移"
    assert abs(det["f1"] - 0.780952380952381) < 1e-9, "f1 漂移"
    gap = raw["gap"]
    assert gap["delta"] == 0.046, "gap delta 漂移"

    return {
        "taskset_version": raw["taskset_version"],
        "n_tasks_run": raw["n_tasks_run"],
        "n_decisions": agg["n_decisions"],
        "n_gold_error": agg["n_gold_error"],
        "n_gold_correct": agg["n_gold_correct"],
        "detection": {
            "recall": det["recall"],
            "precision": det["precision"],
            "f1": det["f1"],
            "edge_detection_rate": agg["edge_handling"]["edge_detection_rate"],
            "mean_score": agg["mean_score"]["point"],
        },
        "strata": raw["aggregate"]["strata"],
        "comparisons": raw["aggregate"]["comparisons"],
        "gap": {
            "delta": gap["delta"],
            "tolerance_interval": gap["tolerance_interval"],
            "in_tolerance": gap["in_tolerance"],
            "delta_after_m": 0.0449,
            "note": "delta 读自 benchmark_run_baseline.json（基线产物）；"
                    "delta_after_m = 复核后复跑，"
                    "预注册口径 ±0.10 区间内无告警",
        },
        "irr": {
            "kappa": kappa,
            "alpha": float(irr.group(2)),
            "n_decisions": n_decisions,
            "agreement": agreement,
            "note": "全量 60 合并 IRR（评测报告 "
                    "§F2，623 决策，一致率 93.58%）",
        },
        "provenance": provenance("benchmark_run_baseline.json",
                                 _sha256_file(base),
                                 raw.get("generated_at"),
                                 "IRR κ 出处评测报告（基准产物无此字段，从报告提炼）"),
    }


# ═══════════════════════════════════════════════════════════════
# R0 锚定摘要
# ═══════════════════════════════════════════════════════════════
def extract_r0() -> dict:
    r0_path = REPO_ROOT / "src" / "bioaudit" / "data" / "validation" / "scrna_r0.json"
    raw = _read_json(r0_path)
    r0 = raw["R0_scRNA"]
    assert "0.9747" in r0["key_metric"], "R0 ρ 锚点漂移"
    return {
        "key_metric": r0["key_metric"],
        "status": r0["status"],
        "detail": r0["detail"],
        "limit": r0["limit"],
        "provenance": provenance("scrna_r0.json",
                                 _sha256_file(r0_path),
                                 None,
                                 "现行版本（复核后更新）"),
    }


# ═══════════════════════════════════════════════════════════════
# reward 校准摘要（映射 + mask 语义 + spike-in 掉分）
# 出处 = reward-mapping.md（宪法）+ reward 校准报告 §三.9 + reward-protocol §七.2
# ═══════════════════════════════════════════════════════════════
def extract_reward() -> dict:
    """reward 校准摘要：level→reward 映射（-1 mask）+ spike-in 三范式掉分。

    数字锚点全部从宪法/报告解析断言（防漂移护栏，与 IRR/gap 同款链路）：
    - 映射表锚点：reward-mapping.md §2 表格逐行正则（{4:1.00, 3:0.85, 2:0.60,
      1:0.30, 0:0.00}）；mask 语义锚点 = "不参与分子也不参与分母"（§3）；
    - 聚合锚点：mean（§5）+ 硬惩罚 ×0.30（§6）；
    - spike-in 实测锚点：reward 校准报告 §三.9 三范式行（0.85 → 0.2354/0.2125/0.2400，
      drop 0.6146/0.6375/0.6100）+ reward-protocol §七.2 交叉锚点；
    - 状态锚点：experimental_uncalibrated（reward 校准报告 §六）。
    """
    e4 = REPO_ROOT / "docs" / "migration" / "E4-phase4-reward-report.md"
    mapping_doc = REPO_ROOT / "docs" / "reward-mapping.md"
    protocol = REPO_ROOT / "docs" / "reward-protocol.md"
    e4_text = e4.read_text(encoding="utf-8")
    map_text = mapping_doc.read_text(encoding="utf-8")
    proto_text = protocol.read_text(encoding="utf-8")

    # 映射表（宪法 §2 冻结值；逐行锚点断言）
    mapping = {4: 1.0, 3: 0.85, 2: 0.6, 1: 0.3, 0: 0.0}
    for level, value in mapping.items():
        pat = rf"\| {level} \|[^|]*\| {value:.2f} \|"
        if not re.search(pat, map_text):
            raise AssertionError(f"reward-mapping.md 未找到 L{level} → {value:.2f} 锚点")
    # mask 语义（§3）与聚合（§5 mean / §6 γ=0.30）
    if "不参与分子也不参与分母" not in map_text:
        raise AssertionError("reward-mapping.md 未找到 -1 mask 语义锚点")
    if "聚合语义 | **mean**" not in map_text or "× **0.30**" not in map_text:
        raise AssertionError("reward-mapping.md 未找到 mean / γ=0.30 锚点")
    if "experimental_uncalibrated" not in e4_text:
        raise AssertionError("reward 校准报告未找到 experimental_uncalibrated 锚点")

    # spike-in 三范式（reward 校准报告 §三.9 实测行解析）
    spike_in = []
    for paradigm in ("scrna", "deg", "pan"):
        m = re.search(
            rf"{paradigm}_correct \+ `([a-z_0-9]+)`[^\n]*?→ \*\*0\.85 → "
            r"([\d.]+)，drop = ([\d.]+)\*\*",
            e4_text)
        if not m:
            raise AssertionError(f"reward 校准报告未找到 {paradigm} spike-in 锚点")
        injection, after, drop = m.group(1), float(m.group(2)), float(m.group(3))
        assert abs(drop - (0.85 - after)) < 1e-9, f"{paradigm} spike-in drop 不自洽"
        spike_in.append({
            "paradigm": paradigm,
            "trajectory": f"{paradigm}_correct",
            "injection": injection,
            "before": 0.85,
            "after": after,
            "drop": drop,
            "threshold": 0.30,
        })
    if not re.search(r"drop = 0\.6146", proto_text):
        raise AssertionError("reward-protocol §七.2 未找到 spike-in 锚点")

    return {
        "mapping": {str(k): v for k, v in mapping.items()},
        "mask": {
            "level": -1,
            "semantic": ("mask（None）——不参与分子也不参与分母；全 mask → "
                         "trajectory_reward=None（不给 0 虚假信号）"),
        },
        "aggregation": ("mean（未 mask 步骤均值）+ 配方 B 硬惩罚 γ=0.30"
                        "（任一未 mask L0，二元不复利）"),
        "spike_in": spike_in,
        "spike_threshold": 0.30,
        "status": "experimental_uncalibrated",
        "status_note": ("reward 是训练信号候选，未经过 RLHF 校准；任何消费方不得把 "
                        "reward 当校准信号用于生产决策（reward 校准报告 §六）"),
        "provenance": provenance(
            "reward 校准报告 §三.9 + reward-protocol.md §七.2 + "
            "reward-mapping.md §2",
            _sha256_file(e4), None,
            "mapping/mask/γ 锚点 = reward-mapping.md（宪法）；spike-in 实测 = reward 校准报告"
            "+ reward-protocol"),
    }


# ═══════════════════════════════════════════════════════════════
# 工程数字摘要（测试数 / CI 矩阵 / golden 守卫口径）
# ═══════════════════════════════════════════════════════════════
def extract_engineering() -> dict:
    """工程数字摘要：n_tests = pytest --collect-only -q 实测（与 pytest 实跑同源）。

    ci_matrix_versions 从 .github/workflows/ci.yml 双矩阵声明解析（锚点断言）；
    golden_diff 为提交级守卫口径（golden 0 差异硬验收，防漂移护栏 = 测试本身）。
    零硬编码：测试数随仓库实际收集数变化，防漂移。
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"pytest --collect-only 失败（exit {proc.returncode}）："
            f"{proc.stderr[-300:] or proc.stdout[-300:]}")
    m = re.search(r"(\d+) tests? collected", proc.stdout + proc.stderr)
    if not m:
        raise AssertionError(
            f"pytest --collect-only 未能解析收集数: {proc.stdout[-300:]}")
    n_tests = int(m.group(1))

    # CI 双矩阵版本（ci.yml 锚点解析）
    ci_yml = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    ci_text = ci_yml.read_text(encoding="utf-8")
    m = re.search(r'python-version:\s*\["([\d.]+)",\s*"([\d.]+)"\]', ci_text)
    if not m:
        raise AssertionError("ci.yml 未找到 python-version 双矩阵锚点")
    ci_matrix_versions = [m.group(1), m.group(2)]

    golden = REPO_ROOT / "tests" / "golden" / "golden_expected_output_after.json"
    return {
        "n_tests": n_tests,
        "note": "pytest --collect-only -q 实测（与 pytest 实跑同源）",
        "ci_matrix": ("Python 3.10 / 3.12 双矩阵（pytest 全量 + golden 重放 + "
                      "三/四/五闸 + reward-validate；.github/workflows/ci.yml）"),
        "ci_matrix_versions": ci_matrix_versions,
        "golden": ("20 轨迹 137 决策重放 0 差异（tests/golden/"
                   "golden_expected_output_after.json 冻结基线；提交级守卫）"),
        "golden_diff": 0,
        "provenance": provenance(
            "pytest --collect-only -q + ci.yml + golden 基线",
            _sha256_file(golden), None,
            "工程数字提炼；n_tests 与 pytest 实跑同源；"
            "ci_matrix_versions 读 ci.yml 锚点"),
    }


# ═══════════════════════════════════════════════════════════════
# 轨迹索引（v2 目录 + golden 基线提炼）
# ═══════════════════════════════════════════════════════════════
def extract_trajectories() -> dict:
    golden = REPO_ROOT / "tests" / "golden" / "golden_expected_output_after.json"
    v2_dir = REPO_ROOT / "src" / "bioaudit" / "data" / "trajectories" / "v2"
    if not golden.exists():
        raise FileNotFoundError(f"缺少 golden 基线: {golden}")

    raw = _read_json(golden)
    # 兼容两种容器：dict{n_trajectories,trajectories:[...]} / 裸 list
    rows = raw["trajectories"] if isinstance(raw, dict) else raw
    assert len(rows) == 20, f"golden 基线应为 20 条轨迹，实得 {len(rows)}"

    index = []
    for row in rows:
        traj_id = row["trajectory"]
        f = v2_dir / f"{traj_id}.json"
        if not f.exists():
            raise FileNotFoundError(f"golden 基线引用的轨迹缺失: {f.name}")
        index.append({
            "trajectory_id": traj_id,
            "act": row["act"],
            "n_decisions": row["n_decisions"],
            "golden_score": row["trajectory_score"],
            "golden_verdict": row["verdict"],
            "source_file": f.name,
        })

    # 数字锚点（设计 §1.3 / 验收 §2.1：85.0×6 / 60.0·pass / 29.0）
    n_85 = sum(1 for r in index if r["golden_score"] == 85.0)
    assert n_85 == 6, f"85.0 轨迹数漂移: {n_85}"
    single = next(r for r in index if r["trajectory_id"] == "scrna_edge_singleanno")
    assert single["golden_score"] == 60.0 and single["golden_verdict"] == "pass", \
        "scrna_edge_singleanno 锚点漂移"
    cv = next(r for r in index if r["trajectory_id"] == "scrna_melanoma_cellvoyager")
    assert cv["golden_score"] == 29.0 and cv["golden_verdict"] == "blocked", \
        "scrna_melanoma_cellvoyager 锚点漂移"

    return {
        "n_trajectories": len(index),
        "trajectories": sorted(index, key=lambda r: r["trajectory_id"]),
        "provenance": provenance(
            "golden_expected_output_after.json + trajectories/v2",
            _sha256_file(golden),
            None,
            "分数/verdict 来自 golden 基线（当前引擎实测，golden 0 差异）；"
            "轨迹本体运行时读 bioaudit 包内 v2 目录",
        ),
    }


# ═══════════════════════════════════════════════════════════════
# 输入副本（verdicts jsonl / executed.py / 63.7 断言基准）
# ═══════════════════════════════════════════════════════════════
#: 案例 id → (verdicts 源, executed 源, benchmark 源)，相对 cellvoyager-outputs 根。
#: 目标文件名、范式、declared 与断言分数来自 ``case_registry``（案例语义的
#: 单一事实源）；本表只登记**源布局**（属导出侧关注点）。
CAPTURE_SOURCES: dict[str, tuple[str, str, str]] = {
    "windowL_10X_B_expected": (
        "data/verdicts/golden_winL_10X_B_20260816.jsonl",
        "windowL/runs/10X_B/golden_agent_10X_B_executed.py",
        "windowL/reports/windowL_10X_B_expected.json",
    ),
    "windowI_A": (
        "data/verdicts/golden_winI_A_20260816.jsonl",
        "windowI/runs/A/golden_agent_A_executed.py",
        "windowI/reports/windowI_A.json",
    ),
    "windowI_B": (
        "data/verdicts/golden_winI_B_20260816.jsonl",
        "windowI/runs/B/golden_agent_B_executed.py",
        "windowI/reports/windowI_B.json",
    ),
    "windowI_C": (
        "data/verdicts/golden_winI_C_20260816.jsonl",
        "windowI/runs/C/golden_agent_C_executed.py",
        "windowI/reports/windowI_C.json",
    ),
    "windowL_10X_A": (
        "data/verdicts/golden_winL_10X_A_20260816.jsonl",
        "windowL/runs/10X_A/golden_agent_10X_A_executed.py",
        "windowL/reports/windowL_10X_A.json",
    ),
    "cellvoyager_g": (
        "data/verdicts/cv_gse115978_winG_20260816.jsonl",
        "runs/GSE115978_winG_20260816_final/GSE115978_melanoma_analysis_1.ipynb",
        "reports/windowK1_reeval.json",
    ),
}


def extract_capture_cases(src: Path) -> dict:
    """按案例注册表提炼采集链路三件副本（**同一套逻辑覆盖所有案例**）。

    Returns
    -------
    dict
        ``{输出文件名: manifest 元数据}``（n_verdicts / source / source_sha256）。
    """
    metas: dict = {}
    for case_id in case_registry.case_ids():
        case = case_registry.get_case(case_id)
        v_rel, e_rel, b_rel = CAPTURE_SOURCES[case_id]
        verdicts_src, executed_src, expected_src = (
            src / v_rel, src / e_rel, src / b_rel,
        )
        for p in (verdicts_src, executed_src, expected_src):
            if not p.exists():
                raise FileNotFoundError(f"[{case_id}] 缺少采集输入源文件: {p}")
        metas.update(
            _extract_one_capture_case(
                case, verdicts_src, executed_src, expected_src
            )
        )
    return metas


def _read_executed_text(path: Path, kind: str) -> str:
    """读取执行产物文本（M3 解析输入，不执行）。

    - ``"py"``：脚本直读；
    - ``"notebook"``：``.ipynb`` → 提取全部 code cell 源码按序拼接
      （保留 cell 分隔注释，便于定位；不执行任何单元）。
    """
    if kind == "notebook":
        nb = json.loads(path.read_text(encoding="utf-8"))
        chunks: list[str] = []
        for idx, cell in enumerate(nb.get("cells", []), start=1):
            if cell.get("cell_type") != "code":
                continue
            src = cell.get("source")
            text = "".join(src) if isinstance(src, list) else str(src or "")
            if not text.strip():
                continue
            chunks.append(f"# ── notebook cell {idx} ──\n{text.rstrip()}")
        return "\n\n".join(chunks) + "\n"
    return path.read_text(encoding="utf-8")


def _normalize_benchmark(raw: dict) -> dict:
    """基准报告字段归一化——下游不感知源报告形态。

    - **A 形态**（源报告产物，如 windowI_A.json / windowL_10X_B_expected.json）：
      ``audit{trajectory_score, eval_verdict, dimension_scores, per_decision}``
      + ``final_trajectory{n_decisions}`` + ``cross_validate`` + ``m1/m3``
    - **B 形态**（重评报告，如 windowK1_reeval.json）：顶层
      ``trajectory_score`` / ``eval_verdict`` / ``dimension_scores`` /
      ``per_decision[n]`` / ``level_counts``（无 audit / final_trajectory 块）
    """
    audit = raw.get("audit") or {}
    final = raw.get("final_trajectory") or {}
    per_decision = audit.get("per_decision") or raw.get("per_decision") or []
    n_decisions = final.get("n_decisions")
    if n_decisions is None:
        n_decisions = len(per_decision)
    return {
        "ok": raw.get("ok", True),
        "variant": raw.get("variant") or raw.get("window"),
        "session_id": raw.get("session_id") or raw.get("window"),
        "generated_at": raw.get("generated_at"),
        "trajectory_score": audit.get("trajectory_score", raw.get("trajectory_score")),
        "eval_verdict": audit.get("eval_verdict", raw.get("eval_verdict")),
        "dimension_scores": audit.get("dimension_scores") or raw.get("dimension_scores"),
        "critical_issues": audit.get("critical_issues") or [],
        "per_decision": per_decision,
        "n_decisions": n_decisions,
        "decision_types": final.get("decision_types"),
        "cross_validate": raw.get("cross_validate") or {},
    }


def _extract_one_capture_case(
    case, verdicts_src: Path, executed_src: Path, expected_src: Path,
) -> dict:
    """单一案例的三件副本提炼（verdicts / executed / benchmark）。"""

    # 1) verdicts 提炼副本（M1 声明重建输入；保留每行原样，剥离绝对路径）
    verdicts_out = OUT_DIR / case.verdicts_file
    n_verdicts = 0
    with verdicts_out.open("w", encoding="utf-8", newline="\n") as fw:
        for line in verdicts_src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            fw.write(json.dumps(sanitize_obj(rec), ensure_ascii=False) + "\n")
            n_verdicts += 1

    # 2) executed 提炼副本（M3 解析输入；按源形态读取，仅解析不执行）
    #    .py 直读；.ipynb 提取 code cells 拼接。路径以占位符剥离
    #    （防泄漏优先于可执行性，M3 解析不受影响——签名匹配只看调用形态）。
    executed_out = OUT_DIR / case.executed_file
    executed_text = _strip_win_paths(
        _read_executed_text(executed_src, case.executed_source_kind)
    )
    if _WIN_PATH.search(executed_text):
        raise AssertionError(f"{case.executed_file} 提炼后仍含 Windows 绝对路径")
    executed_out.write_text(executed_text, encoding="utf-8", newline="\n")

    # 3) 断言基准提炼副本（provenance 保留源文件名；**字段归一化**）
    #    报告形态有两类，下游（capture_chain.load_benchmark）不感知差异：
    #      A 形态（源报告产物）：audit{trajectory_score,eval_verdict} +
    #        final_trajectory{n_decisions} + cross_validate + m1/m3
    #      B 形态（重评报告，如 windowK1_reeval）：顶层 trajectory_score /
    #        eval_verdict / per_decision[n] / level_counts
    expected_raw = _read_json(expected_src)
    norm = _normalize_benchmark(expected_raw)
    # expected_types.config 是**包内资产**路径（src/bioaudit/data/expected_types.yaml）：
    # 替换为包内相对引用（保留引用身份，不落盘符、不抹成占位符）。
    # 常规报告（如黄金 A）无 expected_types 块 → 保持 None，不伪造。
    expected_types_block = expected_raw.get("expected_types")
    if isinstance(expected_types_block, dict) and "config" in expected_types_block:
        expected_types_block = dict(expected_types_block)
        expected_types_block["config"] = "src/bioaudit/data/expected_types.yaml"
    slim = {
        "ok": norm["ok"],
        "variant": norm["variant"],
        "session_id": norm["session_id"],
        "generated_at": norm["generated_at"],
        "expected_types": expected_types_block,
        "cross_validate": {
            "stats": norm["cross_validate"].get("stats"),
            "n_added": norm["cross_validate"].get("n_added"),
        },
        "final_trajectory": {
            "n_decisions": norm["n_decisions"],
            "decision_types": norm["decision_types"],
        },
        "audit": {
            "trajectory_score": norm["trajectory_score"],
            "eval_verdict": norm["eval_verdict"],
            "dimension_scores": norm["dimension_scores"],
            "critical_issues": norm["critical_issues"],
            "per_decision": norm["per_decision"],
        },
        "provenance": provenance(
            expected_src.name,
            _sha256_file(expected_src),
            norm["generated_at"],
            "采集链路断言基准提炼副本（63.7 复现链路说明 §6；断言读本副本）",
        ),
    }
    slim = sanitize_obj(slim)
    # 导出断言用**源报告值**（护栏：源数据未被改动）；
    # 链路核验的重放口径由 case.expected_score 承担（见 capture_chain）。
    # 两者不同 = 该案例存在口径演进（如 windowI_B：63.0 → J 后重评 69.0）。
    want_source = (
        case.source_report_score
        if case.source_report_score is not None
        else case.expected_score
    )
    assert slim["audit"]["trajectory_score"] == want_source, (
        f"[{case.case_id}] 源报告分数与注册表登记不符（源数据可能被改动）："
        f"{slim['audit']['trajectory_score']} != {want_source}"
    )
    expected_out = OUT_DIR / case.benchmark_file
    expected_out.write_text(
        json.dumps(slim, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    return {
        case.verdicts_file: {"n_verdicts": n_verdicts,
                             "source": verdicts_src.name,
                             "source_sha256": _sha256_file(verdicts_src)},
        case.executed_file: {
            "source": executed_src.name,
            "source_sha256": _sha256_file(executed_src),
            "note": "解析专用副本（M3Parser 输入，不执行）；Windows 路径以占位符剥离",
        },
        case.benchmark_file: {"source": expected_src.name,
                              "source_sha256": _sha256_file(expected_src)},
    }


def _write_json(name: str, data: dict) -> None:
    # newline="\n"：输出一律 LF（.gitattributes 强制仓库内 LF——
    # manifest 指纹按 LF 规范化，Windows 工作区与 Linux CI 字节一致）
    (OUT_DIR / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")


def _final_scan() -> None:
    """全输出文件扫描：不得含 Windows 绝对路径（`D:\\`/`C:\\`）。"""
    offenders = []
    for f in OUT_DIR.iterdir():
        if not f.is_file():
            continue
        if _WIN_PATH.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(f.name)
    if offenders:
        raise AssertionError(f"输出仍含 Windows 绝对路径: {offenders}")


def main() -> int:
    # 中文 Windows 控制台（GBK）下 ¥ 等字符会抛 UnicodeEncodeError——统一 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=None,
                        help="cellvoyager-outputs 根目录（仓库外）；未给出时读环境变量 "
                             f"{SOURCES_ENV}")
    args = parser.parse_args()
    src: Path = _resolve_sources(args.sources, parser)
    if not src.exists():
        print(f"[export] 源根不存在: {src}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    golden = extract_golden(src)
    eval_ = extract_eval(src)
    benchmark = extract_benchmark(src)
    r0 = extract_r0()
    reward = extract_reward()
    engineering = extract_engineering()
    traj = extract_trajectories()
    capture = extract_capture_cases(src)

    _write_json("golden_summary.json", golden)
    _write_json("eval_summary.json", eval_)
    _write_json("benchmark_summary.json", benchmark)
    _write_json("r0_summary.json", r0)
    _write_json("reward_summary.json", reward)
    _write_json("engineering_summary.json", engineering)
    _write_json("trajectories_index.json", traj)

    manifest = {
        "generated_at": _now(),
        "exported_by": "demo/scripts/export_demo_data.py",
        "sources_note": "源为 cellvoyager-outputs（仓库外，--sources 传入；"
                        "文件指纹登记于各条目 source_sha256）",
        "files": {},
    }
    for name in OUT_FILES:
        if name == "manifest.json":
            continue
        path = OUT_DIR / name
        manifest["files"][name] = {"sha256": _sha256_file(path)}
    # 输入副本的来源指纹（manifest 一并登记；键 = 文件名，与 files 对齐）
    for name, meta in capture.items():
        if name in manifest["files"]:
            manifest["files"][name].update(meta)
    _write_json("manifest.json", manifest)

    _final_scan()

    print(f"[export] 完成 → {OUT_DIR}")
    print("  黄金对照: " + " / ".join(
        f"{e['id']}={e['trajectory_score']}" for e in golden["entries"]))
    print("  真实评测: " + " / ".join(
        f"{r['id']}={r['trajectory_score']}" for r in eval_["runs"]))
    print(f"  benchmark: recall {benchmark['detection']['recall']} / "
          f"precision {benchmark['detection']['precision']:.4f} / "
          f"F1 {benchmark['detection']['f1']:.4f} / "
          f"gap {benchmark['gap']['delta']} / κ={benchmark['irr']['kappa']}")
    print(f"  R0: {r0['key_metric']}")
    print(f"  reward: 映射 {len(reward['mapping'])} 档 + spike-in "
          + " / ".join(f"{s['paradigm']} {s['before']}→{s['after']}"
                       for s in reward["spike_in"]))
    print(f"  工程数字: pytest 收集 {engineering['n_tests']} 测试")
    print(f"  轨迹索引: {traj['n_trajectories']} 条 / "
          f"采集案例 {len(case_registry.case_ids())} 个（"
          + " / ".join(
              f"{cid} verdicts {meta['n_verdicts']} 行"
              for cid, meta in (
                  (cid, capture[case_registry.get_case(cid).verdicts_file])
                  for cid in case_registry.case_ids()
              )
          )
          + "）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
