#!/usr/bin/env python3
"""Ablation: raw one-shot proof generation (no harness) on formal problems.

Each problem gets exactly one LLM completion — no premise retrieval, no
compile-feedback repair, no tools, no escalation — and the extracted Lean
artifact is checked once with the strict verifier. Comparing this against the
full-harness result on the same problems quantifies the harness lift.

Usage:
    uv run python scripts/ablation_raw_oneshot.py \
        --dataset data/benchmarks/sampled/tier4_minif2f_sample30.jsonl \
        --output data/eval-results/ablation-raw-oneshot.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_agent.config import load_config
from math_agent.lean.runner import LeanRunner
from math_agent.llm import create_backend
from math_agent.llm.base import Message
from math_agent.text_utils import extract_lean_code

SYSTEM = (
    "You are a Lean 4 expert. The user gives a theorem as a Lean 4 snippet "
    "whose proof is `sorry`. Reply with the complete Lean 4 source file: the "
    "same imports, the same theorem name and signature, and a complete proof "
    "replacing the `sorry`. Output only Lean code, no explanation."
)


async def run_case(llm, runner, case) -> dict:
    start = time.monotonic()
    response = await llm.complete(
        [Message(role="user", content=case["problem"])], system=SYSTEM
    )
    code = extract_lean_code(response.text)
    verified = False
    errors: list[str] = []
    if code.strip():
        result = await runner.check_proof(code)
        verified = bool(result.success)
        errors = list(result.errors or [])[:3]
    else:
        errors = ["no Lean code block in model output"]
    return {
        "case_id": case["id"],
        "correct": verified,
        "verification_status": "verified" if verified else "failed",
        "mode": "raw_oneshot",
        "latency_seconds": round(time.monotonic() - start, 2),
        "total_tokens": response.total_tokens,
        "errors": errors,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cases = [json.loads(line) for line in open(args.dataset) if line.strip()]
    config = load_config()
    llm = create_backend(config.llm)
    runner = LeanRunner(config.lean)

    out = Path(args.output)
    done_ids = set()
    if out.exists():
        for line in out.open():
            r = json.loads(line)
            if r.get("case_id"):
                done_ids.add(r["case_id"])

    with out.open("a") as f:
        for i, case in enumerate(cases):
            if case["id"] in done_ids:
                continue
            try:
                row = await run_case(llm, runner, case)
            except Exception as exc:  # keep the ablation running on LLM faults
                row = {
                    "case_id": case["id"],
                    "correct": False,
                    "verification_status": "error",
                    "mode": "raw_oneshot",
                    "errors": [str(exc)[:200]],
                }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(
                f"[{i+1}/{len(cases)}] {case['id']}: {row['verification_status']}",
                flush=True,
            )

    rows = [json.loads(line) for line in out.open()]
    verified = sum(1 for r in rows if r.get("correct"))
    print(f"RAW ONESHOT: {verified}/{len(rows)} verified ({verified/len(rows)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
