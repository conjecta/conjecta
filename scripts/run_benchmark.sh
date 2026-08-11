#!/usr/bin/env bash
# Run the tiered fixed evaluation benchmark.
#
# Usage:
#   scripts/run_benchmark.sh
#   scripts/run_benchmark.sh --fast-floor 0.55
#
# Outputs per-tier result JSONLs under data/eval-results/.
# The fast tier uses trials=1; the formal tier uses trials=3.
# If any formal case is falsely verified, the script exits non-zero (inherited
# from evaluate_math_agent.py). An optional --fast-floor enforces a minimum
# fast-tier accuracy.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVAL_DIR="${ROOT_DIR}/data/eval"
RESULTS_DIR="${ROOT_DIR}/data/eval-results"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

FAST_TRIALS=1
FORMAL_TRIALS=3
FAST_FLOOR="0.5"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fast-floor)
            FAST_FLOOR="$2"
            shift 2
            ;;
        --fast-trials)
            FAST_TRIALS="$2"
            shift 2
            ;;
        --formal-trials)
            FORMAL_TRIALS="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--fast-floor ACCURACY] [--fast-trials N] [--formal-trials N]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

mkdir -p "${RESULTS_DIR}"

run_tier() {
    local name="$1"
    local dataset="$2"
    local trials="$3"
    local output="$4"

    echo "=== ${name} tier (trials=${trials}) ==="
    uv run python "${ROOT_DIR}/scripts/evaluate_math_agent.py" \
        --dataset "${dataset}" \
        --trials "${trials}" \
        --output "${output}"
}

FAST_OUT="${RESULTS_DIR}/fast-${TIMESTAMP}.jsonl"
FORMAL_OUT="${RESULTS_DIR}/formal-${TIMESTAMP}.jsonl"

run_tier "fast" "${EVAL_DIR}/fast.jsonl" "${FAST_TRIALS}" "${FAST_OUT}"

if [[ -n "${FAST_FLOOR}" ]]; then
    echo "Checking fast-tier accuracy floor: ${FAST_FLOOR}"
    uv run python - <<PY
import json, sys
with open("${FAST_OUT}", "r", encoding="utf-8") as handle:
    summary = None
    for line in handle:
        row = json.loads(line)
        if row.get("type") == "summary":
            summary = row
    if summary is None:
        print("No summary found in ${FAST_OUT}", file=sys.stderr)
        sys.exit(1)
    acc = summary.get("accuracy", 0.0)
    floor = float("${FAST_FLOOR}")
    print(f"Fast-tier accuracy: {acc:.3f} (floor: {floor:.3f})")
    sys.exit(0 if acc >= floor else 1)
PY
fi

run_tier "formal" "${EVAL_DIR}/formal.jsonl" "${FORMAL_TRIALS}" "${FORMAL_OUT}"

echo "Benchmark complete."
echo "  Fast results:  ${FAST_OUT}"
echo "  Formal results: ${FORMAL_OUT}"
