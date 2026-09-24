"""采集案例注册表（case_registry）——采集链路的多案例**单一事实源**。

设计定位（2026-09-20 试点）：
原先 `capture_chain.py` 把「verdicts / executed / benchmark」三条路径与
`act`、`declared` 全部硬编码为 10X-B 单案例——要支持多案例只能复制代码，
属于结构性缺陷。本模块把案例元数据抽出来，链路实现只消费注册表条目：

- **链路层**（`capture_chain.py`）：按 ``case_id`` 取数据、跑同一条逻辑；
- **数据层**（`demo/data/`）：每条目的三个提炼副本；导出见
  `demo/scripts/export_demo_data.py`（源路径映射属导出侧关注点）；
- **呈现层**（`pages/02_capture.py` 等）：后续以注册表为选项来源。

新增案例 = 在 ``CAPTURE_CASES`` 加一条 + 导出脚本补源路径映射，
不改链路实现、不改页面逻辑。

口径纪律：``expected_*`` 字段是**该案例历史报告的基准值**（产生时的
ruleset 口径）。重放若与基准不符，须如实登记口径差异，不得悄然改写数字。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaptureCase:
    """一个可重放的采集案例（元数据 + 基准）。"""

    case_id: str
    """注册表键；同时作为 demo/data 提炼副本的命名前缀。"""

    label: str
    """中文显示名（后续 UI 选项用）。"""

    paradigm: str
    """范式（= `run_audit` / `M3Parser` / `CrossValidator` 的 act 参数）。"""

    declared: dict
    """会话事实声明（评测者/数据事实注入，与 Agent 自证 M1 严格区分）。"""

    verdicts_file: str
    """demo/data 内的 M1 声明记录（jsonl）。"""

    executed_file: str
    """demo/data 内的执行产物副本（M3 解析输入，不执行）。"""

    benchmark_file: str
    """demo/data 内的基准提炼副本（重放核验用）。"""

    expected_score: float
    """**重放期望值**（当前 ruleset 口径）——链路核验用（见 capture_chain）。"""

    expected_verdict: str
    """基准 verdict（重放口径）。"""

    expected_n_decisions: int
    """基准最终决策数。"""

    expected_added_types: list
    """基准补入的决策类型清单（无补入则为空列表）。"""

    executed_source_kind: str = "py"
    """执行产物**源形态**：``"py"``（脚本直读）或 ``"notebook"``（.ipynb →
    提取 code cells 拼接为解析用文本）。导出脚本按此选择读取方式；
    链路层只消费提炼后的文本，不感知源形态。"""

    source_report_score: float | None = None
    """**源报告记载的分数**（该案例历史报告的原始值）——导出完整性断言用。

    两类断言各管一段，不可混用：
    - 导出脚本断言 ``source_report_score``：护栏是"源数据未被改动"；
    - 链路核验断言 ``expected_score``：护栏是"重放行为符合当前口径"。
    二者不同的案例 = 有**口径演进**（如 windowI_B：源报告 63.0·blocked 为旧口径
    旧口径，修复 cell-level wilcoxon 语义后重评为 69.0·needs_correction；
    当前 ruleset 重放 = 69.0）。``None`` 表示与 expected_score 相同。
    """

    source_report_n_decisions: int | None = None
    """**源报告记载的决策数**（与 ``source_report_score`` 同型的口径登记）。

    重放链路（M1 全集 → 交叉验证 → expected 补入）与源报告消费的最终轨迹
    可能是**不同环节的产物**——如真实 Agent 案例：重放 46 条（40 声明 + 6 补入），
    源报告 20 条（筛选后的 final trajectory）。``None`` 表示与
    ``expected_n_decisions`` 相同。"""

    caliber_note: str = ""
    """口径说明（产生基准时的 ruleset 版本与报告出处）。"""

    story: str = ""
    """机制看点（一句话）：该案例在采集/交叉验证层面**演示什么**。

    采集页的案例卡片由本字段 + 链路实测数据（声明数/候选数/一致/虚报/
    补入/分数）共同生成——不逐案例手写长文案，避免文案与数据漂移。
    """

    extra: dict = field(default_factory=dict)
    """预留：平台、数据来源等展示用元数据。"""


#: 注册表（默认案例 = windowL_10X_B_expected：现行演示所用案例，行为不变）
CAPTURE_CASES: dict[str, CaptureCase] = {
    "windowL_10X_B_expected": CaptureCase(
        case_id="windowL_10X_B_expected",
        label="黄金对照 B · 10X 平台（静默跳过双联体检测）",
        paradigm="scrna",
        declared={"sequencing": "10X_scRNA_seq"},
        verdicts_file="verdicts_10X_B.jsonl",
        executed_file="golden_agent_10X_B_executed.py",
        benchmark_file="windowL_10X_B_expected.json",
        expected_score=63.7,
        expected_verdict="blocked",
        expected_n_decisions=11,
        expected_added_types=["doublet_detection"],
        caliber_note=(
            "63.7 断言基准 = windowL_10X_B_expected.json（10X 平台；"
            "仅限 10X-B expected 口径，禁止与 66.7 混写）"
        ),
        story=(
            "Agent 声明了双联体检测却未执行（交叉验证判虚报并撤销），"
            "且该步骤是 10X 标准流程的预期决策点——缺失被补入，判致命级。"
            "本条演示采集层**唯一能抓到的那类问题**：说了没做 / 该做没做。"
        ),
        extra={"platform": "10X_scRNA_seq", "window": "10X 平台（黄金对照 A/B）", "dataset": "GSE132465"},
    ),
    "windowI_A": CaptureCase(
        case_id="windowI_A",
        label="黄金对照 A · Smart-seq2 平台（教科书式执行）",
        paradigm="scrna",
        declared={"sequencing": "smartseq2"},
        verdicts_file="verdicts_windowI_A.jsonl",
        executed_file="golden_agent_A_executed.py",
        benchmark_file="windowI_A_benchmark.json",
        expected_score=80.0,
        expected_verdict="pass",
        expected_n_decisions=10,
        expected_added_types=[],
        caliber_note=(
            "80.0 基准 = windowI_A.json（Smart-seq2 黄金对照 A 口径）"
        ),
        story=(
            "10 条声明与执行产物逐条对齐、无虚报、无补入——**正确执行的基线样本**，"
            "用来说明「声明 = 事实」时采集层无告警、分数由规则层决定。"
        ),
        extra={"platform": "smartseq2", "window": "Smart-seq2 平台", "dataset": "GSE115978"},
    ),
    "windowI_B": CaptureCase(
        case_id="windowI_B",
        label="黄金对照 B · Smart-seq2 平台（细胞级 DEG：逻辑断裂）",
        paradigm="scrna",
        declared={"sequencing": "smartseq2"},
        verdicts_file="verdicts_windowI_B.jsonl",
        executed_file="golden_agent_B_executed.py",
        benchmark_file="windowI_B_benchmark.json",
        expected_score=69.0,
        expected_verdict="needs_correction",
        expected_n_decisions=10,
        expected_added_types=[],
        source_report_score=63.0,
        caliber_note=(
            "两口径并存：源报告 windowI_B.json = 63.0 · blocked（原始口径）；"
            "修复 cell-level wilcoxon 语义（L0→L1）后重评 = 69.0 · "
            "needs_correction（当前 ruleset 重放实测 69.0，与 golden 摘要一致）。"
            "禁止混写。"
        ),
        story=(
            "声明与执行**都对得上**（Agent 确实执行了细胞级检验），错在方法本身——"
            "采集层抓不到这类问题，只有规则层能判：这是「交叉验证 ≠ 方法学审查」的对照。"
        ),
        extra={"platform": "smartseq2", "window": "Smart-seq2 平台（复核后重评）", "dataset": "GSE115978"},
    ),
    "windowI_C": CaptureCase(
        case_id="windowI_C",
        label="黄金对照 C · Smart-seq2 平台（QC 硬阈值：微妙错误）",
        paradigm="scrna",
        declared={"sequencing": "smartseq2"},
        verdicts_file="verdicts_windowI_C.jsonl",
        executed_file="golden_agent_C_executed.py",
        benchmark_file="windowI_C_benchmark.json",
        expected_score=66.7,
        expected_verdict="needs_correction",
        expected_n_decisions=10,
        expected_added_types=[],
        caliber_note=(
            "66.7 基准 = windowI_C.json（Smart-seq2-C 口径，"
            "禁止与 63.7 混写）"
        ),
        story=(
            "用固定硬阈值替代自适应 QC——该数据集恰好一个细胞都没滤掉，**产出与正确版几乎相同**；"
            "分数差异全部来自方法学等级，说明审计评的是决策逻辑而非输出表象。"
        ),
        extra={"platform": "smartseq2", "window": "Smart-seq2 平台", "dataset": "GSE115978"},
    ),
    "windowL_10X_A": CaptureCase(
        case_id="windowL_10X_A",
        label="黄金对照 A · 10X 平台（教科书式执行，含双联体检测）",
        paradigm="scrna",
        declared={"sequencing": "10X_scRNA_seq"},
        verdicts_file="verdicts_windowL_10X_A.jsonl",
        executed_file="golden_agent_10X_A_executed.py",
        benchmark_file="windowL_10X_A_benchmark.json",
        expected_score=80.0,
        expected_verdict="pass",
        expected_n_decisions=11,
        expected_added_types=[],
        caliber_note="80.0 基准 = windowL_10X_A.json（10X-A 口径）",
        story=(
            "10X 平台标准流程含双联体检测（多一个 10X 专属决策维度）——"
            "与 Smart-seq2 A 同为正确基线，用来说明**范式相同、平台不同则决策集不同**。"
        ),
        extra={"platform": "10X_scRNA_seq", "window": "10X 平台", "dataset": "GSE132465"},
    ),
    "cellvoyager_g": CaptureCase(
        case_id="cellvoyager_g",
        label="真实 Agent 运行 · CellVoyager（黑色素瘤 GSE115978）",
        paradigm="scrna",
        declared={"sequencing": "smartseq2"},
        verdicts_file="verdicts_cellvoyager_g.jsonl",
        executed_file="cellvoyager_g_executed.py",
        executed_source_kind="notebook",
        benchmark_file="cellvoyager_g_benchmark.json",
        expected_score=30.0,
        expected_verdict="needs_correction",
        expected_n_decisions=46,
        expected_added_types=[
            "batch_correction",
            "deg_method",
            "dim_reduction",
            "hv_gene_selection",
            "multiple_testing_correction",
            "significance_threshold",
        ],
        source_report_n_decisions=20,
        caliber_note=(
            "分数口径：30.0 · needs_correction = 复核后重评（windowK1_reeval.json，"
            "ruleset 1.5.0；L1×19 / L3×1 / L0=0 / L-1=0）；重放实测同为 "
            "30.0 · needs_correction（一致）。决策数口径：重放链路（M1 全部 40 条"
            "声明 → 交叉验证 → 6 类预期决策点补入）产出 46 条；重评报告消费的是"
            "筛选后的 final trajectory 20 条——**不同环节口径，不混写**。"
            "更早一次重评同分 30.0 但构成不同（L1×7 + L-1×12）。"
        ),
        story=(
            "真实 LLM Agent（CellVoyager）在真实数据上**自主完成分析**（非确定性脚本、"
            "非重放）：重放其 M1 声明与执行产物——声明全部有执行证据（无虚报），"
            "但**有 6 类预期决策点缺失**（该做没做，被 expected_types 自动补入）。"
            "本页唯一的真实 Agent 案例：同时演示「声明↔执行一致」与「预期清单捕获遗漏」。"
        ),
        extra={
            "platform": "smartseq2",
            "window": "真实 Agent 运行（复核后重评）",
            "dataset": "GSE115978",
            "agent": "CellVoyager",
        },
    ),
}

#: 默认案例（保持现行演示行为：工坊页/采集页默认案例不变）
DEFAULT_CAPTURE_CASE = "windowL_10X_B_expected"


def get_case(case_id: str | None = None) -> CaptureCase:
    """按 id 取案例；``None`` 取默认案例；未知 id 抛 KeyError（不静默回落）。"""
    key = case_id or DEFAULT_CAPTURE_CASE
    if key not in CAPTURE_CASES:
        raise KeyError(
            f"未知采集案例 '{key}'；可用：{sorted(CAPTURE_CASES)}"
        )
    return CAPTURE_CASES[key]


def case_ids() -> list[str]:
    """全部案例 id（注册顺序即展示顺序）。"""
    return list(CAPTURE_CASES)
