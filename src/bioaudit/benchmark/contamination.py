"""规则字符串污染扫描（refactor-plan-v1.1 E2；execution-plan D4.11）。

黑盒评测协议：评测期规则文本不可见——任务集与生成器提示词中**不得携带
规则内容**。本模块把"规则字符串命中"登记为污染特征（命中即标记）：

- 扫描对象：任务文件（JSON 文本）+ 生成器提示词（generator_prompt.md）
- 污染特征三类：
  a) ``rule_id`` 模式（如 ``A1.2-ANNO-002_marker_validation``）——致命特征；
  b) 规则标题（title 全文）——致命特征；
  c) 规则 description 的共享长 n-gram（≥8 词）——信息特征（标记不致命）。

注意：任务中的 **choice/方法名**（如 DESeq2、BH）是 Agent 自己的方法学词汇，
不是规则内容（规则词表与 Agent 词汇天然同源），不登记为污染——否则任务
无法编写。预注册定义见 protocol.PRE_REGISTRATION.contamination_policy。

golden 不变量：本模块只读扫描，不触碰任何评分路径。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import yaml

from bioaudit.paths import RULES_DIR

#: rule_id 模式（如 A1.2-ANNO-002_marker_validation / M1.1-DEG-001）
RULE_ID_RE = re.compile(r"\b[A-Z]\d(?:\.\d)?-[A-Z]{2,6}-\d{3}(?:_[A-Za-z0-9_]+)?\b")

#: n-gram 长度阈值（description 共享片段 ≥ 8 词 → 信息特征）
NGRAM_MIN_WORDS = 8


def collect_rule_fragments(rules_dir: Optional[Path | str] = None) -> dict:
    """从规则库提取污染特征（rule_ids / titles / descriptions）。"""
    rd = Path(rules_dir) if rules_dir else RULES_DIR
    rule_ids: set[str] = set()
    titles: list[str] = []
    descriptions: list[str] = []
    for f in sorted(rd.rglob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        if data.get("rule_id"):
            rule_ids.add(str(data["rule_id"]))
        if data.get("title"):
            titles.append(str(data["title"]))
        if data.get("description"):
            descriptions.append(str(data["description"]))
    return {
        "rule_ids": sorted(rule_ids),
        "titles": titles,
        "descriptions": descriptions,
    }


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text)


def _shared_ngrams(text: str, description: str, n: int = NGRAM_MIN_WORDS) -> list[str]:
    """text 与 description 的共享 n-gram（≥n 词）——大小写不敏感。"""
    text_words = _words(text.lower())
    desc_words = _words(description.lower())
    if len(text_words) < n or len(desc_words) < n:
        return []
    desc_ngrams = {" ".join(desc_words[i:i + n]) for i in range(len(desc_words) - n + 1)}
    hits = []
    for i in range(len(text_words) - n + 1):
        ng = " ".join(text_words[i:i + n])
        if ng in desc_ngrams and ng not in hits:
            hits.append(ng)
    return hits


def scan_text(text: str, fragments: dict) -> dict:
    """扫描一段文本 → 污染特征命中报告。"""
    hits: dict[str, list] = {"rule_id": [], "title": [], "shared_ngram": []}
    for rid in fragments["rule_ids"]:
        if rid in text:
            hits["rule_id"].append(rid)
    for title in fragments["titles"]:
        if title in text:
            hits["title"].append(title[:80])
    for desc in fragments["descriptions"]:
        for ng in _shared_ngrams(text, desc):
            hits["shared_ngram"].append(ng)
            break  # 每条 description 最多记 1 个命中（去噪）
    return {
        "n_rule_id_hits": len(hits["rule_id"]),
        "n_title_hits": len(hits["title"]),
        "n_ngram_hits": len(hits["shared_ngram"]),
        "rule_id_hits": sorted(set(hits["rule_id"])),
        "title_hits": sorted(set(hits["title"]))[:20],
        "ngram_hits": hits["shared_ngram"][:20],
        "ok": not hits["rule_id"] and not hits["title"],
    }


def scan_file(path: Path, fragments: dict) -> dict:
    """扫描单个文件（JSON 任务或 md 提示词）。"""
    text = path.read_text(encoding="utf-8")
    report = scan_text(text, fragments)
    report["file"] = str(path)
    return report


def scan_dir(tasks_dir: Path, fragments: dict) -> dict:
    """扫描任务目录全部文件 → 汇总报告（E2：命中即标记）。"""
    reports = []
    for f in sorted(tasks_dir.rglob("*.json")):
        if f.name == "taskset.json":
            continue
        reports.append(scan_file(f, fragments))
    return summarize(reports)


def summarize(reports: list[dict]) -> dict:
    """汇总多个文件扫描报告。"""
    files_with_hits = [r["file"] for r in reports
                       if r.get("n_rule_id_hits") or r.get("n_title_hits")]
    total_rule_id = sum(r.get("n_rule_id_hits", 0) for r in reports)
    total_title = sum(r.get("n_title_hits", 0) for r in reports)
    total_ngram = sum(r.get("n_ngram_hits", 0) for r in reports)
    return {
        "ok": not files_with_hits,
        "n_files": len(reports),
        "files_with_rule_hits": files_with_hits,
        "n_rule_id_hits": total_rule_id,
        "n_title_hits": total_title,
        "n_ngram_hits": total_ngram,
        "note": "rule_id/title 命中即污染标记（E2）；ngram 为信息特征",
    }


def scan_json_task(task_dict: dict, fragments: dict) -> dict:
    """扫描单个任务 dict（供 runner/测试复用）。"""
    return scan_text(json.dumps(task_dict, ensure_ascii=False), fragments)


__all__ = [
    "RULE_ID_RE",
    "NGRAM_MIN_WORDS",
    "collect_rule_fragments",
    "scan_text",
    "scan_file",
    "scan_dir",
    "scan_json_task",
    "summarize",
]
