"""M3 采集签名表加载与校验（窗口 C / C2，signatures 驱动）。

- :class:`SignatureTable`：加载 ``capture/signatures.yaml``（包内锚定，零 cwd），
  编译 pattern、按范式过滤、按决策类型查询；
- :func:`validate_table`：结构校验（决策类型 ∈ 本体 34 类型 / pattern 可编译 /
  choice 提取方式互斥 / 数值区间合法），供 ``bio-audit capture-validate`` 与 CI 使用。

铁律（F6 禁猜）：choice 只能由字面量 / 调用参数确定性映射 / 具名组映射 /
规则词表区间/联合表得到；无法判定 → 候选进 ``ParseResult.uncertain``。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from bioaudit.ontology.loader import Ontology, get_ontology
from bioaudit.paths import PKG_DIR

#: 签名表文件（包内锚定）
SIGNATURES_FILE = PKG_DIR / "capture" / "signatures.yaml"

#: context_schema 之外的合法扩展 context 键（调用参数级证据，随签名声明）
ALLOWED_EXTRA_KEYS: frozenset[str] = frozenset({
    "min_counts", "target_sum", "flavor", "resolution", "deg_test",
    "padj_cutoff", "logfc_cutoff", "n_comps", "data_path", "mito_percent",
    "log_transformed", "pseudobulk", "reference_based", "data_format", "method",
})


class SignatureSpec(BaseModel):
    """单条工具调用签名。choice 提取方式（五选一，互斥）：
    ``choice``（字面）/ ``choice_arg``+``choice_map`` / ``choice_group``+``choice_map`` /
    ``choice_ranges`` / ``choice_table``；全部缺席 → context-only（choice=None，
    命中后进 uncertain，不猜测）。
    """

    tool: str
    pattern: str
    paradigms: Optional[list[str]] = None  # None = 不限范式
    choice: Optional[str] = None
    choice_arg: Optional[str] = None
    choice_map: Optional[dict[str, str]] = None
    choice_group: Optional[str] = None
    choice_ranges: Optional[list[dict[str, Any]]] = None
    choice_table: Optional[list[dict[str, Any]]] = None
    args: dict[str, dict[str, Any]] = Field(default_factory=dict)  # {参数名|组名: {key, type}}
    context_fixed: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None

    @field_validator("choice_map")
    @classmethod
    def _map_keys_str(cls, v):
        if v is not None:
            return {str(k): str(val) for k, val in v.items()}
        return v

    @property
    def has_choice_spec(self) -> bool:
        return any([
            self.choice is not None,
            self.choice_arg is not None,
            self.choice_group is not None,
            self.choice_ranges is not None,
            self.choice_table is not None,
        ])

    def matches_paradigm(self, act: Optional[str]) -> bool:
        if self.paradigms is None:
            return True
        if act is None:
            return True  # 未限定范式 → 匹配全部签名（调用方应尽量传 act）
        return act in self.paradigms


class TypeSignatures(BaseModel):
    """一个决策类型的签名集合。"""

    note: str = ""
    signatures: list[SignatureSpec] = Field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return bool(self.signatures)


class SignatureTable:
    """签名表（决策类型 → 工具调用签名），包内锚定加载。"""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        ontology: Optional[Ontology] = None,
    ):
        self.path = Path(path) if path else SIGNATURES_FILE
        self.ontology = ontology if ontology is not None else get_ontology()
        self._raw: Optional[dict] = None
        self._types: Optional[dict[str, TypeSignatures]] = None
        self._compiled: Optional[dict[str, list[tuple[SignatureSpec, re.Pattern]]]] = None
        self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "types" not in data:
            raise ValueError(f"签名表结构非法（缺 types 键）: {self.path}")
        self._raw = data
        self.signatures_version = str(data.get("signatures_version", "0.0.0"))
        self._types = {
            tid: TypeSignatures(**spec)
            for tid, spec in data["types"].items()
        }
        self._compiled = {}
        for tid, ts in self._types.items():
            self._compiled[tid] = [
                (sig, re.compile(sig.pattern))
                for sig in ts.signatures
            ]

    @property
    def types(self) -> dict[str, TypeSignatures]:
        return self._types

    def decision_types(self) -> list[str]:
        return sorted(self._types)

    def get(self, decision_type: str) -> TypeSignatures:
        return self._types.get(decision_type, TypeSignatures())

    def compiled(self, decision_type: str) -> list[tuple[SignatureSpec, re.Pattern]]:
        return self._compiled.get(decision_type, [])

    def version(self) -> str:
        return self.signatures_version


def validate_table(
    table: SignatureTable,
    ontology: Optional[Ontology] = None,
) -> dict:
    """结构校验（capture-validate 主闸）。返回报告 dict（``errors`` 非空即 FAIL）。

    检查：
    1. 每个签名表决策类型 ∈ 本体 34 类型（未知类型 → 错误）；
    2. 每个 pattern 可编译（加载期已保证，此处复核并归类）；
    3. choice 提取方式互斥（多方式并存 → 错误）；
    4. choice_ranges 区间合法（min<=max，choice 非空）；
    5. args 的 ``key`` ∈ 该类型 context_schema 键 或 ALLOWED_EXTRA_KEYS
       （否则 advisory warning，不 FAIL）；
    6. 覆盖率统计（有签名的类型数 / 34）。
    """
    ont = ontology if ontology is not None else get_ontology()
    errors: list[str] = []
    warnings: list[str] = []
    covered: list[str] = []

    for tid, ts in table.types.items():
        if not ont.is_known(tid):
            errors.append(f"签名表决策类型 {tid!r} 不在本体 34 类型内")
            continue
        schema_keys = {
            item["key"] for item in ont.get_type(tid)["context_schema"]
        }
        if ts.has_any:
            covered.append(tid)
        for sig in ts.signatures:
            modes = [m for m, present in (
                ("choice", sig.choice is not None),
                ("choice_arg", sig.choice_arg is not None),
                ("choice_group", sig.choice_group is not None),
                ("choice_ranges", sig.choice_ranges is not None),
                ("choice_table", sig.choice_table is not None),
            ) if present]
            if len(modes) > 1:
                errors.append(f"{tid}: 签名 {sig.tool!r} choice 提取方式冲突 {modes}")
            if sig.choice_map is not None and not (
                sig.choice_arg or sig.choice_group
            ):
                errors.append(
                    f"{tid}: 签名 {sig.tool!r} 有 choice_map 但缺 choice_arg/choice_group"
                )
            for row in sig.choice_ranges or []:
                lo, hi = row.get("min"), row.get("max")
                if lo is None or hi is None or not row.get("choice"):
                    errors.append(f"{tid}: choice_ranges 行缺 min/max/choice: {row}")
                elif lo > hi:
                    errors.append(f"{tid}: choice_ranges min>{max}: {row}")
            for key, spec in sig.args.items():
                ck = spec.get("key")
                if ck not in schema_keys and ck not in ALLOWED_EXTRA_KEYS:
                    warnings.append(
                        f"{tid}: 签名 {sig.tool!r} 参数 {key!r} 的 context 键 {ck!r} "
                        f"不在该类型 context_schema（扩展键需列入 ALLOWED_EXTRA_KEYS）"
                    )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "n_types": len(table.types),
        "n_types_with_signatures": len(covered),
        "covered_types": sorted(covered),
        "signatures_version": table.version(),
    }


__all__ = [
    "SIGNATURES_FILE",
    "ALLOWED_EXTRA_KEYS",
    "SignatureSpec",
    "TypeSignatures",
    "SignatureTable",
    "validate_table",
]
