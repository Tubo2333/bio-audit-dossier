"""演示素材索引：demo/data 提炼摘要 + 包内资产统一出口。

自包含性硬约束（深色审计台设计规范 §5/§6）：
- demo 运行时**只读** ``demo/data/``（提炼摘要，带 provenance）与
  bioaudit 包内资产（trajectories v2 / expected_types.yaml 等）；
- **禁止**读仓库外 cellvoyager-outputs；
- 启动校验：manifest 指纹比对，缺失给中文提示（见 :func:`verify_data_ready`）。

数据文件由 ``demo/scripts/export_demo_data.py`` 生成。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bioaudit.paths import EXPECTED_TYPES_PATH, TRAJECTORIES_DIR

DEMO_ROOT = Path(__file__).resolve().parent
DATA_DIR = DEMO_ROOT / "data"

#: demo/data 必需数据文件（export_demo_data.py 生成；manifest.json 单独校验存在）
REQUIRED_FILES: tuple[str, ...] = (
    "golden_summary.json",
    "eval_summary.json",
    "benchmark_summary.json",
    "r0_summary.json",
    "reward_summary.json",
    "engineering_summary.json",
    "trajectories_index.json",
    # 采集链路案例副本（多案例；案例元数据见 case_registry.CAPTURE_CASES）
    "verdicts_10X_B.jsonl",
    "golden_agent_10X_B_executed.py",
    "windowL_10X_B_expected.json",
    "verdicts_windowI_A.jsonl",
    "golden_agent_A_executed.py",
    "windowI_A_benchmark.json",
    "verdicts_windowI_B.jsonl",
    "golden_agent_B_executed.py",
    "windowI_B_benchmark.json",
    "verdicts_windowI_C.jsonl",
    "golden_agent_C_executed.py",
    "windowI_C_benchmark.json",
    "verdicts_windowL_10X_A.jsonl",
    "golden_agent_10X_A_executed.py",
    "windowL_10X_A_benchmark.json",
    "verdicts_cellvoyager_g.jsonl",
    "cellvoyager_g_executed.py",
    "cellvoyager_g_benchmark.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_data_ready() -> list[str]:
    """启动数据校验：清单存在 + manifest 指纹匹配。

    返回问题描述列表（空 = 就绪）。缺任一必需文件或指纹不匹配 → 提示
    ``python demo/scripts/export_demo_data.py``，由 app.py 展示并停止，
    不崩溃。
    """
    problems: list[str] = []
    manifest_path = DATA_DIR / "manifest.json"
    if not manifest_path.exists():
        return [f"缺少 {manifest_path.name}（演示数据未生成）"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{manifest_path.name} 损坏或不可读（{exc}），请重新导出演示数据"]

    expected = manifest.get("files", {})
    if not isinstance(expected, dict):
        return [f"{manifest_path.name} 结构非法（files 非映射），请重新导出演示数据"]
    for name in REQUIRED_FILES:
        if name not in expected:
            problems.append(f"manifest 未登记 {name}")
            continue
        path = DATA_DIR / name
        if not path.exists():
            problems.append(f"缺少 {name}")
            continue
        want = expected[name].get("sha256")
        if not want:
            problems.append(f"{name} 的 manifest 条目缺 sha256 指纹（请重新导出）")
        elif _sha256(path) != want:
            problems.append(f"{name} 指纹不匹配（数据被改动，请重新导出）")
    return problems


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def golden_summary() -> dict:
    """黄金对照摘要（windowI A/B/C + windowL 10X A/B_expected；口径分列）。"""
    return _load("golden_summary.json")


def eval_summary() -> dict:
    """真实评测摘要（CellVoyager G / L-b；30.0×2 口径注）。"""
    return _load("eval_summary.json")


def benchmark_summary() -> dict:
    """benchmark 摘要（60 任务 aggregate + gap + IRR，读提炼副本）。"""
    return _load("benchmark_summary.json")


def r0_summary() -> dict:
    """R0 锚定摘要（scrna_r0.json 提炼）。"""
    return _load("r0_summary.json")


def reward_summary() -> dict:
    """reward 校准摘要（映射 + mask + spike-in 掉分）。"""
    return _load("reward_summary.json")


def engineering_summary() -> dict:
    """工程数字摘要（测试数/CI/golden 口径）。"""
    return _load("engineering_summary.json")


def trajectories_index() -> list[dict]:
    """20 条轨迹索引（v2 目录 + golden 基线提炼；工坊页素材）。"""
    return _load("trajectories_index.json")


def verdicts_10X_B_path() -> Path:
    """M1 声明重建输入（verdicts 提炼副本，采集页）。"""
    return DATA_DIR / "verdicts_10X_B.jsonl"


def executed_10X_B_path() -> Path:
    """M3 解析输入（executed.py 提炼副本，采集页）。"""
    return DATA_DIR / "golden_agent_10X_B_executed.py"


def windowL_10X_B_expected_path() -> Path:
    """63.7 断言基准（windowL_10X_B_expected.json 提炼副本，provenance
    保留 source=windowL_10X_B_expected.json）。"""
    return DATA_DIR / "windowL_10X_B_expected.json"


# ── 包内资产出口（bioaudit 包数据，非仓库外）──────────────────

def trajectories_dir() -> Path:
    return TRAJECTORIES_DIR


def expected_types_path() -> Path:
    return EXPECTED_TYPES_PATH
