import asyncio
import os
from unittest.mock import patch

import pytest

import math_agent.tools.python_sandbox as python_sandbox
from math_agent.tools.python_sandbox import run_python


@pytest.mark.asyncio
async def test_print_arithmetic():
    result = await run_python("print(2 + 2)")
    assert result.success
    assert "4" in result.output


@pytest.mark.asyncio
async def test_single_expression_auto_print():
    result = await run_python("2 + 2")
    assert result.success
    assert "4" in result.output


@pytest.mark.asyncio
async def test_sympy_simplify():
    result = await run_python(
        "import sympy\nx = sympy.symbols('x')\nprint(sympy.simplify((x**2 - 1)/(x - 1)))"
    )
    assert result.success
    assert "x+1" in result.output.replace(" ", "")


@pytest.mark.asyncio
async def test_mpmath_high_precision():
    result = await run_python(
        "import mpmath as mp\nmp.mp.dps = 30\nprint(mp.nstr(mp.zeta(2), 12))"
    )
    assert result.success
    assert "1.64493406685" in result.output


@pytest.mark.asyncio
async def test_loop_search():
    code = (
        "found=None\n"
        "for n in range(1,20):\n"
        "    if n*n==49:\n"
        "        found=n\n"
        "        break\n"
        "print(found)"
    )
    result = await run_python(code)
    assert result.success
    assert "7" in result.output


@pytest.mark.asyncio
async def test_blocks_os_import():
    result = await run_python("import os\nprint(os.getcwd())")
    assert not result.success
    lowered = result.output.lower()
    assert "os" in lowered or "not allowed" in lowered or "importerror" in lowered


@pytest.mark.asyncio
async def test_timeout_kills_loop():
    result = await run_python("while True:\n    pass", timeout=1.0)
    assert not result.success
    assert result.timed_out or "timeout" in result.output.lower()


@pytest.mark.asyncio
async def test_rejects_empty_and_oversized():
    empty = await run_python("   ")
    assert not empty.success
    huge = await run_python("x=1\n" + ("y=1\n" * 20000))
    assert not huge.success


# ---------------------------------------------------------------------------
# Regression tests for demonstrated sandbox escape chains.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        # Leaking sys/os through attributes of whitelisted modules.
        "import fractions\nprint(fractions.sys.version)",
        "import random\nprint(random._os.system('id'))",
        # Using sympy's import helper to load os.
        "import sympy\nprint(sympy.external.import_module('os').system('id'))",
        # Dunder introspection bypasses.
        "print((lambda: None).__getattribute__('__globals__'))",
        "print((lambda: None).__getattr__('__globals__'))",
        # Direct builtins / sys / os names.
        "import sys\nprint(sys.version)",
        "import os\nprint(os.getcwd())",
        "print(__builtins__)",
        # __dict__ bypasses (Critical review finding).
        "import fractions\nprint(fractions.__dict__['sys'].version)",
        "import random\nprint(random.__dict__['_os'].system('id'))",
        "import sympy\nprint(sympy.external.__dict__['import_module']('os').system('id'))",
        # getattr with forbidden string attribute (Critical review finding).
        "print(getattr(fractions, 'sys').version)",
    ],
)
async def test_escape_chains_are_blocked(code):
    result = await run_python(code)
    assert not result.success
    assert "not allowed" in result.output.lower()


@pytest.mark.asyncio
async def test_normal_fractions_still_works():
    result = await run_python(
        "from fractions import Fraction\nprint(Fraction(1, 2) + Fraction(1, 3))"
    )
    assert result.success
    assert "5/6" in result.output.replace(" ", "")


@pytest.mark.asyncio
async def test_normal_sympy_solve_still_works():
    result = await run_python(
        "import sympy\nx = sympy.symbols('x')\nprint(sympy.solve(x**2 - 4, x))"
    )
    assert result.success
    assert "-2" in result.output and "2" in result.output


@pytest.mark.asyncio
async def test_cancelled_error_kills_child():
    """Cancelling the parent task must terminate and reap the subprocess."""

    started = asyncio.Event()
    child_proc = None
    original_create = python_sandbox.asyncio.create_subprocess_exec

    async def _recording_create(*args, **kwargs):
        nonlocal child_proc
        proc = await original_create(*args, **kwargs)
        child_proc = proc
        started.set()
        return proc

    with patch.object(python_sandbox.asyncio, "create_subprocess_exec", _recording_create):
        task = asyncio.create_task(
            run_python("while True:\n    print('x')", timeout=60.0)
        )
        await asyncio.wait_for(started.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert child_proc is not None, "subprocess was never started"
    # The implementation must wait on the child after killing it.
    assert child_proc.returncode is not None, "subprocess was not reaped"
    # And the OS process must actually be gone.
    with pytest.raises((ProcessLookupError, OSError)):
        os.kill(child_proc.pid, 0)


# ---------------------------------------------------------------------------
# Regression tests for ImportFrom bypasses.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        # Importing forbidden names from otherwise allowed modules.
        "from fractions import sys\nprint(sys.version)",
        "from random import _os\nprint(_os.system('id'))",
        "from sympy.external import import_module\nprint(import_module('os').system('id'))",
        # Wildcard imports can leak unknown dangerous attributes.
        "from fractions import *\nprint(sys.version)",
    ],
)
async def test_from_import_escape_variants_blocked(code):
    """`from ... import ...` must not bypass the sandbox."""
    result = await run_python(code)
    assert not result.success
    assert "not allowed" in result.output.lower()


@pytest.mark.asyncio
async def test_normal_from_imports_still_work():
    """Common math imports continue to work after the ImportFrom hardening."""
    result = await run_python(
        "from fractions import Fraction\n"
        "from collections import defaultdict\n"
        "from sympy import solve\n"
        "print(Fraction(1, 2) + Fraction(1, 3))\n"
        "print(solve.__name__)\n"
        "d = defaultdict(int)\n"
        "d['x'] += 1\n"
        "print(d['x'])"
    )
    assert result.success
    assert "5/6" in result.output.replace(" ", "")
    assert "solve" in result.output
    assert "1" in result.output


# ---------------------------------------------------------------------------
# Network access: re/json/urllib allowed, internal targets blocked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_and_json_imports_allowed():
    result = await run_python(
        "import re, json\n"
        "print(re.findall(r'\\d+', 'a1b22'))\n"
        "print(json.dumps({'a': 1}))"
    )
    assert result.success
    assert "['1', '22']" in result.output
    assert '"a": 1' in result.output


@pytest.mark.asyncio
async def test_urllib_import_allowed():
    result = await run_python("import urllib.request\nprint('ok')")
    assert result.success
    assert "ok" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, marker",
    [
        ("http://127.0.0.1:9/", "private or reserved"),
        ("http://192.168.1.1/", "private or reserved"),
        ("http://10.0.0.1/", "private or reserved"),
        ("http://100.100.100.200/latest/meta-data/", "private or reserved"),
        ("http://169.254.169.254/", "private or reserved"),
        ("http://user:pass@example.com/", "embedded credentials"),
        ("file:///etc/passwd", "Blocked URL scheme"),
    ],
)
async def test_urllib_guard_blocks_internal_targets(url, marker):
    code = (
        "import urllib.request\n"
        f"urllib.request.urlopen({url!r}, timeout=5)"
    )
    result = await run_python(code)
    assert not result.success
    assert marker in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        # Raw sockets via urllib internals would bypass the urlopen guard.
        "import urllib.request as u\nu.socket.socket(('100.100.100.200', 80))",
        "import urllib.request as u\nu.http.client.HTTPConnection('100.100.100.200')",
        "import urllib.request as u\nu.request.os.system('id')",
    ],
)
async def test_urllib_escape_chains_blocked(code):
    result = await run_python(code)
    assert not result.success
    assert "not allowed" in result.output.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        # IPv4-mapped IPv6 literals must be normalized before the blocked-range
        # check (mirrors net_safety.normalize_ip).
        "http://[::ffff:127.0.0.1]:9/",
        "http://[::ffff:169.254.169.254]/",
        # Unspecified and multicast addresses are blocked outright.
        "http://0.0.0.0:9/",
        "http://224.0.0.1:9/",
    ],
)
async def test_urllib_guard_blocks_normalized_special_ips(url):
    code = "import urllib.request\n" f"urllib.request.urlopen({url!r}, timeout=5)"
    result = await run_python(code)
    assert not result.success
    assert "private or reserved" in result.output
