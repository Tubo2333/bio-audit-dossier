"""刷新 golden 基线（tests/golden/golden_expected_output_after.json）。

**这是一次经人类批准的基线刷新**，不是自动步骤。为什么允许刷：
- 第一次（2026-08-14 前后）：只动了**依据的引用文字**（Schurch 数字更正、
  edgeR 的 source_type 与标题），经 `scripts/prove_citations_only.py` 脱敏对比
  证明：**137 个决策、除引用文本外 0 处差异**（level / numeric_score /
  matched_rules / verdict / dimension_scores 全部不变）。
- 第二次（2026-09-15，2C 笔 2）：引擎解释句改规范英文（explanation 属**呈现、
  不是判定**；判定层字段独立校验不变）——掩码相应合法扩展到 explanation，
  同样先证「判定层 0 差异」再刷。

脚本自带两道保险：
1. 刷新**前**必须先跑一次同样的脱敏对比，确认「判定层 0 差异」——否则拒绝刷新；
2. 刷新**后**立即回读新基线再做一次 replay 对比，必须是 0 差异。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\refresh_golden_baseline.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts\\refresh_golden_baseline.py --apply
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bioaudit.regression import REPO_GOLDEN, replay_all  # noqa: E402

GOLDEN = pathlib.Path(REPO_GOLDEN)
# 掩码字段：引用文字 + 解释句。explanation 属**呈现、不是判定**（2C 笔 2，
# 2026-09-15）：解释句只说明"为什么这么评"，不参与任何判定；判定层字段
# （level / numeric_score / matched_rules / verdict / dimension_scores）
# 由 decisions_only_differ_in_text 的脱敏对比独立校验。
TEXT_FIELDS = ("evidence_citations", "evidence", "citations", "explanation")


def strip_text(obj):
    if isinstance(obj, dict):
        return {k: ("<TEXT>" if k in TEXT_FIELDS else strip_text(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_text(v) for v in obj]
    return obj


def decisions_only_differ_in_text(expected: dict, actual: dict) -> tuple[bool, list[str]]:
    """返回 (除文本类字段外是否完全相同, 有文本类差异的步骤列表)。"""
    e_by = {t["trajectory"]: t for t in expected["trajectories"]}
    a_by = {t["trajectory"]: t for t in actual["trajectories"]}
    structural = [n for n, e in e_by.items() if strip_text(a_by[n]) != strip_text(e)]
    textchanged = []
    for name, e in e_by.items():
        for se, sa in zip(e.get("step_scores", []), a_by[name].get("step_scores", [])):
            for key in TEXT_FIELDS:
                before, after = se.get(key), sa.get(key)
                if before != after and not (before in (None, [], "")
                                            and after in (None, [], "")):
                    textchanged.append(f"{name}/{se.get('step_id')}:{key}")
    return (not structural), textchanged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="刷新 golden 基线（需人类批准）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = replay_all()

    same_decisions, textchanged = decisions_only_differ_in_text(expected, actual)
    print(f"基线：{len(expected['trajectories'])} 轨迹 / {expected['n_decisions']} 决策")
    print(f"重放：{len(actual['trajectories'])} 轨迹 / {actual['n_decisions']} 决策")
    print(f"保险①：除文本类字段外判定层是否 0 差异 → {'是（允许刷新）' if same_decisions else '否 ✗'}")
    print(f"       文本类字段有差异的步骤：{len(textchanged)} 处 {textchanged}")
    if not same_decisions:
        print("\nFAIL 判定层有差异 → 拒绝刷新基线。这不是文字更正，需要先查清。")
        return 1

    payload = json.dumps(actual, ensure_ascii=False, indent=1) + "\n"
    old = GOLDEN.read_bytes()
    new = payload.encode("utf-8")
    print(f"保险②前：变更前 {len(old)} 字节 → 变更后 {len(new)} 字节")

    if args.dry_run:
        print("\ndry-run：未写盘。（确认后加 --apply）")
        return 0

    GOLDEN.write_bytes(new)

    # 保险②：用新基线立即再比一次，必须 0 差异
    ok_after, _ = decisions_only_differ_in_text(actual, replay_all())
    back = json.loads(GOLDEN.read_text(encoding="utf-8"))
    identical = back == replay_all()
    print(f"保险②：刷新后回读再比 → {'0 差异 ✓' if identical and ok_after else '仍有差异 ✗'}")
    print(f"已写入 {GOLDEN}")
    return 0 if (identical and ok_after) else 1


if __name__ == "__main__":
    raise SystemExit(main())
