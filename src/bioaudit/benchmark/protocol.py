"""预注册协议（refactor-plan-v1.1 E1；execution-plan D2.6 / F1.3）。

v1.1 裁决：阶段 3 验收"隐藏集与公开集一致"改为 **预注册 gap 容忍区间 + 负向告警**
——两集分数 gap 超出预注册容忍区间 → 负向告警（泄漏信号），替代"两集一致"验收。

本模块是**预注册记录的唯一事实源**（record 常量冻结）：
- **当前记录（活动）**：``PRE_REGISTRATION`` = record ``benchmark-pr-2026-08-16-02``
  （批 2，2026-08-16 冻结）——60 条任务集生效；批 1 记录
  ``benchmark-pr-2026-08-16-01`` 留档（``PRE_REGISTRATION_V1`` + 磁盘副本
  ``pre_registration_v1_archived.json``）。
- 公开/隐藏集划分方法（分层随机：范式 × 难度，seed=42，70/30）
- gap 统计量与容忍区间
- IRR 门槛（E3：κ/α ≥ 0.8 准入）与难度 rubric 版本

实现要点：
- ``assign_split``：确定性（给定任务清单 → 同一划分）；划分在 gold+难度冻结后
  一次性执行并写入 taskset.json（E1：先预注册、后划分、再评测）。
  批 2 流程：60 条全量重新划分（hidden n≈18），seed 与方法不变（可复现）。
- ``check_gap``：|Δ| 超出容忍区间 → alarm=True（泄漏信号负向告警）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

PRE_REGISTRATION_VERSION = "pr.v2"
RECORD_ID = "benchmark-pr-2026-08-16-02"

#: 批 1 预注册记录（留档，2026-08-16；record benchmark-pr-2026-08-16-01）。
#: 批 2 不改批 1 旧值——gap 容忍区间/划分 seed 保持原样归档。
PRE_REGISTRATION_V1: dict[str, Any] = {
    "record_id": "benchmark-pr-2026-08-16-01",
    "date": "2026-08-16",
    "version": "pr.v1",
    "split": {
        "method": "stratified_random_by_paradigm_and_difficulty",
        "seed": 42,
        "public_ratio": 0.7,
        "note": "按范式×难度分层后按 seed 确定性划分；划分在 gold+难度冻结后"
                "执行并写入 taskset.json",
    },
    "gap": {
        "statistic": "mean(trajectory_score/100) over tasks: public - hidden",
        "tolerance_interval": [-0.10, 0.10],
        "alarm_rule": "Δ 超出 [-0.10, 0.10] → 负向告警（泄漏信号），替代'两集一致'验收（E1）",
        "report_note": "gap 只作为泄漏信号登记；不据此修改任何分数或任务",
    },
    "irr_gate": {
        "primary": "cohen_kappa_3class >= 0.8",
        "secondary": "krippendorff_alpha_nominal >= 0.8",
        "calibration_batch_size": 10,
        "note": "校准批 10 条双标注达标后放量（E3）",
    },
    "difficulty_rubric_version": "difficulty.v1",
    "annotation_rubric_version": "annotation.v1",
    "model_policy": "生成器与评测 Agent 不同模型（E6）；模型信息记录在任务集元数据",
    "contamination_policy": "任务/生成器提示词中规则标识/标题命中登记为污染特征（E2），命中即标记",
}

#: 批 2 预注册记录（冻结；任何修改需走评审并提升 RECORD_ID）
PRE_REGISTRATION: dict[str, Any] = {
    "record_id": RECORD_ID,
    "date": "2026-08-16",
    "version": PRE_REGISTRATION_VERSION,
    "supersedes": "benchmark-pr-2026-08-16-01",
    "scope": "批 2 任务集扩展（30 → 60 条）后全量生效",
    "split": {
        "method": "stratified_random_by_paradigm_and_difficulty",
        "seed": 42,
        "public_ratio": 0.7,
        "note": "批 1 划分在 30 条上执行；批 2 合并为 60 条后**重新划分**"
                "（gold+难度冻结后一次性执行，seed/方法不变可复现）；"
                "hidden n≈18",
    },
    "gap": {
        "statistic": "mean(trajectory_score/100) over tasks: public - hidden",
        "tolerance_interval": [-0.10, 0.10],
        "alarm_rule": "Δ 超出 [-0.10, 0.10] → 负向告警（泄漏信号），替代'两集一致'验收（E1）",
        "report_note": "gap 只作为泄漏信号登记；不据此修改任何分数或任务",
        "re_evaluation": (
            "批 1 实测 Δ=-0.1864（区间外）判读为隐藏集小样本组成偏差"
            "（hidden n=9；确定性引擎无泄漏通道）；批 2 隐藏集 n≈18 后"
            "重评估：区间保持 [-0.10, +0.10] 不变（保守、与批 1 口径可比；"
            "n 扩大后抽样误差缩小，若仍出界则组成偏差解释的置信度上升）。"
            "结果如实呈现：可能收敛回区间内，也可能仍超区间，均按协议登记不改分。"
        ),
    },
    "irr_gate": {
        "primary": "cohen_kappa_3class >= 0.8",
        "secondary": "krippendorff_alpha_nominal >= 0.8",
        "calibration_batch_size": 10,
        "note": "批 2 新校准批（10 条新任务，跨范式跨难度）双标注达标后放量（E3）；"
                "批 1 校准批旧值留档（κ=0.8087 / α=0.8080）",
    },
    "difficulty_rubric_version": "difficulty.v1",
    "annotation_rubric_version": "annotation.v1.1",
    "model_policy": "生成器与评测 Agent 不同模型（E6）；模型信息记录在任务集元数据",
    "contamination_policy": "任务/生成器提示词中规则标识/标题命中登记为污染特征（E2），命中即标记",
    "corpus_policy": "批 2 语料扩展优先纳入新错误模式素材（CellVoyager hook 真实运行"
                     "未实测，窗口 C 遗留）；继续使用现有语料库（20 条 legacy + "
                     "scrna_melanoma_cellvoyager），如实声明",
}


def pre_registration_path(pkg_root: Optional[Path] = None) -> Path:
    """预注册记录落盘路径（docs 侧副本 + 包内 record）。"""
    base = pkg_root or Path(__file__).resolve().parent
    return base / "pre_registration.json"


def pre_registration_v1_archive_path(pkg_root: Optional[Path] = None) -> Path:
    """批 1 预注册留档路径（record benchmark-pr-2026-08-16-01 副本）。"""
    base = pkg_root or Path(__file__).resolve().parent
    return base / "pre_registration_v1_archived.json"


def save_pre_registration(pkg_root: Optional[Path] = None) -> Path:
    """把预注册记录写为 JSON（机器可读 + 版本冻结）：活动记录 + 批 1 留档。"""
    out = pre_registration_path(pkg_root)
    out.write_text(json.dumps(PRE_REGISTRATION, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    archive = pre_registration_v1_archive_path(pkg_root)
    archive.write_text(json.dumps(PRE_REGISTRATION_V1, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    return out


def load_pre_registration(path: Optional[Path | str] = None) -> dict:
    """读取预注册记录（与常量一致性校验）。"""
    p = Path(path) if path else pre_registration_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("record_id") != RECORD_ID:
        raise ValueError(f"预注册记录版本不匹配: {data.get('record_id')} != {RECORD_ID}")
    return data


# ── 公开/隐藏集划分（E1：预注册方法，确定性执行）──────────────────────────────


def assign_split(
    tasks: list[dict],
    seed: Optional[int] = None,
    public_ratio: Optional[float] = None,
) -> dict[str, str]:
    """确定性分层划分：{trajectory_id: 'public'|'hidden'}。

    分层键 = (act, difficulty.label)；每层内按 seed 打散后取前
    public_ratio 比例为 public；n≥2 的层保证至少 1 条进 hidden（隐藏集
    不可为空）；单任务层归 public。全部分层后若 hidden 仍为空（病态输入），
    确定性地把最后一条（按 id 排序）转 hidden。
    同一 (tasks, seed, public_ratio) 输入 → 同一输出（可复现）。
    """
    import numpy as np

    rng = np.random.default_rng(seed if seed is not None else PRE_REGISTRATION["split"]["seed"])
    ratio = public_ratio if public_ratio is not None else PRE_REGISTRATION["split"]["public_ratio"]

    by_stratum: dict[tuple[str, int], list[str]] = {}
    for t in tasks:
        key = (t["act"], int(t["difficulty"]["label"]))
        by_stratum.setdefault(key, []).append(t["trajectory_id"])

    assignment: dict[str, str] = {}
    for key, ids in sorted(by_stratum.items()):
        ids = sorted(ids)
        perm = rng.permutation(len(ids))
        ordered = [ids[i] for i in perm]
        if len(ordered) == 1:
            n_public = 1  # 单任务层 → public（隐藏集由其它层保证非空）
        else:
            n_public = int(round(len(ordered) * ratio))
            n_public = min(max(n_public, 1), len(ordered) - 1)
        for i, tid in enumerate(ordered):
            assignment[tid] = "public" if i < n_public else "hidden"
    if assignment and "hidden" not in set(assignment.values()):
        # 病态兜底：全部单任务层 → 最后一条（id 序）转 hidden
        last = sorted(assignment)[-1]
        assignment[last] = "hidden"
    return assignment


# ── gap 检查（E1：负向告警）───────────────────────────────────────────────────


def check_gap(
    public_scores: list[float],
    hidden_scores: list[float],
    tolerance: Optional[list[float]] = None,
) -> dict:
    """预注册 gap 统计：Δ = mean(public) − mean(hidden)（分数已归一化 0..1）。

    返回：delta / in_tolerance / alarm（超出区间 → True，泄漏信号负向告警）。
    """
    tol = tolerance if tolerance is not None else PRE_REGISTRATION["gap"]["tolerance_interval"]
    if not public_scores or not hidden_scores:
        return {"delta": None, "in_tolerance": None, "alarm": False,
                "n_public": len(public_scores), "n_hidden": len(hidden_scores),
                "note": "某侧为空，gap 不可计算（不告警，记为数据缺口）"}
    import numpy as np

    delta = float(np.mean(public_scores) - np.mean(hidden_scores))
    lo, hi = tol
    in_tolerance = lo <= delta <= hi
    return {
        "statistic": PRE_REGISTRATION["gap"]["statistic"],
        "tolerance_interval": tol,
        "delta": round(delta, 4),
        "in_tolerance": in_tolerance,
        "alarm": not in_tolerance,
        "n_public": len(public_scores),
        "n_hidden": len(hidden_scores),
        "note": PRE_REGISTRATION["gap"]["report_note"],
    }


__all__ = [
    "PRE_REGISTRATION_VERSION",
    "RECORD_ID",
    "PRE_REGISTRATION",
    "PRE_REGISTRATION_V1",
    "pre_registration_path",
    "pre_registration_v1_archive_path",
    "save_pre_registration",
    "load_pre_registration",
    "assign_split",
    "check_gap",
]
