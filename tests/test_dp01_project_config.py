"""DP-01 project-config self-sufficiency tests (DP01-INSTALL-IMPORTABILITY).

Deliberately contains NO sys.path manipulation and relies on the project's own
pytest configuration ([tool.pytest.ini_options].pythonpath = ["src"]) to prove
that package importability needs neither PYTHONPATH nor source-dir
monkey-patching. All imports live inside test bodies so collection succeeds
and each scenario reports its own red/green state.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_REQ = {
    "audit_id": "cfg-1",
    "analysis_context": {
        "project_id": "p",
        "analysis_id": "a",
        "comparison": "case_vs_control",
        "data_type": "bulk_rnaseq",
        "audit_scope": "filtering",
        "intended_use": "scientific_analysis",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
        "exclusions": "none — no explicit exclusions declared",
    },
    "decision_declaration": {
        "method": "low_count_filter",
        "threshold": 10,
        "sample_rule": "at_least_two_samples",
        "unit": "gene",
        "source": "declared",
    },
    "evidence_observations": [{"source_type": "observed", "verification": "confirmed", "value": "low_count_filter", "observed_at": "2026-09-12T00:00:00+00:00", "valid_for_seconds": 31536000}],
    "integrity_metadata": {
        **{d: {"state": "confirmed"} for d in ("identity", "version", "source", "binding", "snapshot", "permission", "unique_authority")},
        "material_ids": ["m"],
        "pending_targets": ["p"],
    },
}


def test_project_config_resolves_src_for_import():
    from bioaudit.api import audit_dp01_filtering, replay_dp01_filtering
    from bioaudit.dp01_store import DP01JSONLStore  # noqa: F401

    assert callable(audit_dp01_filtering)
    assert callable(replay_dp01_filtering)


def test_config_resolved_import_runs_audit_and_replay(tmp_path):
    from bioaudit.api import audit_dp01_filtering, replay_dp01_filtering
    from bioaudit.dp01_store import DP01JSONLStore

    store = DP01JSONLStore(tmp_path / "cfg.jsonl")
    audit_dp01_filtering(_REQ, store)
    record = replay_dp01_filtering(store, "cfg-1")
    assert record.audit_id == "cfg-1"
    assert record.result == store.get("cfg-1").result


def test_pyproject_has_pythonpath_and_aligned_version():
    if sys.version_info < (3, 11):
        pytest.skip("tomllib requires Python 3.11+")
    # 导入置于版本闸**之后**：tomllib 是 3.11+ 标准库，模块级导入会让 3.10 在
    # **收集期**就 ImportError（整个文件 1 error，连本文件的其余用例也一并丢失），
    # 版本闸根本来不及生效。与本文件 docstring 的约定一致：所有导入都在用例体内。
    import tomllib
    with (ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    ini = data["tool"]["pytest"]["ini_options"]
    assert ini["pythonpath"] == ["src"]
    import bioaudit

    assert data["project"]["version"] == bioaudit.__version__