"""任务集 manifest（E8/D1.4：semver + 变更走评审；与 B5 规则治理同门禁风格）。

taskset.json 结构（schema: benchmark.task.v1）::

    {
      "taskset_version": "1.0.0",
      "schema_version": "benchmark.task.v1",
      "generated_at": "...",
      "n_tasks": 30,
      "model_info": {"generator_model": "...", "evaluated_engine": "0.1.3",
                     "prompt_version": "sha256..."},
      "snapshot": {"engine": ..., "ruleset": ..., "ontology": ...},   # C1/P2 三元组
      "split": {"seed": 42, "public": [...], "hidden": [...]},        # E1 预注册划分
      "irr": {"cohen_kappa": ..., "krippendorff_alpha": ..., "n_items": ...},  # E3 实测
      "files": [{"path": "scrna/bmd_scrna_001.json", "sha256": ..., "size": ...}]
    }

校验（benchmark-validate 闸 1）：
  1. taskset_version 为 semver；schema_version 匹配
  2. files 与磁盘一一对应 + SHA256/size 一致
  3. 每条任务通过 Task schema（含 gold + difficulty）
  4. trajectory_id 唯一；split 覆盖全部任务且无重复
  5. 任务 provenance.source == benchmark（生成器信息在场，E6）
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from bioaudit.benchmark.models import TASKSET_SCHEMA_VERSION, Task
from bioaudit.benchmark.paths import TASKS_DIR

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                       r"(?:[-+][0-9A-Za-z.-]+)?$")

DEFAULT_TASKSET_VERSION = "1.0.0"


class TasksetError(ValueError):
    """taskset.json 缺失/非法/版本错误。"""


def taskset_path(tasks_dir: Optional[Path | str] = None) -> Path:
    return (Path(tasks_dir) if tasks_dir else TASKS_DIR) / "taskset.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_tasks(tasks_dir: Optional[Path | str] = None) -> list[dict]:
    """加载任务目录全部任务文件（不含 taskset.json），按路径排序。"""
    td = Path(tasks_dir) if tasks_dir else TASKS_DIR
    tasks = []
    for f in sorted(td.rglob("*.json")):
        if f.name == "taskset.json":
            continue
        tasks.append(json.loads(f.read_text(encoding="utf-8")))
    return tasks


def load_taskset(tasks_dir: Optional[Path | str] = None) -> dict:
    """读取 taskset.json；版本必须为 semver。"""
    p = taskset_path(tasks_dir)
    if not p.exists():
        raise TasksetError(f"taskset.json 不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TasksetError(f"taskset.json 不是合法 JSON: {exc}") from exc
    version = data.get("taskset_version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        raise TasksetError(f"taskset_version 必须为 semver，实际: {version!r}")
    return data


def validate_taskset(
    tasks_dir: Optional[Path | str] = None,
    manifest_path: Optional[Path | str] = None,
) -> dict:
    """校验任务集（清单 + schema + 唯一性 + split 完整性）。

    返回结构化报告（失败进 report["errors"]，不抛异常）。
    """
    td = Path(tasks_dir) if tasks_dir else TASKS_DIR
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        manifest = load_taskset(td)
    except TasksetError as exc:
        return {"ok": False, "errors": [{"kind": "manifest_unreadable", "detail": str(exc)}],
                "warnings": [], "n_tasks": 0}

    if manifest.get("schema_version") != TASKSET_SCHEMA_VERSION:
        errors.append({"kind": "schema_version_mismatch",
                       "actual": manifest.get("schema_version"),
                       "expected": TASKSET_SCHEMA_VERSION})

    # 1. files vs 磁盘
    disk_files = sorted(p for p in td.rglob("*.json") if p.name != "taskset.json")
    disk_by_rel = {str(p.relative_to(td)).replace("\\", "/"): p for p in disk_files}
    listed = [e["path"] for e in manifest.get("files", [])]
    for rel in sorted(set(disk_by_rel) - set(listed)):
        errors.append({"kind": "file_not_in_manifest", "path": rel})
    for rel in sorted(set(listed) - set(disk_by_rel)):
        errors.append({"kind": "file_missing_on_disk", "path": rel})

    # 2. 哈希
    for entry in manifest.get("files", []):
        f = disk_by_rel.get(entry["path"])
        if f is None:
            continue
        if _sha256(f) != entry.get("sha256"):
            errors.append({"kind": "hash_mismatch", "path": entry["path"]})

    # 3. 任务 schema + 唯一性
    ids: list[str] = []
    for rel, f in sorted(disk_by_rel.items()):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            task = Task(**data)
            ids.append(task.trajectory_id)
            if task.provenance.source != "benchmark":
                errors.append({"kind": "provenance_source", "path": rel,
                               "detail": f"source={task.provenance.source!r}（应为 benchmark）"})
            if task.provenance.generator is None:
                errors.append({"kind": "missing_generator_info", "path": rel,
                               "detail": "E6：任务须记录生成器模型/prompt 版本"})
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"kind": "task_schema", "path": rel, "detail": str(exc)[:200]})
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errors.append({"kind": "duplicate_trajectory_id", "ids": sorted(dup)})

    # 4. split 完整性
    split = manifest.get("split", {})
    pub = set(split.get("public", []))
    hid = set(split.get("hidden", []))
    overlap = pub & hid
    if overlap:
        errors.append({"kind": "split_overlap", "ids": sorted(overlap)})
    missing = set(ids) - pub - hid
    if missing:
        errors.append({"kind": "split_missing", "ids": sorted(missing)})
    extra = (pub | hid) - set(ids)
    if extra:
        errors.append({"kind": "split_extra", "ids": sorted(extra)})

    # 5. 模型信息与快照在场（E6 + C1/P2）
    if not isinstance(manifest.get("model_info"), dict):
        errors.append({"kind": "missing_model_info"})
    if not isinstance(manifest.get("snapshot"), dict):
        errors.append({"kind": "missing_snapshot"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "taskset_version": manifest.get("taskset_version"),
        "n_tasks": len(disk_files),
        "n_public": len(pub),
        "n_hidden": len(hid),
    }


def generate_taskset(
    tasks_dir: Optional[Path | str] = None,
    taskset_version: str = DEFAULT_TASKSET_VERSION,
    model_info: Optional[dict] = None,
    snapshot: Optional[dict] = None,
    split: Optional[dict] = None,
    irr: Optional[dict] = None,
    note: Optional[str] = None,
) -> dict:
    """重新生成 taskset.json（任务集变更流程的落盘步骤；版本显式传入，不自动 bump）。"""
    if not SEMVER_RE.match(taskset_version):
        raise TasksetError(f"taskset_version 必须为 semver，实际: {taskset_version!r}")
    td = Path(tasks_dir) if tasks_dir else TASKS_DIR
    out = taskset_path(td)

    files = []
    ids = []
    for f in sorted(td.rglob("*.json")):
        if f.name == "taskset.json":
            continue
        rel = str(f.relative_to(td)).replace("\\", "/")
        files.append({"path": rel, "sha256": _sha256(f), "size": f.stat().st_size})
        ids.append(json.loads(f.read_text(encoding="utf-8"))["trajectory_id"])

    from datetime import date as _date

    manifest = {
        "taskset_version": taskset_version,
        "schema_version": TASKSET_SCHEMA_VERSION,
        "generated_at": _date.today().isoformat(),
        "n_tasks": len(files),
        "model_info": model_info or {},
        "snapshot": snapshot or {},
        "split": split or {"seed": None, "public": [], "hidden": []},
        "irr": irr or {},
        "note": note or "任务集由 bioaudit.benchmark.manifest.generate_taskset 生成",
        "files": files,
    }
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return manifest


__all__ = [
    "SEMVER_RE",
    "DEFAULT_TASKSET_VERSION",
    "TASKSET_SCHEMA_VERSION",
    "TasksetError",
    "taskset_path",
    "load_tasks",
    "load_taskset",
    "validate_taskset",
    "generate_taskset",
]
