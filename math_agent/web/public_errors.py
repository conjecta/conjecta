"""User-safe error messages for public web transports.

Detailed provider and gateway errors belong in server logs. They can contain
upstream hostnames, account identifiers, routing details, or operational
configuration that should never become product UI copy.
"""
from __future__ import annotations

from typing import Any


DEFAULT_SOLVE_ERROR = "服务暂时遇到问题，请稍后重试。"


def public_solve_error(exc: BaseException | None = None, *, status_code: int | None = None) -> str:
    """Map an internal failure to stable, provider-neutral user guidance."""
    code = status_code
    if code is None and exc is not None:
        raw_code: Any = getattr(exc, "status_code", None)
        if isinstance(raw_code, int):
            code = raw_code

    if code in {401, 403}:
        return "登录状态已失效，请重新登录后再试。"
    if code == 413:
        return "这次提交的内容过大，请减少附件或缩短内容后重试。"
    if code == 429:
        return "当前请求较多，请稍后重试。"
    return DEFAULT_SOLVE_ERROR
