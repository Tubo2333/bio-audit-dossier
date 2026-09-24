"""规则集包（v1 蓝图：规则集版本化打包 + shared_evidence）。

- data/{DEG,pancancer,scRNA}/**: 43 条规则 YAML（A3 统一后 DEG 血统）
- ruleset.json: 规则集快照清单（C1 三元组之 ruleset 版本；B5 起为唯一事实源）
- manifest.py: 清单加载/校验/生成（B5 规则治理：semver + 内容哈希 + 唯一 id）
"""

from bioaudit.paths import RULES_DIR
from bioaudit.rules.manifest import RULESET_PATH, RulesetError
from bioaudit.rules.manifest import ruleset_version as _ruleset_version

# B5（2026-08-14）：版本从 ruleset.json 读取（单一事实源），不再硬编码。
# ruleset.json 缺失/非 semver 时 import 即失败（fail-closed，防止版本漂移）。
try:
    RULESET_VERSION = _ruleset_version()
except RulesetError as _exc:  # pragma: no cover - 仅包损坏时触发
    raise ImportError(f"ruleset.json 不可用（包数据损坏）: {_exc}") from _exc

__all__ = ["RULES_DIR", "RULESET_VERSION", "RULESET_PATH", "RulesetError"]
