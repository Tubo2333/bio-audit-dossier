"""benchmark 数据路径锚定（与 bioaudit.paths 同一风格：包内锚定，零 cwd 依赖）。"""

from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent

#: 任务集（D1.4：src/bioaudit/data/tasks/ + semver + 评审）
TASKS_DIR = PKG_DIR.parent / "data" / "tasks"

#: 标注产物（双标注原始 JSONL / IRR 报告 / 仲裁记录 / 合并 gold）
ANNOTATION_DIR = PKG_DIR.parent / "data" / "annotation"

#: 生成器提示词（E6：零规则内容，sha256 记入任务 provenance）
GENERATOR_PROMPT = PKG_DIR / "generator_prompt.md"

#: 标注 rubric（E3：标注者只读 rubric 与任务，角色与规则作者分离）
ANNOTATION_RUBRIC = PKG_DIR / "annotation_rubric.md"

#: 预注册记录（E1）
PRE_REGISTRATION = PKG_DIR / "pre_registration.json"

__all__ = [
    "TASKS_DIR",
    "ANNOTATION_DIR",
    "GENERATOR_PROMPT",
    "ANNOTATION_RUBRIC",
    "PRE_REGISTRATION",
]
