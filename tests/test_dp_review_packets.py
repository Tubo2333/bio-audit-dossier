"""复核材料包生成器的测试（DP-DEG-EVIDENCE-REVIEW-PACKET / DP-OVERRIDE-N2-REVIEW-PACKET）。

这两个脚本从规则文件**派生**材料，所以它们最重要的性质不是「能跑」，
而是**规则库一改，材料不会悄悄过期**：
- override_n2 的核对器：处置后**任何地方再出现空机制 → 必须 FAIL**；
  （2026-09-14 裁定执行前，这条 FAIL 条件是「分组 ≠ 全部空机制」；
  执行后空机制归零，核对方向随之反转——记在这里，免得后来者以为是漏测。）
- 同一次提交重跑两次必须逐字节一致（否则「可重跑」是假的）。

本文件不触碰 `src/bioaudit/rules/**`。篡改测试一律在 `tmp_path` 里造副本，
把模块的 `DATA` 指向副本——**绝不在真规则目录上动手**。
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
RULES_DATA = REPO / "src" / "bioaudit" / "rules" / "data"


class _ThrowawayStdout(io.StringIO):
    """给被导入脚本用的假 stdout（它们在 import 时包 sys.stdout.buffer）。"""

    @property
    def buffer(self) -> io.BytesIO:
        return io.BytesIO()


def _load(name: str):
    """按路径加载 scripts/ 下的模块（scripts 不是包）。"""
    saved = sys.stdout
    try:
        sys.stdout = _ThrowawayStdout()
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved
    return mod


@pytest.fixture(scope="module")
def deg():
    return _load("make_deg_evidence_review_packet")


@pytest.fixture(scope="module")
def ovn2():
    return _load("make_override_n2_review_packet")


# ── 材料包的口径必须与实测一致 ────────────────────────────────────────────


def test_deg_packet_roster_matches_rules(deg):
    """DEG 族 13 条依据 / 5 条规则（锁定当前快照）。

    2026-09-16 跨族 Conesa 处置：D1.2-DEG-001 的 Conesa 2016 槽位经原文核对删除
    （filterByExpr 全文 0 命中，真出处为 edgeR Guide / Bourgon 2010）→ 14 → 13 条；
    有年份条目 11 条，中位年份随之 2016 → 2014（见 DP-scRNA-REVIEW-FEEDBACK.md §7.4/§7.6B）。
    """
    items, rule_meta = deg.collect()
    assert len(items) == 13
    assert len(rule_meta) == 5
    years = sorted(int(i["year"]) for i in items if i["year"])
    assert (years[0], years[-1]) == (1995, 2026)
    assert years[len(years) // 2] == 2014
    assert [y for y in years if y >= 2019] == [2026]
    # 无持久标识（无 DOI 且无 PMID）的 2 条：edgeR 手册、Bonferroni 不等式
    assert sum(1 for i in items if i["has_persistent_id"] == "否") == 2


def test_deg_packet_is_deterministic(deg):
    """同一输入渲染两次 → 逐字节一致（不得含墙钟时间/随机顺序）。"""
    items, rule_meta = deg.collect()
    assert deg.build_markdown(*deg.collect()) == deg.build_markdown(items, rule_meta)


def test_deg_packet_writes_and_reruns_identically(deg, tmp_path, monkeypatch):
    monkeypatch.setattr(deg, "MD_OUT", tmp_path / "p.md")
    monkeypatch.setattr(deg, "CSV_OUT", tmp_path / "p.csv")
    assert deg.main() == 0
    first = ((tmp_path / "p.md").read_bytes(), (tmp_path / "p.csv").read_bytes())
    assert deg.main() == 0
    second = ((tmp_path / "p.md").read_bytes(), (tmp_path / "p.csv").read_bytes())
    assert first == second
    assert (tmp_path / "p.csv").read_bytes().startswith(b"\xef\xbb\xbf")


# ── pancancer 家族（2026-09-15 参数化后新增；人类排序：DEG 之后先做本族）──


def test_pancancer_packet_roster_matches_rules(deg):
    """pancancer 35 条依据 / 16 条规则；其中 5 条规则（13 条依据）是 DEG 同名副本。

    2026-09-16 跨族 Conesa 处置：D1.2-DEG-001 的 Conesa 2016 槽位经原文核对删除
    → 36 → 35 条、共享条目 14 → 13；有年份条目 31 条，中位年份随之 2013 → 2012
    （见 DP-scRNA-REVIEW-FEEDBACK.md §7.4/§7.6B）。
    """
    items, rule_meta = deg.collect("pancancer")
    assert len(items) == 35
    assert len(rule_meta) == 16
    years = sorted(int(i["year"]) for i in items if i["year"])
    assert (years[0], years[-1]) == (1972, 2026)
    # 中位年份 2012：2026-09-15 人类裁定把 N7.1 的 Beroukhim 2010 Nature 换挂
    # Zack et al. 2013 Nat Genet（该标题的真实归属）——依据年份集合随之 2012 → 2013；
    # 2026-09-16 删除 D1.2 的 Conesa 2016 后，中位年份回落 2013 → 2012
    assert years[len(years) // 2] == 2012
    # 无持久标识（无 DOI 且无 PMID）的 5 条：edgeR 手册、GSEA 手册、cBioPortal 文档、
    # Bonferroni 数学事实、Kowalski 1972（2026-09-15 更正：原 PMID 指向无关德文文献 → 置 null；
    # 该文按标题在 PubMed 检索 0 命中，登记为「未核对」，见 DP-PANCANCER-REVIEW-WORKSHEET.md #9）
    assert sum(1 for i in items if i["has_persistent_id"] == "否") == 5
    shared = deg.shared_rule_ids("pancancer")
    assert shared == {"D1.2-DEG-001", "D1.3-DEG-001", "M1.1-DEG-001",
                      "M1.2-DEG-001", "M1.3-DEG-001"}
    shared_items = [i for i in items if i["rule_id"] in shared]
    assert len(shared_items) == 13
    own = [r for r in rule_meta if r["rule_id"] not in shared]
    assert len(own) == 11
    assert sum(r["n_evidence"] for r in own) == 22


def test_pancancer_packet_is_deterministic(deg):
    items, rule_meta = deg.collect("pancancer")
    a = deg.build_markdown(items, rule_meta, "pancancer",
                           shared=deg.shared_rule_ids("pancancer"))
    b = deg.build_markdown(*deg.collect("pancancer"), "pancancer",
                           shared=deg.shared_rule_ids("pancancer"))
    assert a == b


def test_committed_packets_match_current_rules(deg, tmp_path):
    """★ 守卫：仓库里的材料包必须与当前规则库逐字节一致——规则库一改、产物没重生成就红。

    2026-09-15 首次复跑发现 DEG 包过期一天（`830146a` 改了 excerpt 的「D2 FIX:」前缀，
    产物停在 `daa9393`）——正是脚本存在的理由。这条守卫把「悄悄过期」变成「红」。
    """
    families = ("DEG", "pancancer", "scRNA")
    missing = [f for f in families
               if not (REPO / f"DP-{f}-EVIDENCE-REVIEW-PACKET.md").exists()]
    if missing:
        pytest.skip(
            "材料包未随本发行版发布（根级 DP-*.md 属治理件、不进公开导出）："
            + ", ".join(missing)
            + "——本守卫在完整工作线内仍然生效"
        )
    for fam in families:
        md = REPO / f"DP-{fam}-EVIDENCE-REVIEW-PACKET.md"
        csv = REPO / f"DP-{fam}-EVIDENCE-REVIEW-PACKET.csv"
        tmd = tmp_path / f"{fam}.md"
        tcsv = tmp_path / f"{fam}.csv"
        assert deg.main(family=fam, md_out=tmd, csv_out=tcsv) == 0
        assert md.read_bytes() == tmd.read_bytes(), \
            f"{fam} 材料包 md 与当前规则库不同步（改规则后必须重生成）"
        assert csv.read_bytes() == tcsv.read_bytes(), \
            f"{fam} 材料包 csv 与当前规则库不同步（改规则后必须重生成）"


# ── override_n2：处置后状态不得漂移 ───────────────────────────────────────


def test_override_n2_states_reflect_the_ruling(ovn2):
    """2026-09-14 裁定执行后的三态：scRNA 24 = 0 空 + 3 有条件 + 21 无键。

    组 A/B 补全（G1.2 / G1.3 / G1.1），组 C/D/E 删空键，组 F 维持无键。
    """
    rules = ovn2.index_rules()
    scrna = [r for r, i in rules.items() if i["rel"].startswith("scRNA/")]
    empty, cond, nokey = [], [], []
    for rid in scrna:
        sc = rules[rid]["doc"].get("scoring") or {}
        if "override_n2" not in sc:
            nokey.append(rid)
        elif sc["override_n2"]:
            cond.append(rid)
        else:
            empty.append(rid)
    assert len(scrna) == 24
    assert (len(empty), len(cond), len(nokey)) == (0, 3, 21)
    assert sorted(cond) == ["G1.1-DEG-001_pseudobulk", "G1.2-DEG-002_multiple_testing",
                            "G1.3-DEG-003_method"]
    # 补全的三条必须内容完整，且触发键是引擎真正解析的那个
    for rid in cond:
        ov = rules[rid]["doc"]["scoring"]["override_n2"]
        assert ov["condition"] == "n_patients <= 2", rid
        assert ov.get("all_methods") == "level_0", rid
        assert ov.get("note"), rid
    # 全库有内容的是这四条（M1.1 在 DEG / pancancer 各一份文件，按 rule_id 去重后计一次）
    filled = sorted(r for r, i in rules.items()
                    if (i["doc"].get("scoring") or {}).get("override_n2"))
    assert filled == ["G1.1-DEG-001_pseudobulk", "G1.2-DEG-002_multiple_testing",
                      "G1.3-DEG-003_method", "M1.1-DEG-001"]


def test_override_n2_grouping_covers_all_scrna_and_matches_ruling(ovn2):
    """分组必须恰好覆盖 scRNA 24 条、不重复，且每条的实测状态等于裁定期望。"""
    rules = ovn2.index_rules()
    scrna = sorted(r for r, i in rules.items() if i["rel"].startswith("scRNA/"))
    grouped = [r for g in ovn2.GROUPS for r in g["rules"]]
    assert len(grouped) == len(set(grouped)), "有规则被重复归类"
    assert sorted(grouped) == scrna, "分组与 scRNA 规则集合不一致"
    for g in ovn2.GROUPS:
        for rid in g["rules"]:
            sc = rules[rid]["doc"].get("scoring") or {}
            state = "nokey" if "override_n2" not in sc else ("cond" if sc["override_n2"] else "empty")
            assert state == g["want_state"], f"组{g['id']} {rid}: 期望 {g['want_state']} 实测 {state}"


def test_override_n2_main_passes_on_real_rules(ovn2, tmp_path, monkeypatch):
    monkeypatch.setattr(ovn2, "MD_OUT", tmp_path / "o.md")
    monkeypatch.setattr(ovn2, "CSV_OUT", tmp_path / "o.csv")
    assert ovn2.main() == 0
    first = (tmp_path / "o.md").read_bytes()
    assert ovn2.main() == 0
    assert (tmp_path / "o.md").read_bytes() == first


def test_override_n2_fails_when_an_empty_mechanism_reappears(ovn2, tmp_path, monkeypatch):
    """**这是本脚本存在的理由**：空机制一旦重新出现，必须 FAIL、不写材料。

    造法：把规则目录复制到 tmp_path，再给一条**已删除该键**的规则塞回空壳
    （`override_n2: {}`）——它从「无键」变成「空」，核对必须失败。
    """
    fake = tmp_path / "data"
    shutil.copytree(RULES_DATA, fake)
    target = fake / "scRNA" / "Q1.1-QC-001_filtering.yaml"
    text = target.read_text(encoding="utf-8")
    assert "override_n2" not in text, "Q1.1 现在应当没有这个键"
    # 在 scoring 块末尾（evidence 之前，2 空格缩进）塞回一个空壳
    assert "\nevidence:" in text
    target.write_text(text.replace("\nevidence:", '\n  override_n2: {}\nevidence:', 1),
                      encoding="utf-8", newline="\n")
    monkeypatch.setattr(ovn2, "DATA", fake)
    monkeypatch.setattr(ovn2, "MD_OUT", tmp_path / "o.md")
    monkeypatch.setattr(ovn2, "CSV_OUT", tmp_path / "o.csv")
    assert ovn2.main() == 1
    assert not (tmp_path / "o.md").exists(), "核对失败时不得写出材料"
