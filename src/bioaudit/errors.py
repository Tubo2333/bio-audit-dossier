"""错误码体系（B3 API 契约，refactor-plan-v1.1 B1；audit-report A5/A7/A15）。

原则（契约文档 docs/api-contract.md §错误码）：
- 所有 API 输入经 pydantic schema 校验，非法输入**显式报错**（携带错误码），
  不静默降级（如旧实现 act 未知 → 静默回退全量规则）；
- 内部异常统一包装为 :class:`BioAuditError` 后抛出，**异常不裸抛**；
- ``ErrorCode`` 为唯一错误码清单（与契约文档保持一致，测试有守卫）。

错误码一览：
==========  =================  ===========================================
代码         HTTP 状态          含义
==========  =================  ===========================================
bad-request  400               请求负载结构性非法（非数组/非对象、缺 decisions 键）
validation-error 422           字段级校验失败（pydantic），含 human_overrides 越界、
                               轨迹 v2 schema 缺必填字段
paradigm-not-found 404         未知范式（act/paradigm 不在 deg/pan/scrna）
rule-not-found  404             引用的规则 ID 在注册表中不存在（不再静默丢弃）
internal-error  500             未预期内部异常（包装后抛出）
==========  =================  ===========================================
"""

from __future__ import annotations

from typing import Any, Optional


class ErrorCode:
    """错误码常量（单一事实源；契约文档与测试均以此为据）。"""

    BAD_REQUEST = "bad-request"
    VALIDATION_ERROR = "validation-error"
    PARADIGM_NOT_FOUND = "paradigm-not-found"
    RULE_NOT_FOUND = "rule-not-found"
    INTERNAL_ERROR = "internal-error"


#: 全部合法错误码（frozenset；契约测试守卫：文档化集合 == 本集合）
ERROR_CODES: frozenset[str] = frozenset({
    ErrorCode.BAD_REQUEST,
    ErrorCode.VALIDATION_ERROR,
    ErrorCode.PARADIGM_NOT_FOUND,
    ErrorCode.RULE_NOT_FOUND,
    ErrorCode.INTERNAL_ERROR,
})

#: 错误码 → HTTP 状态映射（供未来 MCP/HTTP 暴露层使用；阶段 2 契约）
ERROR_HTTP_STATUS: dict[str, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.PARADIGM_NOT_FOUND: 404,
    ErrorCode.RULE_NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}


class BioAuditError(Exception):
    """结构化 API 错误：code + message + details（可序列化，不裸抛）。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Optional[dict[str, Any]] = None,
        status: Optional[int] = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"未知错误码 {code!r}（合法: {sorted(ERROR_CODES)}）")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status if status is not None else ERROR_HTTP_STATUS[code]

    def to_dict(self) -> dict[str, Any]:
        """契约错误负载：``{"error": {"code", "message", "details"}}``。"""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

    def __str__(self) -> str:  # pragma: no cover - 仅展示
        return f"[{self.code}] {self.message}"


def pydantic_error_details(exc: Exception) -> list[dict[str, Any]]:
    """把 pydantic ValidationError 归一化为契约 details（loc/type/msg）。"""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [{"loc": [], "type": type(exc).__name__, "msg": str(exc)}]
    details: list[dict[str, Any]] = []
    for err in errors():
        details.append({
            "loc": list(err.get("loc", [])),
            "type": err.get("type", "unknown"),
            "msg": err.get("msg", ""),
        })
    return details


def validation_error(
    message: str, exc: Exception, *, details: Optional[dict[str, Any]] = None
) -> BioAuditError:
    """构造 validation-error（携带 pydantic 字段级明细）。"""
    merged = dict(details or {})
    merged["field_errors"] = pydantic_error_details(exc)
    return BioAuditError(ErrorCode.VALIDATION_ERROR, message, details=merged)


__all__ = [
    "ErrorCode",
    "ERROR_CODES",
    "ERROR_HTTP_STATUS",
    "BioAuditError",
    "pydantic_error_details",
    "validation_error",
]
