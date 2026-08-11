"""Build the miniF2F v2 (Lean 4) acceptance-gate datasets for the eval harness.

Source: ``data/benchmarks/_src/miniF2F-lean4`` (the LeanDojo ``yangky11/
miniF2F-lean4`` port of openai/miniF2F, pinned to mathlib v4.24.0). This
project verifies against mathlib4 v4.30.0 (``.lean_workspace/lean-toolchain``),
and its Lean critic forbids the umbrella ``import Mathlib`` (build-timeout /
RAM risk), so every statement is re-elaborated against the local toolchain
with a precise module import set before it lands in the dataset.

Outputs (same per-case jsonl schema as ``data/eval/formal_hard.jsonl``)::

    data/eval/minif2f_valid.jsonl   iteration split
    data/eval/minif2f_test.jsonl    milestone-acceptance split
    data/eval/minif2f_build_report.json   dropped statements + provenance

Usage:
    python -m math_agent.evaluation.minif2f build [--jobs N] [--batch-size N]
        [--no-verify] [--out-dir data/eval]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "data/benchmarks/_src/miniF2F-lean4/MiniF2F"
WORKSPACE_DIR = REPO_ROOT / ".lean_workspace"
NL_DATASETS = {
    "valid": REPO_ROOT / "data/benchmarks/formal/minif2f_valid.jsonl",
    "test": REPO_ROOT / "data/benchmarks/formal/minif2f_test.jsonl",
}
DEFAULT_OUT_DIR = REPO_ROOT / "data/eval"
# Crash-safety: elaboration results are appended here one row per statement
# as they complete, so a killed run resumes instead of restarting from zero.
# Kept under ``_src`` so the benchmark-suite schema tests (which rglob
# ``data/benchmarks/**/*.jsonl`` excluding ``_src``) ignore it.
DEFAULT_CACHE_PATH = REPO_ROOT / "data/benchmarks/_src/minif2f_verify_cache.jsonl"

# Precise (non-umbrella) import set covering the miniF2F statement vocabulary
# (ℝ/ℕ/ℤ/ℚ/ℂ arithmetic, ∑/∏ over Finset ranges and intervals, trig/log/sqrt,
# floor/ceil, Nat primes/factorial/choose/digits/divisors). Verified to
# elaborate every shipped statement against mathlib4 v4.30.0. The umbrella
# `import Mathlib` is deliberately not used: this host cannot load it.
STANDARD_IMPORTS = (
    "Mathlib.Tactic.Common",
    "Mathlib.Data.Real.Basic",
    "Mathlib.Data.Complex.Basic",
    "Mathlib.Data.Int.Basic",
    "Mathlib.Data.Rat.Defs",
    "Mathlib.Algebra.BigOperators.Group.Finset.Basic",
    "Mathlib.Data.Finset.Interval",
    "Mathlib.Analysis.SpecialFunctions.Trigonometric.Basic",
    "Mathlib.Analysis.SpecialFunctions.Log.Basic",
    "Mathlib.Analysis.SpecialFunctions.Log.Base",
    "Mathlib.Analysis.SpecialFunctions.Sqrt",
    "Mathlib.Analysis.SpecialFunctions.Pow.Real",
    "Mathlib.NumberTheory.Real.Irrational",
    "Mathlib.Algebra.Order.Floor.Defs",
    "Mathlib.Algebra.Order.Floor.Ring",
    "Mathlib.Data.Nat.Choose.Basic",
    "Mathlib.Data.Nat.Prime.Basic",
    "Mathlib.Data.Nat.Factorial.Basic",
    "Mathlib.Data.Nat.Digits.Defs",
    "Mathlib.Data.Nat.GCD.Basic",
    "Mathlib.Data.Nat.Sqrt",
    "Mathlib.NumberTheory.Divisors",
    "Mathlib.Order.Filter.Basic",
    "Mathlib.Topology.Basic",
    "Mathlib.Data.List.Pairwise",
)

# Extra modules tried one at a time for statements the standard set rejects
# (e.g. ℝ≥0 / NNReal.sqrt users). Each must exist in the pinned mathlib.
FALLBACK_IMPORTS = (
    "Mathlib.Data.NNReal.Basic",
    "Mathlib.Analysis.SpecialFunctions.Pow.NNReal",
    "Mathlib.Analysis.Convex.NNReal",
)

# Same `open` line as upstream miniF2F-lean4 (valid under mathlib4 v4.30.0).
OPEN_LINE = "open BigOperators Real Nat Topology Rat"

_THEOREM_RE = re.compile(
    r"^theorem\s+(?P<name>\w+)\b(?P<body>.*?):=\s*by\s+sorry\s*$",
    re.DOTALL | re.MULTILINE,
)

_LEAN_TIMEOUT_SECONDS = 600


class VerifyCache:
    """Append-only jsonl progress cache for elaboration results.

    One row per statement per attempt; the last row for a name wins, so
    fallback retries just append a fresher row. Rows are flushed and fsync'd
    immediately, so a hard kill loses at most the in-flight batch.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self.rows: dict[str, dict] = {}
        self._handle = None
        if path is None:
            return
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self.rows[str(row["name"])] = row
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def record(
        self,
        name: str,
        imports: list[str] | None,
        error: str | None,
        *,
        stage: str,
    ) -> None:
        row = {
            "name": name,
            "ok": error is None,
            "stage": stage,
            "imports": list(imports or []),
            "error": (error or "")[:4000],
        }
        self.rows[name] = row
        if self._handle is not None:
            self._handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


@dataclass(frozen=True)
class Statement:
    """One miniF2F theorem: name plus the signature text (no proof body)."""

    name: str
    split: str
    signature: str  # "theorem name (binders) : type"


def extract_statement(text: str) -> tuple[str, str]:
    """Pull ``(name, signature)`` out of an upstream miniF2F-lean4 file."""
    match = _THEOREM_RE.search(text)
    if match is None:
        raise ValueError("no `theorem ... := by sorry` declaration found")
    signature = f"theorem {match.group('name')}{match.group('body').rstrip()}"
    return match.group("name"), signature


def load_statements(source_dir: Path = SOURCE_DIR) -> list[Statement]:
    statements: list[Statement] = []
    for split in ("valid", "test"):
        lean_dir = source_dir / split.capitalize()
        for path in sorted(lean_dir.glob("*.lean")):
            name, signature = extract_statement(path.read_text(encoding="utf-8"))
            statements.append(Statement(name=name, split=split, signature=signature))
    return statements


def load_nl_problems() -> dict[str, str]:
    """Map miniF2F theorem name -> natural-language problem text.

    Comes from the tier4 benchmark jsonl (built earlier from the HuggingFace
    miniF2F-lean4 datasets); statements without a known NL text get a generic
    lead-in instead.
    """
    problems: dict[str, str] = {}
    for path in NL_DATASETS.values():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                problems[str(row["id"])] = str(row["problem"])
    return problems


def lean_library_path(workspace: Path = WORKSPACE_DIR) -> str:
    """LEAN_PATH covering the workspace build and every prebuilt dependency."""
    entries = [str(workspace / ".lake/build/lib/lean")]
    packages = workspace / ".lake/packages"
    if packages.is_dir():
        for package in sorted(packages.iterdir()):
            lib = package / ".lake/build/lib/lean"
            if lib.is_dir():
                entries.append(str(lib))
    return ":".join(entries)


def mathlib_package_dir(workspace: Path = WORKSPACE_DIR) -> Path:
    return workspace / ".lake/packages/mathlib4"


def existing_imports(
    modules: list[str] | tuple[str, ...],
    *,
    package_dir: Path | None = None,
) -> list[str]:
    """Keep only modules that exist in a mathlib source tree.

    ``package_dir`` makes the filesystem dependency explicit for unit tests;
    production builds default to the repository's pinned Lean workspace.
    """
    root = package_dir or mathlib_package_dir()
    kept = []
    for module in modules:
        rel = Path(*module.split(".")).with_suffix(".lean")
        if module.startswith("Mathlib.") and (root / rel).exists():
            kept.append(module)
    return kept


def _render_check_file(
    statements: list[tuple[str, str]], imports: list[str]
) -> str:
    """One lean file holding several ``:= by sorry`` statements."""
    header = "\n".join(f"import {module}" for module in imports)
    body = "\n\n".join(f"{signature} := by sorry" for _, signature in statements)
    return f"{header}\n\n{OPEN_LINE}\n\n{body}\n"


def _run_lean(path: Path, library_path: str) -> str:
    """Run ``lean`` on one file; return combined output (empty means clean).

    ``sorry`` warnings are expected and stripped; anything else (errors)
    makes the batch fail.
    """
    env = dict(os.environ)
    env["LEAN_PATH"] = library_path
    try:
        proc = subprocess.run(
            ["lean", str(path)],
            capture_output=True,
            text=True,
            timeout=_LEAN_TIMEOUT_SECONDS,
            env=env,
            cwd=WORKSPACE_DIR,
        )
    except subprocess.TimeoutExpired:
        return f"lean timed out after {_LEAN_TIMEOUT_SECONDS}s"
    output = (proc.stdout + "\n" + proc.stderr).strip()
    lines = [
        line
        for line in output.splitlines()
        if "declaration uses `sorry`" not in line and line.strip()
    ]
    return "\n".join(lines)


def verify_statements(
    statements: list[Statement],
    *,
    jobs: int = 2,
    batch_size: int = 25,
    library_path: str | None = None,
    mathlib_dir: Path | None = None,
    cache_path: Path | None = None,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Elaborate every statement against the local mathlib toolchain.

    Returns ``(imports_by_name, errors)``: the import set that elaborated each
    statement, plus per-statement error text for the ones that never did.
    When ``cache_path`` is given, results are appended to it as they complete
    and a rerun skips statements already cached, so a killed run resumes
    where it stopped.
    """
    library_path = library_path or lean_library_path()
    standard = existing_imports(list(STANDARD_IMPORTS), package_dir=mathlib_dir)
    fallbacks = existing_imports(list(FALLBACK_IMPORTS), package_dir=mathlib_dir)
    cache = VerifyCache(cache_path)
    pending: dict[str, Statement] = {}
    imports_by_name: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for stmt in statements:
        row = cache.rows.get(stmt.name)
        if row is None:
            pending[stmt.name] = stmt
        elif row["ok"]:
            imports_by_name[stmt.name] = list(row["imports"])
        else:
            errors[stmt.name] = row["error"] or "elaboration failed (cached)"
    resumed = len(statements) - len(pending)
    if resumed and cache_path is not None:
        print(f"Resumed {resumed}/{len(statements)} cached results from {cache_path}")

    def check(items: list[tuple[str, str]], imports: list[str]) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lean", prefix="minif2f_check_", delete=False
        ) as handle:
            handle.write(_render_check_file(items, imports))
            check_path = Path(handle.name)
        try:
            return _run_lean(check_path, library_path)
        finally:
            check_path.unlink(missing_ok=True)

    def check_batch(names: list[str], imports: list[str]) -> str:
        return check([(name, pending[name].signature) for name in names], imports)

    try:
        # Phase 1: batches with the standard import set; failures are rechecked
        # individually so one bad statement does not sink its whole batch.
        names = [stmt.name for stmt in statements if stmt.name in pending]
        batches = [names[i : i + batch_size] for i in range(0, len(names), batch_size)]
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for batch_names, output in zip(
                batches, pool.map(lambda b: (b, check_batch(b, standard)), batches)
            ):
                if not output:
                    for name in batch_names:
                        imports_by_name[name] = standard
                        cache.record(name, standard, None, stage="standard")
                else:
                    retry_errors = pool.map(
                        lambda n: (n, check_batch([n], standard)), batch_names
                    )
                    for name, retry_output in retry_errors:
                        if retry_output:
                            errors[name] = retry_output
                            cache.record(name, None, retry_output, stage="standard")
                        else:
                            imports_by_name[name] = standard
                            cache.record(name, standard, None, stage="standard")
                done += len(batch_names)
                print(f"phase1: {done}/{len(names)} elaborated", flush=True)

        # Phase 2: standard-set failures get one retry with the fallback modules
        # appended (e.g. NNReal users need both Data.NNReal and Pow.NNReal).
        for name in list(errors):
            row = cache.rows.get(name)
            if row is not None and row.get("stage") == "fallback":
                continue  # fallback already failed for this one in a prior run
            stmt = pending.get(name) or _stmt_by_name(statements, name)
            output = check([(name, stmt.signature)], standard + fallbacks)
            if not output:
                imports_by_name[name] = standard + fallbacks
                del errors[name]
                cache.record(name, standard + fallbacks, None, stage="fallback")
            else:
                errors[name] = output
                cache.record(name, None, output, stage="fallback")
    finally:
        cache.close()
    return imports_by_name, errors


def _stmt_by_name(statements: list[Statement], name: str) -> Statement:
    for stmt in statements:
        if stmt.name == name:
            return stmt
    raise KeyError(name)


def _category(name: str) -> str:
    if name.startswith("mathd_algebra"):
        return "algebra"
    if name.startswith("mathd_numbertheory"):
        return "number_theory"
    if name.startswith("mathd"):
        return "mathd"
    if name.startswith("numbertheory"):
        return "number_theory"
    if name.startswith("algebra"):
        return "algebra"
    if name.startswith("induction"):
        return "induction"
    if name.startswith(("amc", "aime", "imo")):
        return "competition"
    return "other"


def render_problem(nl_problem: str | None, name: str, signature: str, imports: list[str]) -> str:
    lead = nl_problem or f"Prove the miniF2F theorem `{name}` in Lean 4."
    block = "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            OPEN_LINE,
            "",
            f"{signature} := by",
            "  sorry",
        ]
    )
    return (
        f"{lead}\n\n"
        "The target Lean 4 statement (mathlib4 v4.30.0) is:\n\n"
        f"```lean\n{block}\n```\n\n"
        "Replace the `sorry` with a complete proof of this exact statement. "
        "Keep the theorem name and signature unchanged, and use specific "
        "Mathlib module imports — the umbrella `import Mathlib` is not "
        "available in this environment."
    )


def build_rows(
    statements: list[Statement],
    imports_by_name: dict[str, list[str]],
    nl_problems: dict[str, str],
) -> list[dict]:
    rows = []
    split = statements[0].split
    for index, stmt in enumerate(statements, start=1):
        imports = imports_by_name[stmt.name]
        rows.append(
            {
                "id": f"minif2f-{split}-{index:03d}",
                "problem": render_problem(
                    nl_problems.get(stmt.name), stmt.name, stmt.signature, imports
                ),
                "judge": "formal",
                # The verified Lean artifact must contain the target theorem
                # name; this blocks trivially-verified placeholder proofs.
                "expected": stmt.name,
                "require_formal_verification": True,
                "tags": ["formal", "minif2f", split, _category(stmt.name)],
                "source": "miniF2F-lean4 (openai/miniF2F statements; LeanDojo yangky11/miniF2F-lean4 Lean 4 port)",
                "split": split,
                "minif2f_name": stmt.name,
            }
        )
    return rows


def _cmd_build(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    statements = load_statements()
    print(f"Loaded {len(statements)} miniF2F statements from {SOURCE_DIR}")
    if args.verify:
        imports_by_name, errors = verify_statements(
            statements,
            jobs=args.jobs,
            batch_size=args.batch_size,
            cache_path=Path(args.cache),
        )
    else:
        standard = existing_imports(list(STANDARD_IMPORTS))
        imports_by_name = {stmt.name: standard for stmt in statements}
        errors = {}
    nl_problems = load_nl_problems()
    kept: dict[str, list[Statement]] = {"valid": [], "test": []}
    for stmt in statements:
        if stmt.name in errors:
            print(f"DROP {stmt.split}/{stmt.name}: elaboration failed")
        else:
            kept[stmt.split].append(stmt)
    for split, split_statements in kept.items():
        rows = build_rows(split_statements, imports_by_name, nl_problems)
        path = out_dir / f"minif2f_{split}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"Wrote {len(rows)} cases to {path}")
    standard = existing_imports(list(STANDARD_IMPORTS))
    report = {
        "source": str(SOURCE_DIR.relative_to(REPO_ROOT)),
        "toolchain": (WORKSPACE_DIR / "lean-toolchain").read_text().strip(),
        "verified": args.verify,
        "counts": {
            "total": len(statements),
            "kept": {split: len(items) for split, items in kept.items()},
            "dropped": len(errors),
        },
        "dropped_statements": {name: err.splitlines()[:5] for name, err in errors.items()},
        "fallback_imports_used": {
            name: [module for module in imports if module not in standard]
            for name, imports in imports_by_name.items()
            if imports != standard
        },
    }
    report_path = out_dir / "minif2f_build_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Build report: {report_path}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Convert + verify + write the datasets.")
    build.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    build.add_argument("--jobs", type=int, default=2)
    build.add_argument("--batch-size", type=int, default=25)
    build.add_argument(
        "--cache",
        default=str(DEFAULT_CACHE_PATH),
        help="Persistent verification progress cache (resumed on rerun).",
    )
    build.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip elaboration against the local toolchain (not recommended).",
    )
    build.set_defaults(verify=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "build":
        return _cmd_build(args)
    raise ValueError(f"Unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
