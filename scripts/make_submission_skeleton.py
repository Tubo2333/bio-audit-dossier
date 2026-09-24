"""提交骨架生成器（A1 输入面 · 第一版）。

为什么有这个脚本
----------------
A1 的问题不是「要不要输入面」，而是**观测证据不能变成第二个「声明」**。
若让研究者自己把「实际跑了什么」手打一遍，审计就退化成「听他说」——
而 `EvidenceItem` 的既有约束明写：**相似性、文件名或方法名永远不能替代「来源」**。

所以第一版的形状是：**工具生成骨架 → 人补声明 → 交回**。
- 工具能自己知道的，骨架**已经填好**（版本三元组等）；
- 只有研究者知道的（方法/阈值/样本规则/单位/来源），留**待填占位**并标注；
- 机器产生的观测（观测时点/有效期/来源），留空并给出**格式指引**，由提交方从真实运行材料填写。

本脚本**不校验、不判定、不写账**：它只产出一份待填文件。
校验发生在真正提交时（见 `DP-A1-INPUT-CONTRACT.md` §三）。

边界（与全项目一致）
--------------------
- **不进公开面**：`bioaudit.api.__all__` 保持不变（本脚本在 `scripts/` 下，不 import 进包）。
- **不改任何规则文件**、**不写账本**、**不做判定**。
- **不新增**公共语义、状态域、判定词、lane 值或权威路径。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\make_submission_skeleton.py --out _tmp_skeleton.json
    .\\.venv\\Scripts\\python.exe scripts\\make_submission_skeleton.py --out x.json --now 2026-09-13T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

# 注意：不改写 sys.stdout（本模块会被 ui/a1_submit_app 的 /api/skeleton 在请求路径
# import；若在这里包 TextIOWrapper 会把调用方的 stdout 链弄坏——测试实测踩过）。
# 控制台编码统一靠 PYTHONIOENCODING=utf-8。

REPO = pathlib.Path(__file__).resolve().parent.parent

# 五个决策点：register 键 -> (审计 id 后缀, 中文名, 该点常见决策类型)
POINTS: list[tuple[str, str, str]] = [
    ("filtering", "dp01", "低表达基因过滤"),
    ("normalization", "dp02", "归一化"),
    ("differential_method", "dp03", "差异分析方法"),
    ("multiple_testing_correction", "dp04", "多重检验校正"),
    ("significance_threshold", "dp05", "显著性/效应量阈值"),
]

# ★ 这五键是硬要求：`_valid_declaration` 要求键集**精确等于**这五个，多一个少一个都 fail-closed。
DECLARATION_KEYS = ("method", "threshold", "sample_rule", "unit", "source")

# ★ analysis_context 的硬要求：6 个键必须存在；`exclusions` 必须存在且非空（书面写 "none" 可以）。
# 本线（跨步骤科学连贯）另加一个必填键 `n_samples`：分组样本量，JSON 对象，每组一个正整数，
# 组名与 `comparison` 对齐；它不进 adapter 的必需键集（adapter 不校验、不拒绝），
# 缺失时由连贯判定如实标「未知」（fail-closed，不猜、不代填）。
REQUIRED_CONTEXT = ("project_id", "analysis_id", "comparison", "data_type",
                    "audit_scope", "intended_use")
NONBLANK_CONTEXT = ("exclusions",)

# ★ integrity_metadata 的七域（adapter 按域检查确认状态）
INTEGRITY_DOMAINS = ("identity", "version", "source", "binding", "snapshot",
                     "permission", "unique_authority")

# 观测项里**本工具不替提交方猜**的字段（必须来自真实运行材料）
OBSERVATION_PLACEHOLDER = {
    "source_type": "<来自真实运行材料：observed / declared / ...>",
    "verification": "<如 confirmed / unverified>",
    "value": "<观测到的具体值>",
    "observed_at": "<ISO8601 带时区，如 2026-09-13T10:00:00+00:00>",
    "valid_for_seconds": "<正整数：这条观测多久内有效>",
    "provenance": "<材料从哪来（trace/报告/容器摘要的定位），不得用方法名或文件名代替>",
    "provenance_subject": "<这条材料关乎哪个被指名的对象>",
    "supports": "<它为哪一句具体主张作证>",
    "evidence_role": "support",
}


def version_triple() -> dict[str, str]:
    """版本三元组：**系统自己知道，所以骨架直接填好**（不由提交方声明）。"""
    import bioaudit
    from bioaudit.rules import RULESET_VERSION

    return {
        "ruleset_version": RULESET_VERSION,
        "ontology_version": bioaudit.ONTOLOGY_VERSION,
        "engine_version": bioaudit.ENGINE_VERSION,
    }


def build(now: str | None, audit_id: str) -> dict:
    triple = version_triple()
    ts = now or datetime.now(timezone.utc).isoformat(timespec="seconds")

    return {
        "_skeleton_meta": {
            "generated_at": ts,
            "generator": "scripts/make_submission_skeleton.py",
            "what_this_is": (
                "一份**待填**的审计提交骨架。已填好的字段是系统自己知道的事实；"
                "带 <...> 的必须由提交方填写，且**观测类字段必须来自真实运行材料**，"
                "不得凭记忆或方法名代填。"
            ),
            "what_this_is_not": (
                "不是审计结论，不是提交，不含任何判定；未校验（校验发生在提交时）。"
            ),
            "version_snapshot_autofilled": triple,
        },
        # register 顶层必需四件
        "audit_id": audit_id,
        "analysis_context": {
            **{k: f"<必填：{k}>" for k in REQUIRED_CONTEXT},
            "n_samples": (
                "<必填：分组样本量，JSON 对象，每组一个正整数，组名与 comparison 对齐；"
                '例如 {"treated": 8, "control": 8}>'
            ),
            "exclusions": "<必填且不得留空；若确实无排除项，请明确写 \"none\">",
        },
        "integrity_metadata": {
            **{d: {"state": "<confirmed|unverified>", "note": "<依据>"}
               for d in INTEGRITY_DOMAINS},
            "material_ids": ["<material id>"],
            "pending_targets": ["<尚待确认的目标>"],
        },
        "decision_points": {
            key: {
                "decision_declaration": {
                    k: f"<必填：{k}>" for k in DECLARATION_KEYS
                },
                "evidence_observations": [dict(OBSERVATION_PLACEHOLDER)],
                "_point_note": f"{cn}（{key}）",
            }
            for key, _suffix, cn in POINTS
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成待填的审计提交骨架")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--now", default=None, help="覆盖生成时刻（ISO8601，便于可复现）")
    ap.add_argument("--audit-id", default="<必填：本次提交的 audit_id>")
    args = ap.parse_args(argv)

    skeleton = build(args.now, args.audit_id)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)   # 父目录不存在时自动建（演示踩过）
    out.write_text(json.dumps(skeleton, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"已写出骨架：{out}（{out.stat().st_size} 字节）")
    print(f"版本三元组（系统已代填）：{skeleton['_skeleton_meta']['version_snapshot_autofilled']}")
    print("待填：analysis_context 7+1 键（含分组样本量 n_samples）、integrity_metadata 七域、"
          "五个决策点的 decision_declaration(5 键) 与 evidence_observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
