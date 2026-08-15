#!/usr/bin/env python3
"""Recompute benchmark aggregates and compare them against the published report.

Reads ``data/eval-results/manifest.toml`` (provenance mapping for
``docs/benchmark-results-2026-08.md``) and the referenced JSONL result files,
recomputes each row's pass@1 score and cost medians, and prints a comparison
table against the numbers recorded in the manifest (copied from the report).

Exit status:
    0 - every ``status = "verified"`` row recomputes within tolerance.
    1 - at least one verified row diverges (or a source file is missing).

Rows marked ``status = "unverified"`` are recomputed and printed for
information but never affect the exit status; see their ``note`` fields.

Stdlib only. Tolerances: solved counts and n must match exactly; median
latency/tokens may differ by at most MEDIAN_REL_TOL (relative) to absorb the
report's 2-3 significant-figure rounding.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "data" / "eval-results" / "manifest.toml"

MEDIAN_REL_TOL = 0.10  # 10% relative tolerance on medians (doc rounds to ~3 s.f.)
SCORE_PP_TOL = 0.1  # percentage points


# --------------------------------------------------------------------------
# Minimal TOML-subset parser (the manifest uses only [[array-of-tables]],
# strings, numbers, booleans, and string arrays). Python 3.10 has no tomllib;
# prefer tomllib when a newer interpreter provides it.
# --------------------------------------------------------------------------

def _parse_toml_value(raw: str):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("["):
        inner = raw.strip()[1:-1].strip()
        if not inner:
            return []
        return [_parse_toml_value(part) for part in _split_array(inner)]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        return float(raw)


def _split_array(inner: str) -> list[str]:
    parts, depth, current, in_str = [], 0, [], False
    for ch in inner:
        if ch == '"':
            in_str = not in_str
        if ch == "," and not in_str and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        if ch == "[" and not in_str:
            depth += 1
        if ch == "]" and not in_str:
            depth -= 1
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _strip_comment(line: str) -> str:
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i]
    return line


def _mini_toml_load(text: str) -> dict:
    doc: dict = {}
    current: dict | None = None
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[[") and line.endswith("]]"):
            name = line[2:-2].strip()
            table: dict = {}
            doc.setdefault(name, []).append(table)
            current = table
            continue
        if "=" not in line:
            raise ValueError(f"manifest line {lineno}: cannot parse {raw_line!r}")
        key, _, value = line.partition("=")
        target = current if current is not None else doc
        target[key.strip()] = _parse_toml_value(value)
    return doc


def load_manifest(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib  # type: ignore  # Python >= 3.11

        return tomllib.loads(text)["row"]
    except ModuleNotFoundError:
        return _mini_toml_load(text)["row"]


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def load_records(results_dir: Path, sources: list[str]) -> list[dict]:
    records: list[dict] = []
    for name in sources:
        path = results_dir / name
        if not path.is_file():
            raise FileNotFoundError(str(path))
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                # The eval runner appends a {"type": "summary"} rollup record;
                # only per-trial records carry case_id.
                if record.get("type") == "summary" or "case_id" not in record:
                    continue
                records.append(record)
    return records


def is_solved(record: dict, judge: str) -> bool:
    if judge == "lean":
        return record.get("verification_status") == "verified"
    return bool(record.get("correct"))


def median_of(records: list[dict], key: str) -> float | None:
    values = [r[key] for r in records if r.get(key)]
    return statistics.median(values) if values else None


def recompute(row: dict, results_dir: Path) -> dict:
    records = load_records(results_dir, list(row["sources"]))
    judge = row["judge"]
    solved = sum(1 for r in records if is_solved(r, judge))
    out = {
        "n": len(records),
        "solved": solved,
        "median_seconds": median_of(records, "latency_seconds"),
        "median_tokens": median_of(records, "total_tokens"),
    }
    if "expected_pass3_total" in row:
        by_case: dict[str, list[dict]] = {}
        for r in records:
            by_case.setdefault(str(r["case_id"]), []).append(r)
        out["pass3_total"] = len(by_case)
        out["pass3_solved"] = sum(
            1 for trials in by_case.values() if any(is_solved(r, judge) for r in trials)
        )
    return out


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def _fmt(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}"


def compare_row(row: dict, got: dict) -> list[str]:
    """Return a list of divergence messages (empty = matches)."""
    problems: list[str] = []
    expected_n = row.get("expected_n")
    if expected_n is not None and got["n"] != expected_n:
        problems.append(f"n doc={expected_n} recomputed={got['n']}")

    solved, total = row.get("expected_solved"), row.get("expected_total")
    if solved is not None:
        if got["solved"] != solved:
            problems.append(f"solved doc={solved} recomputed={got['solved']}")
        if total:
            doc_pct = 100.0 * solved / total
            got_pct = 100.0 * got["solved"] / got["n"] if got["n"] else 0.0
            if abs(doc_pct - got_pct) > SCORE_PP_TOL:
                problems.append(f"score doc={doc_pct:.1f}% recomputed={got_pct:.1f}%")

    for key, label in (("median_seconds", "median s"), ("median_tokens", "median tok")):
        expected = row.get(f"expected_{key}")
        actual = got.get(key)
        if expected is None or actual is None:
            continue
        if abs(actual - expected) > MEDIAN_REL_TOL * expected:
            problems.append(f"{label} doc={_fmt(expected)} recomputed={_fmt(actual)}")

    if "expected_pass3_solved" in row:
        exp_s, exp_t = row["expected_pass3_solved"], row["expected_pass3_total"]
        if got.get("pass3_solved") != exp_s or got.get("pass3_total") != exp_t:
            problems.append(
                f"pass@3 doc={exp_s}/{exp_t} recomputed={got.get('pass3_solved')}/{got.get('pass3_total')}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    rows = load_manifest(args.manifest)
    results_dir = args.manifest.parent

    header = f"{'row':26s} {'status':10s} {'n':>9s} {'score':>15s} {'med s':>13s} {'med tok':>15s}  result"
    print(header)
    print("-" * len(header))

    failures = 0
    for row in rows:
        try:
            got = recompute(row, results_dir)
        except FileNotFoundError as exc:
            print(f"{row['id']:26s} {row['status']:10s} source file missing: {exc}")
            if row["status"] == "verified":
                failures += 1
            continue

        n_doc = row.get("expected_n")
        n_cell = f"{got['n']}" if n_doc is None else f"{n_doc}/{got['n']}"
        solved_doc, total_doc = row.get("expected_solved"), row.get("expected_total")
        if solved_doc is None:
            score_cell = f"{got['solved']}/{got['n']}"
        else:
            score_cell = f"{solved_doc}/{total_doc} vs {got['solved']}/{got['n']}"
        med_s_cell = f"{_fmt(row.get('expected_median_seconds'))}/{_fmt(got['median_seconds'])}"
        med_tok_cell = f"{_fmt(row.get('expected_median_tokens'))}/{_fmt(got['median_tokens'])}"

        if row["status"] != "verified":
            print(
                f"{row['id']:26s} {'unverified':10s} {n_cell:>9s} {score_cell:>15s} "
                f"{med_s_cell:>13s} {med_tok_cell:>15s}  SKIPPED ({row.get('note', '')[:80]}...)"
            )
            continue

        problems = compare_row(row, got)
        if problems:
            failures += 1
            print(
                f"{row['id']:26s} {'verified':10s} {n_cell:>9s} {score_cell:>15s} "
                f"{med_s_cell:>13s} {med_tok_cell:>15s}  DIVERGED: {'; '.join(problems)}"
            )
        else:
            print(
                f"{row['id']:26s} {'verified':10s} {n_cell:>9s} {score_cell:>15s} "
                f"{med_s_cell:>13s} {med_tok_cell:>15s}  OK"
            )

    print()
    if failures:
        print(f"FAIL: {failures} verified row(s) diverge from docs/benchmark-results-2026-08.md")
        return 1
    print("OK: all verified rows recompute within tolerance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
