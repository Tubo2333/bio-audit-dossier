"""A1 输入面 · 第一版实现：提交一份完整输入快照 → 收账 → 出报告。

**这就是「研究者怎么把分析送进来」的第一版落地**（人类已授权实现，见
`DP-A1-INPUT-CONTRACT.md` §一「本批范围」与 §七「实现仍未授权」被授权后的状态）。

流程（按设计文档定死的顺序）
----------------------------
1. **机械合理性检查**（`check_submission_freshness`）：观测时点/有效期是否合理。
   不合格 → 退出码 **2**，**不写任何账**——这是「结构硬」在入口的第一道门。
2. **注册**（`audit_dp_register` 五点一次成型）：形状不合格（缺决策点/声明缺键等）
   由引擎按实测分层处理——批次级拒绝（退出码 **3**）或逐点判定。
3. **权威快照**（`build_dp_acceptance_snapshot`）：一次审计的账户锚。
4. **报告**（`render_dp_report`）：五点判定、四轴、限制、使用边界等。
   **不记 lane**：真实提交不带「申请使用档」字段（那属于人类复核层），
   报告会如实说「无 lane 记录」——与「无角色记录就如实说」同一逻辑。
5. **产出**：`report.json` + `report.html`（检查页渲染）+ `deliverable.html`（交付文档）
   写进 `<out-dir>/<audit_id>/`。

边界
----
- **不进公开面**：本脚本在 `scripts/` 下；`bioaudit.api.__all__` 保持 33 不变。
- **不改规则文件**、不新增公共语义/状态域/判定词/lane 值/权威路径。
- **不解析流程引擎 trace**（第二版范围）。
- 不声称 acceptance / release / lane 授权 / runtime proof / deployment / closure。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\submit_analysis.py --file sub.json
    .\\venv\\Scripts\\python.exe scripts\\submit_analysis.py --file sub.json --now 2026-09-13T12:00:00+00:00
    # 退出码：0 已收账出报告；2 机械合理性不合格（未写账）；3 批次级被拒（未写账）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

#  控制台编码统一靠 PYTHONIOENCODING=utf-8（见 ui/generate_deliverable.py 的注释：
#  本脚本 import 的多个模块曾各自改写 sys.stdout，嵌套包装关掉了彼此缓冲区）。

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = pathlib.Path(__file__).resolve().parent
UI = REPO / "ui"
SRC = REPO / "src"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(UI))
sys.path.insert(0, str(SCRIPTS))  # 本脚本要 import 同目录的 check_submission_freshness

from bioaudit.api import (  # noqa: E402
    audit_dp_register,
    build_dp_acceptance_snapshot,
    dp_authority_store,
    render_dp_report,
)
from bioaudit.dp01_store import DP01JSONLStore  # noqa: E402

import check_submission_freshness as freshness  # noqa: E402
import generate_deliverable as deliverable  # noqa: E402
import generate_report as page  # noqa: E402

# 与页面渲染一致：报告里每个点实际命中的规则 ID（账本里就有，渲染层自用）。
_POINT_NAMES = ("filtering", "normalization", "differential_method",
                "multiple_testing_correction", "significance_threshold")


def _now(value: str | None) -> str:
    return value or datetime.now(timezone.utc).isoformat(timespec="seconds")


def submit(submission: dict, *, out_dir: pathlib.Path, now: str) -> dict:
    """真实提交管线的主干；返回摘要 dict（含产物路径）。写篇日志见 main。"""
    audit_id = submission.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("audit_id is required (non-empty string)")

    target = out_dir / audit_id
    target.mkdir(parents=True, exist_ok=True)
    point_store = DP01JSONLStore(target / "ledger.points.jsonl")
    authority_store = dp_authority_store(target / "ledger.authority.jsonl")

    register = audit_dp_register(submission, point_store)
    snapshot = build_dp_acceptance_snapshot(
        submission, point_store, authority_store,
        snapshot_id=f"snapshot:{audit_id}", register=register)

    report = render_dp_report(register, authority=snapshot)
    report["_matched_rules"] = {
        name: tuple(getattr(register.get(name), "matched_rule_ids", ()) or ())
        for name in _POINT_NAMES}

    (target / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (target / "report.html").write_text(
        page.render_html(report), encoding="utf-8", newline="\n")
    # 渲染来源要如实指向本账户，而不是演示用的 ui/report.sample.json
    ctx = submission.get("analysis_context") or {}
    source_label = f"{ctx.get('project_id')}/{ctx.get('analysis_id')}（audit {audit_id}）"
    (target / "deliverable.html").write_text(
        deliverable.render_deliverable(report, now=now, generated_at=now,
                                       source_label=source_label),
        encoding="utf-8", newline="\n")

    judgments = {k: (getattr(register[k], "judgment", "?")) for k in _POINT_NAMES}
    return {
        "audit_id": audit_id,
        "snapshot_id": f"snapshot:{audit_id}",
        "judgments": judgments,
        "dir": target,
    }


def submit_json(submission: dict, *, out_dir: str | pathlib.Path = "var",
                now: str | None = None) -> tuple[int, dict]:
    """CLI 与 Web 共用的提交逻辑：三道门 + 产物。

    Returns (rc, payload)：rc 语义与 CLI 退出码一致——
      0 已收账出报告（payload=结果摘要）；
      1 输入用法错误（payload={error}）；
      2 机械合理性不合格（未写账，payload={reasons:[...]}）；
      3 批次级被拒（未写账，payload={error}）。
    """
    now_s = _now(now)
    fresh_now = datetime.fromisoformat(now_s)
    if fresh_now.tzinfo is None:
        return 1, {"error": "--now 必须是带时区的 ISO8601"}
    problems, _notes = freshness.check(submission, now=fresh_now, max_days=365 * 5)
    if problems:
        return 2, {"reasons": problems}
    try:
        result = submit(submission, out_dir=pathlib.Path(out_dir), now=now_s)
    except (ValueError, TypeError) as e:
        return 3, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return 0, {
        "audit_id": result["audit_id"],
        "snapshot_id": result["snapshot_id"],
        "judgments": result["judgments"],
        "dir": str(result["dir"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提交一份完整输入快照并出报告（A1 第一版）")
    ap.add_argument("--file", required=True, help="提交 JSON（骨架填好的）")
    ap.add_argument("--out-dir", default="var", help="产物根目录（默认 var/）")
    ap.add_argument("--now", default=None, help="覆盖判定/生成时刻（ISO8601，可复现）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"FAIL 找不到提交文件：{path}")
        return 1
    try:
        submission = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL 提交文件不是合法 JSON：{e}")
        return 1

    rc, payload = submit_json(submission, out_dir=args.out_dir, now=args.now)
    if rc == 1:
        print(f"FAIL 输入用法错误：{payload.get('error')}")
        return 1
    if rc == 2:
        print("FAIL 机械合理性不合格（未写任何账）：")
        for p in payload["reasons"]:
            print(f"  ✗ {p}")
        return 2
    if rc == 3:
        print(f"FAIL 批次级被拒（未写任何账）：{payload['error']}")
        return 3

    print(f"已收账并出报告：{payload['dir']}")
    print(f"  audit_id   = {payload['audit_id']}")
    print(f"  snapshot   = {payload['snapshot_id']}")
    print("  五点判定：")
    for name, j in payload["judgments"].items():
        print(f"    {name:32} {j}")
    print("  产物：report.json / report.html / deliverable.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())