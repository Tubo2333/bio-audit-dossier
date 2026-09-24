"""采集包（窗口 C 落地：M1/M3 采集 + 交叉验证 + verdict 状态位）。

设计依据：docs/specs/2026-08-13-trajectory-capture-design-v1.md（定稿）+
refactor-plan-v1.1（B3/B4/B5）：
- M1 主动上报：CellVoyager hook（wrapper，异常隔离）+ session 白名单 +
  幂等键 + WAL 追加持久化/崩溃恢复（B3）；
- M3 产物解析：signatures 驱动（capture/signatures.yaml）+ 上下文三级可信源
  + 禁猜规则（F6：绝不伪造数字）；
- 交叉验证器：一致/虚报/漏报/未验证 四类判定（F4 进报告不进 reward）+
  多实例建模（B5）；
- verdict 状态位：provisional → final / revoked（B4/P3），报告与 reward
  只消费 final。

模块一览：
- models / signatures / m3_parser：M3 解析
- verdict / cross_validator：verdict 生命周期与交叉验证
- session / wal / m1_reporter / cellvoyager_hook：M1 上报
"""

CAPTURE_VERSION = "1.0.0"

from bioaudit.capture.cross_validator import (  # noqa: E402
    STATUS_CONSISTENT,
    STATUS_FALSE_NEGATIVE,
    STATUS_FALSE_POSITIVE,
    STATUS_UNVERIFIED,
    AlignmentRecord,
    CrossValidationResult,
    CrossValidator,
    final_trajectory,
)
from bioaudit.capture.models import (  # noqa: E402
    PROVENANCE_SOURCE_M1,
    PROVENANCE_SOURCE_M3,
    TRUST_CALL_ARG,
    TRUST_DATA_METADATA,
    TRUST_DECLARED,
    TRUST_UNVERIFIED,
    CapturedDecision,
    DecisionProvenance,
    ParseResult,
    UncertainCandidate,
)
from bioaudit.capture.verdict import (  # noqa: E402
    VerdictRecord,
    VerdictStatus,
    VerdictStore,
    VerdictTransitionError,
)

__all__ = [
    "CAPTURE_VERSION",
    # M3
    "CapturedDecision",
    "DecisionProvenance",
    "UncertainCandidate",
    "ParseResult",
    "PROVENANCE_SOURCE_M1",
    "PROVENANCE_SOURCE_M3",
    "TRUST_CALL_ARG",
    "TRUST_DATA_METADATA",
    "TRUST_DECLARED",
    "TRUST_UNVERIFIED",
    # verdict / 交叉验证
    "VerdictRecord",
    "VerdictStatus",
    "VerdictStore",
    "VerdictTransitionError",
    "AlignmentRecord",
    "CrossValidationResult",
    "CrossValidator",
    "STATUS_CONSISTENT",
    "STATUS_FALSE_POSITIVE",
    "STATUS_FALSE_NEGATIVE",
    "STATUS_UNVERIFIED",
    "final_trajectory",
]
