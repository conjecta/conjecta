"""Minimal pre/post tool-use hook registry for the ReAct loop.

Hooks are process-global synchronous callables. A pre-tool hook receives
``(action_name, args)`` before the tool runs and may raise to veto the call —
the exception message is surfaced to the model as the observation and the
call does not consume the tool budget. A post-tool hook receives
``(action_name, args, observation)`` after the tool runs and is a pure
observer: its own exceptions are logged and swallowed so a broken hook can
never crash the solve loop.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("math_agent.agent.hooks")

PreToolHook = Callable[[str, dict[str, Any]], None]
PostToolHook = Callable[[str, dict[str, Any], Any], None]

_pre_tool_hooks: list[PreToolHook] = []
_post_tool_hooks: list[PostToolHook] = []


def register_pre_tool_hook(fn: PreToolHook) -> PreToolHook:
    if not callable(fn):
        raise TypeError("Pre-tool hook must be callable.")
    _pre_tool_hooks.append(fn)
    return fn


def register_post_tool_hook(fn: PostToolHook) -> PostToolHook:
    if not callable(fn):
        raise TypeError("Post-tool hook must be callable.")
    _post_tool_hooks.append(fn)
    return fn


def clear_hooks() -> None:
    _pre_tool_hooks.clear()
    _post_tool_hooks.clear()


def run_pre_tool_hooks(action_name: str, args: dict[str, Any]) -> None:
    """Run pre-tool hooks in registration order; the first raise vetoes the call."""
    for fn in list(_pre_tool_hooks):
        fn(action_name, args)


def run_post_tool_hooks(
    action_name: str, args: dict[str, Any], observation: Any
) -> None:
    """Run post-tool observers; hook failures are logged and swallowed."""
    for fn in list(_post_tool_hooks):
        try:
            fn(action_name, args, observation)
        except Exception:
            log.exception("Post-tool hook failed for action %s", action_name)
