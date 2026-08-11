#!/usr/bin/env python3
"""Ablation: raw one-shot answering (no harness) on informal problems.

Each problem gets exactly one LLM completion — no tools, no compute sandbox,
no reviewers — judged with the same judge functions as the harness eval.
Pairs with ablation_raw_oneshot.py (formal problems).

Usage:
    uv run python scripts/ablation_raw_informal.py \
        --dataset data/benchmarks/sampled/tier2_sample50.jsonl \
        --output data/eval-results/ablation-raw-tier2.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_agent.config import load_config
from math_agent.evaluation import load_cases
from math_agent.evaluation.judges import judge_solution
from math_agent.llm import create_backend
from math_agent.llm.base import Message

SYSTEM = (
    "You are a competition mathematics expert. Solve the problem and end "
    "your reply with the final answer in \\boxed{...}. No tools, no code, "
    "just reasoning and the boxed answer."
)


async def run_case(llm, case) -> dict:
    start = time.monotonic()
    response = await llm.complete(
        [Message(role="user", content=case.problem)], system=SYSTEM
    )
    solution = SimpleNamespace(
        final_answer=response.text, verification_status="unreviewed", lean_proofs=[]
    )
    correct = judge_solution(case, solution)
    return {
        "case_id": case.id,
        "correct": bool(correct),
        "verification_status": "unreviewed",
        "mode": "raw_oneshot",
        "latency_seconds": round(time.monotonic() - start, 2),
        "total_tokens": response.total_tokens,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    config = load_config()
    llm = create_backend(config.llm)

    out = Path(args.output)
    done_ids = set()
    if out.exists():
        for line in out.open():
            r = json.loads(line)
            if r.get("case_id"):
                done_ids.add(r["case_id"])

    with out.open("a") as f:
        for i, case in enumerate(cases):
            if case.id in done_ids:
                continue
            try:
                row = await run_case(llm, case)
            except Exception as exc:  # keep the ablation running on LLM faults
                row = {
                    "case_id": case.id,
                    "correct": False,
                    "verification_status": "error",
                    "mode": "raw_oneshot",
                    "errors": [str(exc)[:200]],
                }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{i+1}/{len(cases)}] {case.id}: {row['correct']}", flush=True)

    rows = [json.loads(line) for line in out.open()]
    correct = sum(1 for r in rows if r.get("correct"))
    print(f"RAW ONESHOT: {correct}/{len(rows)} correct ({correct/len(rows)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
