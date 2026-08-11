from __future__ import annotations

from typing import Any, Literal

SolveMode = Literal["auto", "react"]
VALID_SOLVE_MODES: set[str] = {"auto", "react"}


def resolve_solve_mode(payload: dict[str, Any]) -> SolveMode:
    raw_mode = payload.get("mode")
    if raw_mode is not None:
        mode = str(raw_mode).strip().lower()
        if mode not in VALID_SOLVE_MODES:
            raise ValueError(f"Invalid solve mode: {raw_mode}")
        return mode  # type: ignore[return-value]
    return "auto"
