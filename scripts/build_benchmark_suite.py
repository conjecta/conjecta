#!/usr/bin/env python3
"""Build the tiered Conjecta benchmark suite from authoritative sources.

Downloads competition / olympiad / formal-math benchmark data, converts each
source to the eval JSONL schema consumed by ``math_agent.evaluation``, and
writes the results under ``data/benchmarks/`` together with a manifest.

Usage:
    .venv/bin/python scripts/build_benchmark_suite.py

Properties:
- Idempotent: every run overwrites the produced JSONL files and the manifest.
  Raw downloads are cached under ``data/benchmarks/_src/`` (gitignored).
- Deterministic: all sampling uses ``random.Random(20260805)``.
- Fault-tolerant: each source is built independently; a failure logs a
  warning and the build continues with the remaining sources.
- Self-validating: every produced file is loaded with
  ``math_agent.evaluation.load_cases`` before the run reports success.
"""

from __future__ import annotations

import csv
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from math_agent.evaluation import load_cases  # noqa: E402

OUT_DIR = REPO_ROOT / "data" / "benchmarks"
SRC_DIR = OUT_DIR / "_src"
SEED = 20260805

DATASETS_SERVER = "https://datasets-server.huggingface.co"

# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[build] WARNING: {msg}", file=sys.stderr, flush=True)


def http_get_text(url: str, *, retries: int = 4, timeout: int = 60) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2**attempt
                warn(f"HTTP {resp.status_code} for {url}; retrying in {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:  # network-level failure
            last_exc = exc
            wait = 2**attempt
            warn(f"request error for {url}: {exc}; retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last_exc


def http_get_json(url: str, **kwargs) -> dict:
    return json.loads(http_get_text(url, **kwargs))


def hf_rows(dataset: str, config: str, split: str) -> list[dict]:
    """Fetch every row of a small HF dataset via the datasets-server API."""
    rows: list[dict] = []
    offset = 0
    while True:
        url = (
            f"{DATASETS_SERVER}/rows?dataset={dataset}&config={config}"
            f"&split={split}&offset={offset}&length=100"
        )
        payload = http_get_json(url)
        batch = payload.get("rows") or []
        rows.extend(entry["row"] for entry in batch)
        if len(batch) < 100:
            return rows
        offset += 100


def ensure_repo(url: str, dest: Path) -> None:
    """Shallow-clone ``url`` into ``dest`` unless it is already cached."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def cached_download(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(http_get_text(url, timeout=120), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# simple-numeric answer parsing (mirrors the numeric judge's accepted forms:
# plain int, decimal, a/b, \frac{a}{b})
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?\d*\.\d+$")
_FRAC_RE = re.compile(r"^(-?\d+)/(\d+)$")
_LATEX_FRAC_RE = re.compile(r"^\\[dt]?frac\{\s*(-?\d+)\s*\}\{\s*(-?\d+)\s*\}$")


def parse_simple_numeric(text) -> int | float | None:
    """Parse ``text`` as a simple numeric the rule-based judge can score.

    Returns an int for integral answers, a float otherwise, or None when the
    answer is a non-numeric expression (which the judge cannot handle).
    """
    if text is None:
        return None
    s = str(text).strip()
    while s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
    s = s.replace("\\!", "").replace("\\,", "").replace("\\ ", "")
    s = re.sub(r"\s+", " ", s).strip()
    if _INT_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return None
    if _DECIMAL_RE.match(s):
        return float(s)
    match = _FRAC_RE.match(s) or _LATEX_FRAC_RE.match(s)
    if match:
        numerator, denominator = int(match.group(1)), int(match.group(2))
        if denominator == 0:
            return None
        value = Fraction(numerator, denominator)
        if value.denominator == 1:
            return int(value)
        return float(value)
    return None


def slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


ANSWER_SUFFIX_NUMERIC = "\n\nAnswer with only the number (an integer, decimal, or simple fraction)."
ANSWER_SUFFIX_INTEGER = "\n\nAnswer with only the integer."


# ---------------------------------------------------------------------------
# case assembly
# ---------------------------------------------------------------------------


def numeric_case(
    case_id: str,
    problem: str,
    expected: int | float,
    tags: list[str],
    suffix: str,
    extra: dict | None = None,
) -> dict:
    row = {
        "id": case_id,
        "problem": problem.strip() + suffix,
        "judge": "numeric",
        "expected": expected,
        "tags": tags,
    }
    if extra:
        row.update(extra)
    return row


def formal_case(
    case_id: str, informal_statement: str, tags: list[str], extra: dict | None = None
) -> dict:
    statement = informal_statement.strip()
    # Minimal prefix adaptation: "solve"-type problems are asked to state the
    # answer as a theorem instead of being forced into a "prove that" frame.
    if re.match(r"(?i)^(find|evaluate|determine|compute|calculate|what|how many)\b", statement):
        prefix = "Formalize in Lean 4 (state the answer as a theorem) and prove:"
    else:
        prefix = "Formalize and prove in Lean 4:"
    row = {
        "id": case_id,
        "problem": f"{prefix} {statement}",
        "judge": "formal",
        "expected": None,
        "require_formal_verification": True,
        "tags": tags,
    }
    if extra:
        row.update(extra)
    return row


# ---------------------------------------------------------------------------
# competition numeric track (tier 2)
# ---------------------------------------------------------------------------


def build_aime_1983_2024() -> tuple[list[dict], list[str]]:
    url = "https://huggingface.co/datasets/gneubig/aime-1983-2024/resolve/main/AIME_Dataset_1983_2024.csv"
    path = cached_download(url, SRC_DIR / "aime-1983-2024" / "AIME_Dataset_1983_2024.csv")
    caveats: list[str] = []
    cases: list[dict] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            year = str(raw["Year"]).strip()
            number = str(raw["Problem Number"]).strip()
            part = str(raw.get("Part") or "").strip()
            answer = str(raw["Answer"]).strip()
            if not answer.isdigit() or not 0 <= int(answer) <= 999:
                skipped += 1
                continue
            case_id = f"aime-{year}"
            if part:
                case_id += f"-{part.lower()}"
            case_id += f"-{int(number):02d}"
            cases.append(
                numeric_case(
                    case_id,
                    str(raw["Question"]).strip(),
                    int(answer),
                    ["competition", "aime", "tier2"],
                    ANSWER_SUFFIX_INTEGER,
                    {"source": "gneubig/aime-1983-2024", "year": int(year), "license": "CC0-1.0"},
                )
            )
    caveats.append(
        f"Upstream CSV contains {len(cases) + skipped} rows (not the ~2250 "
        f"advertised on the dataset card); AIME I 2023-2024 and some recent "
        f"years are missing."
    )
    if skipped:
        caveats.append(
            f"{skipped} row(s) skipped because the answer is not a single "
            f"integer 000-999 (2022-II-8 accepts both 080 and 081)."
        )
    return cases, caveats


def build_aime_2025() -> tuple[list[dict], list[str]]:
    rows = hf_rows("MathArena/aime_2025", "default", "train")
    cases = []
    for raw in rows:
        idx = int(raw["problem_idx"])
        cases.append(
            numeric_case(
                f"aime-2025-{idx:02d}",
                str(raw["problem"]).strip(),
                parse_simple_numeric(raw["answer"]),
                ["competition", "aime", "aime_2025", "tier2"],
                ANSWER_SUFFIX_INTEGER,
                {
                    "source": "MathArena/aime_2025",
                    "year": 2025,
                    "problem_type": raw.get("problem_type"),
                    "license": "CC-BY-NC-SA-4.0",
                },
            )
        )
    caveats = [
        "Covers AIME 2025 I & II (30 problems) as mirrored by MathArena; "
        "ids are sequential problem indices 1-30 across both sessions."
    ]
    return cases, caveats


def build_hmmt_feb_2025() -> tuple[list[dict], list[str]]:
    rows = hf_rows("MathArena/hmmt_feb_2025", "default", "train")
    cases = []
    skipped = 0
    for raw in rows:
        expected = parse_simple_numeric(raw.get("answer"))
        if expected is None:
            skipped += 1
            continue
        idx = int(raw["problem_idx"])
        cases.append(
            numeric_case(
                f"hmmt-feb-2025-{idx:02d}",
                str(raw["problem"]).strip(),
                expected,
                ["competition", "hmmt", "hmmt_feb_2025", "tier2"],
                ANSWER_SUFFIX_NUMERIC,
                {
                    "source": "MathArena/hmmt_feb_2025",
                    "year": 2025,
                    "problem_type": raw.get("problem_type"),
                    "license": "CC-BY-NC-SA-4.0",
                },
            )
        )
    caveats = [
        f"{skipped} of {len(rows)} rows skipped: their answers are non-numeric "
        "expressions the rule-based numeric judge cannot score."
    ]
    return cases, caveats


# ---------------------------------------------------------------------------
# olympiad ceiling track (tier 3)
# ---------------------------------------------------------------------------


def _omni_domain_tag(domain) -> str:
    if isinstance(domain, list) and domain and "->" in str(domain[0]):
        return slug(str(domain[0]).split("->")[1])
    return "other"


def build_omni_math() -> tuple[list[dict], list[str]]:
    url = "https://raw.githubusercontent.com/KbsdJames/Omni-MATH/main/Omni-Math.jsonl"
    path = cached_download(url, SRC_DIR / "omni-math" / "Omni-Math.jsonl")
    eligible: list[dict] = []
    skipped = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            expected = parse_simple_numeric(raw.get("answer"))
            if expected is None:
                skipped += 1
                continue
            eligible.append({**raw, "_expected": expected})
    rng = random.Random(SEED)
    by_level: dict[int, list[dict]] = {}
    for raw in eligible:
        by_level.setdefault(int(raw["difficulty"]), []).append(raw)
    sampled: list[dict] = []
    for level in sorted(by_level):
        pool = by_level[level]
        rng.shuffle(pool)
        sampled.extend(pool[:30])
    rng.shuffle(sampled)
    sampled = sampled[:250]
    cases = []
    for n, raw in enumerate(sampled, 1):
        level = int(raw["difficulty"])
        cases.append(
            numeric_case(
                f"omni-math-{n:04d}",
                str(raw["problem"]).strip(),
                raw["_expected"],
                ["olympiad", "omni_math", "tier3", f"d{level}", _omni_domain_tag(raw.get("domain"))],
                ANSWER_SUFFIX_NUMERIC,
                {
                    "source": "KbsdJames/Omni-MATH",
                    "difficulty": raw["difficulty"],
                    "license": "Apache-2.0",
                },
            )
        )
    caveats = [
        f"{skipped} of {skipped + len(eligible)} rows dropped: answers are "
        "non-numeric expressions the rule-based numeric judge cannot score.",
        "Stratified sample: up to 30 per integer difficulty level, capped at "
        "250 total (deterministic seed 20260805).",
        "Answers were checked against Omni-MATH's reference `answer` field; "
        "the official Omni-Judge (GPT-based) is not used, so scoring is "
        "limited to exact numeric matches.",
    ]
    return cases, caveats


def build_olympiadbench() -> tuple[list[dict], list[str]]:
    configs = [
        ("OE_TO_maths_en_COMP", "oe"),
        ("TP_TO_maths_en_COMP", "tp"),
    ]
    eligible: list[dict] = []
    stats = {"rows": 0, "non_numeric_type": 0, "multi_answer": 0, "figure": 0, "unparseable": 0}
    for config, short in configs:
        for raw in hf_rows("Hothan/OlympiadBench", config, "train"):
            stats["rows"] += 1
            question = str(raw.get("question") or "")
            if raw.get("answer_type") != "Numerical":
                stats["non_numeric_type"] += 1
                continue
            if raw.get("is_multiple_answer"):
                stats["multi_answer"] += 1
                continue
            if "<image" in question or "[asy]" in question:
                stats["figure"] += 1
                continue
            finals = raw.get("final_answer") or []
            if len(finals) != 1:
                stats["multi_answer"] += 1
                continue
            expected = parse_simple_numeric(finals[0])
            if expected is None:
                stats["unparseable"] += 1
                continue
            eligible.append(
                {
                    "case_id": f"olympiadbench-{short}-{int(raw['id'])}",
                    "question": question,
                    "expected": expected,
                    "subfield": raw.get("subfield"),
                    "config": config,
                }
            )
    rng = random.Random(SEED)
    rng.shuffle(eligible)
    sampled = eligible[:200]
    cases = [
        numeric_case(
            raw["case_id"],
            raw["question"],
            raw["expected"],
            ["olympiad", "olympiadbench", "tier3", slug(str(raw["subfield"] or "other"))],
            ANSWER_SUFFIX_NUMERIC,
            {"source": "Hothan/OlympiadBench", "config": raw["config"], "license": "Apache-2.0"},
        )
        for raw in sampled
    ]
    caveats = [
        "Only the English text-only math configs (OE_TO_maths_en_COMP, "
        "TP_TO_maths_en_COMP) are used.",
        f"Of {stats['rows']} rows: dropped {stats['non_numeric_type']} with "
        f"non-Numerical answer_type, {stats['multi_answer']} multi-answer, "
        f"{stats['figure']} figure-dependent, {stats['unparseable']} whose "
        "final answer is not a simple numeric; sampled up to 200 of the "
        f"remaining {len(eligible)} (deterministic seed 20260805).",
    ]
    return cases, caveats


# ---------------------------------------------------------------------------
# formal (Lean 4) track (tiers 4-5)
# ---------------------------------------------------------------------------


def build_minif2f() -> dict[str, tuple[list[dict], list[str]]]:
    repo = SRC_DIR / "miniF2F-lean4"
    ensure_repo("https://github.com/yangky11/miniF2F-lean4", repo)
    # yangky11/miniF2F-lean4 carries only the Lean statements (one theorem per
    # file, no informal comments). Informal statements are joined from the
    # cat-searcher/minif2f-lean4 HF mirror, keyed by theorem name.
    informal: dict[str, str] = {}
    for split in ("validation", "test"):
        for raw in hf_rows("cat-searcher/minif2f-lean4", "default", split):
            stmt = str(raw.get("informal_stmt") or "").strip()
            if stmt:
                informal[str(raw["id"])] = stmt
    # The mirror lowercases a few theorem names; fall back to a
    # case-insensitive match (e.g. numbertheory_notEquiv2i2jasqbsqdiv8).
    informal_lower = {key.lower(): value for key, value in informal.items()}
    outputs: dict[str, tuple[list[dict], list[str]]] = {}
    total_missing = 0
    for split_dir, out_key, split_tag in (
        ("Valid", "minif2f_valid", "valid"),
        ("Test", "minif2f_test", "test"),
    ):
        cases = []
        missing = 0
        for lean_file in sorted((repo / "MiniF2F" / split_dir).glob("*.lean")):
            name = lean_file.stem
            text = lean_file.read_text(encoding="utf-8")
            if f"theorem {name}" not in text:
                warn(f"miniF2F: {lean_file.name} has no `theorem {name}`; skipped")
                missing += 1
                continue
            statement = informal.get(name) or informal_lower.get(name.lower())
            if not statement:
                missing += 1
                continue
            cases.append(
                formal_case(
                    name,
                    statement,
                    ["formal", "minif2f", "tier4", split_tag],
                    {"source": "yangky11/miniF2F-lean4 + cat-searcher/minif2f-lean4", "split": split_tag},
                )
            )
        total_missing += missing
        outputs[out_key] = (
            cases,
            [
                f"{len(cases)} theorems from MiniF2F/{split_dir}. "
                f"{missing} file(s) skipped (no matching informal statement).",
                "yangky11/miniF2F-lean4 ships Lean 4 statements only; informal "
                "statements are joined by theorem name (with a case-insensitive "
                "fallback) from the cat-searcher/minif2f-lean4 HF mirror of "
                "openai/miniF2F; that mirror lacks an informal statement for "
                "imo_2006_p3. The repo's pinned mathlib4 toolchain is old and "
                "not rebuilt here.",
            ],
        )
    if total_missing:
        warn(f"miniF2F: {total_missing} theorem(s) lacked an informal statement")
    return outputs


def build_putnam() -> tuple[list[dict], list[str]]:
    repo = SRC_DIR / "PutnamBench"
    ensure_repo("https://github.com/trishullab/PutnamBench", repo)
    informal = {
        entry["problem_name"]: entry
        for entry in json.loads((repo / "informal" / "putnam.json").read_text(encoding="utf-8"))
    }
    cases = []
    missing = 0
    for lean_file in sorted((repo / "lean4" / "src").glob("putnam_*.lean")):
        name = lean_file.stem
        entry = informal.get(name)
        statement = str((entry or {}).get("informal_statement") or "").strip()
        if len(statement) < 20:
            missing += 1
            continue
        cases.append(
            formal_case(
                name,
                statement,
                ["formal", "putnam", "tier5"],
                {
                    "source": "trishullab/PutnamBench",
                    "license": "Apache-2.0",
                    "year": int(name.split("_")[1]),
                },
            )
        )
    caveats = [
        f"{len(cases)} Lean 4 problems matched to informal statements in "
        f"informal/putnam.json; {missing} skipped (no usable statement).",
        "Informal statements are used by permission of the Mathematical "
        "Association of America for benchmark use (see PutnamBench README); "
        "the PutnamBench authors request that model-generated proofs not be "
        "published. Attribution: PutnamBench, trishullab/PutnamBench.",
    ]
    return cases, caveats

IMO_MODULE_DOC_RE = re.compile(r"/-!(.*?)-/", re.DOTALL)
IMO_HEADING_RE = re.compile(r"^#+\s*International\s+Mathematical\s+Olympiad.*?$", re.MULTILINE)


def build_compfiles_imo() -> tuple[list[dict], list[str]]:
    repo = SRC_DIR / "compfiles"
    ensure_repo("https://github.com/dwrensha/compfiles", repo)
    cases = []
    short = 0
    for lean_file in sorted((repo / "Compfiles").glob("Imo*.lean")):
        text = lean_file.read_text(encoding="utf-8")
        marker = text.find("problem_file")
        if marker < 0:
            short += 1
            continue
        match = IMO_MODULE_DOC_RE.search(text, marker)
        if not match:
            short += 1
            continue
        body = IMO_HEADING_RE.sub("", match.group(1))
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) < 40:
            short += 1
            continue
        stem_match = re.match(r"Imo(\d{4})P(\d+)", lean_file.stem)
        if stem_match:
            case_id = f"imo-{stem_match.group(1)}-p{int(stem_match.group(2))}"
            year = int(stem_match.group(1))
        else:
            case_id = slug(lean_file.stem)
            year = None
        extra = {"source": "dwrensha/compfiles", "license": "Apache-2.0"}
        if year:
            extra["year"] = year
        cases.append(formal_case(case_id, body, ["formal", "imo", "compfiles", "tier5"], extra))
    caveats = [
        f"{len(cases)} of {len(cases) + short} Compfiles/Imo*.lean files yield "
        "an informal statement >= 40 chars (extracted from the module "
        "docstring following `problem_file`); the rest are skipped.",
    ]
    return cases, caveats


COMBI_DOC_THM_RE = re.compile(r"/--(.*?)-/\s*(?:theorem|lemma)\s+(\w+)", re.DOTALL)


def build_combibench() -> tuple[list[dict], list[str]] | None:
    repo = SRC_DIR / "CombiBench"
    ensure_repo("https://github.com/MoonshotAI/CombiBench", repo)
    lean_dir = repo / "lean" / "CombiBench"
    if not lean_dir.is_dir():
        warn("CombiBench: lean/CombiBench directory not found; source skipped")
        return None
    cases = []
    for lean_file in sorted(lean_dir.glob("*.lean")):
        text = lean_file.read_text(encoding="utf-8")
        matches = COMBI_DOC_THM_RE.findall(text)
        if not matches:
            warn(f"CombiBench: no informal docstring in {lean_file.name}; skipped")
            continue
        # Prefer the docstring attached to the declaration named after the
        # file (the problem itself); helper lemmas may carry docstrings too.
        statement, name = next(
            ((stmt, n) for stmt, n in matches if n == lean_file.stem), matches[0]
        )
        statement = re.sub(r"\s+", " ", statement).strip()
        if len(statement) < 20:
            continue
        cases.append(
            formal_case(
                f"combibench-{name}",
                statement,
                ["formal", "combinatorics", "combibench", "tier5"],
                {"source": "MoonshotAI/CombiBench", "license": "MIT"},
            )
        )
    if not cases:
        warn("CombiBench: no informal statements extractable; source skipped")
        return None
    caveats = [
        f"{len(cases)} problems with informal statements extracted from the "
        "`/-- ... -/` docstrings preceding each theorem in lean/CombiBench/.",
    ]
    return cases, caveats


# ---------------------------------------------------------------------------
# manifest + driver
# ---------------------------------------------------------------------------

TIERS = {
    "tier0": {
        "name": "冒烟",
        "files": ["data/eval_smoke.jsonl"],
        "description": "Minimal smoke set; verifies the harness end-to-end in seconds.",
    },
    "tier1": {
        "name": "基础",
        "files": ["data/eval/fast.jsonl"],
        "description": "Hand-written core skills: basic computation, algebra, simple formalization.",
    },
    "tier2": {
        "name": "竞赛数值",
        "files": [
            "data/benchmarks/competition/aime_1983_2024.jsonl",
            "data/benchmarks/competition/aime_2025.jsonl",
            "data/benchmarks/competition/hmmt_feb_2025.jsonl",
        ],
        "description": "Competition problems with numeric answers (AIME 1983-2025, HMMT Feb 2025).",
    },
    "tier3": {
        "name": "奥赛上限",
        "files": [
            "data/benchmarks/olympiad/omni_math.jsonl",
            "data/benchmarks/olympiad/olympiadbench_text.jsonl",
        ],
        "description": "Olympiad-ceiling numeric problems (Omni-MATH, OlympiadBench text-only).",
    },
    "tier4": {
        "name": "形式化基线",
        "files": [
            "data/eval/formal.jsonl",
            "data/benchmarks/formal/minif2f_valid.jsonl",
            "data/benchmarks/formal/minif2f_test.jsonl",
        ],
        "description": "Lean 4 formalization baseline (miniF2F valid/test).",
    },
    "tier5": {
        "name": "形式化高难",
        "files": [
            "data/benchmarks/formal/putnam.jsonl",
            "data/benchmarks/formal/compfiles_imo.jsonl",
            "data/benchmarks/formal/combibench.jsonl",
        ],
        "description": "Hard formalization: PutnamBench, IMO (compfiles), CombiBench.",
    },
    "tier6": {
        "name": "研究级",
        "files": ["data/eval/formal_hard.jsonl", "data/eval/research.jsonl"],
        "description": "Research-level planning and hardest formal cases (hand-curated).",
    },
}

# (output relpath, tier, track, source name, source url, license, builder key)
FILE_SPECS = {
    "aime_1983_2024": (
        "competition/aime_1983_2024.jsonl", "tier2", "informal-numeric",
        "gneubig/aime-1983-2024",
        "https://huggingface.co/datasets/gneubig/aime-1983-2024", "CC0-1.0",
    ),
    "aime_2025": (
        "competition/aime_2025.jsonl", "tier2", "informal-numeric",
        "MathArena/aime_2025",
        "https://huggingface.co/datasets/MathArena/aime_2025", "CC-BY-NC-SA-4.0",
    ),
    "hmmt_feb_2025": (
        "competition/hmmt_feb_2025.jsonl", "tier2", "informal-numeric",
        "MathArena/hmmt_feb_2025",
        "https://huggingface.co/datasets/MathArena/hmmt_feb_2025", "CC-BY-NC-SA-4.0",
    ),
    "omni_math": (
        "olympiad/omni_math.jsonl", "tier3", "informal-numeric",
        "KbsdJames/Omni-MATH",
        "https://github.com/KbsdJames/Omni-MATH", "Apache-2.0",
    ),
    "olympiadbench_text": (
        "olympiad/olympiadbench_text.jsonl", "tier3", "informal-numeric",
        "Hothan/OlympiadBench",
        "https://huggingface.co/datasets/Hothan/OlympiadBench", "Apache-2.0",
    ),
    "minif2f_valid": (
        "formal/minif2f_valid.jsonl", "tier4", "formal",
        "yangky11/miniF2F-lean4 (statements) + cat-searcher/minif2f-lean4 (informal)",
        "https://github.com/yangky11/miniF2F-lean4", "MIT",
    ),
    "minif2f_test": (
        "formal/minif2f_test.jsonl", "tier4", "formal",
        "yangky11/miniF2F-lean4 (statements) + cat-searcher/minif2f-lean4 (informal)",
        "https://github.com/yangky11/miniF2F-lean4", "MIT",
    ),
    "putnam": (
        "formal/putnam.jsonl", "tier5", "formal",
        "trishullab/PutnamBench",
        "https://github.com/trishullab/PutnamBench", "Apache-2.0",
    ),
    "compfiles_imo": (
        "formal/compfiles_imo.jsonl", "tier5", "formal",
        "dwrensha/compfiles (IMO archive)",
        "https://github.com/dwrensha/compfiles", "Apache-2.0",
    ),
    "combibench": (
        "formal/combibench.jsonl", "tier5", "formal",
        "MoonshotAI/CombiBench",
        "https://github.com/MoonshotAI/CombiBench", "MIT",
    ),
}

GLOBAL_CAVEATS = [
    "FrontierMath (Epoch AI) is not publicly available and is therefore "
    "absent from this suite.",
    "All informal-numeric tracks are scored by the rule-based `numeric` "
    "judge, so only problems whose reference answer is a plain integer, "
    "decimal, or simple fraction are included.",
    "MathArena mirrors (aime_2025, hmmt_feb_2025) are CC-BY-NC-SA-4.0: "
    "non-commercial use only, with attribution.",
]


def write_cases(relpath: str, cases: list[dict]) -> Path:
    dest = OUT_DIR / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    return dest


def main() -> int:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    generated: dict[str, tuple[list[dict], list[str]]] = {}

    def attempt(key: str, builder) -> None:
        try:
            result = builder()
        except Exception as exc:  # noqa: BLE001 - per-source graceful failure
            warn(f"source {key!r} FAILED: {exc}")
            return
        if result is None:
            warn(f"source {key!r} produced nothing; skipped")
            return
        generated[key] = result

    attempt("aime_1983_2024", build_aime_1983_2024)
    attempt("aime_2025", build_aime_2025)
    attempt("hmmt_feb_2025", build_hmmt_feb_2025)
    attempt("omni_math", build_omni_math)
    attempt("olympiadbench_text", build_olympiadbench)

    # Build miniF2F once; register both splits.
    try:
        minif2f = build_minif2f()
        generated.update(minif2f)
    except Exception as exc:  # noqa: BLE001
        warn(f"source 'minif2f' FAILED: {exc}")

    attempt("putnam", build_putnam)
    attempt("compfiles_imo", build_compfiles_imo)
    attempt("combibench", build_combibench)

    # Write outputs, validate with the real loader, assemble the manifest.
    entries = []
    all_ids: set[str] = set()
    ok = True
    for key, (relpath, tier, track, source, source_url, license_) in FILE_SPECS.items():
        if key not in generated:
            warn(f"{relpath}: not produced (source failed)")
            continue
        cases, caveats = generated[key]
        dest = write_cases(relpath, cases)
        try:
            loaded = load_cases(dest)
        except Exception as exc:  # noqa: BLE001
            warn(f"{relpath}: validation with load_cases FAILED: {exc}")
            ok = False
            continue
        ids = [case.id for case in loaded]
        overlap = all_ids.intersection(ids)
        if overlap:
            warn(f"{relpath}: {len(overlap)} id(s) collide with other files: {sorted(overlap)[:5]}")
            ok = False
        all_ids.update(ids)
        entries.append(
            {
                "path": f"data/benchmarks/{relpath}",
                "tier": tier,
                "track": track,
                "source": source,
                "source_url": source_url,
                "license": license_,
                "count": len(loaded),
                "caveats": caveats,
            }
        )
        log(f"{relpath}: {len(loaded)} cases")

    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/build_benchmark_suite.py",
        "seed": SEED,
        "tiers": TIERS,
        "caveats": GLOBAL_CAVEATS,
        "files": entries,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n=== benchmark suite summary ===")
    print(f"{'file':<55} {'tier':<6} {'cases':>6}")
    for entry in entries:
        print(f"{entry['path']:<55} {entry['tier']:<6} {entry['count']:>6}")
    print(f"{'TOTAL':<55} {'':<6} {sum(e['count'] for e in entries):>6}")
    missing = [k for k in FILE_SPECS if k not in generated]
    if missing:
        print(f"sources failed/skipped: {', '.join(missing)}")
    if not ok:
        print("validation errors occurred; see warnings above", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
