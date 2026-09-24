"""引擎单元测试（B1）：核心链路 sanity + 规则库治理基础。

覆盖：
- deg_correct 全链路 → 85.0 / pass（golden 摘要一致）
- C2：全库加载去重后 39 唯一规则；DEG 血统统一（DEG/ 与 pancancer/ 同名同哈希）
- 未知决策类型 → -1（无法评估）；未识别 choice → 规则级跳过 → -1（K2，
  A2 修复 2026-08-16：不再兜底 L0"危险"——详见 tests/test_k2_minus_one.py）
- mappings 加载（B8：别名归一化生效）
"""

import json
from pathlib import Path

import pytest

from bioaudit.api import audit_decision, run_audit
from bioaudit.engine.matcher import RuleMatcher
from bioaudit.models.decision import Decision
from bioaudit.paths import RULES_DIR, TRAJECTORIES_DIR, MAPPINGS_DIR
from bioaudit.storage.rule_registry import RuleRegistry


def _load(name: str) -> list[dict]:
    data = json.loads((TRAJECTORIES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["decisions"]


def test_deg_correct_full_pipeline():
    result = run_audit(_load("deg_correct"), act="deg")
    assert result.get("error") is None
    assert result["trajectory_score"] == 85.0
    assert result["eval_verdict"] == "pass"
    assert len(result["step_scores"]) == 5
    assert all(s["level"] == 3 for s in result["step_scores"])


def test_deg_error_blocked():
    result = run_audit(_load("deg_error"), act="deg")
    assert result["eval_verdict"] == "blocked"
    assert result["trajectory_score"] == 15.0
    assert any(s["level"] == 0 for s in result["step_scores"])


def test_c2_dedup_all_rules_40_unique():
    reg = RuleRegistry()  # 全量规则目录（含 DEG 与 pancancer 双副本）
    n = reg.load_all()
    assert n == 40, f"C2 去重后应 40 唯一规则（J2 新增 G1.4 + K1 新增 I4.1 scRNA 版），实际 {n}"
    assert reg.rule_count == 40


def test_deg_bloodline_unified():
    """A3 裁决：DEG 副本为血统，pancancer 5 同名文件内容全同。"""
    import hashlib

    def sha(rel: str) -> str:
        p = RULES_DIR / rel
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(65536), b""):
                h.update(c)
        return h.hexdigest()

    for fname in ["D1.2-DEG-001_filtering.yaml", "D1.3-DEG-001_normalization.yaml",
                  "M1.1-DEG-001_method_selection.yaml", "M1.2-DEG-001_multiple_testing.yaml",
                  "M1.3-DEG-001_threshold.yaml"]:
        assert sha(f"DEG/{fname}") == sha(f"pancancer/{fname}"), fname


def test_mappings_loaded():
    matcher = RuleMatcher(RuleRegistry(RULES_DIR / "DEG"))
    parsed, _ = matcher.match(Decision(
        step_id="s1", decision_type="filtering", choice="filterByExpr",
        context={"n_replicates": 6, "paired": False},
    ))
    assert parsed.decision_type == "filtering"  # 别名映射加载正常


def test_audit_decision_single():
    d = audit_decision({
        "step_id": "s1",
        "decision_type": "normalization",
        "choice": "TPM",
        "rationale": "TPM for cross-sample comparison",
        "context": {"n_replicates": 6, "paired": False,
                    "data_category": "raw_counts", "sequencing": "bulk_RNA_seq"},
    }, paradigm="deg")  # B3: paradigm 必填
    assert d["level"] == 2  # TPM 为 L2（D5 修复后不再虚增到 L3）
    assert d["matched_rules"] == ["D1.3-DEG-001"]


def test_unknown_decision_type_is_unevaluable():
    d = audit_decision({
        "step_id": "s9",
        "decision_type": "totally_unknown_type",
        "choice": "whatever",
        "context": {},
    }, paradigm="deg")  # B3: paradigm 必填
    assert d["level"] == -1
    assert d["matched_rules"] == []
