"""提交前的机械合理性检查（A1 输入面 · 第一版）。

为什么有这一步
--------------
`EvidenceItem` 的注释把 `observed_at` / `valid_for_seconds` 称为 **caller-declared inputs**，
而 register 里**没有任何一处校验它们合不合理**。这意味着：
- 提交方可以把观测时点写成 1900 年；
- 或把有效期写成一百年——于是「过期证据会停掉受影响主张」这条机制**被绕过**。

本检查**不要求证明**（证明来源属下一版：接管线那次），只堵住**明显不合理的值**。
它如实说明自己防不了什么：**它不防伪造**，它防的是「填写者无意或有意写了一个不可能的窗口」。

检查内容
--------
1. `observed_at` 不得**晚于当前时刻**（未来观测不存在）；
2. `valid_for_seconds` 必须是正整数，且**不得超过上限**（默认 5 年，见 `--max-days`）；
3. **诚实性联动**：若一条观测**自称仍然有效**（观测时点 + 有效期 > 现在），
   则它必须确实落在有效期内——否则说明写的人以为它还有效但实际已过期，应显式改口径。

**退出码**：0 = 全部通过；1 = 有不合格项（打印明细）；2 = 用法/读入错误。

边界
----
- 不改任何文件、不写账、不做判定；**不新增公共语义**（本脚本在 `scripts/` 下）。
- 不替代 register 自己的结构校验——它管的是「形状对不对」，本脚本管的是「时刻合不合理」。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\check_submission_freshness.py --file sub.json
    .\\.venv\\Scripts\\python.exe scripts\\check_submission_freshness.py --file sub.json --now 2026-09-13T12:00:00+00:00
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

# 注意：不改写 sys.stdout（本模块会被 submit_analysis 等 import；嵌套包装会关掉彼此缓冲区。
# 控制台编码统一靠 PYTHONIOENCODING=utf-8。）

DEFAULT_MAX_DAYS = 365 * 5


def _parse(value: str, label: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def check(submission: dict, *, now: datetime, max_days: int) -> tuple[list[str], list[str]]:
    """返回 (问题列表, 提示列表)。"""
    problems: list[str] = []
    notes: list[str] = []
    max_secs = max_days * 86400

    points = submission.get("decision_points")
    if not isinstance(points, dict):
        return ["decision_points 不是映射，无法检查"], []

    for point_key, point in points.items():
        if not isinstance(point, dict):
            continue
        obs = point.get("evidence_observations")
        if not isinstance(obs, list):
            continue
        for idx, item in enumerate(obs):
            if not isinstance(item, dict):
                continue
            label = f"{point_key}[{idx}]"

            raw_at = item.get("observed_at")
            at = _parse(raw_at, "observed_at") if isinstance(raw_at, str) else None
            if at is None:
                problems.append(f"{label} observed_at 缺失/不是带时区的 ISO8601：{raw_at!r}")
                continue
            if at > now:
                problems.append(f"{label} observed_at 在未来：{at.isoformat()} > 现在 {now.isoformat()}")

            secs = item.get("valid_for_seconds")
            if not isinstance(secs, int) or isinstance(secs, bool) or secs <= 0:
                problems.append(f"{label} valid_for_seconds 不是正整数：{secs!r}")
                continue
            # 上限衡量的是**声明的窗口长度**，而不是「观测时点 + 窗口」这个端点——
            # 否则一条未来时点的观测会因为端点更靠后而被误报两次（实测踩过）。
            if secs > max_secs:
                problems.append(
                    f"{label} valid_for_seconds={secs}（约 {secs / 86400:.0f} 天）超过上限 "
                    f"{max_days} 天——疑似「一百年有效期」这类不可能窗口")

            # 诚实性联动：声称仍有效 → 必须真的仍在有效期内
            still_current = at + timedelta(seconds=secs) > now
            if not still_current:
                notes.append(
                    f"{label} 已过期（观测 {at.date()} + {secs / 86400:.0f} 天 < 现在）"
                    f"——请显式确认这是有意的")

    return problems, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提交前的机械合理性检查（不防伪造）")
    ap.add_argument("--file", required=True, help="提交 JSON 路径")
    ap.add_argument("--now", default=None, help="覆盖当前时刻（ISO8601，便于可复现）")
    ap.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS,
                    help=f"有效期的机械上限（天），默认 {DEFAULT_MAX_DAYS}")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"用法/读入错误：找不到 {path}")
        return 2
    try:
        submission = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"用法/读入错误：{path} 不是合法 JSON：{e}")
        return 2

    now = _parse(args.now, "now") if args.now else datetime.now(timezone.utc)
    if now is None:
        print(f"用法/读入错误：--now 不是带时区的 ISO8601：{args.now!r}")
        return 2

    problems, notes = check(submission, now=now, max_days=args.max_days)

    print(f"检查对象：{path}（判定基准时刻 {now.isoformat()}，有效期上限 {args.max_days} 天）")
    for n in notes:
        print(f"  提示：{n}")
    if problems:
        print(f"\n不合格 {len(problems)} 项：")
        for p in problems:
            print(f"  ✗ {p}")
        print("\nFAIL —— 这些是机械不合理值，建议先改再交。")
        print("（本检查不防伪造：它只看「时刻是否可能」，不看「观测是否真实发生」。）")
        return 1

    print("\nPASS —— 观测时点与有效期均未见机械不合理值。")
    print("（本检查不防伪造：它只看「时刻是否可能」，不看「观测是否真实发生」。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
