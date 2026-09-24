"""家族依据复核材料包生成器（DP-<FAMILY>-EVIDENCE-REVIEW-PACKET）。

为什么有这个脚本
----------------
`DP-SOURCE-INVENTORY.md` §三 的复核排序是**按「影响面 × 陈旧度」**、不按家族；
DEG 族（14 条依据）2026-09-13/14 收口后，人类 2026-09-15 决定**接着做 pancancer**，
再做 scRNA。复核要有人做，但**材料不该由人手打**：手打的清单一旦规则库改动就会
悄悄过期，而且没人知道它过期了。所以沿用本项目已有的模式（见
`DP-SOURCE-INVENTORY.md` §五「复核（可重跑）」）：**从规则文件读出来，写成可重跑的两份产物**。

家族参数化（2026-09-15）
------------------------
本脚本原为 DEG 专用（文件名保留历史名，避免破坏既有引用与测试），现在按 `--family`
生成任意家族的同一份材料包。**DEG 的输出与参数化之前逐字节一致**（同一提交下），
其家族专属文案原样保留；其它家族使用各自的「为什么看这一族」段落。

产物
----
- `DP-<FAMILY>-EVIDENCE-REVIEW-PACKET.md`  —— 给人逐条填的复核表
- `DP-<FAMILY>-EVIDENCE-REVIEW-PACKET.csv` —— 同样内容，Excel 可直接打开（带 BOM）

本脚本**不做的事**（边界，与全项目一致）
--------------------------------------
- 不判断任何一条文献的科学有效性；「需人工判断」一栏只标**要回答什么**，不给答案。
- 不改任何规则文件；产物是**只读派生**。
- 不新增公共语义、状态域、判定词、lane 值、分数或权威路径。
- 不对复核状态做任何声称：全部条目一律从规则文件里读出的 `reviewed_at` 出发。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\make_deg_evidence_review_packet.py [--family <目录名>]
    # 默认 DEG（历史行为）；家族名 = 规则目录名（src/bioaudit/rules/data/<family>）
"""

from __future__ import annotations

import argparse
import csv
import io
import pathlib
import re
import sys

import yaml

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "src" / "bioaudit" / "rules" / "data"

#: 默认家族（历史行为：无参数即 DEG），也是模块级 MD_OUT/CSV_OUT 指向的家族。
DEFAULT_FAMILY = "DEG"
FAMILY_DIR = DEFAULT_FAMILY
MD_OUT = REPO / f"DP-{DEFAULT_FAMILY}-EVIDENCE-REVIEW-PACKET.md"
CSV_OUT = REPO / f"DP-{DEFAULT_FAMILY}-EVIDENCE-REVIEW-PACKET.csv"

#: 可选家族 = 规则目录下真实存在的家族目录（不硬编码更多名字）。
def available_families() -> list[str]:
    return sorted(p.name for p in DATA.iterdir() if p.is_dir())


#: 家族专属文案。**DEG 项逐字符照抄参数化之前的原文**——它必须保持逐字节一致。
FAMILY_PROFILES: dict[str, dict[str, str]] = {
    "DEG": {
        "why": ("- `DP-SOURCE-INVENTORY.md` §三 把 DEG 家族列为复核**第一优先**："
                "MVP 走的就是这条线。\n"),
        # 原文：source_type 写错会让问法问错问题的例子（含行号，DEG 专属）
        "source_type_example": (
            "> 例如 `edgeR User's Guide §2.5 Filtering`（第 2 行）被标成 `consensus_guideline`，\n"
            "> 于是问它「现行版本是否仍是这一版」——它其实是软件手册。**这本身就是一条复核发现**：\n"
            "> 遇到问法不对的条目，请把「来源类型标错了」一并写进「复核结论」。\n\n"
        ),
        "rerun": ".\\.venv\\Scripts\\python.exe scripts\\make_deg_evidence_review_packet.py",
    },
    "pancancer": {
        "why": ("- 人类 2026-09-15 决定：DEG 族收口后**接着复核本族**，再做 scRNA"
                "（排序依据见 `DP-SOURCE-INVENTORY.md` §三，按「影响面 × 陈旧度」）。\n"),
        "source_type_example": (
            "> 例如某条其实是软件手册、却被标成 `consensus_guideline`——这一栏就会问它"
            "「现行版本是否仍是这一版」，问法不对。\n"
            "> **这本身就是一条复核发现**：遇到问法不对的条目，请把「来源类型标错了」"
            "一并写进「复核结论」。\n\n"
        ),
        "rerun": (".\\.venv\\Scripts\\python.exe scripts\\make_deg_evidence_review_packet.py "
                  "--family pancancer"),
    },
    "scRNA": {
        "why": ("- 人类 2026-09-15 决定：本族排在 pancancer **之后**复核"
                "（排序依据见 `DP-SOURCE-INVENTORY.md` §三，按「影响面 × 陈旧度」）。\n"),
        "source_type_example": (
            "> 例如某条其实是软件手册、却被标成 `consensus_guideline`——这一栏就会问它"
            "「现行版本是否仍是这一版」，问法不对。\n"
            "> **这本身就是一条复核发现**：遇到问法不对的条目，请把「来源类型标错了」"
            "一并写进「复核结论」。\n\n"
        ),
        "rerun": (".\\.venv\\Scripts\\python.exe scripts\\make_deg_evidence_review_packet.py "
                  "--family scRNA"),
    },
}

# 「需人工判断」一栏的问法。按来源类型给不同的问法——因为复核一本书册和复核一篇
# 基准论文要回答的问题本来就不一样。这里只**提问**，不含任何结论。
QUESTION_BY_TYPE = {
    "benchmark_paper": "这篇基准研究至今仍被支持吗？有无更新的同类基准取代它？",
    "method_paper": "这个方法学文献至今仍被支持吗？有无更新的方法学共识取代它？",
    "consensus_guideline": "这条共识/标准现行版本是否仍是本项目引用的这一版？",
    "textbook": "该教科书版本是否仍是现行版本？有无更新的版本改变该结论？",
    "software_doc": "该手册/文档的现行版本是否仍如此规定？",
    "software_manual": "该手册/文档的现行版本是否仍如此规定？",
    "database_doc": "该 API/数据库文档的现行版本是否仍如此规定？",
    "review": "这篇综述的结论有无被后续工作更新？",
}
DEFAULT_QUESTION = "该依据至今是否仍成立？（本脚本未分类此来源类型，请人工判断该问什么）"


def read_yaml(p: pathlib.Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def year_of(title: str) -> str:
    """从标题里取四位年份。取不到就留空——**不猜**。"""
    m = re.search(r"(1[89]\d{2}|20\d{2})", title or "")
    return m.group(1) if m else ""


def key_of(title: str, doi: str, pmid: str) -> str:
    """同一份文献的合并键：有 DOI 用 DOI，否则用 PMID，否则用标题。"""
    return doi or pmid or (title or "").strip().lower()


def collect(family: str = DEFAULT_FAMILY) -> tuple[list[dict], list[dict]]:
    """读一个家族的全部规则，摊平成「条目」（每条 evidence 一行）+ 规则级元数据。"""
    files = sorted((DATA / family).rglob("*.yaml"))
    items: list[dict] = []
    rule_meta: list[dict] = []

    for p in files:
        d = read_yaml(p)
        rid = d.get("rule_id") or p.stem
        prov = d.get("provenance") or {}
        pol = d.get("update_policy") or {}
        scope = d.get("scope_of_validity") or {}
        scoring = d.get("scoring") or {}

        # 档位阶梯：把该规则的档位与方法数读出来（只呈现，不判定）
        ladder = []
        for lvl in ("level_4", "level_3", "level_2", "level_1", "level_0"):
            blk = scoring.get(lvl)
            if isinstance(blk, dict):
                n = len(blk.get("methods") or [])
                status = blk.get("status")
                ladder.append(f"{lvl}({n} 方法{'/status=' + str(status) if status else ''})")
            elif isinstance(blk, list):
                ladder.append(f"{lvl}({len(blk)} 方法)")

        rule_meta.append({
            "rule_id": rid,
            "file": str(p.relative_to(DATA)).replace("\\", "/"),
            "title": d.get("title", ""),
            "status": d.get("status", ""),
            "version": d.get("version", ""),
            "reviewed_at": prov.get("reviewed_at"),
            "reviewed_by": prov.get("reviewed_by"),
            "review_kind": prov.get("review_kind"),
            "review_window_months": pol.get("review_window_months"),
            "applies_to": scope.get("applies_to"),
            "not_applicable_to": scope.get("not_applicable_to"),
            "level_4_occupancy": "空" if not (scoring.get("level_4") or {}).get("methods") else "有内容",
            "ladder": " → ".join(ladder),
            "n_evidence": len(d.get("evidence") or []),
        })

        for ev in (d.get("evidence") or []):
            title = ev.get("title", "")
            doi = (ev.get("doi") or "").strip()
            pmid = (ev.get("pmid") or "").strip()
            items.append({
                "rule_id": rid,
                "year": year_of(title),
                "confidence": ev.get("confidence", ""),
                "supports_levels": "/".join(ev.get("supports_levels") or []),
                "source_type": ev.get("source_type", ""),
                "title": title,
                "doi": doi,
                "pmid": pmid,
                "excerpt": " ".join((ev.get("excerpt") or "").split()),
                "has_persistent_id": "是" if (doi or pmid) else "否",
                "question": QUESTION_BY_TYPE.get(ev.get("source_type", ""), DEFAULT_QUESTION),
                "key": key_of(title, doi, pmid),
            })

    return items, rule_meta


def shared_rule_ids(family: str, other: str = DEFAULT_FAMILY) -> set[str]:
    """本族里与 ``other`` 家族**同名**的 rule_id（同名副本，不是重复错误）。

    用途只有一个：在材料包里如实说明「本族有多少条规则其实是别族的同名副本、
    已随那一族登记」，避免复核人重复劳动。它是**读出来的事实**，不是判断。
    """
    if family == other:
        return set()
    def ids_of(fam: str) -> set[str]:
        out = set()
        for p in sorted((DATA / fam).rglob("*.yaml")):
            d = read_yaml(p)
            out.add(d.get("rule_id") or p.stem)
        return out
    return ids_of(family) & ids_of(other)


def build_markdown(items: list[dict], rule_meta: list[dict], family: str = DEFAULT_FAMILY,
                   shared: set[str] | None = None) -> str:
    profile = FAMILY_PROFILES.get(family, {})
    years = sorted(int(i["year"]) for i in items if i["year"])
    median = years[len(years) // 2] if years else None
    newest = max(years) if years else None
    oldest = min(years) if years else None
    since2019 = sum(1 for y in years if y >= 2019)
    no_pid = [i for i in items if i["has_persistent_id"] == "否"]
    placed = [i for i in items if i["supports_levels"]]

    # 待复核 / 已登记：以规则自身的 reviewed_at 为准（读出来的事实，不是判断）
    pending_rules = {r["rule_id"] for r in rule_meta if not r["reviewed_at"]}
    pending_items = [i for i in items if i["rule_id"] in pending_rules]
    done_rules = [r for r in rule_meta if r["reviewed_at"]]
    pending_no_pid = [i for i in pending_items if i["has_persistent_id"] == "否"]

    # 同一份文献被多条规则引用 → 影响面（按合并键分组）
    by_key: dict[str, list[str]] = {}
    for i in items:
        by_key.setdefault(i["key"], []).append(i["rule_id"])
    shared_docs = {k: sorted(set(v)) for k, v in by_key.items() if len(set(v)) > 1}

    # 与默认家族（DEG）同名的规则副本：如实报数，省的复核人重复劳动
    shared = shared or set()
    shared_items = [i for i in items if i["rule_id"] in shared]
    own_items = [i for i in items if i["rule_id"] not in shared]
    own_rules = [r for r in rule_meta if r["rule_id"] not in shared]

    o = io.StringIO()
    w = o.write

    w(f"# {family} 家族依据复核材料包（给领域复核的**可填表**）\n\n")
    w("> **这一页是材料，不是结论。** 本文件由 `scripts/make_deg_evidence_review_packet.py`\n")
    w("> 从规则文件**读出来**，可重跑；不含任何对文献科学有效性的判断，也不含任何\n")
    w("> 「已复核」声称——每条的复核状态都从规则文件自身的 `reviewed_at` 读出。\n\n")

    w("## 一、为什么先看这一族\n\n")
    w(profile.get("why", ""))
    w(f"- 本材料包实测：**{len(items)} 条依据**、分布在 **{len(rule_meta)} 条规则**上；"
      f"最早 **{oldest}**、中位 **{median}**、最新 **{newest}**、2019 年及以后 **{since2019}** 条。\n")
    if shared:
        w(f"- 其中 **{len(shared)} 条规则**（{len(shared_items)} 条依据）是 "
          f"`{DEFAULT_FAMILY}` 家族的**同名副本**，已随那一族登记（复核状态见 §三）；"
          f"**本族真正待复核的是 {len(own_rules)} 条规则 / {len(own_items)} 条依据**。\n")
    w(f"- 按规则自身的 `reviewed_at` 读出来：**待复核 {len(pending_items)} 条依据**"
      f"（{len(pending_rules)} 条规则），已登记 {len(items) - len(pending_items)} 条"
      f"（见 §五 附录）。\n")
    if pending_no_pid:
        w(f"- 待复核条目里 **{len(pending_no_pid)} 条没有 DOI 也没有 PMID**："
          f"登记前必须先核对（见 §二 口径）。\n")
    if no_pid:
        w(f"- 其中 **{len(no_pid)} 条**没有 DOI 也没有 PMID，只能靠标题指认。\n\n")
    else:
        w("- 每一条都带 DOI 或 PMID：可以按持久标识核对，不靠标题指认。\n\n")

    w("## 二、怎么用这份材料（给复核人）\n\n")
    w("逐条只需回答**一个问题**（每题的问法按来源类型给，见下表「要回答什么」）：\n\n")
    w("1. **仍成立** —— 该依据至今仍支撑它下面那句话，且无更新版本取代。\n")
    w("2. **已被更新** —— 有更新的文献/版本应取代它 → 请填「建议替代依据」（有 DOI/PMID 最好）。\n")
    w("3. **不适用** —— 该依据本来就不支撑这句话（含「它根本不是文献」）→ 请填一句理由。\n\n")
    w("> **「要回答什么」一栏是按规则文件里写的 `source_type` 生成的**，所以：\n")
    w("> **如果某条的 `source_type` 本身就写错了，这一栏会问错问题。**\n")
    w(profile.get("source_type_example", ""))
    w("> **口径（人类 2026-09-15 拍板）**：默认按领域判断登记即可；但**既无 DOI 也无 PMID\n")
    w("> 的条目必须先有核对证据**（联网查证或人眼看原文）才可登记 `domain_review`，\n")
    w("> 否则只能如实标「未核对」——本材料包不替提交方猜出处。\n\n")
    w("**复核结果怎么落回仓库**（由窗口代做，你只出结论）：改的是该规则 yaml 的这几个字段，\n")
    w("然后**必须重跑清单生成器**（显式递增 `ruleset_version`），否则 `check_rule_lifecycle.py`\n")
    w("的 C1 会以哈希不符失败——那正是它的用处。\n\n")
    w("```yaml\n")
    w("provenance:\n")
    w('  reviewed_at: "2026-09-15"        # 复核当天日期（不是生成日）\n')
    w('  reviewed_by: "<复核人署名>"\n')
    w('  review_kind: "domain_review"     # none / authoring / domain_review / literature_check / correction\n')
    w("scope_of_validity:\n")
    w('  applies_to: "<这条规则自称适用于什么>"        # 复核人填\n')
    w('  not_applicable_to: "<它明确不管什么>"          # 复核人填\n')
    w("```\n\n")
    w("填完之后，`check_rule_lifecycle.py` 的 C4 里「未复核」条数会从这个数往下走，\n")
    w("这**就是**验收判据（`HANDOFF.md` 待办 1 的成功判据）。\n\n")

    w("## 三、待复核条目（主体：逐条小块，直接在这几行里填）\n\n")
    w(f"每一「条」= 一条 `evidence` 记录。本节**只列待复核的 {len(pending_items)} 条**"
      f"（规则 `reviewed_at` 为空）；已登记的 {len(items) - len(pending_items)} 条见 §五 附录。\n")
    w("**同一份文献出现在多条规则里就出现多行**（这是口径，不是重复错误"
      "——见 `DP-SOURCE-INVENTORY.md` §一）。\n\n")
    if pending_items:
        for n, i in enumerate(pending_items, 1):
            idtxt = f"`{i['doi']}`" if i["doi"] else "—"
            pmtxt = f"`{i['pmid']}`" if i["pmid"] else "—"
            no_pid_note = ("　**（无持久标识：登记前必须先核对）**"
                           if i["has_persistent_id"] == "否" else "")
            w(f"### {n}. `{i['rule_id']}` · {i['year'] or '年份未知'} · {i['confidence']}\n\n")
            w(f"- **标题**：{i['title']}\n")
            w(f"- **DOI / PMID**：{idtxt} / {pmtxt}{no_pid_note}\n")
            w(f"- **来源类型 / 支撑档位**：{i['source_type']} / {i['supports_levels'] or '—'}\n")
            w(f"- **它支撑的那句话**：{i['excerpt'] or '—'}\n")
            w(f"- **要回答什么**：{i['question']}\n")
            w("- **复核结论**：〔仍成立 / 已被更新 / 不适用〕\n")
            w("- **建议替代依据**：\n")
            w("- **复核人 / 日期**：\n\n")
    else:
        w("本族当前**没有**待复核条目（全部已登记，见 §五 附录）。\n\n")

    w(f"## 四、规则一览（全族 {len(rule_meta)} 条规则，含已登记）\n\n")
    w("| 规则 ID | 文件 | 规则名 | 档位阶梯 | level_4 占位 | 依据条数 | 复核状态 |\n")
    w("|---|---|---|---|---|---|---|\n")
    for r in rule_meta:
        ra = r["reviewed_at"] or "**null（尚无人工/领域复核）**"
        w(f"| `{r['rule_id']}` | `{r['file']}` | {r['title']} | {r['ladder']} | "
          f"{r['level_4_occupancy']} | {r['n_evidence']} | {ra} |\n")
    w("\n> 「档位阶梯」只呈现规则库自己的档位与各档方法数，**不参与任何判定**、\n")
    w("> 不合成分数、不排名、不出总评（项目已定边界）。\n\n")

    w("## 五、附录：已登记条目（无需重复复核）\n\n")
    if done_rules:
        w("| 规则 ID | 依据条数 | 复核状态 |\n|---|---|---|\n")
        for r in done_rules:
            w(f"| `{r['rule_id']}` | {r['n_evidence']} | {r['reviewed_at']} · "
              f"{r['reviewed_by'] or ''} |\n")
        w("\n> 这些条目已随其所属族的复核登记；本节不重复列逐条内容"
          "（逐条口径见同目录 CSV）。\n\n")
    else:
        w("本族暂无已登记条目。\n\n")

    if shared_docs:
        w("## 六、影响面：这几份文献被多条规则共用（一份被推翻，影响面就是几条）\n\n")
        w("| 文献（合并键） | 被几条规则引用 | 规则 ID |\n|---|---|---|\n")
        for k, rids in sorted(shared_docs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            w(f"| {k} | {len(rids)} | {', '.join('`' + r + '`' for r in rids)} |\n")
        w("\n> 合并键优先用 DOI、其次 PMID、否则标题。**所以同一份文献若在两处用了不同标题\n")
        w("> 且都没带 DOI，本表会把它们当成两份**——这一点见 `DP-SOURCE-INVENTORY.md` §三 的注。\n\n")
    else:
        w("## 六、影响面\n\n本族内没有一份文献被多条规则共用。\n\n")

    w("## 七、本材料包**没有**做的事（划界）\n\n")
    w("- **未判断**任何一条文献的科学有效性；「要回答什么」只是**提问**。\n")
    w("- **未改**任何规则文件（含未填任何 `reviewed_at`）；产物为只读派生。\n")
    w("- **未声称**复核已完成或规则库内容正确。\n")
    w("- **未新增**产品语义、状态域、判定词、lane 值、分数或权威路径。\n\n")
    w("## 八、复跑\n\n")
    w("```\n")
    w(profile.get("rerun", "") + "\n")
    w("```\n\n")
    w("同一次提交重跑两次 → 两份产物**逐字节一致**（无墙钟时间、无随机顺序；排序键显式）。\n")

    return o.getvalue()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="生成家族依据复核材料包（可重跑、只读派生）")
    ap.add_argument("--family", default=DEFAULT_FAMILY, choices=available_families(),
                    help=f"规则家族目录名（默认 {DEFAULT_FAMILY}）")
    return ap.parse_args(argv)


def _out_paths(family: str, md_out: pathlib.Path | None,
               csv_out: pathlib.Path | None) -> tuple[pathlib.Path, pathlib.Path]:
    """输出路径：显式给定 > 默认家族的模块级常量（可被测试 monkeypatch）> 按家族派生。"""
    if md_out is None:
        md_out = MD_OUT if family == DEFAULT_FAMILY else \
            REPO / f"DP-{family}-EVIDENCE-REVIEW-PACKET.md"
    if csv_out is None:
        csv_out = CSV_OUT if family == DEFAULT_FAMILY else \
            REPO / f"DP-{family}-EVIDENCE-REVIEW-PACKET.csv"
    return md_out, csv_out


def main(argv: list[str] | None = None, *, family: str | None = None,
         md_out: pathlib.Path | None = None, csv_out: pathlib.Path | None = None) -> int:
    if argv is not None:
        args = _parse_args(argv)
        family = family or args.family
    family = family or DEFAULT_FAMILY
    md_path, csv_path = _out_paths(family, md_out, csv_out)

    items, rule_meta = collect(family)
    if not items:
        print(f"FAIL {family} 家族没有读到任何 evidence 条目")
        return 1

    md_path.write_text(build_markdown(items, rule_meta, family,
                                      shared=shared_rule_ids(family)),
                       encoding="utf-8", newline="\n")

    cols = ["序号", "规则ID", "年份", "置信档", "支撑档位", "来源类型", "标题",
            "DOI", "PMID", "有无持久标识", "支撑的原话", "要回答什么",
            "复核结论", "建议替代依据", "复核人", "复核日期"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(cols)
        for n, i in enumerate(items, 1):
            wr.writerow([n, i["rule_id"], i["year"], i["confidence"], i["supports_levels"],
                         i["source_type"], i["title"], i["doi"], i["pmid"],
                         i["has_persistent_id"], i["excerpt"], i["question"], "", "", "", ""])

    years = sorted(int(i["year"]) for i in items if i["year"])
    print(f"条目 {len(items)} 条 / 规则 {len(rule_meta)} 条 / "
          f"年份 {min(years)}–{max(years)} 中位 {years[len(years) // 2]} / "
          f"无持久标识 {sum(1 for i in items if i['has_persistent_id'] == '否')} 条")
    print(f"写入 {md_path.name} 与 {csv_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
