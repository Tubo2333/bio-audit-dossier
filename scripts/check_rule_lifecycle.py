r"""生命周期检查器（第一版，先跑成红的）。

五项检查：
  C1 清单与磁盘一致（ruleset.json 的每个 sha256 与磁盘实测相符；无多无缺）
  C2 同一 rule_id 的多份必须逐字段一致（治跨家族重复悄悄分歧）
  C3 空的 level_4 占位必须自述为 planned（当前只是**报告**，不失败——标记 level_4
     的实现方式尚未获人类裁定）
  C4 报告"距上次复核超过声明窗口"的规则（无复核日期 → 记为未复核）
  C5 supersedes / superseded_by 必须成对且指向存在的文件

用法：.venv\Scripts\python.exe scripts\check_rule_lifecycle.py
退出码：0 = C1/C2/C5 全过；1 = 有不通过项。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RULES = REPO / "src" / "bioaudit" / "rules"
DATA = RULES / "data"
MANIFEST = RULES / "ruleset.json"

failures: list[str] = []
notes: list[str] = []


def read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def field(text: str, name: str, default=None):
    """取顶层或嵌套字段（`name: value`），**剥掉行尾注释**。

    踩过的坑：不剥注释时，`supersedes: null  # 它替换了谁` 会被当成值
    `null  # 它替换了谁`，于是"null"检查失败、"指向不存在"误报一片。
    """
    m = re.search(rf"^\s*{name}:\s*(.*?)\s*$", text, re.M)
    if not m:
        return default
    v = m.group(1).split("#", 1)[0].strip().strip('"').strip("'")
    return None if v in ("null", "~", "") else v


def family_hint_of(text: str):
    """scope_of_validity.family_hint 的值（用于 C2 比较时忽略家族提示行）。

    family_hint 是**按分析家族给读者的提示**，同一条规则在 DEG 与 pancancer 下
    本来就该不同——它不属于"内容"，所以不参与同名规则的一致性比较。
    """
    return field(text, "family_hint")


def main() -> int:
    if not MANIFEST.exists():
        print("FAIL 找不到 ruleset.json")
        return 1
    manifest = json.loads(read(MANIFEST))
    disk = sorted(DATA.rglob("*.yaml"))

    # ---- C1 清单与磁盘一致 ------------------------------------------------
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    on_disk = {str(p.relative_to(DATA)).replace("\\", "/"): p for p in disk}
    missing = sorted(set(listed) - set(on_disk))
    extra = sorted(set(on_disk) - set(listed))
    if missing:
        failures.append(f"C1 清单里有磁盘上不存在的规则: {missing}")
    if extra:
        failures.append(f"C1 磁盘上有清单未收录的规则: {extra}")
    hash_bad = []
    for path, want in listed.items():
        p = on_disk.get(path)
        if not p:
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != want:
            hash_bad.append(path)
    if hash_bad:
        failures.append(f"C1 哈希与清单不符: {hash_bad[:5]}")
    print(f"C1 清单与磁盘: {len(listed)} 条记录 / {len(on_disk)} 个文件 / "
          f"哈希不符 {len(hash_bad)} 处")

    # ---- C2 同 rule_id 的多份必须一致 -------------------------------------
    by_id: dict[str, list[pathlib.Path]] = {}
    for p in disk:
        rid = field(read(p), "rule_id")
        if rid:
            by_id.setdefault(rid, []).append(p)
    dups = {k: v for k, v in by_id.items() if len(v) > 1}
    c2_bad = []
    for rid, paths in sorted(dups.items()):
        # 强要求：同 rule_id 的多份必须**逐字节相同**。既有基线
        # （tests/test_engine.py::test_deg_bloodline_unified）就是这么断言的，
        # 本检查与它一致——不给"按家族可以不一样"留任何豁免，否则就等于
        # 给未来的分歧开了后门。
        bodies = {p: read(p) for p in paths}
        if len(set(bodies.values())) != 1:
            # 找出第一处不同的行
            import difflib
            a, b = (bodies[p].splitlines() for p in paths[:2])
            diff = [line for line in difflib.unified_diff(a, b, lineterm="")
                    if line[:1] in "+-"][:3]
            c2_bad.append(f"{rid}: {[p.name for p in paths]} 内容不一致 {diff}")
    if c2_bad:
        failures.append(f"C2 同名规则内容分歧: {c2_bad}")
    print(f"C2 跨文件同名 rule_id: {len(dups)} 个，内容不一致 {len(c2_bad)} 个")

    # ---- C3 空的 level_4 必须自述（硬项：不自述就是 fail） -------------------
    # level_4 是 LLM 评估档位的占位，当前 35 个文件里 methods 为空。空占位如果
    # 不自述，读者会以为那一档已经实现——所以这里要求：只要 methods 为空，就必须
    # 在同一个块里写明 status（planned / not_implemented）。
    silent_placeholder = []
    for p in disk:
        t = read(p)
        block = re.search(r"  level_4:\n((?:    .+\n)+)", t)
        if not block:
            continue
        body = block.group(1)
        empty = re.search(r"^\s*methods:\s*\[\]\s*$", body, re.M)
        if empty and not re.search(r"^\s*status:\s*\S+", body, re.M):
            silent_placeholder.append(str(p.relative_to(DATA)))
    if silent_placeholder:
        failures.append(f"C3 {len(silent_placeholder)} 个文件的空 level_4 未自述状态: "
                        f"{silent_placeholder[:3]}")
    print(f"C3 未自述的空 level_4 占位: {len(silent_placeholder)} 个（硬项）")

    # ---- C4 待重看（派生，不判罪） ----------------------------------------
    never, overdue = [], []
    for p in disk:
        t = read(p)
        rid = field(t, "rule_id") or p.stem
        reviewed = field(t, "reviewed_at")
        window = field(t, "review_window_months")
        if reviewed is None:
            never.append(rid)
        elif window is not None:
            try:
                from datetime import date, datetime
                d = datetime.fromisoformat(reviewed).date()
                months = (date.today() - d).days / 30.44
                if months > float(window):
                    overdue.append(f"{rid}({int(months)}月 > {window}月)")
            except ValueError:
                notes.append(f"C4 {rid} 的 reviewed_at 无法解析: {reviewed!r}")
    print(f"C4 未复核 {len(never)} 条；超过声明窗口 {len(overdue)} 条")
    if overdue:
        notes.append(f"C4 待重看: {overdue[:5]}")

    # ---- C5 supersede 成对 ------------------------------------------------
    known = {p.stem for p in disk} | {str(p.relative_to(DATA)) for p in disk}
    c5_bad = []
    for p in disk:
        t = read(p)
        sup = field(t, "supersedes")
        by = field(t, "superseded_by")
        if sup and sup not in known and not any(sup in k for k in known):
            c5_bad.append(f"{p.name} supersedes={sup} 指向不存在")
        if by and by not in known and not any(by in k for k in known):
            c5_bad.append(f"{p.name} superseded_by={by} 指向不存在")
    if c5_bad:
        failures.append(f"C5 supersede 指向不存在: {c5_bad[:3]}")
    print(f"C5 supersede 成对问题: {len(c5_bad)} 处")

    print()
    for n in notes:
        print("NOTE", n)
    if failures:
        print()
        for f_ in failures:
            print("FAIL", f_)
        return 1
    print("PASS  规则库生命周期检查（C1/C2/C3/C5 硬项全过；C4 为报告项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
