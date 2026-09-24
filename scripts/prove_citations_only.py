"""证明：本次更正只动了「文本类字段」，没有动任何「判定」。

文本类字段 = 引用文字 + 解释句（explanation）：`evidence_citations` / `evidence` /
`citations` 是引用文本；`explanation` 是解释句——解释句是**呈现、不是判定**：
它只向读者说明"为什么这么评"，不参与任何判定；判定层字段
（level / numeric_score / matched_rules / verdict / dimension_scores 等）
由脱敏对比**独立校验**（① 必须 0 处差异）。两件事互不覆盖。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\prove_citations_only.py
"""

from __future__ import annotations

import io
import json
import pathlib
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bioaudit.regression import REPO_GOLDEN, replay_all  # noqa: E402

# 会被本次改写影响的字段：只应是"文本类字段"——引用的文字 + 解释句。
# explanation 是**呈现、不是判定**（2C 笔 2，2026-09-15）：解释句只说明"为什么
# 这么评"，不参与任何判定；判定层字段（level / numeric_score / matched_rules /
# verdict / dimension_scores）由①的脱敏对比独立校验，解释句变文 ≠ 判定层变化。
TEXT_FIELDS = ("evidence_citations", "evidence", "citations", "explanation")


def strip_text(obj):
    """把引用文本类字段整体替换成占位符，其余原样保留（递归）。"""
    if isinstance(obj, dict):
        return {k: ("<TEXT>" if k in TEXT_FIELDS else strip_text(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_text(v) for v in obj]
    return obj


def main() -> int:
    expected = json.loads(pathlib.Path(REPO_GOLDEN).read_text(encoding="utf-8"))
    actual = replay_all()

    e_by = {t["trajectory"]: t for t in expected["trajectories"]}
    a_by = {t["trajectory"]: t for t in actual["trajectories"]}

    # ---- ① 除引用文本外，必须逐字段完全相同 ----
    structural = []
    for name, e in e_by.items():
        a = a_by[name]
        if strip_text(a) != strip_text(e):
            structural.append(name)

    print(f"轨迹数 {len(e_by)}；决策数 {expected['n_decisions']} → {actual['n_decisions']}")
    print(f"① 除引用文本外存在差异的轨迹：{len(structural)} 处 {structural or '（无）'}")

    # ---- ② 列出文本类字段的差异（含 explanation；逐条给出新旧值） ----
    changed = []
    for name, e in e_by.items():
        a = a_by[name]
        for se, sa in zip(e.get("step_scores", []), a.get("step_scores", [])):
            for key in TEXT_FIELDS:
                before, after = se.get(key), sa.get(key)
                if before != after:
                    changed.append((name, se.get("step_id"), key, before, after))

    print(f"② 文本类字段有差异的步骤：{len(changed)} 处")
    seen = set()
    for name, sid, key, before, after in changed:
        mark = f"[{name} {sid}] {key}"
        if mark in seen:
            continue
        seen.add(mark)
        print(f"   {mark}")
        print(f"     旧: {before}")
        print(f"     新: {after}")

    ok = not structural
    print()
    print("结论：" + ("判定层 0 差异——本次只改了文本类字段（可刷新 golden）"
                   if ok else "⚠️ 判定层有差异——这不是单纯文字更正，需停下调查"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
