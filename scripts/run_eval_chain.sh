#!/usr/bin/env bash
# Serial eval chain: fast → formal → formal_hard → sampled tiers.
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

TS="${1:-$(date +%Y%m%d-%H%M%S)}"
mkdir -p logs/eval data/eval-results
LOG="logs/eval/chain-${TS}.log"

# Locked eval config: config.toml is the pinned configuration for the eval
# chain (config.eval.toml is an alternate). Override via EVAL_CONFIG if a
# one-off experiment needs a different file.
EVAL_CONFIG="${EVAL_CONFIG:-config.toml}"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

run_tier() {
  local name="$1" dataset="$2" trials="$3" out="$4"
  log "=== START ${name} dataset=${dataset} trials=${trials} out=${out} config=${EVAL_CONFIG} ==="
  if uv run python scripts/evaluate_math_agent.py \
      --config "${EVAL_CONFIG}" \
      --dataset "${dataset}" \
      --trials "${trials}" \
      --output "${out}" >>"$LOG" 2>&1; then
    log "=== DONE ${name} ==="
    return 0
  fi
  local rc=$?
  log "=== FAIL ${name} exit=${rc} ==="
  return "$rc"
}

echo "$TS" > logs/eval/latest_ts.txt
echo "$LOG" > logs/eval/latest_log.txt
echo "$$" > logs/eval/latest_pid.txt

log "CHAIN START TS=${TS} pid=$$"
run_tier fast data/eval/fast.jsonl 1 "data/eval-results/fast-${TS}.jsonl" || true
run_tier formal data/eval/formal.jsonl 3 "data/eval-results/formal-${TS}.jsonl" || true
run_tier formal_hard data/eval/formal_hard.jsonl 3 "data/eval-results/formal-hard-${TS}.jsonl" || true
run_tier tier2_sample50 data/benchmarks/sampled/tier2_sample50.jsonl 1 "data/eval-results/tier2-sample50-${TS}.jsonl" || true
run_tier tier3_sample50 data/benchmarks/sampled/tier3_sample50.jsonl 1 "data/eval-results/tier3-sample50-${TS}.jsonl" || true
run_tier tier4_minif2f_sample30 data/benchmarks/sampled/tier4_minif2f_sample30.jsonl 1 "data/eval-results/tier4-minif2f-sample30-${TS}.jsonl" || true
run_tier tier5_sample20 data/benchmarks/sampled/tier5_sample20.jsonl 1 "data/eval-results/tier5-sample20-${TS}.jsonl" || true
log "CHAIN COMPLETE TS=${TS}"
