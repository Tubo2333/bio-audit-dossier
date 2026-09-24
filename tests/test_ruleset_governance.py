"""B5 规则治理测试（refactor-plan-v1.1 D1/C1；B5 验收项 1/2/3/6）。

覆盖：
1. ruleset.json 正式启用：RULESET_VERSION 从 ruleset.json 读取（semver）；
   verify_manifest 全绿（43 文件 / 38 唯一 rule_id / 内容哈希 / Rule schema）
2. 报告三元组快照：run_audit 的 report 含 engine+ruleset+ontology 版本（非 None）
3. ruleset-validate 三闸命令：一条命令 = 清单 + 冲突 + golden，exit 0
4. D1 变更流程：generate_manifest 重新生成 → verify_manifest 仍全绿
5. fail-closed：ruleset.json 版本非 semver → 显式报错
"""

import json

import pytest

import bioaudit
from bioaudit.paths import RULES_DIR
from bioaudit.rules import RULESET_VERSION
from bioaudit.rules.manifest import (
    RulesetError,
    generate_manifest,
    load_ruleset,
    verify_manifest,
)
from bioaudit.rules.validator import validate_ruleset

# ── 1. ruleset.json 正式启用（B5 验收项 1）──


def test_ruleset_version_reads_from_manifest():
    """RULESET_VERSION 不再硬编码：与 ruleset.json 的 ruleset_version 一致。"""
    manifest = load_ruleset()
    assert RULESET_VERSION == manifest["ruleset_version"]
    assert RULESET_VERSION.count(".") == 2  # semver 形状
    # 清单三元组元数据完整
    assert manifest["engine_version"] == bioaudit.ENGINE_VERSION == bioaudit.__version__
    assert manifest["ontology_version"] == bioaudit.ONTOLOGY_VERSION == "0.1.3"


def test_verify_manifest_all_green():
    report = verify_manifest()
    assert report["ok"] is True, report["errors"]
    assert report["errors"] == []
    assert report["n_rule_files"] == 45
    assert report["n_unique_rule_ids"] == 40
    assert report["duplicate_copies"] == 5  # DEG/pancancer 同名副本（C2 预期）
    # 每个清单条目都有内容哈希
    manifest = load_ruleset()
    assert all("sha256" in e and "size" in e for e in manifest["files"])


def test_verify_manifest_detects_hash_mismatch(tmp_path):
    """篡改规则文件 → 哈希校验必须报错（内容哈希防静默改动）。

    注意：必须用字节级读写（read_bytes/write_bytes）——Windows 上文本模式
    write_text 会把 \\n 转成 \\r\\n，破坏文件字节（行尾约定漂移）。
    """
    target = RULES_DIR / "scRNA" / "G1.3-DEG-003_method.yaml"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# tampered\n")
        report = verify_manifest()
        assert report["ok"] is False
        assert any(e["kind"] == "hash_mismatch" and "G1.3" in e["path"]
                   for e in report["errors"])
    finally:
        target.write_bytes(original)


def test_load_ruleset_rejects_bad_semver(tmp_path):
    bad = tmp_path / "ruleset.json"
    bad.write_text(json.dumps({"ruleset_version": "banana", "files": []}),
                   encoding="utf-8")
    with pytest.raises(RulesetError):
        load_ruleset(bad)


# ── 2. 报告三元组快照（B5 验收项 2：engine + ruleset + ontology 全写进 report）──


def test_report_snapshot_triple_complete():
    from bioaudit.api import run_audit
    from bioaudit.models.trajectory import validate_trajectory
    from bioaudit.paths import TRAJECTORIES_DIR

    v2 = json.loads((TRAJECTORIES_DIR / "deg_correct.json").read_text(encoding="utf-8"))
    validate_trajectory(v2)
    result = run_audit(v2, act="deg")
    assert result["error"] is None

    report = result["report"]
    # 三元组不再为 None（B5 验收项 1/2）；版本从清单/本体读取，不硬编码
    from bioaudit.rules.manifest import load_ruleset
    assert report["ruleset_version"] == load_ruleset()["ruleset_version"]
    assert report["ontology_version"] == "0.1.3"
    assert report["engine_version"] == bioaudit.__version__
    # 完整快照字典（C1/P2）
    snap = report["snapshot"]
    assert snap["ruleset_version"] == report["ruleset_version"]
    assert snap["ontology_version"] == report["ontology_version"]
    assert snap["engine_version"] == report["engine_version"]


# ── 3. ruleset-validate 三闸命令（B5 验收项 3）──


def test_validate_ruleset_three_gates_all_pass():
    report = validate_ruleset()
    assert report["ok"] is True
    assert report["stages"] == {"manifest": "PASS", "conflicts": "PASS", "golden": "PASS"}
    # 闸 2：B5 裁决后冲突归零（G1.3 修订 + 范式感知检测）
    assert report["ontology"]["conflicts"]["n_conflicts"] == 0
    assert report["ontology"]["conflicts"]["scope"] == "same-rule-set"
    # 闸 3：137 决策 0 差异
    assert report["golden"]["n_diffs"] == 0
    assert report["golden"]["n_decisions_replayed"] == 137


def test_ruleset_validate_cli_exit_zero():
    from bioaudit.rules.validator import main
    assert main(["--json"]) == 0


def test_ruleset_validate_cli_fails_on_tamper(tmp_path):
    """篡改规则 → 三闸 FAIL → exit 1（CI 门禁拦截，D1）。"""
    from bioaudit.rules.validator import main

    rules_dir = tmp_path / "rules"
    (rules_dir / "scRNA").mkdir(parents=True)
    target = RULES_DIR / "scRNA" / "G1.3-DEG-003_method.yaml"
    (rules_dir / "scRNA" / "G1.3-DEG-003_method.yaml").write_text(
        target.read_text(encoding="utf-8"), encoding="utf-8")
    # 无 ruleset.json 清单 → 闸 1 必须失败
    assert main(["--rules-dir", str(rules_dir), "--json"]) == 1


# ── 4. D1 变更流程：重新生成清单 → 校验仍全绿 ──


def test_generate_manifest_roundtrip(tmp_path):
    manifest = generate_manifest(
        rules_dir=RULES_DIR, manifest_path=tmp_path / "ruleset.json",
        ruleset_version="1.1.0", engine_version="0.1.1", ontology_version="0.1.2",
    )
    assert manifest["n_rule_files"] == 45
    assert manifest["n_unique_rule_ids"] == 40
    # 生成后清单可被 verify_manifest 接受（用同一规则目录）
    check = verify_manifest(rules_dir=RULES_DIR, manifest_path=tmp_path / "ruleset.json")
    assert check["ok"] is True

def test_manifest_line_ending_insensitive(tmp_path):
    """2026-08-15 回归：manifest 哈希/大小对行尾不敏感（CI hash_mismatch 修复）。

    Windows 工作区 CRLF vs Linux CI LF 不应导致 verify FAIL。
    """
    from bioaudit.rules import manifest as m

    # 临时规则目录：一份 LF、一份 CRLF 的同一文件
    rd = tmp_path / "rules"
    (rd / "sub").mkdir(parents=True)
    content = b"rule_id: T-001\nstatus: active\ncondition:\n  decision_type: test\nscoring: {}\n"
    (rd / "sub" / "a.yaml").write_bytes(content)          # LF
    (rd / "sub" / "b.yaml").write_bytes(content.replace(b"\n", b"\r\n"))  # CRLF

    man = m.generate_manifest(rules_dir=rd, manifest_path=tmp_path / "ruleset.json",
                              ruleset_version="1.1.1")
    # 同一内容不同行尾 → 规范化后哈希一致
    assert man["files"][0]["sha256"] == man["files"][1]["sha256"]
    assert man["files"][0]["size"] == man["files"][1]["size"]

    # 校验通过（CRLF 文件在 manifest 下也不报 mismatch）
    rep = m.verify_manifest(rules_dir=rd, manifest_path=tmp_path / "ruleset.json")
    kinds = [e["kind"] for e in rep["errors"]]
    assert "hash_mismatch" not in kinds
    assert "size_mismatch" not in kinds
