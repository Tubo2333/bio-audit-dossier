"""路径锚定测试（F7 验收核心）：引擎在任意 cwd 下可独立运行。

策略：用 subprocess 在"异 cwd"（临时目录）中运行引擎：
1. golden 重放 0 差异（证明规则/轨迹/mappings 全部包内锚定）
2. api.run_audit 单轨迹（证明事件存储等运行时写路径也 cwd 无关）

注意：测试自身不依赖仓库根作为 cwd（pytest 从任何目录启动都应通过）。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_py(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def test_engine_runs_from_foreign_cwd():
    """在临时 cwd（非仓库根）运行完整 golden 重放 → 0 差异。"""
    script = (
        "import sys, json; sys.path.insert(0, r'%s'); "
        "from bioaudit.regression import replay_golden; "
        "ok, summary = replay_golden(); "
        "print(json.dumps(summary)); sys.exit(0 if ok else 1)"
        % (REPO_ROOT / "src")
    )
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_py(script, Path(tmp))
        assert proc.returncode == 0, f"异 cwd 运行失败:\n{proc.stdout}\n{proc.stderr}"
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        assert summary["ok"] is True
        assert summary["n_decisions_replayed"] == 137


def test_api_run_audit_from_foreign_cwd():
    """异 cwd 下 api.run_audit 正常（deg_correct → 85.0 pass）。"""
    script = (
        "import sys, json; sys.path.insert(0, r'%s'); "
        "from bioaudit.api import run_audit; "
        "from bioaudit.paths import TRAJECTORIES_DIR; "
        "traj = json.loads((TRAJECTORIES_DIR / 'deg_correct.json').read_text(encoding='utf-8')); "
        "r = run_audit(traj, act='deg'); "
        "print(r['trajectory_score'], r['eval_verdict']); "
        "sys.exit(0 if r['trajectory_score'] == 85.0 and r['eval_verdict'] == 'pass' else 1)"
        % (REPO_ROOT / "src")
    )
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_py(script, Path(tmp))
        assert proc.returncode == 0, f"异 cwd api 运行失败:\n{proc.stdout}\n{proc.stderr}"
        assert proc.stdout.strip() == "85.0 pass"


def test_ui_has_no_cwd_path_hacks():
    """UI 薄壳不含 sys.path hack 与相对 cwd 数据路径。"""
    for f in ["app.py", "pages/02_audit.py", "pages/03_report.py"]:
        text = (REPO_ROOT / "ui" / f).read_text(encoding="utf-8")
        assert "sys.path.insert" not in text, f"ui/{f} 仍含 sys.path hack"
        assert "os.getcwd" not in text and "Path.cwd()" not in text, f"ui/{f} 含 cwd 依赖"
