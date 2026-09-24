"""真值标注管线（refactor-plan-v1.1 E3；execution-plan D3.7/D3.8）。

- **双标注 + IRR 门槛**：Cohen's κ（3 类 nominal）+ Krippendorff's α
  （nominal）；预注册门槛 κ/α ≥ 0.8（protocol.PRE_REGISTRATION.irr_gate）；
  校准批 10 条先跑，达标后放量。
- **分歧仲裁**：两标不一致 → 仲裁者按 rubric 定案；共识强度
  strong（双标一致）/ medium（仲裁与一方一致，2:1）/ weak（仲裁与双方均不一致）。
- **gold 与三元组快照绑定**（D3.8）：标注产物带 ruleset/ontology/engine 版本
  （C1/P2 复用），gold 组装时写入任务文件。

标注产物（src/bioaudit/data/annotation/）：
  annotator_A.jsonl / annotator_B.jsonl   双标注原始输出
  arbitration.jsonl                       仲裁记录（分歧条目）
  merged_annotations.json                 合并结果（每决策 consensus）
  irr_report.json                         IRR 实测报告
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

#: 标注管线版本（批 2 起 = annotation.v1.1：D 窗口遗留 6 条澄清点，见
#: benchmark/annotation_rubric.md §四；批 1 标注保留 annotation.v1 不追溯重判）
ANNOTATION_VERSION = "annotation.v1.1"


# ── IRR 统计量 ─────────────────────────────────────────────────────────────────


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's κ（nominal，多类）。两列表长度必须相等。"""
    if len(a) != len(b):
        raise ValueError(f"双标注长度不一致: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return 1.0
    counts_a = Counter(a)
    counts_b = Counter(b)
    p_o = sum(1 for x, y in zip(a, b) if x == y) / n
    p_e = sum((counts_a[k] / n) * (counts_b[k] / n) for k in set(counts_a) | set(counts_b))
    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)


def krippendorff_alpha_nominal(items: dict[str, list[str]]) -> float:
    """Krippendorff's α（nominal 距离：相异度 0/1）。

    items: {item_id: [标注1, 标注2, ...]}——每条目至少 2 个标注。
    """
    # 只有 2 个标注者、无缺失：退化为 coincidence 矩阵版
    values = sorted({v for labels in items.values() for v in labels})
    n = sum(len(v) for v in items.values())
    if n <= 1 or len(values) < 2:
        return 1.0
    # 观察一致性（coincidence 对角和）
    do = 0.0
    for labels in items.values():
        cnt = Counter(labels)
        m = len(labels)
        do += sum(c * (c - 1) for c in cnt.values()) / (m * (m - 1)) if m > 1 else 0.0
    do /= len(items)
    # 期望一致性（边际）
    total_cnt = Counter(v for labels in items.values() for v in labels)
    de = 0.0
    for v in values:
        de += (total_cnt[v] / n) ** 2
    if de == 1.0:
        return 1.0
    return 1.0 - (1.0 - do) / (1.0 - de)


def _align_key(r: dict) -> str:
    """标注行对齐键 = (task_id, step_id)；无 task_id（单任务测试）回退 step_id。

    跨任务 step_id 会碰撞（每个任务都有 S1/D1/A1），只按 step_id 对齐会
    把不同任务的决策混在一起——必须带上 task_id。
    """
    tid = r.get("task_id")
    return f"{tid}::{r['step_id']}" if tid else r["step_id"]


def compute_irr(anno_a: Iterable[dict], anno_b: Iterable[dict]) -> dict:
    """双标注 → IRR 报告（按 (task_id, step_id) 对齐）。"""
    a_map = {_align_key(r): r["label"] for r in anno_a}
    b_map = {_align_key(r): r["label"] for r in anno_b}
    keys = sorted(set(a_map) & set(b_map))
    a = [a_map[k] for k in keys]
    b = [b_map[k] for k in keys]
    kappa = cohen_kappa(a, b)
    alpha = krippendorff_alpha_nominal({k: [a_map[k], b_map[k]] for k in keys})
    n_agreed = sum(1 for x, y in zip(a, b) if x == y)
    dist = Counter(zip(a, b))
    return {
        "n_items": len(keys),
        "n_agreed": n_agreed,
        "agreement_ratio": round(n_agreed / len(keys), 4) if keys else 1.0,
        "cohen_kappa": round(kappa, 4),
        "krippendorff_alpha": round(alpha, 4),
        "gate": {"primary": "cohen_kappa_3class >= 0.8",
                 "secondary": "krippendorff_alpha_nominal >= 0.8",
                 "primary_pass": kappa >= 0.8,
                 "secondary_pass": alpha >= 0.8},
        "confusion": {f"{x}->{y}": c for (x, y), c in sorted(dist.items())},
    }


# ── 仲裁与合并 ─────────────────────────────────────────────────────────────────


def merge_annotations(
    anno_a: Iterable[dict],
    anno_b: Iterable[dict],
    arbitration: Optional[Iterable[dict]] = None,
) -> list[dict]:
    """双标注 + 仲裁 → 每决策 {task_id, step_id, label, consensus}。

    一致 → strong；不一致：仲裁存在 → 取仲裁标签（若与任一标注一致 → medium，
    否则 weak）；仲裁缺失 → 保留双方标签并标 consensus="disputed"（调用方裁决）。
    对齐键 = (task_id, step_id)。
    """
    a_map = {_align_key(r): r for r in anno_a}
    b_map = {_align_key(r): r for r in anno_b}
    arb_map = {}
    if arbitration:
        for r in arbitration:
            arb_map[_align_key(r)] = r["label"]

    merged = []
    for key in sorted(set(a_map) | set(b_map)):
        ra, rb = a_map.get(key), b_map.get(key)
        la = ra["label"] if ra else None
        lb = rb["label"] if rb else None
        base = {"task_id": (ra or rb).get("task_id"),
                "step_id": (ra or rb)["step_id"]}
        if la is not None and la == lb:
            merged.append({**base, "label": la, "consensus": "strong"})
            continue
        if la is None:
            merged.append({**base, "label": lb, "consensus": "strong",
                           "note": "仅 B 标注"})
            continue
        if lb is None:
            merged.append({**base, "label": la, "consensus": "strong",
                           "note": "仅 A 标注"})
            continue
        # 分歧
        if key in arb_map:
            lab = arb_map[key]
            if lab in (la, lb):
                merged.append({**base, "label": lab, "consensus": "medium"})
            else:
                merged.append({**base, "label": lab, "consensus": "weak"})
        else:
            merged.append({**base, "label": la, "consensus": "disputed",
                           "labels": [la, lb], "note": "双标分歧且无仲裁记录"})
    return merged


def label_counts(merged: list[dict]) -> dict:
    """合并结果 → 标签分布 + 共识强度分布。"""
    return {
        "labels": dict(Counter(m["label"] for m in merged)),
        "consensus": dict(Counter(m["consensus"] for m in merged)),
        "n_items": len(merged),
    }


def save_annotation_report(
    out_dir: Path,
    irr: dict,
    merged: list[dict],
    snapshot: dict,
) -> Path:
    """标注报告落盘（irr_report.json + merged_annotations.json）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "annotation_version": ANNOTATION_VERSION,
        "snapshot": snapshot,
        "irr": irr,
        "merged_counts": label_counts(merged),
    }
    p = out_dir / "irr_report.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "merged_annotations.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


__all__ = [
    "ANNOTATION_VERSION",
    "cohen_kappa",
    "krippendorff_alpha_nominal",
    "compute_irr",
    "merge_annotations",
    "label_counts",
    "save_annotation_report",
]
