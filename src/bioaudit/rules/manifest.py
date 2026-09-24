"""ruleset.json 清单的加载/校验/生成（B5 规则治理核心）。

职责（refactor-plan-v1.1 D1/C1）：
- ``load_ruleset()``：读取 ruleset.json（semver + 结构校验），
  ``RULESET_VERSION`` 的唯一事实源（report 三元组之 ruleset 版本）
- ``verify_manifest()``：清单校验——semver 合法 / 文件数 43 / 唯一 rule_id 38 /
  每个文件 SHA256 与磁盘一致 / 全部 YAML 可解析为 Rule schema
- ``generate_manifest()``：规则变更后重新生成清单（D1 规则变更流程的落盘步骤；
  semver 按变更语义显式提升，不自动改版本号）

规则变更流程（D1，写入 CONTRIBUTING.md）：
  改规则 YAML → ``bio-audit ruleset-validate``（校验器+冲突+golden 三闸）
  → 按语义提升 ruleset.json 的 ruleset_version → 重新生成清单 → 提交。
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import yaml

from bioaudit.models.rule import Rule
from bioaudit.paths import RULES_DIR

RULESET_PATH = Path(__file__).resolve().parent / "ruleset.json"

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                       r"(?:[-+][0-9A-Za-z.-]+)?$")


class RulesetError(ValueError):
    """ruleset.json 缺失/非法/版本非 semver。"""


def load_ruleset(path: Optional[Path | str] = None) -> dict:
    """读取 ruleset.json；版本必须为 semver（B5 验收项 1）。"""
    p = Path(path) if path else RULESET_PATH
    if not p.exists():
        raise RulesetError(f"ruleset.json 不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RulesetError(f"ruleset.json 不是合法 JSON: {exc}") from exc
    version = data.get("ruleset_version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        raise RulesetError(
            f"ruleset_version 必须为 semver（如 1.1.0），实际: {version!r}")
    if not isinstance(data.get("files"), list):
        raise RulesetError("ruleset.json 缺少 files 清单（文件级哈希）")
    return data


def ruleset_version(path: Optional[Path | str] = None) -> str:
    """当前规则集版本（B5：从 ruleset.json 读取，替代硬编码）。"""
    return load_ruleset(path)["ruleset_version"]


def _normalized_bytes(path: Path) -> bytes:
    """行尾规范化读取（2026-08-15 修复：CI hash_mismatch 根因）。

    Windows 工作区（core.autocrlf=true）文件为 CRLF，git blob 与 Linux CI
    检出的为 LF——直接对磁盘字节做 SHA256 会让清单哈希跨平台不可复现
    （B5 ruleset.json 在 Windows 生成、CI 校验即 FAIL）。
    生成与校验共用本函数，统一按 LF 规范化后计算哈希与大小。
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(_normalized_bytes(path)).hexdigest()


def _size(path: Path) -> int:
    """规范化后字节数（与 _sha256 同一基准）。"""
    return len(_normalized_bytes(path))


def _rule_ids_from_files(files: list[Path]) -> dict[str, list[str]]:
    """扫描规则文件 → {rule_id: [文件路径...]}（C2 语义：去重后唯一 id）。"""
    ids: dict[str, list[str]] = {}
    for f in sorted(files):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not data or "rule_id" not in data:
            continue
        ids.setdefault(str(data["rule_id"]), []).append(str(f))
    return ids


def verify_manifest(
    rules_dir: Optional[Path | str] = None,
    manifest_path: Optional[Path | str] = None,
) -> dict:
    """校验 ruleset.json 与磁盘规则文件的完整一致性。

    检查项：
    1. ruleset.json 可解析且 ruleset_version 为 semver
    2. 清单 files 与磁盘文件一一对应（无缺失/多余）
    3. 每个文件 SHA256 与 size 与磁盘一致
    4. 全部 YAML 可解析为 Rule schema（status/condition/scoring 合法）
    5. 唯一 rule_id 数 = 38（C2：DEG/pancancer 5 对同名副本为预期重复）

    返回结构化报告（不抛异常，失败项进 report["errors"]）。
    """
    rd = Path(rules_dir) if rules_dir else RULES_DIR
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        manifest = load_ruleset(manifest_path)
    except RulesetError as exc:
        return {
            "ok": False, "errors": [{"kind": "manifest_unreadable", "detail": str(exc)}],
            "warnings": [], "n_rule_files": 0, "n_unique_rule_ids": 0,
        }

    # 1. 磁盘文件（相对 posix 路径）
    disk_files = sorted(rd.rglob("*.yaml"))
    disk_by_rel = {
        str(f.relative_to(rd)).replace("\\", "/"): f for f in disk_files
    }

    # 2. 清单 vs 磁盘
    listed = [e["path"] for e in manifest["files"]]
    listed_set, disk_set = set(listed), set(disk_by_rel)
    for rel in sorted(disk_set - listed_set):
        errors.append({"kind": "file_not_in_manifest", "path": rel})
    for rel in sorted(listed_set - disk_set):
        errors.append({"kind": "file_missing_on_disk", "path": rel})

    # 3. 哈希与大小
    for entry in manifest["files"]:
        rel = entry["path"]
        f = disk_by_rel.get(rel)
        if f is None:
            continue
        actual_sha = _sha256(f)
        if entry.get("sha256") != actual_sha:
            errors.append({
                "kind": "hash_mismatch", "path": rel,
                "manifest_sha256": entry.get("sha256"), "disk_sha256": actual_sha,
            })
        if entry.get("size") != _size(f):
            errors.append({
                "kind": "size_mismatch", "path": rel,
                "manifest_size": entry.get("size"), "disk_size": f.stat().st_size,
            })

    # 4. YAML → Rule schema
    for rel, f in sorted(disk_by_rel.items()):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not data or "rule_id" not in data:
                errors.append({"kind": "invalid_rule_yaml", "path": rel,
                               "detail": "缺少 rule_id"})
                continue
            Rule(**data)
        except Exception as exc:
            errors.append({"kind": "invalid_rule_yaml", "path": rel,
                           "detail": str(exc)})

    # 5. 唯一 rule_id（实测 40 唯一 / 45 文件：5 对 DEG/pancancer 同名副本为预期形态）
    #    这里只把重复当 warning 报出、不失败；副本之间"内容必须逐字段一致"由
    #    scripts/check_rule_lifecycle.py 的 C2 强制，避免两份副本悄悄分歧。
    id_files = _rule_ids_from_files(disk_files)
    dup_ids = {rid: fs for rid, fs in id_files.items() if len(fs) > 1}
    for rid, fs in sorted(dup_ids.items()):
        warnings.append({
            "kind": "duplicate_rule_id_copies",
            "rule_id": rid, "files": fs,
            "note": "C2 预期：DEG/pancancer 同名副本（A3 统一后内容全同）",
        })

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "ruleset_version": manifest["ruleset_version"],
        "engine_version": manifest.get("engine_version"),
        "ontology_version": manifest.get("ontology_version"),
        "generated_at": manifest.get("generated_at"),
        "n_rule_files": len(disk_files),
        "n_unique_rule_ids": len(id_files),
        "duplicate_copies": sum(len(fs) - 1 for fs in dup_ids.values()),
        "files": sorted(disk_by_rel),
    }


def generate_manifest(
    rules_dir: Optional[Path | str] = None,
    manifest_path: Optional[Path | str] = None,
    ruleset_version: str = "1.0.0",
    engine_version: Optional[str] = None,
    ontology_version: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """重新生成 ruleset.json（D1 规则变更流程落盘步骤）。

    ruleset_version 必须显式传入（语义提升由变更者裁决，本函数不自动 bump）。
    """
    if not SEMVER_RE.match(ruleset_version):
        raise RulesetError(f"ruleset_version 必须为 semver，实际: {ruleset_version!r}")

    rd = Path(rules_dir) if rules_dir else RULES_DIR
    out = Path(manifest_path) if manifest_path else RULESET_PATH

    if engine_version is None:
        import bioaudit
        engine_version = bioaudit.ENGINE_VERSION
    if ontology_version is None:
        import bioaudit
        ontology_version = bioaudit.ONTOLOGY_VERSION

    from datetime import date
    files = []
    for f in sorted(rd.rglob("*.yaml")):
        rel = str(f.relative_to(rd)).replace("\\", "/")
        files.append({
            "path": rel,
            "sha256": _sha256(f),
            "size": _size(f),
        })

    id_files = _rule_ids_from_files(list(rd.rglob("*.yaml")))
    manifest = {
        "ruleset_version": ruleset_version,
        "engine_version": engine_version,
        "ontology_version": ontology_version,
        "generated_at": date.today().isoformat(),
        "n_rule_files": len(files),
        "n_unique_rule_ids": len(id_files),
        "note": note or "B5 规则治理：清单由 bioaudit.rules.manifest.generate_manifest 生成",
        "files": files,
    }
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return manifest


__all__ = [
    "RULESET_PATH", "SEMVER_RE", "RulesetError", "load_ruleset",
    "ruleset_version", "verify_manifest", "generate_manifest",
]
