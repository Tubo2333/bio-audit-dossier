"""任务生成器（D1.2/D1.3；E6 防泄漏落地）。

管线：**LLM 变体规格（generator_prompt.md，零规则内容）+ 确定性变换
（本模块）+ 人工审核**。错误注入素材只来自真实 Agent 运行语料
（``base_trajectories`` / ``error_pattern_sources`` 记录在 provenance），
**不做规则反推**（防 E1 循环）。

- ``apply_spec``：语料轨迹 + 变体规格 → 任务草稿（无 gold——gold 由独立标注
  管线产出，生成器不写 gold）。
- ``write_draft``：草稿落盘（供标注）。
- 模型信息：generator.model 记录实际生成 LLM（E6：与评测 Agent 不同模型，
  评测 Agent = 确定性引擎 bioaudit.engine，版本记入 taskset snapshot）。

golden 不变量：任务写进 ``data/tasks/``，与 ``data/trajectories/`` 分开；
引擎/评分路径零改动。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from bioaudit.benchmark.models import BenchmarkGeneratorInfo, BenchmarkProvenance
from bioaudit.benchmark.paths import GENERATOR_PROMPT, TASKS_DIR
from bioaudit.paths import TRAJECTORIES_DIR

TRANSFORM_VERSION = "generator.transform.v1"

#: 生成器 LLM 标识（E6：与评测 Agent 不同模型；评测 Agent = 确定性引擎）
GENERATOR_MODEL = "deepseek-v4-flash"


def prompt_hash() -> str:
    """generator_prompt.md 的 sha256 前 12 位（E6：提示词版本锁定）。"""
    return hashlib.sha256(GENERATOR_PROMPT.read_bytes()).hexdigest()[:12]


def load_corpus(corpus_dir: Optional[Path | str] = None) -> dict[str, dict]:
    """加载真实 Agent 语料轨迹（v2 canonical 目录）。"""
    cd = Path(corpus_dir) if corpus_dir else TRAJECTORIES_DIR
    corpus: dict[str, dict] = {}
    for f in sorted(cd.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        corpus[data["trajectory_id"]] = data
    return corpus


def _clone_decisions(base_traj: dict) -> list[dict]:
    """深拷贝 base 的 decisions。"""
    return [json.loads(json.dumps(d)) for d in base_traj["decisions"]]


def apply_spec(
    spec: dict,
    corpus: dict[str, dict],
    reviewed_by: str = "window-D review",
    reviewed_at: str = "2026-08-16",
    generator_model: str = GENERATOR_MODEL,
) -> dict:
    """变体规格 → 任务草稿（确定性变换；gold/difficulty 由后续管线填充）。

    spec 字段（由生成器 LLM 产出、人工审核，见 generator_prompt.md）：
      trajectory_id / act / base / dataset / context_overrides /
      choice_replacements / add_decisions / remove_steps / error_pattern_sources
    """
    base_id = spec["base"]
    if base_id not in corpus:
        raise KeyError(f"语料中不存在基础轨迹: {base_id}")
    base = corpus[base_id]

    decisions = _clone_decisions(base)

    # 1. remove_steps
    remove = set(spec.get("remove_steps", []))
    if remove:
        decisions = [d for d in decisions if d["step_id"] not in remove]

    # 2. context_overrides: {step_id: {key: value}}
    for step_id, overrides in spec.get("context_overrides", {}).items():
        for d in decisions:
            if d["step_id"] == step_id:
                d["context"] = dict(d.get("context", {}))
                d["context"].update(overrides)

    # 3. choice_replacements: [{step, choice, rationale}]
    for rep in spec.get("choice_replacements", []):
        for d in decisions:
            if d["step_id"] == rep["step"]:
                d["choice"] = rep["choice"]
                if rep.get("rationale"):
                    d["rationale"] = rep["rationale"]
                break

    # 4. dataset: 数据集信息注入（数值自洽由规格作者保证）
    ds = spec.get("dataset", {})
    if ds:
        for d in decisions:
            d.setdefault("context", {})
            for key in ("data_source", "data_format"):
                if key in ds and key not in d["context"]:
                    d["context"][key] = ds[key]

    # 5. add_decisions: 追加决策（step_id 自动编号，避免冲突）
    existing = {d["step_id"] for d in decisions}
    n = 1
    for add in spec.get("add_decisions", []):
        while f"A{n}" in existing:
            n += 1
        step_id = f"A{n}"
        n += 1
        decisions.append({
            "step_id": step_id,
            "decision_type": add["decision_type"],
            "choice": add["choice"],
            "rationale": add.get("rationale", ""),
            "context": add.get("context", {}),
        })

    # 6. step_id 稳定性（删除后保持顺序；保留原 id 便于 gold 对齐）
    provenance = BenchmarkProvenance(
        source="benchmark",
        base_trajectories=[base_id],
        error_pattern_sources=sorted(spec.get("error_pattern_sources", [])),
        generator=BenchmarkGeneratorInfo(
            model=generator_model,
            prompt_version=prompt_hash(),
            transform_version=TRANSFORM_VERSION,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        ),
        note=(
            "benchmark 任务草稿：语料变换生成（E6：错误注入来自真实语料，"
            "非规则反推）；gold/difficulty 由标注管线填充"
        ),
    )
    draft = {
        "version": 2,
        "trajectory_id": spec["trajectory_id"],
        "act": spec["act"],
        "provenance": provenance.model_dump(mode="json"),
        "decisions": decisions,
    }
    # 草稿不含 gold/difficulty——Task 模型校验在 gold 组装后进行
    return draft


def write_draft(
    spec: dict,
    corpus: dict[str, dict],
    tasks_dir: Optional[Path | str] = None,
    **provenance_kwargs,
) -> Path:
    """生成并落盘任务草稿 → tasks/<act>/<trajectory_id>.json。"""
    td = Path(tasks_dir) if tasks_dir else TASKS_DIR
    draft = apply_spec(spec, corpus, **provenance_kwargs)
    out_dir = td / spec["act"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{spec['trajectory_id']}.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return out


__all__ = [
    "TRANSFORM_VERSION",
    "GENERATOR_MODEL",
    "prompt_hash",
    "load_corpus",
    "apply_spec",
    "write_draft",
]
