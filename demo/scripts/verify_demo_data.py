"""demo/data 独立核对（验收工具）：源产物重读 vs demo/data 摘要比对。

验收 §3.4「数字核对（独立重算）」落地：
- 从 cellvoyager-outputs 源报告**重新读取**关键分数/verdict/级别分布；
- 与 demo/data 提炼摘要逐项比对（不一致 → 退出码 1）；
- 核对变换最大的两个副本（verdicts jsonl / executed.py）与源一致；
- 全输出扫描 Windows 绝对路径（含 `D:\\`/`C:\\` 即失败）；
- manifest 指纹自检（demo/data 文件与导出时指纹一致）。

说明：本脚本是**独立重读核对**（对照读取源产物并比对摘要），数字锚点断言
与 export 脚本共享同一组护栏值——护栏的作用是源值意外变化时显式报错，
系统性错误（如源路径写错）由 `--sources` 参数与实际文件存在性检查兜底。

用法（源根必给，脚本内不写死本机路径）：
    python demo/scripts/verify_demo_data.py --sources <cellvoyager-outputs 根>
    # 或先设环境变量 BIOAUDIT_DEMO_SOURCES，再不带参数运行

**不可独立运行**：源数据在仓库外，且比对锚点取自未随本仓发布的内部评测
记录。本脚本在此的用途是**登记核对口径**（核对哪几项、对照什么锚点），
不是提供可直接复跑的工具。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "demo" / "data"
#: 源根**不写死在本脚本里**（源数据在仓库外，不入 git，公开仓不持有）。
#: 取值顺序：``--sources`` 参数 → 环境变量 ``BIOAUDIT_DEMO_SOURCES`` → 报错退出。
SOURCES_ENV = "BIOAUDIT_DEMO_SOURCES"

# 与 export_demo_data.py 同源的正则（剥离/扫描一致；反斜杠 ≥2 级 + UNC；
# 刻意不支持正斜杠盘符形态——与 URL https:// 无法区分，见 export 注释）
_WIN_PATH = re.compile(
    r"(?:[A-Za-z]:\\(?:[^\"'\n]*\\)+[^\"'\n]*"
    r"|\\\\[^\"'\n]+)"
)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  [OK] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILURES.append(msg)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    # 中文 Windows 控制台（GBK）下 ¥ 等字符会抛 UnicodeEncodeError（新增
    # 成本核对消息含 ¥）——统一 UTF-8，保证默认调用可复现
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=None,
                        help="cellvoyager-outputs 根目录（仓库外）；未给出时读环境变量 "
                             f"{SOURCES_ENV}")
    args = parser.parse_args()
    if args.sources is not None:
        src: Path = args.sources
    elif os.environ.get(SOURCES_ENV, "").strip():
        src = Path(os.environ[SOURCES_ENV].strip())
    else:
        parser.error(
            f"必须给出 --sources（或设置环境变量 {SOURCES_ENV}）："
            "源数据在仓库外，不随本仓库分发")
    if not src.exists():
        print(f"[FAIL] 源根不存在: {src}（--sources 指定 cellvoyager-outputs 根）")
        return 2

    print("== 1. 黄金对照（源报告重读 vs demo/data）==")
    g = json.loads((DATA_DIR / "golden_summary.json").read_text(encoding="utf-8"))
    for entry in g["entries"]:
        wid = entry["id"]
        path = src / ("windowI" if entry["window"] == "I" else "windowL") / "reports"
        fname = "windowI_B.json" if wid == "windowI_B" else f"{wid}.json"
        if wid == "windowI_B":
            raw = json.loads((path / "windowI_B.json").read_text(encoding="utf-8"))
            orig = raw["audit"]
            check(entry["score_original"] == orig["trajectory_score"]
                  and entry["verdict_original"] == orig["eval_verdict"],
                  f"{wid} 原始分 {orig['trajectory_score']}·{orig['eval_verdict']} 一致")
            # 69.0 重评：J1 报告锚点（提炼脚本已断言；此处复核报告含锚点）
            j1 = (REPO_ROOT / "docs" / "migration" / "J1-rule-quality-report.md").read_text(
                encoding="utf-8")
            check(re.search(r"\*\*69\.0 · needs_correction", j1) is not None
                  and entry["trajectory_score"] == 69.0,
                  f"{wid} 重评 69.0·needs_correction（J1 报告锚点）一致")
            check(entry["n_decisions"] == len(orig["per_decision"]),
                  f"{wid} 决策数 {len(orig['per_decision'])} 一致")
            continue
        raw = json.loads((path / fname).read_text(encoding="utf-8"))
        audit = raw["audit"]
        check(entry["trajectory_score"] == audit["trajectory_score"]
              and entry["eval_verdict"] == audit["eval_verdict"],
              f"{wid} {audit['trajectory_score']}·{audit['eval_verdict']} 一致")
        check(entry["n_decisions"] == len(audit["per_decision"]),
              f"{wid} 决策数 {len(audit['per_decision'])} 一致")

    print("== 2. 真实评测（30.0×2）==")
    ev = json.loads((DATA_DIR / "eval_summary.json").read_text(encoding="utf-8"))
    k1 = json.loads((src / "reports" / "windowK1_reeval.json").read_text(encoding="utf-8"))
    lb = json.loads((src / "windowL" / "reports" / "windowLb_analysis.json").read_text(
        encoding="utf-8"))
    run_g = next(r for r in ev["runs"] if r["id"] == "cellvoyager_g")
    run_lb = next(r for r in ev["runs"] if r["id"] == "cellvoyager_lb")
    check(run_g["trajectory_score"] == k1["trajectory_score"] == 30.0
          and run_g["level_counts"] == k1["level_counts"],
          f"cellvoyager_g 30.0 · {k1['level_counts']} 一致（复核后重评）")
    lb_levels = dict(sorted(Counter(
        d.get("level") for d in lb["audit"]["per_decision"]).items()))
    # JSON 序列化后键为字符串，重读为 int —— 规范化后比较
    run_lb_levels = {int(k): v for k, v in run_lb["level_counts"].items()}
    check(run_lb["trajectory_score"] == lb["audit"]["trajectory_score"] == 30.0
          and run_lb_levels == lb_levels,
          f"cellvoyager_lb 30.0 · {lb_levels} 一致")

    print("== 3. benchmark 摘要 ==")
    bm = json.loads((DATA_DIR / "benchmark_summary.json").read_text(encoding="utf-8"))
    base = json.loads((src / "reports" / "benchmark_run_baseline.json").read_text(
        encoding="utf-8-sig"))
    agg = base["aggregate"]["overall"]
    check(bm["detection"]["recall"] == agg["detection"]["recall"] == 0.82, "recall 0.820")
    check(abs(bm["detection"]["precision"] - agg["detection"]["precision"]) < 1e-9,
          f"precision {agg['detection']['precision']:.4f}")
    check(abs(bm["detection"]["f1"] - agg["detection"]["f1"]) < 1e-9,
          f"F1 {agg['detection']['f1']:.4f}")
    check(bm["gap"]["delta"] == base["gap"]["delta"] == 0.046, "gap +0.046")
    f1_text = (REPO_ROOT / "docs" / "migration" / "F1-phase3-benchmark-batch2-report.md"
               ).read_text(encoding="utf-8")
    m = re.search(r"全量 60 合并 IRR κ=([\d.]+)", f1_text)
    check(m is not None and abs(bm["irr"]["kappa"] - float(m.group(1))) < 1e-9,
          f"IRR κ={bm['irr']['kappa']}")
    m60 = re.search(r"全量 60 合并[^\n]*?（(\d+) 决策，\s*一致率 ([\d.]+)%",
                    f1_text, re.DOTALL)
    check(m60 is not None
          and bm["irr"]["n_decisions"] == int(m60.group(1)) == 623
          and abs(bm["irr"]["agreement"] - float(m60.group(2)) / 100.0) < 1e-9,
          f"IRR 决策数 {bm['irr']['n_decisions']} / 一致率 {bm['irr']['agreement']:.4f} "
          "（评测报告锚点）")
    check(bm["gap"]["delta_after_m"] == 0.0449, "gap 后 M 复跑 +0.0449 注明")

    print("== 4. R0 锚定 ==")
    r0 = json.loads((DATA_DIR / "r0_summary.json").read_text(encoding="utf-8"))
    check("0.9747" in r0["key_metric"], f"R0 {r0['key_metric']}")

    print("== 5. 轨迹索引（golden 基线核对 + 抽查 ≥3 条）==")
    ti = json.loads((DATA_DIR / "trajectories_index.json").read_text(encoding="utf-8"))
    golden = json.loads((REPO_ROOT / "tests" / "golden" /
                         "golden_expected_output_after.json").read_text(encoding="utf-8"))
    golden_rows = golden["trajectories"] if isinstance(golden, dict) else golden
    check(len(ti["trajectories"]) == len(golden_rows) == 20, "20 条轨迹索引")
    for tid in ("scrna_correct", "scrna_error", "scrna_edge_singleanno"):
        idx = next(r for r in ti["trajectories"] if r["trajectory_id"] == tid)
        gld = next(r for r in golden_rows if r["trajectory"] == tid)
        check(idx["golden_score"] == gld["trajectory_score"]
              and idx["golden_verdict"] == gld["verdict"],
              f"{tid} {idx['golden_score']}·{idx['golden_verdict']} 与 golden 基线一致")

    print("== 6. 输入副本（63.7 基准 / verdicts / executed）==")
    w = json.loads((DATA_DIR / "windowL_10X_B_expected.json").read_text(encoding="utf-8"))
    wsrc = json.loads((src / "windowL" / "reports" /
                       "windowL_10X_B_expected.json").read_text(encoding="utf-8"))
    check(w["audit"]["trajectory_score"] == wsrc["audit"]["trajectory_score"] == 63.7
          and w["audit"]["eval_verdict"] == "blocked", "63.7 · blocked 副本一致")
    check(w["provenance"]["source"] == "windowL_10X_B_expected.json",
          "provenance 保留 source=windowL_10X_B_expected.json")
    check(w["expected_types"]["config"] == "src/bioaudit/data/expected_types.yaml",
          "expected_types.config 保留包内相对引用（引用身份未丢失）")

    # verdicts jsonl：行数与每行内容（sanitize 后）与源一致
    v_out = (DATA_DIR / "verdicts_10X_B.jsonl").read_text(encoding="utf-8").splitlines()
    v_src = (src / "data" / "verdicts" / "golden_winL_10X_B_20260816.jsonl"
             ).read_text(encoding="utf-8").splitlines()
    v_src = [ln for ln in v_src if ln.strip()]
    check(len(v_out) == len(v_src) == 25, f"verdicts 行数 {len(v_out)} == 源 {len(v_src)}")
    same = all(
        json.loads(a) == json.loads(b) for a, b in zip(v_out, v_src))
    check(same, "verdicts 每行与源逐行一致（无变换）")

    # executed.py：副本 == 源（换行规范化为 LF）剥离绝对路径后逐字节一致
    exec_out = (DATA_DIR / "golden_agent_10X_B_executed.py").read_text(encoding="utf-8")
    exec_src = (src / "windowL" / "runs" / "10X_B" / "golden_agent_10X_B_executed.py"
                ).read_text(encoding="utf-8").replace("\r\n", "\n")
    check(exec_out == _WIN_PATH.sub("<absolute-path-stripped>", exec_src),
          "executed.py 副本 == 源（LF 规范化）剥离绝对路径后（逐字节一致）")
    check(exec_out.count("<absolute-path-stripped>") == 1,
          f"executed.py 占位符 {exec_out.count('<absolute-path-stripped>')} 处（预期 1："
          "仅 --rscript 默认值；failed:\\n 不再误判）")

    print("== 7. manifest 指纹自检 ==")
    mf = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    ok = True
    for name, meta in mf["files"].items():
        if _sha256(DATA_DIR / name) != meta["sha256"]:
            ok = False
            print(f"  [FAIL] {name} 指纹不匹配")
    check(ok, "demo/data 全部文件指纹与 manifest 一致")
    # 来源登记仅对三副本生效（摘要类文件指纹在各自 provenance 内）
    copy_names = ("verdicts_10X_B.jsonl", "golden_agent_10X_B_executed.py",
                  "windowL_10X_B_expected.json")
    check(all("source" in mf["files"][n] for n in copy_names),
          "manifest 三副本条目含来源登记（source/source_sha256）")

    print("== 8. Windows 绝对路径扫描（demo/data 全文件）==")
    hits = []
    for f in DATA_DIR.iterdir():
        if f.is_file():
            for mm in _WIN_PATH.finditer(f.read_text(encoding="utf-8", errors="replace")):
                hits.append(f"{f.name}: {mm.group(0)[:50]}")
    check(not hits, "无 Windows 绝对路径" if not hits else f"命中 {hits}")

    print("== 9. 真实评测成本（评测报告锚点重读）==")
    g_report = (REPO_ROOT / "docs" / "migration" / "agent-eval-report.md"
                ).read_text(encoding="utf-8")
    m = re.search(r"总花费（窗口 G）.*?¥([\d.]+)", g_report)
    check(m is not None
          and abs(run_g["cost"]["amount"] - float(m.group(1))) < 1e-9,
          f"cellvoyager_g 成本 ¥{run_g['cost']['amount']}（评测报告 §2 "
          "锚点：总花费 ¥2.55 权威口径）")
    l1_text = (REPO_ROOT / "docs" / "migration" / "L1-broader-eval-report.md"
               ).read_text(encoding="utf-8")
    m = re.search(r"成本（平台余额差，权威口径）.*?¥([\d.]+)", l1_text)
    check(m is not None
          and abs(run_lb["cost"]["amount"] - float(m.group(1))) < 1e-9,
          f"cellvoyager_lb 成本 ¥{run_lb['cost']['amount']}（评测报告 §7.2 锚点："
          "余额差权威口径）")

    print("== 10. reward 校准摘要（宪法/报告锚点重读）==")
    rw = json.loads((DATA_DIR / "reward_summary.json").read_text(encoding="utf-8"))
    map_text = (REPO_ROOT / "docs" / "reward-mapping.md").read_text(encoding="utf-8")
    for level, value in rw["mapping"].items():
        pat = rf"\| {level} \|[^|]*\| {value:.2f} \|"
        check(re.search(pat, map_text) is not None,
              f"映射 L{level} → {value:.2f} 与宪法 §2 一致")
    check("不参与分子也不参与分母" in map_text, "-1 mask 语义锚点存在")
    e4 = (REPO_ROOT / "docs" / "migration" / "E4-phase4-reward-report.md"
          ).read_text(encoding="utf-8")
    for row in rw["spike_in"]:
        m = re.search(
            rf"{row['paradigm']}_correct \+ `{row['injection']}`[^\n]*?→ "
            rf"\*\*0\.85 → {row['after']:.4f}，drop = {row['drop']:.4f}\*\*",
            e4)
        check(m is not None,
              f"{row['paradigm']} spike-in {row['after']:.4f} / drop "
              f"{row['drop']:.4f} 与 E4 §三.9 一致")
        check(abs(row["drop"] - (0.85 - row["after"])) < 1e-9,
              f"{row['paradigm']} spike-in drop 数值自洽")

    print("== 11. 工程数字（pytest 实收集重放）==")
    eng = json.loads((DATA_DIR / "engineering_summary.json").read_text(encoding="utf-8"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        check(False, f"pytest --collect-only 失败（exit {proc.returncode}）")
    m = re.search(r"(\d+) tests? collected", proc.stdout + proc.stderr)
    check(m is not None and eng["n_tests"] == int(m.group(1)),
          f"n_tests {eng['n_tests']} == pytest --collect-only 实测 "
          f"{m.group(1) if m else '?'}")

    if FAILURES:
        print(f"\n核对失败 {len(FAILURES)} 项")
        return 1
    print("\n全部核对通过 [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
