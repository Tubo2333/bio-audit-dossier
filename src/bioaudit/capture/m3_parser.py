"""M3 产物解析器（窗口 C / C2，signatures 驱动，禁猜规则 F6）。

设计依据：trajectory-capture-design-v1 §四/§五
- 决策点发现（主路径）：签名表命中 → 候选决策点 → 本体 context_schema 校验；
- 上下文四级可信源：**调用参数 > 数据元数据 > declared（评测者/数据事实声明，
  运行宪法/评测配置注入，与 Agent 自证严格区分）**；任一级缺失 →
  键标 ``unverified``，**绝不正则猜数字**（旧 trajectory_capture 伪造
  n_patients=11 / n_cells=50000 即反面教材）；
- choice 无法确定性判定 → 候选进 :class:`UncertainCandidate`（不猜测；
  如 UMAP 投影≠聚类降维、pca 不证明 elbow 选择）；
- provenance 逐决策记录：{来源: M3解析, 时间戳, 证据}；
- LLM 辅助模糊发现（补漏）：:meth:`M3Parser.validate_nominations` 只做
  "提名 → 过本体 context_schema 校验"，不过则丢弃标 unknown，LLM 不"定案"。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bioaudit.capture.models import (
    PROVENANCE_SOURCE_M3,
    TRUST_CALL_ARG,
    TRUST_DATA_METADATA,
    TRUST_DECLARED,
    CapturedDecision,
    DecisionProvenance,
    ParseResult,
    UncertainCandidate,
)
from bioaudit.capture.signatures import SignatureSpec, SignatureTable
from bioaudit.ontology.loader import Ontology, get_ontology

#: 数值规范化（choice_map / choice_table 键比较用）：25 → "25"，0.8 → "0.8"
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
#: kwargs 解析（仅限调用 span 内；字面量/数字/布尔/裸词）
_KWARG_RE = re.compile(
    r"([A-Za-z_]\w*)\s*=\s*"
    r"(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(True|False|None)|([A-Za-z_][\w.]*))"
)

_TYPENAME_RE = re.compile(r"[A-Za-z_][\w-]*")

#: 不合法枚举/数值的提名 → 拒绝原因（LLM 提名校验）
def _norm_num_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        s = f"{value:g}"
        return s
    return str(value)


def extract_call_span(code: str, match: re.Match) -> tuple[str, int]:
    """从匹配的最后一个 ``(`` 起找平衡右括号，返回 (call_text, 结束位置)。

    pattern 以 ``\\s*\\("`` 结尾时 match 已含左括号；此处取匹配内最后一个
    ``(`` 作为起点（兼容无括号命中，如 import 语句）。
    忽略字符串字面量内的括号。
    """
    lparen = match.start() + match.group().rfind("(")
    if lparen < match.start():
        return code[match.start():match.end()], match.end()
    depth = 0
    i = lparen
    quote: Optional[str] = None
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\":
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return code[match.start():i + 1], i + 1
        i += 1
    return code[match.start():match.end()], match.end()


def parse_kwargs(call_text: str) -> dict[str, Any]:
    """解析调用文本内的 keyword 参数（仅字面量；解析失败不猜测）。"""
    out: dict[str, Any] = {}
    for m in _KWARG_RE.finditer(call_text):
        name, s1, s2, num, flag, word = m.groups()
        if s1 is not None:
            out[name] = s1
        elif s2 is not None:
            out[name] = s2
        elif num is not None:
            out[name] = float(num) if "." in num or "e" in num.lower() else int(num)
        elif flag is not None:
            out[name] = {"True": True, "False": False, "None": None}[flag]
        elif word is not None:
            out[name] = word
    return out


def _coerce(value: Any, type_name: str) -> Optional[Any]:
    """按签名 args 的 type 转换；转换失败返回 None（不猜测，调用方标缺失）。"""
    try:
        if type_name == "int":
            if isinstance(value, bool):
                return None
            return int(value)
        if type_name == "float":
            if isinstance(value, bool):
                return None
            return float(value)
        if type_name == "bool":
            if isinstance(value, bool):
                return value
            return {"true": True, "false": False, "1": True, "0": False}.get(
                str(value).lower()
            )
        if type_name == "string":
            return str(value)
        return value
    except (TypeError, ValueError):
        return None


class M3Parser:
    """signatures 驱动的 M3 产物解析器。

    Parameters
    ----------
    act : str | None
        范式（deg/pan/scrna）；限定签名集（同名异构消歧）；None = 全签名。
    metadata : dict | None
        数据元数据（二级可信源：adata.uns/obs 摘要，如 n_cells/n_patients/
        sequencing/n_genes…）。
    declared : dict | None
        评测者/数据事实声明（三级可信源：运行宪法/评测配置注入的键值，
        如数据集平台 sequencing=smartseq2；**与 Agent claim（M1 声明）严格区分**，
        G-2 纪律：Agent 上报的键永远不进 declared）。
    """

    def __init__(
        self,
        act: Optional[str] = None,
        metadata: Optional[dict] = None,
        declared: Optional[dict] = None,
        ontology: Optional[Ontology] = None,
        table: Optional[SignatureTable] = None,
    ):
        self.act = act
        self.metadata = dict(metadata or {})
        self.declared = dict(declared or {})
        self.ontology = ontology if ontology is not None else get_ontology()
        self.table = table if table is not None else SignatureTable(ontology=self.ontology)

    # ── 对外入口 ──

    def parse_notebook(self, path: str | Path) -> ParseResult:
        """解析 .ipynb（或 .py 单文件）→ ParseResult（候选 + 未定 + 元信息）。"""
        p = Path(path)
        if p.suffix == ".py":
            result = self.parse_code(p.read_text(encoding="utf-8"), source=str(p))
            self._finalize_instances(result)
            return result
        if p.suffix != ".ipynb":
            raise ValueError(f"仅支持 .ipynb / .py，收到 {p.suffix!r}")
        nb = json.loads(p.read_text(encoding="utf-8"))
        cells = nb.get("cells", [])
        result = ParseResult(
            n_code_cells=sum(
                1 for c in cells if c.get("cell_type") == "code"
            ),
            paradigm=self.act,
        )
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            source = "".join(cell.get("source", []))
            sub = self.parse_code(source, cell_index=idx)
            result.candidates.extend(sub.candidates)
            result.uncertain.extend(sub.uncertain)
            result.warnings.extend(sub.warnings)
        self._finalize_instances(result)  # 全局实例编号（跨 cell 按执行顺序）
        return result

    def parse_code(
        self, code: str, cell_index: int = 0, source: str = "code"
    ) -> ParseResult:
        """解析一段代码（一个 notebook cell / .py 文件）→ 该段落的候选。"""
        result = ParseResult(n_code_cells=1, paradigm=self.act)
        for tid, ts in self.table.types.items():
            if not ts.has_any:
                continue
            type_schema = self.ontology.get_type(tid)
            if type_schema is None:
                result.warnings.append(f"签名表类型 {tid!r} 不在本体，跳过")
                continue
            schema_keys = [item["key"] for item in type_schema["context_schema"]]
            for sig, pattern in self.table.compiled(tid):
                if not sig.matches_paradigm(self.act):
                    continue
                for m in pattern.finditer(code):
                    hit = self._build_candidate(
                        tid, sig, m, code, cell_index, schema_keys
                    )
                    if hit is None:
                        continue
                    candidate, uncertain = hit
                    if candidate is not None:
                        result.candidates.append(candidate)
                    elif uncertain is not None:
                        result.uncertain.append(uncertain)
        self._finalize_instances(result)  # 本段落实例编号（按代码位置）
        return result

    # ── 单签名命中处理 ──

    def _build_candidate(
        self,
        tid: str,
        sig: SignatureSpec,
        match: re.Match,
        code: str,
        cell_index: int,
        schema_keys: list[str],
    ) -> Optional[tuple[Optional[CapturedDecision], Optional[UncertainCandidate]]]:
        call_text, _ = extract_call_span(code, match)
        kwargs = parse_kwargs(call_text)
        groups = match.groupdict()

        # 1) 提取调用参数级上下文（签名 args：参数名或具名组）
        context: dict[str, Any] = {}
        context_trust: dict[str, str] = {}
        for arg_name, spec in sig.args.items():
            raw = kwargs.get(arg_name)
            if raw is None and arg_name in groups:
                raw = groups[arg_name]
            value = _coerce(raw, spec.get("type", "string"))
            if value is None:
                continue
            ck = spec["key"]
            context[ck] = value
            context_trust[ck] = TRUST_CALL_ARG

        # 2) 签名定义性事实（工具语义，如 method=PCA / reference_based=true）
        for k, v in sig.context_fixed.items():
            context.setdefault(k, v)
            context_trust.setdefault(k, TRUST_CALL_ARG)

        # 3) choice 确定性判定；失败 → uncertain（禁猜）
        choice, choice_ok = self._resolve_choice(sig, kwargs, groups, context)
        evidence = f"notebook cell #{cell_index + 1}：{sig.tool}（pattern 命中 {sig.pattern!r}）"
        if not choice_ok:
            reason = (
                f"choice 无法确定性判定（{sig.tool}）；"
                f"签名 note: {sig.note or '需额外证据'}"
            )
            if sig.note:
                reason = f"choice 无法确定性判定：{sig.note}"
            return None, UncertainCandidate(
                decision_type=tid,
                step_id=f"nb{cell_index + 1:02d}",
                evidence=evidence,
                reason=reason,
                partial_context=dict(context),
                tool_call=sig.tool,
                code_snippet=code[:300],
            )

        # 4) 三级可信源补齐 context_schema 键；缺失 → unverified（不猜数字）
        unverified: list[str] = []
        for key in schema_keys:
            if key in context:
                continue
            if key in self.metadata:
                context[key] = self.metadata[key]
                context_trust[key] = TRUST_DATA_METADATA
            elif key in self.declared:
                context[key] = self.declared[key]
                context_trust[key] = TRUST_DECLARED
            else:
                unverified.append(key)

        candidate = CapturedDecision(
            step_id=f"nb{cell_index + 1:02d}-{tid}",
            decision_type=tid,
            choice=choice,
            rationale=f"M3 解析：{sig.tool}（pattern 命中）",
            context=context,
            context_trust=context_trust,
            unverified_keys=unverified,
            tool_call=sig.tool,
            code_snippet=code[:300],
            provenance=DecisionProvenance(
                source=PROVENANCE_SOURCE_M3,
                timestamp=datetime.now().isoformat(timespec="seconds"),
                evidence=evidence,
                detail={
                    "cell_index": cell_index,
                    "pos": match.start(),  # 实例排序键（按代码执行位置）
                    "signature_tool": sig.tool,
                },
            ),
            paradigm=self.act,
        )
        return candidate, None

    def _resolve_choice(
        self,
        sig: SignatureSpec,
        kwargs: dict[str, Any],
        groups: dict[str, str],
        context: dict[str, Any],
    ) -> tuple[Optional[str], bool]:
        """确定性 choice 判定（五选一）；失败 → (None, False)。"""
        if sig.choice is not None:
            return sig.choice, True

        if sig.choice_arg is not None and sig.choice_map is not None:
            raw = kwargs.get(sig.choice_arg)
            if raw is None:
                return None, False
            key = _norm_num_str(raw)
            choice = sig.choice_map.get(key)
            return choice, choice is not None

        if sig.choice_group is not None and sig.choice_map is not None:
            raw = groups.get(sig.choice_group)
            if raw is None:
                return None, False
            choice = sig.choice_map.get(_norm_num_str(raw))
            return choice, choice is not None

        if sig.choice_ranges:
            # 作用于唯一数值型 context 参数（choice_arg 可显式指定）
            arg_name = sig.choice_arg or self._only_numeric_arg(sig)
            if arg_name is None:
                return None, False
            # 窗口 I 实测修复（2026-08-16）：非字面量 kwarg（如 n_comps=n_comps
            # 变量间接）无法确定性取值 → 禁猜（F6）→ uncertain，绝不崩溃。
            # 此前 _coerce 失败的值（None）在 _build_candidate 被跳过，但
            # choice_ranges 直接比较原始 kwarg 值 → str vs int TypeError 崩溃。
            value = _coerce(kwargs.get(arg_name), "float")
            if value is None:
                return None, False
            for row in sig.choice_ranges:
                lo, hi = row.get("min"), row.get("max")
                if lo is not None and hi is not None and lo <= value <= hi:
                    return row["choice"], True
            return None, False

        if sig.choice_table:
            row_values = {
                name: _norm_num_str(groups.get(name))
                for name in groups
                if groups.get(name) is not None
            }
            for row in sig.choice_table:
                if all(
                    _norm_num_str(row[name]) == row_values.get(name)
                    for name in row
                    if name != "choice"
                ):
                    return row["choice"], True
            return None, False

        return None, False  # context-only 签名 → uncertain

    @staticmethod
    def _only_numeric_arg(sig: SignatureSpec) -> Optional[str]:
        numeric = [
            name for name, spec in sig.args.items()
            if spec.get("type") in ("int", "float")
        ]
        return numeric[0] if len(numeric) == 1 else None

    # ── 实例建模与步骤号固化 ──

    def _finalize_instances(self, result: ParseResult) -> None:
        """同类型多实例建模（迭代调参，v1.1 B5）：按执行位置（cell/pos）排序后
        编号 instance_index；重复 step_id 追加实例后缀。"""
        def sort_key(cand: CapturedDecision) -> tuple:
            detail = cand.provenance.detail
            return (detail.get("cell_index", 0), detail.get("pos", 0))
        ordered = sorted(result.candidates, key=sort_key)
        seen: dict[str, int] = {}
        for cand in ordered:
            n = seen.get(cand.decision_type, 0) + 1
            seen[cand.decision_type] = n
            cand.instance_index = n
            if n > 1:
                cand.step_id = f"{cand.step_id}#{n}"

    # ── LLM 辅助模糊发现（补漏；只提名，不定案）──

    def validate_nominations(
        self, nominations: list[dict]
    ) -> dict:
        """LLM 提名校验：过本体 context_schema 校验，不过则丢弃标 unknown。

        返回 {"accepted": [规范化提名], "rejected": [{提名, reason}]}。
        提名不产生任何评分——LLM 只提名，本体校验才算数（设计 §五.2）。
        """
        accepted: list[dict] = []
        rejected: list[dict] = []
        for nom in nominations:
            if not isinstance(nom, dict):
                rejected.append({"nomination": nom, "reason": "提名必须是对象"})
                continue
            raw_type = str(nom.get("decision_type", "")).strip()
            tid = self.ontology.canonicalize(raw_type)
            type_def = self.ontology.get_type(tid)
            if type_def is None:
                rejected.append({
                    "nomination": nom,
                    "reason": f"decision_type {raw_type!r} 不在本体 34 类型内",
                })
                continue
            context = nom.get("context") or {}
            if not isinstance(context, dict):
                rejected.append({"nomination": nom, "reason": "context 必须是对象"})
                continue
            bad_keys: list[str] = []
            for key, value in context.items():
                item = next(
                    (it for it in type_def["context_schema"] if it["key"] == key),
                    None,
                )
                if item is None:
                    bad_keys.append(f"{key} 不在 context_schema")
                    continue
                if item["type"] == "enum" and value not in item.get("values", []):
                    bad_keys.append(f"{key}={value!r} 不在枚举 {item['values']}")
                elif item["type"] == "int" and not isinstance(value, int):
                    bad_keys.append(f"{key}={value!r} 不是 int")
                elif item["type"] == "float" and not isinstance(value, (int, float)):
                    bad_keys.append(f"{key}={value!r} 不是 float")
                elif item["type"] == "bool" and not isinstance(value, bool):
                    bad_keys.append(f"{key}={value!r} 不是 bool")
            if bad_keys:
                rejected.append({
                    "nomination": nom,
                    "reason": "本体 context_schema 校验失败: " + "; ".join(bad_keys),
                })
                continue
            accepted.append({
                "step_id": str(nom.get("step_id", f"nom-{len(accepted) + 1}")),
                "decision_type": tid,
                "choice": str(nom.get("choice", "")).strip(),
                "rationale": str(nom.get("rationale", "")),
                "context": context,
            })
        return {"accepted": accepted, "rejected": rejected}


__all__ = [
    "M3Parser",
    "extract_call_span",
    "parse_kwargs",
]
