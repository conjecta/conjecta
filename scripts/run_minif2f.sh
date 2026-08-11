#!/usr/bin/env bash
# miniF2F v2 (Lean 4) acceptance gate.
#
#   scripts/run_minif2f.sh                iteration: valid split, 1 trial (pass@1)
#   scripts/run_minif2f.sh --acceptance   milestone gate: test split, 8 trials (pass@1 + pass@8)
#   scripts/run_minif2f.sh --trials N     override trial count for the iteration run
#
# Mirrors scripts/run_eval_chain.sh conventions (env, logs, timestamps).
set -uo pipefail
cd "$(dirname "$0")/.."

# Load credentials from the deployment env file when present (self-hosters
# may instead export OPENAI_API_KEY / OPENAI_BASE_URL before running).
if [ -f /opt/conjecta/conjecta.env ]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^OPENAI_(API_KEY|BASE_URL)=' /opt/conjecta/conjecta.env)
  set +a
fi

ACCEPTANCE=0
TRIALS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --acceptance) ACCEPTANCE=1 ;;
    --trials) shift; TRIALS="${1:?--trials needs a value}" ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p logs/eval data/eval-results
LOG="logs/eval/minif2f-${TS}.log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

if [ "$ACCEPTANCE" -eq 1 ]; then
  # Milestone acceptance: held-out test split, 8 trials per case, pass@8.
  DATASET="data/eval/minif2f_test.jsonl"
  TRIALS=8
  PASS_K=8
  OUT="data/eval-results/minif2f-test-${TS}.jsonl"
else
  # Iteration: valid split, single trial by default.
  DATASET="data/eval/minif2f_valid.jsonl"
  PASS_K=8
  OUT="data/eval-results/minif2f-valid-${TS}.jsonl"
fi

echo "$TS" > logs/eval/latest_ts.txt
echo "$LOG" > logs/eval/latest_log.txt
echo "$$" > logs/eval/latest_pid.txt

log "MINIF2F START TS=${TS} pid=$$ dataset=${DATASET} trials=${TRIALS} pass_k=${PASS_K} acceptance=${ACCEPTANCE}"
if uv run python scripts/evaluate_math_agent.py \
    --dataset "${DATASET}" \
    --trials "${TRIALS}" \
    --pass-k "${PASS_K}" \
    --output "${OUT}" >>"$LOG" 2>&1; then
  log "MINIF2F DONE TS=${TS} results=${OUT}"
  exit 0
fi
rc=$?
log "MINIF2F FAIL TS=${TS} exit=${rc}"
exit "$rc"
