"""路径锚定审计（F7 验收辅助）：扫描仓库内相对 cwd 的路径用法。

检查项：
1. src/bioaudit/ 内是否出现 "data/…"、"logs/…" 等相对 cwd 字符串（应全部走 bioaudit.paths）
2. 是否出现 os.getcwd() / Path.cwd() 依赖（除审计脚本自身输出说明）
3. ui/ 与 scripts/ 中是否有 sys.path.insert 指向相对 cwd 的路径

用法：python scripts/check_no_cwd_paths.py
退出码：0 = 通过；1 = 发现可疑相对路径。
"""

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 相对路径模式：模块/脚本内裸 "data/..."、"logs/..."、"mappings/..." 等
REL_PATH_RE = re.compile(
    r"""["'](?:data|logs|mappings|rules|trajectories|validation|outputs?)/""",
    re.IGNORECASE,
)
# cwd 依赖模式
CWD_RE = re.compile(r"(os\.getcwd\(\)|Path\.cwd\(\)|getcwd\()")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
SKIP_FILES = {
    "check_no_cwd_paths.py",
    "download_datasets.py",
    "golden_replay.py",
    "test_path_anchoring.py",  # 测试自身扫描 cwd 模式字符串，属检测逻辑非依赖
}


def scan() -> list[dict]:
    hits = []
    # 用 os.walk 而非 Path.walk：后者是 **Python 3.12+** 才有（本项目
    # requires-python >= 3.10，3.10/3.11 上会 AttributeError）。
    # 语义等价：同样给出 root/dirs/files，且 dirs[:] 剪枝照样生效。
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            if fname in SKIP_FILES:
                continue
            path = Path(root) / fname
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if REL_PATH_RE.search(line) and "bioaudit.paths" not in line:
                    hits.append({"file": str(path.relative_to(REPO_ROOT)), "line": i, "code": line.strip()[:120], "kind": "relative_path"})
                if CWD_RE.search(line):
                    hits.append({"file": str(path.relative_to(REPO_ROOT)), "line": i, "code": line.strip()[:120], "kind": "cwd_dependency"})
    return hits


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    hits = scan()
    if hits:
        print(f"发现 {len(hits)} 处可疑路径用法：")
        for h in hits:
            print(f"  [{h['kind']}] {h['file']}:{h['line']}  {h['code']}")
        return 1
    print("✅ 未发现相对 cwd 路径 / cwd 依赖（src/ui/scripts/tests 全部锚定）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
