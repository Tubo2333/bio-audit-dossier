"""评测运行器与功效分析（refactor-plan-v1.1 E7；execution-plan D4.9/D4.10）。

- **批量审计**：对任务集（或指定 split/范式/难度）逐条 run_audit → 结果表
  （trajectory_score / verdict / L0-L4/-1 计数 / 决策类型错误率）。
- **可重复运行**：全部随机源固定 seed（bootstrap 用 np.random.default_rng(seed)）。
- **功效分析（E7）**：
  * bootstrap CI（percentile 2.5/97.5，默认 B=2000，任务级重采样）——
    mean score / 错误检出 recall / precision / F1；
  * 多重比较协议：范式 × 难度分层的组间均值比较走两样本 bootstrap 检验，
    p 值经 Holm-Bonferroni 校正（校正协议在报告 method 字段声明）。
- **黑盒口径（E2）**：运行器只消费任务 JSON（decisions + gold + difficulty），
  不读规则文本；规则字符串污染由 contamination 模块独立登记。
- **gap 检查（E1）**：public vs hidden 分数 gap 超出预注册容忍区间 → 负向告警。

检出口径（预注册，与 gold 的关系）：
  - 检出（positive）= 引擎 level ∈ {0, 1}（危险/有风险）
  - gold error → level∈{0,1} = TP；gold error → 其他 = FN
  - gold correct → level∈{0,1} = FP；gold correct → 其他 = TN
  - gold edge → 不参与 TP/FP（单独报 edge 处理率 = 被检出的 edge 比例）
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bioaudit.api import run_audit
from bioaudit.benchmark.manifest import load_tasks
from bioaudit.benchmark.protocol import check_gap

#: 检出判定：level ∈ DETECTION_LEVELS 视为"检出风险/错误"
DETECTION_LEVELS = frozenset({0, 1})

DEFAULT_SEED = 42
DEFAULT_N_BOOTSTRAP = 2000


# ── 单任务审计 ─────────────────────────────────────────────────────────────────


def audit_task(task: dict) -> dict:
    """单任务审计 → {trajectory_id, act, score, verdict, levels, ...}。"""
    result = run_audit(task, act=task["act"])
    step_scores = result.get("step_scores", [])
    levels = {s["step_id"]: s["level"] for s in step_scores}
    level_counts = {str(level): sum(1 for v in levels.values() if v == level)
                    for level in (-1, 0, 1, 2, 3, 4)}
    return {
        "trajectory_id": task["trajectory_id"],
        "act": task["act"],
        "trajectory_score": result.get("trajectory_score", 0.0),
        "verdict": result.get("eval_verdict", "unknown"),
        "n_decisions": len(step_scores),
        "level_counts": level_counts,
        "levels": levels,
        "error": result.get("error"),
    }


# ── 指标（决策级，任务级聚合）─────────────────────────────────────────────────


def decision_metrics(task: dict, audit: dict) -> dict:
    """单任务决策级指标（gold vs 引擎 level）。"""
    gold_by_step = {g["step_id"]: g["label"] for g in task["gold"]["labels"]}
    levels = audit["levels"]
    tp = fp = fn = tn = 0
    n_edge = n_edge_detected = 0
    per_type_error: dict[str, int] = {}
    per_type_total: dict[str, int] = {}
    for step in task["decisions"]:
        sid = step["step_id"]
        label = gold_by_step.get(sid)
        level = levels.get(sid, -1)
        detected = level in DETECTION_LEVELS
        if label == "error":
            if detected:
                tp += 1
            else:
                fn += 1
            per_type_error[step["decision_type"]] = per_type_error.get(step["decision_type"], 0) + 1
        elif label == "correct":
            if detected:
                fp += 1
            else:
                tn += 1
        elif label == "edge":
            n_edge += 1
            if detected:
                n_edge_detected += 1
        per_type_total[step["decision_type"]] = per_type_total.get(step["decision_type"], 0) + 1
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "n_edge": n_edge, "n_edge_detected": n_edge_detected,
        "decision_type_error_rates": {
            t: round(per_type_error.get(t, 0) / per_type_total.get(t, 1), 4)
            for t in per_type_total
        },
    }


def precision_recall_f1(tp: int, fp: int, fn: int) -> dict:
    """检出指标（gold error 检出）。分母为 0 → None（不报告 0 误导）。"""
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec is not None and rec is not None
                                             and prec + rec > 0) else None
    return {"precision": prec, "recall": rec, "f1": f1}


# ── 聚合 + bootstrap CI ───────────────────────────────────────────────────────


def _bootstrap_ci(values: list[float], stat: str, seed: int, n_boot: int,
                  alpha: float = 0.05) -> dict:
    """percentile bootstrap CI（任务级重采样；空输入 → None）。"""
    import numpy as np

    if not values:
        return {"point": None, "ci": None, "n": 0}
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    point = float(np.mean(arr))
    boots = []
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        boots.append(float(np.mean(sample)))
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"point": round(point, 4), "ci": [round(float(lo), 4), round(float(hi), 4)],
            "n": len(arr)}


def _bootstrap_two_sample(a: list[float], b: list[float], seed: int,
                          n_boot: int) -> float:
    """两样本均值差 bootstrap p（零假设：均值相等；双尾）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    aa, bb = np.asarray(a, float), np.asarray(b, float)
    obs = float(np.mean(aa) - np.mean(bb))
    combined = np.concatenate([aa, bb])
    n_a = len(aa)
    count = 0
    for _ in range(n_boot):
        perm = rng.permutation(len(combined))
        mean_a = combined[perm[:n_a]].mean()
        mean_b = combined[perm[n_a:]].mean()
        if abs(mean_a - mean_b) >= abs(obs):
            count += 1
    return count / n_boot


def holm_bonferroni(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni 校正（多重比较协议：报告方法学声明）。"""
    n = len(p_values)
    order = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [0.0] * n
    prev = 0.0
    for rank, idx in enumerate(order, start=1):
        adjusted[idx] = max(min(p_values[idx] * (n - rank + 1), 1.0), prev)
        prev = adjusted[idx]
    return adjusted


def aggregate(tasks: list[dict], audits: list[dict], seed: int = DEFAULT_SEED,
              n_boot: int = DEFAULT_N_BOOTSTRAP) -> dict:
    """聚合指标：检出指标 + 分数 CI + 分层（范式/难度）+ 多重比较协议。"""
    metrics = [decision_metrics(t, a) for t, a in zip(tasks, audits)]
    scores = [a["trajectory_score"] / 100.0 for a in audits]  # 归一化 0..1
    tp = sum(m["tp"] for m in metrics)
    fp = sum(m["fp"] for m in metrics)
    fn = sum(m["fn"] for m in metrics)
    tn = sum(m["tn"] for m in metrics)
    n_edge = sum(m["n_edge"] for m in metrics)
    n_edge_detected = sum(m["n_edge_detected"] for m in metrics)

    overall = {
        "n_tasks": len(tasks),
        "n_decisions": sum(len(t["decisions"]) for t in tasks),
        "n_gold_error": tp + fn,
        "n_gold_correct": fp + tn,
        "detection": precision_recall_f1(tp, fp, fn),
        "edge_handling": {
            "n_edge": n_edge,
            "n_edge_detected": n_edge_detected,
            "edge_detection_rate": round(n_edge_detected / n_edge, 4) if n_edge else None,
        },
        "mean_score": _bootstrap_ci(scores, "mean", seed, n_boot),
    }
    # 检出指标 bootstrap CI（任务级重采样，**pooled** 口径——与点估计一致）
    # pooled: 每条 bootstrap 样本把重采样任务的 tp/fp/fn 汇总后算 recall/F1
    import numpy as np

    rng = np.random.default_rng(seed)
    m_tp = np.asarray([m["tp"] for m in metrics], dtype=int)
    m_fp = np.asarray([m["fp"] for m in metrics], dtype=int)
    m_fn = np.asarray([m["fn"] for m in metrics], dtype=int)
    rec_boots, f1_boots = [], []
    for _ in range(n_boot):
        idx = rng.integers(0, len(metrics), size=len(metrics))
        pr = precision_recall_f1(int(m_tp[idx].sum()), int(m_fp[idx].sum()),
                                 int(m_fn[idx].sum()))
        if pr["recall"] is not None:
            rec_boots.append(pr["recall"])
        if pr["f1"] is not None:
            f1_boots.append(pr["f1"])
    overall["detection"]["recall_ci"] = {
        "point": round(sum(rec_boots) / len(rec_boots), 4) if rec_boots else None,
        "ci": [round(float(np.percentile(rec_boots, 2.5)), 4),
               round(float(np.percentile(rec_boots, 97.5)), 4)] if rec_boots else None,
        "n": len(metrics), "aggregation": "pooled（任务级重采样）",
    }
    overall["detection"]["f1_ci"] = {
        "point": round(sum(f1_boots) / len(f1_boots), 4) if f1_boots else None,
        "ci": [round(float(np.percentile(f1_boots, 2.5)), 4),
               round(float(np.percentile(f1_boots, 97.5)), 4)] if f1_boots else None,
        "n": len(metrics), "aggregation": "pooled（任务级重采样）",
    }

    # 分层：范式 × 难度
    strata = {}
    for key in ("act", "difficulty"):
        groups: dict[str, list[float]] = {}
        for t, s in zip(tasks, scores):
            if key == "act":
                g = t["act"]
            else:
                g = str(t["difficulty"]["label"])
            groups.setdefault(g, []).append(s)
        strata[key] = {
            g: _bootstrap_ci(v, "mean", seed, n_boot) for g, v in sorted(groups.items())
        }

    # 多重比较协议：范式两两 + 难度两两，bootstrap p + Holm 校正
    comparisons = []
    p_values = []
    pairs = []
    for key, labels in (("act", ["deg", "pan", "scrna"]),
                        ("difficulty", ["1", "2", "3"])):
        groups = {g: [s for t, s in zip(tasks, scores) if (
            t["act"] if key == "act" else str(t["difficulty"]["label"])) == g]
            for g in labels}
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a, b = labels[i], labels[j]
                if groups[a] and groups[b]:
                    pairs.append((key, a, b))
                    p_values.append(_bootstrap_two_sample(groups[a], groups[b], seed, n_boot))
    adjusted = holm_bonferroni(p_values) if p_values else []
    for (key, a, b), p, padj in zip(pairs, p_values, adjusted):
        comparisons.append({
            "grouping": key, "group_a": a, "group_b": b,
            "bootstrap_p": round(p, 4),
            "holm_adjusted_p": round(padj, 4),
            "protocol": "两样本置换 bootstrap 检验（B={}），Holm-Bonferroni 校正".format(n_boot),
        })

    return {
        "overall": overall,
        "strata": strata,
        "comparisons": comparisons,
        "method": {
            "bootstrap": {"n_boot": n_boot, "seed": seed,
                          "method": "percentile, 任务级重采样"},
            "multiple_comparison": "Holm-Bonferroni on bootstrap p"
                                   "（两样本置换检验）",
            "detection_definition": "level ∈ {0,1} = 检出；gold error → TP/FN；"
                                    "gold correct → FP/TN；gold edge 单独报告",
            "difficulty_independence": "难度标签由 gold 特征预注册 rubric 计算，"
                                       "不依赖审计分数（E4）",
        },
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────


def run_benchmark(
    tasks_dir: Optional[Path | str] = None,
    split: Optional[str] = None,       # None=全部 | "public" | "hidden"
    act: Optional[str] = None,         # None=全部范式
    difficulty: Optional[int] = None,  # None=全部难度
    seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_N_BOOTSTRAP,
) -> dict:
    """批量评测 → 结果表 + 聚合 + gap 检查。可重复（seed 固定）。"""
    from bioaudit.benchmark.manifest import load_taskset
    from bioaudit.benchmark.paths import TASKS_DIR

    td = Path(tasks_dir) if tasks_dir else TASKS_DIR
    tasks = load_tasks(td)
    manifest = load_taskset(td)
    split_map = {}
    if split is not None:
        for s in ("public", "hidden"):
            for tid in manifest.get("split", {}).get(s, []):
                split_map[tid] = s

    selected = []
    for t in tasks:
        if split is not None and split_map.get(t["trajectory_id"]) != split:
            continue
        if act is not None and t["act"] != act:
            continue
        if difficulty is not None and t["difficulty"]["label"] != difficulty:
            continue
        selected.append(t)

    audits = [audit_task(t) for t in selected]
    agg = aggregate(selected, audits, seed=seed, n_boot=n_boot)

    # gap 检查（E1）：仅当 split=None 时（全量）才有意义；public/hidden 各自分数
    gap = None
    if split is None:
        pub_ids = set(manifest.get("split", {}).get("public", []))
        hid_ids = set(manifest.get("split", {}).get("hidden", []))
        pub_scores = [a["trajectory_score"] / 100.0 for t, a in zip(selected, audits)
                      if t["trajectory_id"] in pub_ids]
        hid_scores = [a["trajectory_score"] / 100.0 for t, a in zip(selected, audits)
                      if t["trajectory_id"] in hid_ids]
        gap = check_gap(pub_scores, hid_scores)

    result_table = []
    metrics = [decision_metrics(t, a) for t, a in zip(selected, audits)]
    for t, a, m in zip(selected, audits, metrics):
        result_table.append({
            "trajectory_id": t["trajectory_id"],
            "act": t["act"],
            "difficulty": t["difficulty"]["label"],
            "split": split_map.get(t["trajectory_id"], ""),
            "n_decisions": a["n_decisions"],
            "trajectory_score": a["trajectory_score"],
            "verdict": a["verdict"],
            "level_counts": a["level_counts"],
            "gold_counts": {
                "error": sum(1 for g in t["gold"]["labels"] if g["label"] == "error"),
                "correct": sum(1 for g in t["gold"]["labels"] if g["label"] == "correct"),
                "edge": sum(1 for g in t["gold"]["labels"] if g["label"] == "edge"),
            },
            "detection": {"tp": m["tp"], "fp": m["fp"], "fn": m["fn"], "tn": m["tn"]},
            "decision_type_error_rates": m["decision_type_error_rates"],
        })

    return {
        "taskset_version": manifest.get("taskset_version"),
        "seed": seed,
        "n_boot": n_boot,
        "n_tasks_run": len(selected),
        "results": result_table,
        "aggregate": agg,
        "gap": gap,
        "generated_at": __import__("datetime").date.today().isoformat(),
    }


__all__ = [
    "DETECTION_LEVELS",
    "DEFAULT_SEED",
    "DEFAULT_N_BOOTSTRAP",
    "audit_task",
    "decision_metrics",
    "precision_recall_f1",
    "aggregate",
    "holm_bonferroni",
    "run_benchmark",
]
