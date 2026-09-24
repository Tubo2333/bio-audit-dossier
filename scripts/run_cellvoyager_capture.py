"""CellVoyager + M1 采集运行器（窗口 C / C1 部署入口）。

用法（仓库根目录，bio-audit 已安装）::

    python scripts/run_cellvoyager_capture.py --h5ad-path <path> --paper-summary <txt> \\
        --model <model> [--paradigm scrna] [--num-analyses 1] [--max-iterations 6] ...

行为：
1. 启动 CellVoyager（AnalysisAgentV2，legacy 或 claude 执行模式）；
2. 挂载 :class:`bioaudit.capture.cellvoyager_hook.CellVoyagerM1Hook`——
   **wrapper 优先，不 fork**；工具调用前后上报 M1 决策
   （session 自注册白名单 + 幂等 + WAL + verdict provisional）；
3. hook 异常隔离（F6）：hook/reporter 任何失败只记日志，分析照跑；
4. 未安装 CellVoyager 或其依赖时优雅失败（不伪装，不半吊子运行）。

结束后的交叉验证：
    bio-audit parse-notebook outputs/<analysis>_analysis_1.ipynb --act scrna --metadata meta.json
    bio-audit cross-validate --m1 <m1.jsonl> --m3 outputs/...ipynb --act scrna --session <session_id>
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h5ad-path", required=True, help="scRNA .h5ad 数据路径")
    parser.add_argument("--paper-summary", required=True, help="论文摘要 txt 路径")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="执行模型")
    parser.add_argument("--paradigm", default="scrna", choices=["deg", "pan", "scrna"])
    parser.add_argument("--num-analyses", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument("--execution-mode", default="legacy",
                        choices=["legacy", "claude"], help="CellVoyager 执行器")
    parser.add_argument("--metadata", default=None, help="数据元数据 JSON（M3 二级可信源）")
    parser.add_argument("--output-home", default=".", help="输出目录（默认 cwd）")
    parser.add_argument("--session-id", default=None, help="采集会话 id（默认自动生成）")
    args = parser.parse_args()

    # ── 优雅降级：CellVoyager 不可用 → 明确报错，不半吊子运行 ──
    try:
        from cellvoyager.agent import AnalysisAgentV2
    except ImportError as exc:
        print(
            f"❌ CellVoyager 不可用（{exc}）。请先安装 CellVoyager "
            f"（D:\\C-file\\scRNA-audit\\CellVoyager）并安装其依赖。",
            file=sys.stderr,
        )
        return 1

    from bioaudit.capture.cellvoyager_hook import make_cellvoyager_hook

    metadata = None
    if args.metadata:
        metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))

    session_id = args.session_id or f"cv_{uuid.uuid4().hex[:10]}"
    hook = make_cellvoyager_hook(
        args.paradigm, session_id, metadata=metadata,
    )
    print(f"✅ M1 采集会话已注册: {session_id}（白名单 + WAL + verdict provisional）")

    os.makedirs(args.output_home, exist_ok=True)
    agent = AnalysisAgentV2(
        h5ad_path=args.h5ad_path,
        paper_summary_path=args.paper_summary,
        openai_api_key=os.getenv("OPENAI_API_KEY", "dummy"),
        model_name=args.model,
        analysis_name="captured",
        num_analyses=args.num_analyses,
        max_iterations=args.max_iterations,
        output_home=args.output_home,
        execution_mode=args.execution_mode,
    )
    wrapped = hook.attach(agent.executor)
    print(f"✅ Hook 已包装执行器方法: {wrapped or '（无可包装方法，回退 NullM1Hook）'}")
    if not wrapped:
        print("⚠️ 执行器无已知 hook 点，本次运行不采集（分析不受影响）")

    try:
        agent.run()
    except Exception as exc:
        print(f"❌ CellVoyager 运行失败: {exc}", file=sys.stderr)
        return 1

    print(f"✅ 分析完成。采集会话 {session_id}：")
    print(f"   上报 {hook.n_reports} 次，hook 隔离错误 {hook.n_errors} 次")
    print("   查看 verdict:  bio-audit verdict " + session_id)
    print("   查看引擎 trace: bio-audit trace " + session_id)
    print(f"   输出目录: {agent.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
