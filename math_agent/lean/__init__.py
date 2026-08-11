from math_agent.lean.result import LeanResult
from math_agent.lean.runner import LeanRunner

__all__ = ["LeanRunner", "LeanResult", "LeanCodegen"]


def __getattr__(name: str):
    if name == "LeanCodegen":
        from math_agent.lean.codegen import LeanCodegen

        return LeanCodegen
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
