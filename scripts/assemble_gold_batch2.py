"""批 2 gold 组装：批 2 双标注合并 → 批 2 任务 gold + 难度 → 60 条 taskset.json（F2d/F3）。

流程（预注册，protocol.PRE_REGISTRATION = benchmark-pr-2026-08-16-02）：
  1. 读批 2 双标注 JSONL（annotator_A/B_batch2_calib/full）+ 批 2 仲裁 JSONL
  2. 校准批 IRR（10 条新任务，κ/α ≥ 0.8 门槛，预注册）
  3. merge_annotations → 批 2 每决策 consensus（strong/medium/weak）
  4. 批 2 任务文件写 gold（annotation.v1.1，带批次 IRR + 快照三元组，D3.8）
  5. difficulty：gold 特征按 difficulty.v1 计算（E4，与审计分数零接触）
  6. **60 条全量重新划分**（assign_split：范式×难度分层，seed=42，70/30；
     hidden n≈18，预注册 v2 声明）
  7. 生成 taskset.json v1.1.0（semver 显式提升，E8；含批 1/批 2 IRR 留档）
  8. validate_taskset 校验

用法：python scripts/assemble_gold_batch2.py [--tasks-dir PATH] [--annotation-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bioaudit.benchmark.annotation import (  # noqa: E402
    ANNOTATION_VERSION,
    compute_irr,
    label_counts,
    merge_annotations,
)
from bioaudit.benchmark.difficulty import assign_difficulty  # noqa: E402
from bioaudit.benchmark.generator import prompt_hash  # noqa: E402
from bioaudit.benchmark.manifest import (  # noqa: E402
    generate_taskset,
    load_tasks,
    validate_taskset,
)
from bioaudit.benchmark.protocol import assign_split  # noqa: E402

#: 批 2 校准批任务清单（预注册，2026-08-16 窗口 F：跨范式 × 跨难度 10 条；
#: κ/α ≥ 0.8 达标后放量标注剩余 20 条）
CALIB_BATCH2_IDS: set[str] = {
    "bmd_scrna_013", "bmd_scrna_017", "bmd_scrna_019", "bmd_scrna_020",
    "bmd_pan_011", "bmd_pan_014", "bmd_pan_017",
    "bmd_deg_009", "bmd_deg_012", "bmd_deg_013",
}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default=None)
    parser.add_argument("--annotation-dir", default=None)
    args = parser.parse_args()

    from bioaudit.benchmark.paths import ANNOTATION_DIR, TASKS_DIR

    ad = Path(args.annotation_dir) if args.annotation_dir else ANNOTATION_DIR
    td = Path(args.tasks_dir) if args.tasks_dir else TASKS_DIR

    # 1. 批 2 双标注（校准批 + 放量批）+ 仲裁
    def _rows(prefix_a: str, prefix_b: str, suffix: str) -> tuple[list[dict], list[dict]]:
        pa, pb = ad / f"{prefix_a}_{suffix}.jsonl", ad / f"{prefix_b}_{suffix}.jsonl"
        if not pa.exists() or not pb.exists():
            raise FileNotFoundError(f"批 2 标注缺失: {pa} / {pb}")
        return load_jsonl(pa), load_jsonl(pb)

    calib_a, calib_b = _rows("annotator_A_batch2", "annotator_B_batch2", "calib")
    full_a, full_b = _rows("annotator_A_batch2", "annotator_B_batch2", "full")
    anno_a = calib_a + full_a
    anno_b = calib_b + full_b

    arb_path = ad / "arbitration_batch2.jsonl"
    arbitration = load_jsonl(arb_path) if arb_path.exists() else None

    # 2. 校准批 IRR（预注册门槛：κ/α ≥ 0.8）
    calib_irr = compute_irr(calib_a, calib_b)
    print(f"校准批 IRR: κ={calib_irr['cohen_kappa']} α={calib_irr['krippendorff_alpha']} "
          f"n={calib_irr['n_items']} agreed={calib_irr['n_agreed']} "
          f"gate={calib_irr['gate']['primary_pass'] and calib_irr['gate']['secondary_pass']}")
    if not (calib_irr["gate"]["primary_pass"] and calib_irr["gate"]["secondary_pass"]):
        print("❌ 校准批 IRR 未达门槛（κ/α ≥ 0.8），禁止放量组装", file=sys.stderr)
        return 1

    # 3. 批 2 全量 IRR + 合并
    batch2_irr = compute_irr(anno_a, anno_b)
    print(f"批 2 全量 IRR: κ={batch2_irr['cohen_kappa']} α={batch2_irr['krippendorff_alpha']} "
          f"n={batch2_irr['n_items']} agreed={batch2_irr['n_agreed']}")
    merged = merge_annotations(anno_a, anno_b, arbitration)
    counts = label_counts(merged)
    print(f"批 2 merged: {counts}")

    # 批 1 全量标注（留档对照）→ 全量 60 IRR
    batch1_a = load_jsonl(ad / "annotator_A.jsonl")
    batch1_b = load_jsonl(ad / "annotator_B.jsonl")
    full60_irr = compute_irr(batch1_a + anno_a, batch1_b + anno_b)
    print(f"全量 60 IRR: κ={full60_irr['cohen_kappa']} α={full60_irr['krippendorff_alpha']} "
          f"n={full60_irr['n_items']} agreed={full60_irr['n_agreed']}")

    # 4. 批 2 任务写 gold + difficulty（只动批 2 文件；批 1 任务不追溯重判）
    tasks = load_tasks(td)
    batch2_ids = CALIB_BATCH2_IDS | {
        "bmd_scrna_014", "bmd_scrna_015", "bmd_scrna_016", "bmd_scrna_018",
        "bmd_scrna_021", "bmd_scrna_022",
        "bmd_pan_012", "bmd_pan_013", "bmd_pan_015", "bmd_pan_016",
        "bmd_pan_018", "bmd_pan_019", "bmd_pan_020",
        "bmd_deg_010", "bmd_deg_011", "bmd_deg_014", "bmd_deg_015",
        "bmd_deg_016", "bmd_deg_017", "bmd_deg_018",
    }
    n_disputed = 0
    for task in tasks:
        tid = task["trajectory_id"]
        if tid not in batch2_ids:
            continue
        task_steps = {d["step_id"] for d in task["decisions"]}
        merged_by_step = {m["step_id"]: m for m in merged
                          if m.get("task_id") == tid and m["step_id"] in task_steps}
        missing = task_steps - set(merged_by_step)
        if missing:
            print(f"❌ {tid} 缺标注: {sorted(missing)}", file=sys.stderr)
            return 1
        disputed = [m for m in merged_by_step.values() if m["consensus"] == "disputed"]
        n_disputed += len(disputed)
        if disputed:
            print(f"⚠ {tid} 有 {len(disputed)} 条 disputed（无仲裁记录）", file=sys.stderr)

        gold_labels = [{"step_id": m["step_id"], "label": m["label"],
                        "consensus": m["consensus"]}
                       for m in merged_by_step.values()]
        task["gold"] = {
            "version": ANNOTATION_VERSION,  # annotation.v1.1（rubric v1.1 生效）
            "annotated_at": datetime.now(timezone.utc).isoformat(),
            "irr": {k: batch2_irr[k] for k in ("n_items", "n_agreed", "cohen_kappa",
                                               "krippendorff_alpha")},
            "labels": gold_labels,
        }
        type_map = {d["step_id"]: d["decision_type"] for d in task["decisions"]}
        from bioaudit.benchmark.models import GoldAnnotation

        gold_model = GoldAnnotation(**task["gold"])
        task["difficulty"] = assign_difficulty(gold_model, type_map).model_dump(mode="json")
        f = td / task["act"] / f"{tid}.json"
        f.write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")

    # 5. 60 条全量重新划分（预注册 v2：seed 42 不变，hidden n≈18）
    split_map = assign_split(tasks)
    split = {"seed": 42, "public": [], "hidden": []}
    for tid, s in sorted(split_map.items()):
        split[s].append(tid)

    # 6. taskset.json v1.1.0（semver 显式提升，E8）
    import bioaudit
    from bioaudit.rules.manifest import ruleset_version

    snapshot = {
        "engine": bioaudit.ENGINE_VERSION,
        "ruleset": ruleset_version(),
        "ontology": bioaudit.ONTOLOGY_VERSION,
    }
    model_info = {
        "generator_model": "deepseek-v4-flash",
        "evaluated_engine": bioaudit.ENGINE_VERSION,
        "prompt_version": prompt_hash(),
        "transform_version": "generator.transform.v1",
    }
    generate_taskset(
        tasks_dir=td,
        taskset_version="1.1.0",
        model_info=model_info,
        snapshot=snapshot,
        split=split,
        irr={
            "calibration_batch1": {  # 批 1 旧值留档（E1：不覆盖旧记录）
                "n_items": 127, "n_agreed": 117, "cohen_kappa": 0.8087,
                "krippendorff_alpha": 0.808, "gate_pass": True,
                "note": "批 1 校准批（record benchmark-pr-2026-08-16-01，留档）",
            },
            "calibration_batch2": {
                "n_items": calib_irr["n_items"],
                "n_agreed": calib_irr["n_agreed"],
                "cohen_kappa": calib_irr["cohen_kappa"],
                "krippendorff_alpha": calib_irr["krippendorff_alpha"],
                "gate_pass": calib_irr["gate"]["primary_pass"]
                             and calib_irr["gate"]["secondary_pass"],
                "note": "批 2 校准批 10 条新任务（预注册门槛 κ/α ≥ 0.8 达标后放量）",
            },
            "batch1_full": {  # 批 1 全量旧值留档
                "n_items": 348, "n_agreed": 316, "cohen_kappa": 0.7292,
                "krippendorff_alpha": 0.7288,
                "note": "批 1 全量 30 条实测（annotation.v1，留档）",
            },
            "batch2_full": {k: batch2_irr[k]
                            for k in ("n_items", "n_agreed", "cohen_kappa",
                                      "krippendorff_alpha")},
            "full_60": {k: full60_irr[k]
                        for k in ("n_items", "n_agreed", "cohen_kappa",
                                  "krippendorff_alpha")},
            "note": "校准批为预注册 IRR 门槛（κ/α ≥ 0.8 达标后放量）；"
                    "full_60 = 批 1 + 批 2 全量合并实测（批 2 用 annotation.v1.1）",
        },
        note="60 条任务集（批 1 30 + 批 2 30；taskset v1.1.0，窗口 F）",
    )

    # 7. 校验
    report = validate_taskset(td)
    print(f"validate: ok={report['ok']} n_tasks={report['n_tasks']} "
          f"public={report['n_public']} hidden={report['n_hidden']}")
    if not report["ok"]:
        print(json.dumps(report["errors"], ensure_ascii=False, indent=1), file=sys.stderr)
        return 1

    # 8. 难度分布复核（3 范式 × 3 梯度）
    from collections import Counter

    diff_by_act: dict[str, Counter] = {}
    for t in load_tasks(td):
        diff_by_act.setdefault(t["act"], Counter())[str(t["difficulty"]["label"])] += 1
    for act in ("scrna", "pan", "deg"):
        print(f"difficulty {act}: {dict(sorted(diff_by_act.get(act, {}).items()))}")

    # 9. 标注报告落盘（批 2 + 全量 60 合并）
    from bioaudit.benchmark.annotation import save_annotation_report  # noqa: F401

    report_data = {
        "annotation_version": ANNOTATION_VERSION,
        "snapshot": snapshot,
        "irr_calibration_batch2": calib_irr,
        "irr_batch2_full": batch2_irr,
        "irr_full_60": full60_irr,
        "irr_batch1_archived": {
            "calibration": {"n_items": 127, "n_agreed": 117, "cohen_kappa": 0.8087,
                            "krippendorff_alpha": 0.808},
            "full": {"n_items": 348, "n_agreed": 316, "cohen_kappa": 0.7292,
                     "krippendorff_alpha": 0.7288},
        },
        "merged_counts_batch2": counts,
        "calibration_batch2_ids": sorted(CALIB_BATCH2_IDS),
    }
    (ad / "irr_report_batch2.json").write_text(
        json.dumps(report_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (ad / "merged_annotations_batch2.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
