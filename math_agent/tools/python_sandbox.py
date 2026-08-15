"""Restricted one-shot Python subprocess sandbox for the compute tool.

Security model: the AST validation below is a first-layer UX / early-error
check only — it is NOT the security boundary (allowed native extensions such
as numpy/sympy make static checks bypassable in principle).  The boundary is
bubblewrap isolation via :mod:`math_agent.tools.isolated_runner`, applied
when ``CONJECTA_SANDBOX_ISOLATION`` is ``bwrap``, or ``auto`` with bwrap
installed.  When bwrap is unavailable the child runs with rlimits + AST
checks only and a WARNING is logged once; treat that fallback as
defense-in-depth, not isolation.  See SECURITY.md.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from math_agent.tools import isolated_runner

_MAX_CODE_CHARS = 16_384
_MAX_OUTPUT_CHARS = 32_768
_DEFAULT_TIMEOUT = 15.0

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
        "heapq",
        "bisect",
        "random",
        "numpy",
        "sympy",
        "mpmath",
        "re",
        "string",
        "json",
        # Network access is allowed only through the guarded urlopen installed
        # in the child preamble: public http(s) URLs pass; private, loopback,
        # link-local, and reserved ranges (incl. cloud metadata endpoints) are
        # rejected before any connection is made.  Under bwrap isolation the
        # child has no network namespace at all, so this guard only matters
        # for the non-isolated fallback.
        "urllib",
    }
)

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
    }
)

# Attribute names that must never be reachable from sandboxed code.  These are
# the stepping stones used in demonstrated escape chains (fractions.sys,
# random._os, sympy.external.import_module, __getattribute__("__globals__"), …)
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
        # urllib internals that would hand user code a raw connection and
        # bypass the guarded urlopen (urllib.request.socket / .http).
        "socket",
        "http",
    }
)


@dataclass(frozen=True)
class SandboxResult:
    success: bool
    output: str
    timed_out: bool = False


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _module_allowed(name: str) -> bool:
    root = name.split(".", 1)[0]
    return root in _ALLOWED_MODULES


def _is_dunder_string(node: ast.AST) -> bool:
    """Return True if ``node`` is a string literal that starts/ends with __."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("__")
        and node.value.endswith("__")
    )


def _is_forbidden_attr_string(value: str) -> bool:
    """Return True if ``value`` is a forbidden attribute name or any dunder."""
    return value in _FORBIDDEN_ATTRS or (
        value.startswith("__") and value.endswith("__")
    )


def _validate_code(code: str) -> str | None:
    """Return an error message if code is rejected, else None."""
    stripped = code.strip()
    if not stripped:
        return "compute requires non-empty Python code."
    if len(code) > _MAX_CODE_CHARS:
        return (
            f"compute code is too long ({len(code)} chars; max {_MAX_CODE_CHARS})."
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
            # Block dangerous attribute names used as stepping stones.
            if node.attr in _FORBIDDEN_ATTRS:
                return f"Attribute '{node.attr}' is not allowed."
        elif isinstance(node, ast.Call):
            func = node.func
            # Block getattr(obj, 'forbidden_attr') even if the getattr name
            # itself were somehow shadowed or missed.
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
            # Block __getattribute__/__getattr__ with dunder string arguments
            # even if the method itself were somehow reachable.
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


def _prepare_source(code: str) -> str:
    stripped = code.strip()
    try:
        ast.parse(stripped, mode="eval")
    except SyntaxError:
        return stripped
    return f"print(repr({stripped}))"


def _child_runner_source(user_source: str) -> str:
    allowed = sorted(_ALLOWED_MODULES)
    return textwrap.dedent(
        f"""
        import builtins
        import sys

        try:
            import resource
            # ~512 MiB address space; soft CPU seconds as a backstop to asyncio timeout.
            resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        except Exception:
            pass

        # Restrict the child so the runner's real namespace and the project
        # source tree are not reachable.  Keep only standard library / venv
        # site-packages paths so allowed imports (sympy, fractions, …) still work.
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

        # Guarded network egress: patch urllib.request.urlopen so user code can
        # fetch public http(s) URLs while private/loopback/reserved targets are
        # rejected pre-connection.  This only matters for the non-isolated
        # fallback: under bwrap the child has no network at all.
        #
        # The blocked-IP logic mirrors math_agent.net_safety (normalize_ip +
        # is_blocked_ip), which is the source of truth — the child prunes
        # sys.path and cannot import the project, so the same checks are
        # inlined here.  Keep the two in sync.
        import ipaddress as _ipaddress
        import socket as _socket
        import urllib.request as _urllib_request
        from urllib.parse import urlparse as _urlparse

        _BLOCKED_NETWORKS = [
            _ipaddress.ip_network(net)
            for net in (
                "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                "127.0.0.0/8", "::1/128", "169.254.0.0/16", "fe80::/10",
                "fc00::/7", "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24",
                "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
                "240.0.0.0/4", "224.0.0.0/4",
            )
        ]

        def _normalize_ip(value):
            # Map IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) back to IPv4 so the
            # IPv4 blocked ranges apply — same as net_safety.normalize_ip.
            ip = _ipaddress.ip_address(value)
            if isinstance(ip, _ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                return ip.ipv4_mapped
            return ip

        def _is_blocked_ip(value):
            # Same semantics as net_safety.is_blocked_ip.
            try:
                ip = _normalize_ip(value)
            except ValueError:
                return True
            if ip.is_unspecified or ip.is_multicast:
                return True
            return any(
                ip in net for net in _BLOCKED_NETWORKS
                if ip.version == net.version
            )

        def _url_blocked(url):
            target = url if isinstance(url, str) else url.full_url
            parsed = _urlparse(target)
            if parsed.scheme.lower() not in ("http", "https"):
                raise ValueError("Blocked URL scheme: " + (parsed.scheme or "<none>"))
            if parsed.username or parsed.password:
                raise ValueError("URLs with embedded credentials are not allowed.")
            host = parsed.hostname or ""
            if not host:
                raise ValueError("URL must include a host.")
            try:
                addresses = [host.strip("[]")]
                _ipaddress.ip_address(addresses[0])
            except ValueError:
                try:
                    infos = _socket.getaddrinfo(host, None, type=_socket.SOCK_STREAM)
                except _socket.gaierror:
                    raise ValueError("Could not resolve URL host: " + host)
                addresses = [info[4][0] for info in infos]
            for address in addresses:
                if _is_blocked_ip(address):
                    raise ValueError(
                        "URL resolves to a private or reserved network address."
                    )

        _real_urlopen = _urllib_request.urlopen

        def _guarded_urlopen(url, *args, **kwargs):
            _url_blocked(url)
            return _real_urlopen(url, *args, **kwargs)

        _urllib_request.urlopen = _guarded_urlopen

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
        globals_dict = {{"__builtins__": safe_builtins, "__name__": "__main__"}}

        # Capture sys helpers, then remove the real builtins/sys/os handles from
        # the runner module namespace so user code cannot reach them.
        _stderr = sys.stderr
        _exit = sys.exit
        del builtins, sys, _real_import, _safe_import, _ALLOWED, _FORBIDDEN, safe_builtins

        try:
            exec(compile(_CODE, "<compute>", "exec"), globals_dict, globals_dict)
        except Exception as exc:
            print(f"{{type(exc).__name__}}: {{exc}}", file=_stderr)
            _exit(1)
        """
    )


async def run_python(code: str, *, timeout: float = _DEFAULT_TIMEOUT) -> SandboxResult:
    """Run Python code in a short-lived restricted subprocess.

    The child is wrapped in bwrap (no network, nothing writable) when
    isolation is active; otherwise it runs with rlimits + AST checks only
    and a warning is logged once.  rlimits and the killpg timeout act as a
    second layer in all modes.
    """
    err = _validate_code(code)
    if err is not None:
        return SandboxResult(success=False, output=err)

    prepared = _prepare_source(code)
    runner = _child_runner_source(prepared)

    argv = [sys.executable, "-c", runner]
    env: dict[str, str] | None = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    if isolated_runner.isolation_active():
        # bwrap controls the child env via --clearenv/--setenv.
        argv = isolated_runner.build_bwrap_argv(
            argv=argv,
            writable_dirs=[],
            workdir=Path("/tmp"),
        )
        env = None
    else:
        isolated_runner.warn_no_isolation_once("python_sandbox")

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
    except OSError as exc:
        return SandboxResult(success=False, output=f"Failed to start sandbox: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_process_group(proc)
        return SandboxResult(
            success=False,
            output=f"Compute timed out after {timeout:g}s.",
            timed_out=True,
        )
    except asyncio.CancelledError:
        # The parent task was cancelled; terminate the child so it cannot hang
        # filling stdout/stderr, then re-raise the cancellation.
        await _kill_process_group(proc)
        raise

    stdout = _truncate(stdout_b.decode("utf-8", errors="replace").strip())
    stderr = _truncate(stderr_b.decode("utf-8", errors="replace").strip())
    if proc.returncode != 0:
        detail = stderr or stdout or f"exited with code {proc.returncode}"
        return SandboxResult(success=False, output=detail)

    if not stdout:
        return SandboxResult(
            success=True,
            output="(no output; use print(...) to return results)",
        )
    if stderr:
        return SandboxResult(success=True, output=f"{stdout}\n[stderr]\n{stderr}")
    return SandboxResult(success=True, output=stdout)


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=2.0)
