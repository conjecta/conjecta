from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from math_agent.lean.verifier import LeanVerifier


def _write(tmp_path, source: str):
    path = tmp_path / "Proof.lean"
    path.write_text(source, encoding="utf-8")
    return path


def test_static_gate_accepts_normal_mathlib_proof(tmp_path):
    path = _write(
        tmp_path,
        "import Mathlib\n\ntheorem safe : True := by trivial\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is True
    assert result.blocked_tokens == ()


def test_static_gate_rejects_constant_as_axiom_alias(tmp_path):
    path = _write(
        tmp_path,
        "import Mathlib\nconstant impossible : False\ntheorem bad : False := impossible\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "constant" in result.blocked_tokens


def test_static_gate_rejects_compile_time_io(tmp_path):
    path = _write(
        tmp_path,
        'import Mathlib\nrun_cmd IO.println "executed"\ntheorem t : True := trivial\n',
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "unsafe:run_cmd" in result.blocked_tokens
    assert "unsafe:IO" in result.blocked_tokens
    assert result.failure_kind == "unsafe_source"


def test_static_gate_rejects_elab_rules_open_io_bypass(tmp_path):
    """`elab_rules` + `open IO` must not slip past the `elab` / `IO.` patterns."""
    path = _write(
        tmp_path,
        "open IO in\n"
        "elab_rules : command\n"
        "| `(command| #check $e:term) => do\n"
        '  discard <| FS.writeFile "/tmp/conjecta_pwn" "pwned"\n'
        "#check (1 : Nat)\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "unsafe:elab" in result.blocked_tokens
    assert "unsafe:IO" in result.blocked_tokens
    assert "unsafe:FS" in result.blocked_tokens
    assert result.failure_kind == "unsafe_source"


def test_scan_source_rejects_elab_rules_open_io_bypass():
    code = (
        "open IO in\n"
        "elab_rules : command\n"
        "| `(command| #check $e:term) => do\n"
        '  discard <| FS.writeFile "/tmp/conjecta_pwn" "pwned"\n'
        "#check (1 : Nat)\n"
    )
    result = LeanVerifier(lean_executable="lean").scan_source(code)
    assert result.static_ok is False
    assert "unsafe:elab" in result.blocked_tokens


def test_static_gate_rejects_untrusted_import(tmp_path):
    path = _write(
        tmp_path,
        "import Local.Payload\n\ntheorem t : True := trivial\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "unsafe:import:Local.Payload" in result.blocked_tokens


def test_verify_file_does_not_execute_source_that_fails_static_gate(tmp_path):
    path = _write(tmp_path, "constant impossible : False\n")
    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
    )

    with patch("math_agent.lean.verifier.subprocess.run") as run:
        result = verifier.verify_file(path)

    run.assert_not_called()
    assert result.static_ok is False
    assert result.verification_ok is False


def test_blocked_words_in_comments_and_strings_do_not_trigger(tmp_path):
    path = _write(
        tmp_path,
        'import Mathlib\n-- run_cmd IO.println "x"\ndef text := "axiom constant #eval"\ntheorem t : True := trivial\n',
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is True


def test_comment_markers_inside_strings_cannot_hide_later_commands(tmp_path):
    path = _write(
        tmp_path,
        'import Mathlib\ndef marker := "/-"\nrun_cmd IO.println "executed"\ntheorem t : True := trivial\n',
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "unsafe:run_cmd" in result.blocked_tokens


def test_static_gate_rejects_proof_wanted(tmp_path):
    path = _write(
        tmp_path,
        "import Mathlib\nproof_wanted bad : False\n",
    )
    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)
    assert result.static_ok is False
    assert "proof_wanted" in result.blocked_tokens


def test_static_gate_rejects_exit_command(tmp_path):
    path = _write(
        tmp_path,
        "import Mathlib\n#exit\ntheorem bad : False := by sorry\n",
    )
    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)
    assert result.static_ok is False
    assert "#exit" in result.blocked_tokens


def test_static_gate_rejects_sorryAx(tmp_path):
    path = _write(
        tmp_path,
        "import Mathlib\ntheorem bad : False := sorryAx False\n",
    )
    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)
    assert result.static_ok is False
    assert "sorryAx" in result.blocked_tokens


def test_verify_file_rejects_proof_with_sorryAx_via_axiom_audit(tmp_path):
    """Even if static gate misses sorryAx, the #print axioms audit catches it."""
    path = _write(
        tmp_path,
        "theorem bad : False := by\n  exact sorryAx False\n",
    )

    def fake_run(cmd, **kwargs):
        # First call: main verification succeeds.
        # Second call: audit with #print axioms reveals sorryAx.
        if "audit" in cmd[-1]:
            stdout = "'bad' depends on axioms: [sorryAx, propext]\n"
        else:
            stdout = ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
        # Simulate a static gate that does not already block sorryAx so the
        # post-verification audit path is exercised.
        blocked_tokens=("sorry", "admit", "axiom", "constant", "proof_wanted", "#exit"),
    )
    with patch("math_agent.lean.verifier.subprocess.run", side_effect=fake_run):
        result = verifier.verify_file(path)

    assert result.static_ok is True
    assert result.verification_ok is False
    assert any("sorryAx" in d.message for d in result.diagnostics)


def test_verify_file_accepts_proof_with_allowed_axioms(tmp_path):
    path = _write(
        tmp_path,
        "theorem good : True := by trivial\n",
    )

    def fake_run(cmd, **kwargs):
        if "audit" in cmd[-1]:
            stdout = "'good' depends on axioms: [propext, Quot.sound, Classical.choice]\n"
        else:
            stdout = ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
    )
    with patch("math_agent.lean.verifier.subprocess.run", side_effect=fake_run):
        result = verifier.verify_file(path)

    assert result.accepted is True


def test_verify_file_audit_timeout_updates_returncode_and_diagnostic(tmp_path):
    path = _write(
        tmp_path,
        "theorem bad : False := by\n  exact sorryAx False\n",
    )

    def fake_run(cmd, **kwargs):
        if str(cmd[-1]).endswith(".audit.lean"):
            raise subprocess.TimeoutExpired(cmd, timeout=kwargs.get("timeout", 30))
        return MagicMock(returncode=0, stdout="", stderr="")

    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
        blocked_tokens=("sorry", "admit", "axiom", "constant", "proof_wanted", "#exit"),
    )
    with patch("math_agent.lean.verifier.subprocess.run", side_effect=fake_run):
        result = verifier.verify_file(path)

    assert result.verification_ok is False
    assert result.returncode != 0
    assert any("timed out" in d.message for d in result.diagnostics)


def test_verify_file_audit_command_failure_updates_returncode_and_diagnostic(tmp_path):
    path = _write(
        tmp_path,
        "theorem bad : False := by\n  exact sorryAx False\n",
    )

    def fake_run(cmd, **kwargs):
        if str(cmd[-1]).endswith(".audit.lean"):
            return MagicMock(returncode=1, stdout="", stderr="unknown identifier 'foo'")
        return MagicMock(returncode=0, stdout="", stderr="")

    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
        blocked_tokens=("sorry", "admit", "axiom", "constant", "proof_wanted", "#exit"),
    )
    with patch("math_agent.lean.verifier.subprocess.run", side_effect=fake_run):
        result = verifier.verify_file(path)

    assert result.verification_ok is False
    assert result.returncode == 1
    assert any(
        "command failed" in d.message and "unknown identifier" in d.message
        for d in result.diagnostics
    )


def test_verify_file_preserves_main_diagnostics_when_audit_finds_forbidden_axioms(
    tmp_path,
):
    path = _write(
        tmp_path,
        "theorem bad : False := by\n  exact sorryAx False\n",
    )

    def fake_run(cmd, **kwargs):
        if "audit" in cmd[-1]:
            stdout = "'bad' depends on axioms: [sorryAx, propext]\n"
            return MagicMock(returncode=0, stdout=stdout, stderr="")
        # Main verification succeeds but emits a warning.
        return MagicMock(
            returncode=0,
            stdout="",
            stderr=":1:1: warning: unused variable",
        )

    verifier = LeanVerifier(
        lean_executable="lean",
        cwd=tmp_path,
        prefer_lake_env=False,
        blocked_tokens=("sorry", "admit", "axiom", "constant", "proof_wanted", "#exit"),
    )
    with patch("math_agent.lean.verifier.subprocess.run", side_effect=fake_run):
        result = verifier.verify_file(path)

    assert result.verification_ok is False
    assert any("sorryAx" in d.message for d in result.diagnostics)
    assert any("warning" in d.message for d in result.diagnostics)


def test_char_literal_quote_does_not_mask_blocked_token(tmp_path):
    """A character literal '"' must not open string state and hide `admit`."""
    path = _write(
        tmp_path,
        'def q : Char := \'"\'\ntheorem t : 1 + 1 = 3 := by admit\n',
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "admit" in result.blocked_tokens


def test_char_literal_backslash_does_not_mask_sorry(tmp_path):
    path = _write(
        tmp_path,
        "def q : Char := '\\\\'\ntheorem t : True := by sorry\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "sorry" in result.blocked_tokens


def test_blocked_words_inside_char_literals_do_not_trigger(tmp_path):
    path = _write(
        tmp_path,
        "def a : Char := 's'\ndef b : Char := 'a'\ntheorem t : True := trivial\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is True


def test_char_literal_hex_and_unicode_escapes_do_not_mask_sorry(tmp_path):
    """Longer Lean escapes ('\\xNN', '\\uNNNN') must be parsed as one literal."""
    path = _write(
        tmp_path,
        "def a : Char := '\\x41'\ndef b : Char := '\\u0041'\ntheorem t : True := by sorry\n",
    )

    result = LeanVerifier(lean_executable="lean", cwd=tmp_path).check_static(path)

    assert result.static_ok is False
    assert "sorry" in result.blocked_tokens


def test_static_gate_draft_mode_allows_sorry_and_admit(tmp_path):
    """Draft mode tolerates sorry/admit holes for cheap partial checks."""
    path = _write(
        tmp_path,
        "import Mathlib\n\ntheorem t : True := by sorry\n",
    )

    result = LeanVerifier(
        lean_executable="lean", cwd=tmp_path, allow_sorry=True
    ).check_static(path)

    assert result.static_ok is True
    assert result.blocked_tokens == ()


def test_static_gate_draft_mode_still_blocks_axioms(tmp_path):
    """Draft mode only relaxes sorry/admit; axioms stay blocked."""
    path = _write(
        tmp_path,
        "import Mathlib\naxiom bad : False\ntheorem t : False := bad\n",
    )

    result = LeanVerifier(
        lean_executable="lean", cwd=tmp_path, allow_sorry=True
    ).check_static(path)

    assert result.static_ok is False
    assert "axiom" in result.blocked_tokens


def test_static_gate_draft_mode_still_blocks_sorry_ax(tmp_path):
    """`sorryAx` proves anything directly and stays blocked in draft mode."""
    path = _write(
        tmp_path,
        "theorem t : True := sorryAx True trivial\n",
    )

    result = LeanVerifier(
        lean_executable="lean", cwd=tmp_path, allow_sorry=True
    ).check_static(path)

    assert result.static_ok is False
    assert "sorryAx" in result.blocked_tokens


def test_static_gate_draft_mode_still_blocks_unsafe_source(tmp_path):
    path = _write(
        tmp_path,
        'import Mathlib\nrun_cmd IO.println "x"\ntheorem t : True := by sorry\n',
    )

    result = LeanVerifier(
        lean_executable="lean", cwd=tmp_path, allow_sorry=True
    ).check_static(path)

    assert result.static_ok is False
    assert "unsafe:run_cmd" in result.blocked_tokens
