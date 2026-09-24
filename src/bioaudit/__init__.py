"""Bio-Audit — 生信 AI Agent 方法学决策的确定性审计层（Scientific Decision CI）。

单仓库重构（bio-audit-v2, 阶段 1 B1/B2）：引擎 / 本体 / 规则集 / 轨迹 /
验证资产 / API / UI 薄壳。
引擎基线：fullflow-demo（D5 修复后 + DEG 双副本统一 + mappings 补齐）。
"""

# 0.3.0（2026-08-16 窗口 M）：missing 三档运行时强制（未验证 level=-2）+ override_n2
# 键映射修复 + expected_types 强制预期决策点（采集层）；0.2.1 = G-2 any-of 语义
__version__ = "0.3.0"

# 快照三元组（v1.1 C1）：规则集版本见 bioaudit/rules/ruleset.json；
# ontology 版本 B2 落地（bioaudit/ontology/paradigms.yaml 的 ontology_version 与之保持一致）。
from bioaudit.ontology import ONTOLOGY_VERSION as _ONTOLOGY_VERSION

ENGINE_VERSION = __version__
ONTOLOGY_VERSION = _ONTOLOGY_VERSION
