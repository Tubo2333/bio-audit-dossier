"""reward 校准与验收（窗口 E / E3：排序一致性 + 分层均值检验 + 锚点）。

依据（refactor-plan-v1.1 F5/F6/F7 + 拍板 #2）：
- **验收 = 排序一致性 + 分层均值检验**（放弃 Spearman 点估计门槛）：
  * 排序一致性：reward 排序 vs gold 标注排序 —— Spearman ρ + Kendall τ_b
    **如实报告 + bootstrap CI**（任务级重采样，percentile 2.5/97.5）；
  * 分层均值检验：好/坏任务组 reward 均值差异 —— 两样本置换 bootstrap 检验
    + 均值差 CI（预注册分组：good = n_gold_error == 0，bad = n_gold_error ≥ 1）；
- **多种子复跑 + 确定性**（F6）：reward 本身无随机源（确定性）；bootstrap
  统计量按固定 seed 复现，跨种子报告 CI 稳定性；
- **校准锚点**（F7）：benchmark gold = 弱锚点（排序一致性分析）；
  spike-in 合成数据 = **强锚点**（注入已知 L0 → reward 显著下降，
  阈值 δ 预注册，见 docs/reward-protocol.md §五）；
- **F4 纪律**：本模块只消费 gold 标注（弱锚点）与引擎 level（经 reward API），
  不消费交叉验证四类判定。

所有统计量 numpy-only（不引入 scipy 核心依赖），seed 固定可复现。
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from bioaudit.benchmark.manifest import load_tasks
from bioaudit.benchmark.paths import TASKS_DIR
from bioaudit.reward.mapping import HARD_PENALTY_GAMMA
from bioaudit.reward.recipes import REWARD_RECIPES

#: 预注册：spike-in 强锚点最小下降量（注入 1 个已知 L0 → reward 下降 ≥ 该值）。
#: 依据（docs/reward-protocol.md §五）：全 L3 轨迹（0.85）注入一个 L0 后
#: recipe B = (n-1)/n·0.85·γ；n≥2 时下降 ≥ 0.85·(1-0.3)/1 = 0.595 > 0.30，
#: 取保守阈值 0.30（远低于最坏情形，抗轨迹长度波动）。
SPIKE_IN_MIN_DROP = 0.30

#: 预注册：多种子集合（F6 稳定性报告）
MULTI_SEEDS = (42, 1, 7, 123, 2026)

DEFAULT_SEED = 42
DEFAULT_N_BOOT = 2000


# ── 统计原语（numpy-only）─────────────────────────────────────────────────────


def _rankdata(values: np.ndarray) -> np.ndarray:
    """平均秩（ties 取平均秩；与 scipy.stats.rankdata 同语义）。"""
    n = len(values)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 平均秩
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman ρ（秩相关；纯 numpy 实现）。"""
    rx, ry = _rankdata(x), _rankdata(y)
    return float(np.corrcoef(rx, ry)[0, 1]) if len(x) > 1 else float("nan")


def kendall_tau_b(x: np.ndarray, y: np.ndarray) -> float:
    """Kendall τ_b（ties 校正；纯 numpy 实现）。"""
    n = len(x)
    if n < 2:
        return float("nan")
    concordant = 0
    discordant = 0
    tx = 0  # x ties
    ty = 0  # y ties
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + tx) * (concordant + discordant + ty))
    if denom == 0:
        return float("nan")
    return float((concordant - discordant) / denom)


def _bootstrap_ci_stat(x: np.ndarray, y: np.ndarray, stat, seed: int,
                       n_boot: int, alpha: float = 0.05) -> dict:
    """percentile bootstrap CI（任务级重采样，seed 固定可复现）。"""
    rng = np.random.default_rng(seed)
    n = len(x)
    point = stat(x, y)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boots.append(stat(x[idx], y[idx]))
    boots = np.asarray([b for b in boots if not math.isnan(b)])
    if len(boots) == 0:
        return {"point": None, "ci": None, "n": n}
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "point": round(float(point), 4) if not math.isnan(point) else None,
        "ci": [round(float(lo), 4), round(float(hi), 4)],
        "n": n, "n_boot": n_boot, "seed": seed,
    }


def _mean_ci(values: list[float], seed: int, n_boot: int) -> dict:
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    point = float(arr.mean())
    boots = [float(rng.choice(arr, size=len(arr), replace=True).mean())
             for _ in range(n_boot)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"point": round(point, 4), "ci": [round(float(lo), 4), round(float(hi), 4)],
            "n": len(arr)}


def _permutation_p(a: np.ndarray, b: np.ndarray, seed: int, n_boot: int) -> float:
    """两样本均值差置换 bootstrap p（零假设：均值相等；双尾）。"""
    rng = np.random.default_rng(seed)
    obs = float(np.mean(a) - np.mean(b))
    combined = np.concatenate([a, b])
    n_a = len(a)
    count = 0
    for _ in range(n_boot):
        perm = rng.permutation(len(combined))
        diff = combined[perm[:n_a]].mean() - combined[perm[n_a:]].mean()
        if abs(diff) >= abs(obs):
            count += 1
    return count / n_boot


# ── gold 弱锚点 ───────────────────────────────────────────────────────────────


def gold_quality(task: dict) -> Optional[float]:
    """任务级 gold 质量：correct / (correct + error)（edge 中立，不参与）。

    F7（方法-结果解耦）提示审计分与 F1 非单调——gold 质量是**弱锚点**：
    排序一致性与分层均值只作为证据报告（带 CI），不做点估计门槛。
    分母为 0（全 edge）→ None（不参与排序）。
    """
    n_correct = sum(1 for g in task["gold"]["labels"] if g["label"] == "correct")
    n_error = sum(1 for g in task["gold"]["labels"] if g["label"] == "error")
    denom = n_correct + n_error
    if denom == 0:
        return None
    return n_correct / denom


def task_reward(task: dict, recipe: str) -> Optional[float]:
    """单任务 reward（配方 A/B/C；全 mask → None）。"""
    from bioaudit.reward.api import reward

    result = reward(task, act=task["act"], recipe=recipe)
    return result["trajectory_reward"]


# ── 排序一致性 + 分层均值检验（E3.7）─────────────────────────────────────────


def rank_consistency(
    qualities: list[float], rewards: list[Optional[float]],
    seed: int = DEFAULT_SEED, n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """reward 排序 vs gold 排序：Spearman ρ + Kendall τ_b + bootstrap CI。

    未评估任务（reward=None / gold=None）成对剔除（listwise）；报告 n。
    """
    pairs = [(q, r) for q, r in zip(qualities, rewards) if q is not None and r is not None]
    n = len(pairs)
    if n < 4:
        return {"n": n, "spearman": None, "kendall_tau_b": None,
                "note": "配对样本不足（n<4），不计算排序一致性"}
    x = np.asarray([q for q, _ in pairs], dtype=float)
    y = np.asarray([r for _, r in pairs], dtype=float)
    return {
        "n": n,
        "spearman": _bootstrap_ci_stat(x, y, spearman, seed, n_boot),
        "kendall_tau_b": _bootstrap_ci_stat(x, y, kendall_tau_b, seed, n_boot),
        "method": "任务级重采样 percentile bootstrap（B={}），"
                  "Spearman ρ / Kendall τ_b".format(n_boot),
    }


def stratified_mean_test(
    tasks: list[dict], rewards: list[Optional[float]],
    seed: int = DEFAULT_SEED, n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """分层均值检验（拍板 #2）：好/坏任务组 reward 显著分离。

    预注册分组（docs/reward-protocol.md §四）：
    - good = n_gold_error == 0；bad = n_gold_error ≥ 1；
    - 统计量 = mean(good) − mean(bad)，> 0 且 p < 0.05 视为显著分离；
    - 报告：组均值 + CI、均值差 + CI、置换 bootstrap p（B=2000 双尾）。
    """
    good, bad = [], []
    for t, r in zip(tasks, rewards):
        if r is None:
            continue
        n_error = sum(1 for g in t["gold"]["labels"] if g["label"] == "error")
        (bad if n_error >= 1 else good).append(r)
    if not good or not bad:
        return {"ok": False, "note": "good/bad 组缺样本，无法检验",
                "n_good": len(good), "n_bad": len(bad)}
    a = np.asarray(good, dtype=float)
    b = np.asarray(bad, dtype=float)
    diff = float(a.mean() - b.mean())
    p = _permutation_p(a, b, seed, n_boot)
    # 均值差 CI：两样本 bootstrap（组内重采样）
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        sa = rng.choice(a, size=len(a), replace=True)
        sb = rng.choice(b, size=len(b), replace=True)
        diffs.append(float(sa.mean() - sb.mean()))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "ok": True,
        "grouping": "good: n_gold_error==0 / bad: n_gold_error>=1（预注册）",
        "good": _mean_ci(good, seed, n_boot),
        "bad": _mean_ci(bad, seed, n_boot),
        "mean_diff_good_minus_bad": round(diff, 4),
        "mean_diff_ci": [round(float(lo), 4), round(float(hi), 4)],
        "permutation_p": round(p, 4),
        "significant_separation": bool(diff > 0 and p < 0.05),
        "protocol": "两样本置换 bootstrap 检验（B={}，双尾）".format(n_boot),
    }


# ── 三组消融（E2.6）───────────────────────────────────────────────────────────


def ablate(
    tasks: list[dict], seed: int = DEFAULT_SEED, n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """配方 A/B/C 同输入三组消融：每配方 reward 表 + 排序一致性 + 分层检验。"""
    qualities = [gold_quality(t) for t in tasks]
    table = []
    per_recipe: dict[str, dict] = {}
    for recipe in REWARD_RECIPES:
        rewards = [task_reward(t, recipe) for t in tasks]
        rows = []
        for t, q, r in zip(tasks, qualities, rewards):
            rows.append({
                "trajectory_id": t["trajectory_id"], "act": t["act"],
                "difficulty": t["difficulty"]["label"],
                "n_gold_error": sum(1 for g in t["gold"]["labels"] if g["label"] == "error"),
                "gold_quality": round(q, 4) if q is not None else None,
                "reward": r,
            })
        per_recipe[recipe] = {
            "reward_table": rows,
            "rank_consistency": rank_consistency(qualities, rewards, seed, n_boot),
            "stratified_mean_test": stratified_mean_test(tasks, rewards, seed, n_boot),
            "mean_reward": round(float(np.mean([r for r in rewards if r is not None])), 4)
                           if any(r is not None for r in rewards) else None,
            "n_evaluable": sum(1 for r in rewards if r is not None),
        }
        table.append({"recipe": recipe, "n": len(rows),
                      "mean_reward": per_recipe[recipe]["mean_reward"],
                      "n_evaluable": per_recipe[recipe]["n_evaluable"]})
    return {
        "recipes": per_recipe,
        "summary_table": table,
        "method": {
            "ablation": "A=纯规则分(mean)；B=A×γ(γ={})当且仅当存在未 mask L0；"
                        "C=PRM 加权 mean（占位权重均匀 → 默认 C≡A，接口独立测试）"
                        .format(HARD_PENALTY_GAMMA),
            "same_input": "同一 60 条任务（同一引擎 step_scores）三组配方",
            "seed": seed, "n_boot": n_boot,
        },
    }


# ── spike-in 强锚点（E3.9）────────────────────────────────────────────────────


def spike_in(
    trajectory: dict, injection: dict, act: str, recipe: str = "B",
) -> dict:
    """**强锚点**：注入已知 L0 → 期望 reward 显著下降。

    Parameters
    ----------
    trajectory : dict
        干净轨迹（v2 对象，全部步骤预期 L3/L4 → reward ≈ 0.85）。
    injection : dict
        注入决策（必须满足 Decision schema；引擎必须判 L0——调用方负责
        用 ``audit_decision`` 验证，测试守卫强制）。
    act : str
        范式（deg/pan/scrna）。
    recipe : str
        配方（默认 B：L0 硬惩罚生效，锚点最敏感）。

    Returns
    -------
    dict
        {clean_reward, injected_reward, drop, min_drop, injected_level,
         n_decisions, n_decisions_after, penalty_applied, pass_}
    """
    from bioaudit.reward.api import reward

    clean = reward(trajectory, act=act, recipe=recipe)
    injected_decisions = list(trajectory["decisions"]) + [injection]
    injected = reward(injected_decisions, act=act, recipe=recipe)

    injected_level = None
    for s in injected["step_rewards"]:
        if s["step_id"] == injection["step_id"]:
            injected_level = s["level"]
            break

    clean_r = clean["trajectory_reward"]
    injected_r = injected["trajectory_reward"]
    drop = (clean_r - injected_r) if (clean_r is not None and injected_r is not None) else None
    return {
        "clean_reward": clean_r,
        "injected_reward": injected_r,
        "drop": round(float(drop), 4) if drop is not None else None,
        "min_drop": SPIKE_IN_MIN_DROP,
        "injected_level": injected_level,
        "n_decisions": len(trajectory["decisions"]),
        "n_decisions_after": len(injected_decisions),
        "penalty_applied": bool(injected["meta"].get("has_l0_penalty_applied")),
        "pass_": bool(
            drop is not None and drop >= SPIKE_IN_MIN_DROP
            and injected_level == 0
        ),
        "note": "注入决策必须被引擎判为 L0（调用方以 audit_decision 验证；"
                "测试守卫断言）",
    }


# ── 多种子稳定性（E3.8 / F6）──────────────────────────────────────────────────


def multi_seed_report(
    tasks: list[dict], seed_list: tuple[int, ...] = MULTI_SEEDS,
    n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """多种子复跑：ρ/τ 点估计跨种子恒定（确定性）+ CI 边界稳定性报告。"""
    qualities = [gold_quality(t) for t in tasks]
    rewards = [task_reward(t, "B") for t in tasks]
    base = rank_consistency(qualities, rewards, seed=seed_list[0], n_boot=n_boot)
    rows = []
    stable = True
    for seed in seed_list:
        rc = rank_consistency(qualities, rewards, seed=seed, n_boot=n_boot)
        row = {
            "seed": seed,
            "spearman_point": rc["spearman"]["point"] if rc["spearman"] else None,
            "spearman_ci": rc["spearman"]["ci"] if rc["spearman"] else None,
            "kendall_point": rc["kendall_tau_b"]["point"] if rc["kendall_tau_b"] else None,
            "kendall_ci": rc["kendall_tau_b"]["ci"] if rc["kendall_tau_b"] else None,
        }
        rows.append(row)
    # 稳定性判据：点估计跨种子完全一致（确定性）+ CI 边界与首种子偏差 ≤ 0.05
    sp_pts = [r["spearman_point"] for r in rows]
    kp_pts = [r["kendall_point"] for r in rows]
    deterministic = (
        all(p == sp_pts[0] for p in sp_pts) and all(p == kp_pts[0] for p in kp_pts)
    )
    base_s = (base["spearman"]["ci"] if base["spearman"] else [0, 0])
    base_k = (base["kendall_tau_b"]["ci"] if base["kendall_tau_b"] else [0, 0])
    for r in rows[1:]:
        if r["spearman_ci"] is not None and (
                abs(r["spearman_ci"][0] - base_s[0]) > 0.05
                or abs(r["spearman_ci"][1] - base_s[1]) > 0.05):
            stable = False
        if r["kendall_ci"] is not None and (
                abs(r["kendall_ci"][0] - base_k[0]) > 0.05
                or abs(r["kendall_ci"][1] - base_k[1]) > 0.05):
            stable = False
    return {
        "rows": rows,
        "deterministic_point_estimates": deterministic,
        "ci_stable_within_0_05": stable,
        "note": "reward 本身无随机源（确定性）；bootstrap CI 边界跨种子应稳定",
    }


def run_calibration(
    tasks_dir: Optional[str] = None, seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """完整校准运行（`bio-audit reward-calibrate`）：消融 + 排序一致性 +
    分层检验 + 多种子 + 锚点。全部离线可运行（只消费包内任务集）。"""
    td = tasks_dir or TASKS_DIR
    tasks = load_tasks(td)
    ab = ablate(tasks, seed=seed, n_boot=n_boot)
    return {
        "n_tasks": len(tasks),
        "seed": seed,
        "n_boot": n_boot,
        "ablation": ab,
        "multi_seed": multi_seed_report(tasks, n_boot=n_boot),
        "recipe_B": {
            "rank_consistency": ab["recipes"]["B"]["rank_consistency"],
            "stratified_mean_test": ab["recipes"]["B"]["stratified_mean_test"],
        },
        "spike_in_anchor": "见 reward-validate 输出（强锚点，需干净轨迹）",
        "method": {
            "acceptance": "排序一致性（ρ/τ + CI）+ 分层均值检验（good vs bad，"
                          "预注册分组）；放弃 Spearman 点估计门槛（拍板 #2）",
            "weak_anchor": "benchmark gold 标注（correct/(correct+error)）",
            "strong_anchor": "spike-in 合成数据（注入已知 L0，drop ≥ {}）"
                             .format(SPIKE_IN_MIN_DROP),
        },
    }


__all__ = [
    "SPIKE_IN_MIN_DROP",
    "MULTI_SEEDS",
    "DEFAULT_SEED",
    "DEFAULT_N_BOOT",
    "spearman",
    "kendall_tau_b",
    "gold_quality",
    "task_reward",
    "rank_consistency",
    "stratified_mean_test",
    "ablate",
    "spike_in",
    "multi_seed_report",
    "run_calibration",
]
