# -*- coding: utf-8 -*-
"""来源清单重生成器（DP-SOURCE-INVENTORY.csv）。

口径（与 DP-SOURCE-INVENTORY.md §一 一致，四个数字各有含义，不得互相替代）：
- 条目数 = 规则文件里 evidence 的条数（同一文献在多条规则里各记一次）
- 不同标题 / 不同 DOI / 不同 PMID = 去重后的数
- 「无持久标识」= DOI 与 PMID 同时为空（不含 url——软件手册/教科书本来就没有 DOI/PMID）

排序：年份升序 → 无年份的排在最后；同年按 规则ID → 分析家族 → 标题。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\make_source_inventory.py                    # 写 DP-SOURCE-INVENTORY.csv
    .\\.venv\\Scripts\\python.exe scripts\\make_source_inventory.py --out <path>       # 写到别处（对账用）
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import pathlib
import re
import sys

import yaml

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"
DEFAULT_OUT = REPO / "DP-SOURCE-INVENTORY.csv"
YEAR_RE = re.compile(r"(19|20)[0-9]{2}")

COLUMNS = ["年份", "置信档", "支撑档位", "规则ID", "分析家族", "来源类型", "标题", "DOI", "PMID"]


def collect() -> list[dict]:
    rows: list[dict] = []
    for f in sorted(glob.glob(str(DATA / "**" / "*.yaml"), recursive=True)):
        p = pathlib.Path(f)
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        rid = doc.get("rule_id")
        if not rid:
            continue
        family = p.relative_to(DATA).parts[0]
        for e in doc.get("evidence") or []:
            title = str(e.get("title", "") or "")
            m = YEAR_RE.search(title)
            rows.append({
                "year": int(m.group(0)) if m else None,
                "confidence": str(e.get("confidence", "") or ""),
                "levels": "、".join(e.get("supports_levels") or []),
                "rule_id": rid,
                "family": family,
                "source_type": str(e.get("source_type", "") or ""),
                "title": title,
                "doi": str(e.get("doi", "") or ""),
                "pmid": str(e.get("pmid", "") or ""),
            })
    rows.sort(key=lambda r: (r["year"] is None, r["year"] or 0, r["rule_id"], r["family"], r["title"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = pathlib.Path(args.out)

    rows = collect()
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(COLUMNS)
        for r in rows:
            wr.writerow([r["year"] if r["year"] else "", r["confidence"], r["levels"],
                         r["rule_id"], r["family"], r["source_type"], r["title"],
                         r["doi"], r["pmid"]])

    n = len(rows)
    titles = len({r["title"] for r in rows})
    dois = len({r["doi"] for r in rows if r["doi"]})
    pmids = len({r["pmid"] for r in rows if r["pmid"]})
    noid = sum(1 for r in rows if not r["doi"] and not r["pmid"])
    fam: dict[str, int] = {}
    for r in rows:
        fam[r["family"]] = fam.get(r["family"], 0) + 1
    print(f"条目 {n}；不同标题 {titles}；不同 DOI {dois}；不同 PMID {pmids}；无 DOI 且无 PMID {noid}")
    print("家族分布：" + ", ".join(f"{k} {v}" for k, v in sorted(fam.items())))
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
