"""gold 组装：双标注合并 → 任务 gold + 难度 → split 划分 → taskset.json（D-b/D-c）。

流程（预注册，见 benchmark/protocol.py）：
  1. 读双标注 JSONL（annotator_A/B，可分批）+ 仲裁 JSONL（分歧条目）
  2. merge_annotations → 每决策 consensus（strong/medium/weak）
  3. 写入任务文件 gold（带标注版本/IRR/快照三元组，D3.8）
  4. difficulty：由 gold 特征按预注册 rubric 计算（E4，与审计分数零接触）
  5. split：按范式×难度分层随机（seed=42，70/30，预注册 E1）
  6. 生成 taskset.json（semver + 模型信息 + 快照 + split + IRR 实测）
  7. validate_taskset 校验

用法：python scripts/assemble_gold.py [--tasks-dir PATH] [--annotation-dir PATH]
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


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _calib_ids(ad: Path) -> set[str]:
    """校准批任务 id 集合（预注册固定清单，2026-08-16：10 条跨范式跨难度）。"""
    return {
        "bmd_scrna_001", "bmd_scrna_005", "bmd_scrna_007", "bmd_scrna_010",
        "bmd_pan_001", "bmd_pan_003", "bmd_pan_006",
        "bmd_deg_001", "bmd_deg_002", "bmd_deg_006",
    }


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

    # 1. 双标注 + 仲裁
    anno_a = load_jsonl(ad / "annotator_A.jsonl")
    anno_b = load_jsonl(ad / "annotator_B.jsonl")
    arb_path = ad / "arbitration.jsonl"
    arbitration = load_jsonl(arb_path) if arb_path.exists() else None

    irr = compute_irr(anno_a, anno_b)
    print(f"IRR: κ={irr['cohen_kappa']} α={irr['krippendorff_alpha']} "
          f"n={irr['n_items']} agreed={irr['n_agreed']}")

    # 校准批 IRR（预注册门槛以校准批为准：κ/α ≥ 0.8 达标后放量）
    calib_a = [r for r in anno_a if (ad / "annotator_A_calib.jsonl").exists()
               and r["task_id"] in _calib_ids(ad)]
    calib_b = [r for r in anno_b if (ad / "annotator_B_calib.jsonl").exists()
               and r["task_id"] in _calib_ids(ad)]
    calib_irr = compute_irr(calib_a, calib_b) if calib_a and calib_b else None

    merged = merge_annotations(anno_a, anno_b, arbitration)
    counts = label_counts(merged)
    print(f"merged: {counts}")

    # 2. 写 gold + difficulty
    tasks = load_tasks(td)
    n_disputed = 0
    for task in tasks:
        tid = task["trajectory_id"]
        # 按任务过滤：合并结果带 task_id（对齐键 = task_id::step_id）
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
            "version": ANNOTATION_VERSION,
            "annotated_at": datetime.now(timezone.utc).isoformat(),
            "irr": {k: irr[k] for k in ("n_items", "n_agreed", "cohen_kappa",
                                        "krippendorff_alpha")},
            "labels": gold_labels,
        }
        # 决策类型映射（难度特征需要）
        type_map = {d["step_id"]: d["decision_type"] for d in task["decisions"]}
        from bioaudit.benchmark.models import GoldAnnotation

        gold_model = GoldAnnotation(**task["gold"])
        task["difficulty"] = assign_difficulty(gold_model, type_map).model_dump(mode="json")
        # 落盘
        f = td / task["act"] / f"{tid}.json"
        f.write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")

    # 3. split（预注册：范式×难度分层，seed=42，70/30）
    split_map = assign_split(tasks)
    split = {"seed": 42, "public": [], "hidden": []}
    for tid, s in sorted(split_map.items()):
        split[s].append(tid)

    # 4. taskset.json
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
        taskset_version="1.0.0",
        model_info=model_info,
        snapshot=snapshot,
        split=split,
        irr={
            "calibration": ({"n_items": calib_irr["n_items"],
                             "n_agreed": calib_irr["n_agreed"],
                             "cohen_kappa": calib_irr["cohen_kappa"],
                             "krippendorff_alpha": calib_irr["krippendorff_alpha"],
                             "gate_pass": calib_irr["gate"]["primary_pass"] and
                                          calib_irr["gate"]["secondary_pass"]}
                            if calib_irr else None),
            "full": {k: irr[k] for k in ("n_items", "n_agreed", "cohen_kappa",
                                         "krippendorff_alpha")},
            "note": "校准批为预注册 IRR 门槛（κ/α ≥ 0.8 达标后放量）；full 为全量实测",
        },
        note="首批 30 条（批 1/2；批 2 排期补齐至 60）",
    )

    # 5. 校验
    report = validate_taskset(td)
    print(f"validate: ok={report['ok']} n_tasks={report['n_tasks']} "
          f"public={report['n_public']} hidden={report['n_hidden']}")
    if not report["ok"]:
        print(json.dumps(report["errors"], ensure_ascii=False, indent=1), file=sys.stderr)
        return 1

    # 6. 难度分布
    from collections import Counter

    diff_dist = Counter(str(t["difficulty"]["label"]) for t in load_tasks(td))
    print(f"difficulty distribution: {dict(sorted(diff_dist.items()))}")

    # 7. 标注报告落盘（irr_report.json + merged_annotations.json，全量）
    from bioaudit.benchmark.annotation import save_annotation_report

    save_annotation_report(ad, irr, merged, snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
