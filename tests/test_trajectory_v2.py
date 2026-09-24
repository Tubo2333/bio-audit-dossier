"""B4 轨迹迁移器测试（refactor-plan-v1.1 C2；audit-report A15）。

覆盖（B4 验收项 1-5）：
- schema：version 字段 + 常量（models/trajectory.py + report/schema.py 再导出）
- 迁移：20 条 v1 → v2（provenance.source=legacy，decisions 语义不变）
- 只读：迁移器不修改任何原文件（字节哈希前后一致）
- 迁移后 golden 仍 0 差异（version/provenance 是元数据，不改变评分）
- schema 校验器：v2 缺必填字段 → 显式报错（validation-error）
"""

import hashlib
import json

import pytest

from bioaudit.capture.trajectory_migrator import TrajectoryMigrator, infer_act
from bioaudit.errors import BioAuditError, ErrorCode
from bioaudit.models.trajectory import (
    LEGACY_TRAJECTORY_VERSION,
    PROVENANCE_SOURCE_LEGACY,
    TRAJECTORY_SCHEMA_VERSION,
    validate_trajectory,
)
from bioaudit.paths import TRAJECTORIES_DIR, TRAJECTORIES_LEGACY_DIR
from bioaudit.report.schema import TRAJECTORY_SCHEMA_VERSION as REPORT_SCHEMA_VERSION_CONST


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


LEGACY_FILES = sorted(TRAJECTORIES_LEGACY_DIR.glob("*.json"))


# ── 1. schema 常量（B4 验收项 1：report/schema 常量定义）──


def test_schema_constants():
    assert TRAJECTORY_SCHEMA_VERSION == 2
    assert LEGACY_TRAJECTORY_VERSION == 1
    assert PROVENANCE_SOURCE_LEGACY == "legacy"
    # report/schema.py 再导出与事实源一致
    assert REPORT_SCHEMA_VERSION_CONST == TRAJECTORY_SCHEMA_VERSION


def test_canonical_trajectories_dir_is_v2():
    """引擎 canonical 目录 = v2（迁移产物）；legacy 目录 = 备份。"""
    assert TRAJECTORIES_DIR.name == "v2"
    assert TRAJECTORIES_LEGACY_DIR.name == "trajectories"
    assert len(LEGACY_FILES) == 20
    assert len(list(TRAJECTORIES_DIR.glob("*.json"))) == 20


# ── 2. 迁移（B4 验收项 2）──


def test_migrate_all_20_to_v2(tmp_path):
    migrator = TrajectoryMigrator(dst_dir=tmp_path)
    rows = migrator.migrate_all()
    assert len(rows) == 20

    for row in rows:
        v2 = json.loads((tmp_path / row["target"]).read_text(encoding="utf-8"))
        assert row["written"] is True
        assert v2["version"] == 2
        assert v2["provenance"]["source"] == PROVENANCE_SOURCE_LEGACY
        assert v2["provenance"]["migrated_from"] == row["source"]
        assert v2["trajectory_id"] == v2["provenance"]["migrated_from"][:-5]
        assert v2["act"] == row["source"].split("_")[0]  # 文件名前缀 → 范式
        assert len(v2["decisions"]) == row["n_decisions"] > 0


def test_v2_decisions_equal_v1(tmp_path):
    """decisions 内容语义不变（迁移不改变评分的基础）。"""
    migrator = TrajectoryMigrator(dst_dir=tmp_path)
    rows = migrator.migrate_all()
    for row in rows:
        v1 = json.loads((TRAJECTORIES_LEGACY_DIR / row["source"]).read_text(encoding="utf-8"))
        v1_decisions = v1 if isinstance(v1, list) else v1["decisions"]
        v2 = json.loads((tmp_path / row["target"]).read_text(encoding="utf-8"))
        assert v2["decisions"] == v1_decisions, row["source"]


# ── 3. 只读迁移器（B4 验收项 3）──


def test_migrator_read_only_does_not_touch_originals(tmp_path):
    pre = {p.name: _sha256(p) for p in LEGACY_FILES}
    migrator = TrajectoryMigrator(dst_dir=tmp_path)
    migrator.migrate_all()
    post = {p.name: _sha256(p) for p in LEGACY_FILES}
    assert pre == post, "迁移器修改了原文件（必须只读）"


def test_migrator_dry_run_writes_nothing(tmp_path):
    migrator = TrajectoryMigrator(dst_dir=tmp_path)
    rows = migrator.migrate_all(dry_run=True)
    assert len(rows) == 20
    assert all(r["written"] is False for r in rows)
    assert not any(tmp_path.iterdir())


# ── 4. 迁移后 golden 仍 0 差异（B4 验收项 4：元数据不改变评分）──


def test_golden_zero_diff_after_migration():
    from bioaudit.regression import replay_golden

    ok, summary = replay_golden()
    assert ok, summary
    assert summary["n_trajectories_replayed"] == 20
    assert summary["n_decisions_replayed"] == 137


# ── 5. schema 校验器：缺必填字段显式报错（B4 验收项 5 / A15）──


def _valid_v2() -> dict:
    v2 = json.loads((TRAJECTORIES_DIR / "deg_correct.json").read_text(encoding="utf-8"))
    return json.loads(json.dumps(v2))  # deep copy


@pytest.mark.parametrize("field", ["version", "trajectory_id", "provenance", "decisions"])
def test_v2_missing_required_field_explicit_error(field):
    bad = _valid_v2()
    del bad[field]
    with pytest.raises(BioAuditError) as ei:
        validate_trajectory(bad)
    assert ei.value.code == ErrorCode.VALIDATION_ERROR
    assert ei.value.details["field_errors"]


def test_v2_unsupported_version_explicit_error():
    bad = _valid_v2()
    bad["version"] = 1  # 旧版本（或未来版本）→ 显式报错
    with pytest.raises(BioAuditError) as ei:
        validate_trajectory(bad)
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


def test_v2_legacy_provenance_ok():
    traj = validate_trajectory(_valid_v2())
    assert traj.version == 2
    assert traj.provenance.source == "legacy"


# ── 6. 迁移器细节 ──


def test_infer_act():
    assert infer_act("deg_correct") == "deg"
    assert infer_act("pan_edge_epv") == "pan"
    assert infer_act("scrna_melanoma_cellvoyager") == "scrna"
    assert infer_act("unknown_xyz") is None


def test_migrator_idempotent_on_v2(tmp_path):
    """输入已是 v2 → 仅校验，不重复包装。"""
    v2_file = TRAJECTORIES_DIR / "deg_correct.json"
    migrator = TrajectoryMigrator(src_dir=TRAJECTORIES_DIR, dst_dir=tmp_path)
    result = migrator.build_v2(v2_file)
    assert result["version"] == 2
    assert result["provenance"]["source"] == "legacy"
    assert "migrated_from" in result["provenance"]  # 原样保留，未重写


def test_validate_trajectory_cli_path(tmp_path):
    """trajectory-validate 命令对缺必填字段文件返回非零（A15 显式报错）。"""
    import subprocess
    import sys

    bad = _valid_v2()
    del bad["decisions"]
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps(bad), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "bioaudit.cli", "trajectory-validate", str(bad_file)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 1
    assert "validation-error" in proc.stdout or "校验失败" in proc.stdout
