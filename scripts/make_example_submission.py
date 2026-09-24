"""A1 示例提交：一份**完整可跑**的五点提交 + 两份**故意出错**的反例。

为什么要有这个
--------------
契约写得好看不等于填得出来。本文用**实跑**回答三件事，每件都留可核输出：
1. 一份完整五点提交**填得出来、跑得通**（五点各自可审）；
2. 缺一个决策点 → **批次级拒绝**，一笔账都不写（fail-closed 在批次层是真的）；
3. 声明缺键 / 完整性七域不合格 → **不拒绝整批**，而是该点返回
   `not_auditable` / `integrity_failed`（**逐点 fail-closed**，混合状态保义）。

⚠️ **第 2 与第 3 的区别是实测出来的，不是设计文档写的**：
批次层只校验「类型与形状」（顶层四件 + 决策点键集必须恰好五个）；
**上下文缺键与完整性域不合格是在每个点内部判的**，结果是「该点不可审」而非「整批被拒」。
我原先以为缺 `comparison` 会让整批被拒——**实测不是**，特此改正。

三个踩过的坑（本项目血换来的，本文照做）
----------------------------------------
- **时刻必须显式声明**：观测时点用模块级常量钉死（沿用 `tests/dp_fixtures.py` 的 `OBSERVED_AT` 模式），
  否则「同一输入两次相同」的断言会退化成比较墙钟。
- **完整性七域的结构是严格的**：每个域必须是**恰好** `{"state": ...}`（**多一个键都算失败**），
  且 `state` 必须**恰好**是 `confirmed`（`unverified` / `failed` / `conflicted` 都算失败）。
  顶层键集必须**恰好**等于七域 + `material_ids` + `pending_targets`。
- **不改规则文件** → 不需要重跑清单生成器；本脚本在 `scripts/` 下，公开面不变。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\make_example_submission.py --out-dir _tmp_a1
"""

from __future__ import annotations

import argparse
import copy
import inspect
import io
import json
import pathlib
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from bioaudit.api import audit_dp_register  # noqa: E402
from bioaudit.dp01_store import DP01JSONLStore  # noqa: E402

# ---- 显式声明的时刻（唯一事实源；本脚本里不许出现第二个 now）------------------
OBSERVED_AT = "2026-09-13T00:00:00+00:00"
ONE_YEAR = 365 * 24 * 60 * 60

# 完整性七域：结构严格 —— 每域**恰好** {"state": "confirmed"}，不得加键
_DOMAINS = ("identity", "version", "source", "binding", "snapshot",
            "permission", "unique_authority")

CONTEXT = {
    "project_id": "project-a1-example",
    "analysis_id": "analysis-a1-example",
    "comparison": "treated_vs_control",
    # 跨步骤科学连贯（DP-COHERENCE）新增输入：分组样本量，组名与 comparison 两侧对齐。
    # 示例数据是伪造的（已标注）；缺失时连贯判定如实标「未知」，不猜、不代填。
    "n_samples": {"treated": 8, "control": 8},
    "data_type": "bulk_rnaseq",
    "audit_scope": "five-point-mvp",
    "intended_use": "scientific_analysis",
    # 存在且非空即可：明确写 "none" 是允许的，沉默不允许
    "exclusions": "none — 本次不排除任何样本或基因类别",
}

INTEGRITY = {
    **{d: {"state": "confirmed"} for d in _DOMAINS},
    "material_ids": ["material-a1-example-1"],
    "pending_targets": ["decision_support"],
}

# ★★ 一条实测出来的硬约束（本文档最重要的一条）：
# 每个点里，**观测项的 `value` 必须精确等于声明的 `method` 字符串**。
# 判定逻辑是（dp01_adapter:288 / dp02_adapter:180 同款）：
#     observed_values = {item.value for observed 且 verification in {confirmed, conflicted}}
#     conflict = bool(observed_values and declared_method not in observed_values)
# 也就是说：`value` 里写散文（如 "filterByExpr(y, design) → 12043 genes kept"）
# **必然触发 `conflicted`**（甚至把点打成 `scientifically_limited`）。
# 观测的**细节**（保留了多少基因、版本号、命令）应放进 `supports` / `provenance`。
# 另外 DP-01 还要求 declared_method **恰好**等于 "low_count_filter"（dp01_adapter:289），
# 否则该点判 `scientifically_limited`（"not covered by the current rule set"）。
OBSERVED_METHOD_VALUES = {
    "filtering": "low_count_filter",
    "normalization": "TMM",
    "differential_method": "DESeq2",
    "multiple_testing_correction": "BH",
    "significance_threshold": "padj_and_logFC",
}

POINTS = {
    "filtering": {
        "declaration": {"method": "low_count_filter", "threshold": "min_count_10",
                        "sample_rule": "at_least_two_samples", "unit": "gene",
                        "source": "declared"},
        "observations": [
            {"detail": "filterByExpr(y, design) → 12043 genes kept",
             "provenance": "run/trace.tsv 第 12 行（task=filter，exit=0）",
             "supports": "过滤实际执行了 filterByExpr，保留 12043 个基因"},
        ],
    },
    "normalization": {
        "declaration": {"method": "TMM", "threshold": "not_applicable",
                        "sample_rule": "all_samples", "unit": "library",
                        "source": "declared"},
        "observations": [
            {"detail": "calcNormFactors(method='TMM') → norm.factors=[0.98,1.03,1.01,0.99]",
             "provenance": "run/trace.tsv 第 15 行（task=norm）",
             "supports": "实际用的是 TMM，且对全部 4 个文库生效"},
        ],
    },
    "differential_method": {
        "declaration": {"method": "DESeq2", "threshold": "not_applicable",
                        "sample_rule": "paired_by_batch", "unit": "gene",
                        "source": "declared"},
        "observations": [
            {"detail": "DESeq(dds)，design=~batch+condition；DESeq2 1.42.0",
             "provenance": "run/trace.tsv 第 21 行（task=deseq2）；run/sessionInfo.txt 第 3 段",
             "supports": "实际跑的模型含 batch 项，版本 DESeq2 1.42.0"},
        ],
    },
    "multiple_testing_correction": {
        "declaration": {"method": "BH", "threshold": 0.05,
                        "sample_rule": "not_applicable", "unit": "gene",
                        "source": "declared"},
        "observations": [
            {"detail": "results(dds, alpha=0.05)，padj 由 BH 计算",
             "provenance": "run/trace.tsv 第 24 行",
             "supports": "实际用的是 BH 校正"},
        ],
    },
    "significance_threshold": {
        "declaration": {"method": "padj_and_logFC",
                        "threshold": "padj<=0.05 & |log2FC|>=1.0",
                        "sample_rule": "not_applicable", "unit": "gene",
                        "source": "declared"},
        "observations": [
            {"detail": "sum(padj<=0.05 & abs(log2FoldChange)>=1.0) → 412 genes",
             "provenance": "run/trace.tsv 第 27 行；run/methods.md §2.4",
             "supports": "双阈值实际按 padj 与 |log2FC| 同时施加，得 412 个基因"},
        ],
    },
}


def _observation(point_key: str, item: dict) -> dict:
    return {
        "source_type": "observed",
        "verification": "confirmed",
        # ★ 必须是「方法标识」本身，不能是散文 —— 细节放 supports
        "value": OBSERVED_METHOD_VALUES[point_key],
        "observed_at": OBSERVED_AT,
        "valid_for_seconds": ONE_YEAR,
        "provenance": item["provenance"],
        "provenance_subject": f"{point_key} 的执行观测（示例）",
        "supports": f"{item['supports']}；观测细节：{item['detail']}",
        "evidence_role": "support",
    }


def build_submission(audit_id: str = "audit-a1-example-0001") -> dict:
    return {
        "audit_id": audit_id,
        "_example_meta": {
            "what_this_is": "A1 示例提交：形状完整，但**数据是伪造的**（示例，不是真实研究）",
            "what_this_is_not": "不是真实审计、不含真实数据、不构成任何科学结论",
            "observed_at": OBSERVED_AT,
        },
        "analysis_context": dict(CONTEXT),
        "integrity_metadata": copy.deepcopy(INTEGRITY),
        "decision_points": {
            key: {
                "decision_declaration": dict(p["declaration"]),
                "evidence_observations": [_observation(key, o) for o in p["observations"]],
            }
            for key, p in POINTS.items()
        },
    }


def build_missing_point() -> dict:
    """反例 A：拿掉一个决策点 → 预期**批次级**拒绝（一笔账都不写）。"""
    sub = build_submission("audit-a1-example-0002-missing-point")
    sub.pop("_example_meta", None)
    sub["decision_points"].pop("multiple_testing_correction")
    return sub


def build_bad_declaration() -> dict:
    """反例 B：某个点的声明缺一个必需键 → 预期**该点**不可审，整批不拒。"""
    sub = build_submission("audit-a1-example-0003-bad-declaration")
    sub.pop("_example_meta", None)
    sub["decision_points"]["filtering"]["decision_declaration"].pop("threshold")
    return sub


def build_unconfirmed_integrity() -> dict:
    """反例 C：某个完整性域 state=unverified → 预期**逐点** integrity_failed，整批不拒。

    注意（实测）：`state` 取值必须在 {confirmed, failed, unverified, conflicted} 之内，
    **超范围的值（如 "banana"）会在批次层直接抛异常**；而 `unverified` 是合法取值，
    形状上过得去，于是走到逐点判定、每点给 `integrity_failed`。
    """
    sub = build_submission("audit-a1-example-0004-unconfirmed-integrity")
    sub.pop("_example_meta", None)
    sub["integrity_metadata"]["identity"] = {"state": "unverified"}
    return sub


def build_missing_context() -> dict:
    """反例 D：上下文缺 comparison 且 exclusions 留空 → 预期**每点** not_auditable。"""
    sub = build_submission("audit-a1-example-0005-missing-context")
    sub.pop("_example_meta", None)
    sub["analysis_context"].pop("comparison")
    sub["analysis_context"]["exclusions"] = "   "
    return sub


def ledger_for(path: pathlib.Path):
    try:
        params = list(inspect.signature(DP01JSONLStore).parameters)
    except (TypeError, ValueError):
        params = []
    return DP01JSONLStore(path) if params else DP01JSONLStore()


def run_one(submission: dict, ledger: pathlib.Path) -> dict:
    reg = audit_dp_register(submission, ledger_for(ledger))
    return {k: (getattr(v, "judgment", "?")) for k, v in reg.items() if not k.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成并实跑 A1 示例提交")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    outcomes: dict[str, bool] = {}

    # ---- ① 完整示例：期望五点都不是 not_auditable/integrity_failed ----
    good = build_submission()
    (out / "submission.example.json").write_text(
        json.dumps(good, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("=== ① 完整五点示例（期望：五点各自拿到判定）===")
    summary = run_one(good, out / "ledger.example.jsonl")
    for k, j in summary.items():
        print(f"    {k:32} {j}")
    bad_states = {"not_auditable", "unverifiable", "integrity_failed"}
    outcomes["① 五点无一落在坏状态"] = not (set(summary.values()) & bad_states)
    outcomes["① 五点全部 auditable"] = set(summary.values()) == {"auditable"}

    # ---- ② 反例 A：缺决策点 → 批次级拒绝 ----
    bad_a = build_missing_point()
    (out / "submission.missing-point.json").write_text(
        json.dumps(bad_a, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    la = out / "ledger.missing-point.jsonl"
    print("\n=== ② 反例 A：缺一个决策点（期望：**批次级**拒绝）===")
    try:
        s = run_one(bad_a, la)
        print("    ⚠️ 没被拒 —— 批次层并不硬。摘要：", s)
        outcomes["② 缺决策点被批次级拒绝"] = False
    except (ValueError, TypeError) as e:
        print(f"    已按预期拒绝：{type(e).__name__}: {str(e)[:140]}")
        outcomes["② 缺决策点被批次级拒绝"] = True
    print(f"    账本为空：{not la.exists() or la.stat().st_size == 0}")

    # ---- ③ 反例 B：声明缺键 → 该点不可审，整批不拒 ----
    bad_b = build_bad_declaration()
    (out / "submission.bad-declaration.json").write_text(
        json.dumps(bad_b, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n=== ③ 反例 B：过滤点声明缺 threshold（期望：**该点**不可审，整批不拒）===")
    try:
        s = run_one(bad_b, out / "ledger.bad-declaration.jsonl")
        for k, j in s.items():
            print(f"    {k:32} {j}")
        outcomes["③ 声明缺键→逐点反映，整批不拒"] = True
    except (ValueError, TypeError) as e:
        print(f"    被整批拒绝（与预测不符）：{type(e).__name__}: {str(e)[:120]}")
        outcomes["③ 声明缺键→逐点反映，整批不拒"] = False

    # ---- ④ 反例 C：完整性域 unverified → 逐点 integrity_failed ----
    bad_c = build_unconfirmed_integrity()
    (out / "submission.unconfirmed-integrity.json").write_text(
        json.dumps(bad_c, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n=== ④ 反例 C：identity 域 state=unverified（期望：逐点 integrity_failed）===")
    try:
        s = run_one(bad_c, out / "ledger.unconfirmed-integrity.jsonl")
        vals = set(s.values())
        for k, j in s.items():
            print(f"    {k:32} {j}")
        outcomes["④ 未确认域→逐点 integrity_failed"] = vals == {"integrity_failed"}
    except (ValueError, TypeError) as e:
        print(f"    整批抛异常（与预测不符）：{type(e).__name__}: {str(e)[:120]}")
        outcomes["④ 未确认域→逐点 integrity_failed"] = False

    # ---- ⑤ 反例 D：上下文缺键 → 每点 not_auditable ----
    bad_d = build_missing_context()
    (out / "submission.missing-context.json").write_text(
        json.dumps(bad_d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\n=== ⑤ 反例 D：上下文缺 comparison + exclusions 留空（期望：每点 not_auditable）===")
    try:
        s = run_one(bad_d, out / "ledger.missing-context.jsonl")
        for k, j in s.items():
            print(f"    {k:32} {j}")
        outcomes["⑤ 上下文缺键→每点 not_auditable"] = set(s.values()) == {"not_auditable"}
    except (ValueError, TypeError) as e:
        print(f"    整批抛异常（与预测不符）：{type(e).__name__}: {str(e)[:120]}")
        outcomes["⑤ 上下文缺键→每点 not_auditable"] = False

    print("\n=== 结论 ===")
    for k, v in outcomes.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print(f"\n产物目录：{out}")
    return 0 if all(outcomes.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
