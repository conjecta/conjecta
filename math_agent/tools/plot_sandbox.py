"""Restricted one-shot matplotlib sandbox for the plot_figure tool.

A looser sibling of ``python_sandbox.run_python``: user code may import
matplotlib/numpy and build figures with ``matplotlib.pyplot``.  The code must
NOT call ``savefig``/``show`` — the child runner saves every open figure into
``out_dir`` itself, which keeps all file writes confined to that directory.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

_MAX_CODE_CHARS = 16_384
_MAX_OUTPUT_CHARS = 32_768
_DEFAULT_TIMEOUT = 30.0
_MAX_FIGURES = 8

_FIGURES_MARKER = "__PLOT_FIGURES__"

_ALLOWED_MODULES = frozenset(
    {
        "math",
        "cmath",
        "decimal",
        "fractions",
        "statistics",
        "itertools",
        "functools",
        "collections",
        "random",
        "numpy",
        "sympy",
        "mpmath",
        "matplotlib",
    }
)

# Same stepping stones as python_sandbox, plus savefig/show: figures are saved
# by the runner so user code never needs a file handle.
_FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "open",
        "__import__",
        "compile",
        "breakpoint",
        "input",
        "memoryview",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "__builtins__",
        "__spec__",
        "__loader__",
        "savefig",
        "show",
    }
)

_FORBIDDEN_ATTRS = frozenset(
    {
        "sys",
        "os",
        "nt",
        "posix",
        "winreg",
        "builtins",
        "__builtins__",
        "subprocess",
        "importlib",
        "import_module",
        "__import__",
        "__subclasses__",
        "__globals__",
        "__code__",
        "__getattribute__",
        "__getattr__",
        "__dict__",
        "_os",
        "_sys",
        "savefig",
        "show",
    }
)


@dataclass(frozen=True)
class PlotResult:
    success: bool
    output: str
    figures: list[str] = field(default_factory=list)
    timed_out: bool = False


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _module_allowed(name: str) -> bool:
    root = name.split(".", 1)[0]
    return root in _ALLOWED_MODULES


def _is_dunder_string(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("__")
        and node.value.endswith("__")
    )


def _is_forbidden_attr_string(value: str) -> bool:
    return value in _FORBIDDEN_ATTRS or (
        value.startswith("__") and value.endswith("__")
    )


def _validate_code(code: str) -> str | None:
    """Return an error message if code is rejected, else None."""
    stripped = code.strip()
    if not stripped:
        return "plot_figure requires non-empty Python code."
    if len(code) > _MAX_CODE_CHARS:
        return (
            f"plot code is too long ({len(code)} chars; max {_MAX_CODE_CHARS})."
        )
    try:
        tree = ast.parse(stripped)
    except SyntaxError as exc:
        return f"SyntaxError: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    return f"Import not allowed: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not _module_allowed(mod):
                return f"Import not allowed: {mod or '*'}"
            for alias in node.names:
                if alias.name == "*":
                    return "Wildcard imports are not allowed."
                if _is_forbidden_attr_string(alias.name):
                    return (
                        f"Import of '{alias.name}' from '{mod}' is not allowed."
                    )
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            return f"Use of '{node.id}' is not allowed."
        elif isinstance(node, ast.Attribute):
            if node.attr in _FORBIDDEN_ATTRS:
                if node.attr in {"savefig", "show"}:
                    return (
                        f"Do not call '{node.attr}': figures are saved "
                        "automatically by the plot_figure tool."
                    )
                return f"Attribute '{node.attr}' is not allowed."
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and _is_forbidden_attr_string(node.args[1].value)
            ):
                return (
                    f"getattr with forbidden attribute name "
                    f"'{node.args[1].value}' is not allowed."
                )
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_NAMES:
                return f"Call to '{func.id}' is not allowed."
            if isinstance(func, ast.Attribute) and func.attr in {
                "__getattribute__",
                "__getattr__",
            }:
                for arg in node.args:
                    if _is_dunder_string(arg):
                        return (
                            "Use of __getattribute__/__getattr__ with dunder "
                            "names is not allowed."
                        )
    return None


def _child_runner_source(user_source: str, out_dir: str) -> str:
    allowed = sorted(_ALLOWED_MODULES)
    return textwrap.dedent(
        f"""
        import builtins
        import json
        import sys

        try:
            import resource
            # ~2 GiB address space (numpy+matplotlib need more than compute),
            # soft CPU seconds as a backstop to the asyncio timeout.
            resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
            resource.setrlimit(
                resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024)
            )
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except Exception:
            pass

        # Restrict the child so the runner's real namespace and the project
        # source tree are not reachable.  Keep only standard library / venv
        # site-packages paths so allowed imports (numpy, matplotlib, …) work.
        _keep = []
        for _p in sys.path:
            _norm = _p.replace("\\\\", "/")
            if not _norm:
                continue
            if (
                _norm.endswith(".zip")
                or "/lib/python" in _norm
                or "\\\\lib\\\\python" in _norm
                or "/lib64/python" in _norm
                or "\\\\lib64\\\\python" in _norm
                or "/lib/python3" in _norm
                or "\\\\lib\\\\python3" in _norm
                or "site-packages" in _norm
            ):
                _keep.append(_p)
        sys.path = _keep

        _ALLOWED = {allowed!r}
        _real_import = builtins.__import__

        def _safe_import(
            name,
            globals=None,
            locals=None,
            fromlist=(),
            level=0,
            _allowed=_ALLOWED,
            _real_import=_real_import,
        ):
            root = name.split(".", 1)[0]
            if root not in _allowed:
                raise ImportError(f"Import not allowed: {{name}}")
            return _real_import(name, globals, locals, fromlist, level)

        _FORBIDDEN = {{
            "eval", "exec", "open", "__import__", "compile", "breakpoint",
            "input", "memoryview", "globals", "locals", "vars",
            "getattr", "setattr", "delattr", "hasattr",
        }}
        safe_builtins = {{
            k: v for k, v in vars(builtins).items()
            if k not in _FORBIDDEN and not k.startswith("_")
        }}
        safe_builtins["__import__"] = _safe_import
        safe_builtins["__build_class__"] = builtins.__build_class__
        safe_builtins["__name__"] = "__main__"

        _CODE = {user_source!r}
        _OUT_DIR = {out_dir!r}
        globals_dict = {{"__builtins__": safe_builtins, "__name__": "__main__"}}

        _stderr = sys.stderr
        _stdout = sys.stdout
        _exit = sys.exit
        del builtins, _real_import, _safe_import, _ALLOWED, _FORBIDDEN, safe_builtins

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as _plt

        try:
            exec(compile(_CODE, "<plot_figure>", "exec"), globals_dict, globals_dict)
        except Exception as exc:
            print(f"{{type(exc).__name__}}: {{exc}}", file=_stderr)
            _exit(1)

        _saved = []
        for _num in _plt.get_fignums()[: {_MAX_FIGURES}]:
            _fig = _plt.figure(_num)
            _name = f"fig-{{_num}}.png"
            _fig.savefig(
                _OUT_DIR + "/" + _name, dpi=150, bbox_inches="tight"
            )
            _saved.append(_name)
        _stdout.write("\\n{_FIGURES_MARKER}" + json.dumps(_saved) + "\\n")
        _stdout.flush()
        """
    )


def _parse_figures(stdout: str) -> tuple[str, list[str]]:
    """Split the runner's figure manifest line off the user-visible stdout."""
    lines = stdout.splitlines()
    figures: list[str] = []
    kept: list[str] = []
    for line in lines:
        if line.startswith(_FIGURES_MARKER):
            try:
                payload = json.loads(line[len(_FIGURES_MARKER) :])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, list):
                figures = [str(name) for name in payload]
            continue
        kept.append(line)
    return "\n".join(kept).strip(), figures


async def run_plot(
    code: str, *, out_dir: Path, timeout: float = _DEFAULT_TIMEOUT
) -> PlotResult:
    """Run matplotlib code in a short-lived restricted subprocess.

    Every figure left open by the code is saved as ``fig-<n>.png`` inside
    ``out_dir``; the returned ``figures`` lists the saved file names.
    """
    err = _validate_code(code)
    if err is not None:
        return PlotResult(success=False, output=err)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mpl_config = out_dir / ".mplconfig"
    mpl_config.mkdir(parents=True, exist_ok=True)

    runner = _child_runner_source(code.strip(), str(out_dir.resolve()))

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            runner,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": "",
                "HOME": os.environ.get("HOME", "/tmp"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "MPLCONFIGDIR": str(mpl_config.resolve()),
            },
        )
    except OSError as exc:
        return PlotResult(success=False, output=f"Failed to start sandbox: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_process_group(proc)
        return PlotResult(
            success=False,
            output=f"Plot timed out after {timeout:g}s.",
            timed_out=True,
        )
    except asyncio.CancelledError:
        await _kill_process_group(proc)
        raise

    stdout_raw = _truncate(stdout_b.decode("utf-8", errors="replace").strip())
    stderr = _truncate(stderr_b.decode("utf-8", errors="replace").strip())
    stdout, figures = _parse_figures(stdout_raw)

    if proc.returncode != 0:
        detail = stderr or stdout or f"exited with code {proc.returncode}"
        return PlotResult(success=False, output=detail)

    if not figures:
        hint = (
            "No figures were created; build the plot with matplotlib.pyplot "
            "(e.g. plt.plot(...)) and do not call savefig/show."
        )
        output = f"{stdout}\n{hint}" if stdout else hint
        return PlotResult(success=False, output=output)

    if stderr:
        stdout = f"{stdout}\n[stderr]\n{stderr}" if stdout else f"[stderr]\n{stderr}"
    return PlotResult(success=True, output=stdout, figures=figures)


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
