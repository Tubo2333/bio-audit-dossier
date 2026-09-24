"""报告包（B1 骨架：报告 schema 版本化 + C1 快照三元组；B3/C3 完善迁移策略）。

快照三元组（refactor-plan-v1.1 C1/P2）：ruleset + ontology + engine 版本，
报告/reward/回归全部绑定，保证跨版本可复现。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SnapshotTriple:
    """C1 报告快照三元组：规则集 + 本体 + 引擎版本。"""

    ruleset_version: str
    ontology_version: str
    engine_version: str
    # 规则集文件级哈希见 bioaudit/rules/ruleset.json（B5 起随报告写死）
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ruleset_version": self.ruleset_version,
            "ontology_version": self.ontology_version,
            "engine_version": self.engine_version,
            **self.extra,
        }


REPORT_SCHEMA_VERSION = "1.0"  # C3：报告 schema 迁移策略（major 破坏性变更）


def current_snapshot() -> SnapshotTriple:
    """当前引擎/本体/规则集版本三元组（B5 正式接入 run_audit 报告）。"""
    import bioaudit
    from bioaudit.rules import RULESET_VERSION

    return SnapshotTriple(
        ruleset_version=RULESET_VERSION,
        ontology_version=bioaudit.ONTOLOGY_VERSION,
        engine_version=bioaudit.ENGINE_VERSION,
    )



def assert_snapshot_compatible(recorded: dict, current: SnapshotTriple | None = None) -> None:
    """Fail closed when a replay snapshot does not match the current runtime."""
    if not isinstance(recorded, dict):
        raise ValueError("recorded snapshot must be an object")
    required = {"ruleset_version", "ontology_version", "engine_version"}
    if not required <= set(recorded) or not set(recorded) <= required | set(current.extra if current else {}):
        raise ValueError("recorded snapshot has invalid shape")
    current = current or current_snapshot()
    expected = current.as_dict()
    mismatches = {key: (recorded[key], expected[key]) for key in recorded if key in expected and recorded[key] != expected[key]}
    if mismatches:
        raise ValueError(f"runtime snapshot mismatch: {mismatches}")


__all__ = ["SnapshotTriple", "REPORT_SCHEMA_VERSION", "current_snapshot", "assert_snapshot_compatible"]
