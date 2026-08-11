#!/usr/bin/env bash
# Full miniF2F run: chunked, resumable, serial (this host has 14GB RAM).
#
#   scripts/run_minif2f_full.sh valid 1    # full valid split, pass@1
#   scripts/run_minif2f_full.sh test 8     # full test split, pass@1+pass@8
#
# Chunks of 12 problems run one at a time with the production config. Each
# chunk appends to one merged output file; already-solved case_ids are
# skipped, so restarting the script after a crash resumes where it stopped.
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

SPLIT="${1:-valid}"
TRIALS="${2:-1}"
CHUNK_SIZE=12
DATASET="data/eval/minif2f_${SPLIT}.jsonl"

TS="$(date +%Y%m%d-%H%M%S)"
WORK="logs/eval/minif2f-full-${SPLIT}-${TS}"
# Resume across restarts: pass MERGED_OVERRIDE=<previous merged file> to keep
# appending to the same results file instead of starting a fresh run.
MERGED="${MERGED_OVERRIDE:-data/eval-results/minif2f-${SPLIT}-full-${TS}.jsonl}"
LOG="${WORK}/run.log"
mkdir -p "$WORK" data/eval-results

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# Split the dataset into chunk files once.
uv run python - "$DATASET" "$WORK" "$CHUNK_SIZE" <<'PY'
import json, sys
dataset, work, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
rows = [l for l in open(dataset) if l.strip()]
for i in range(0, len(rows), size):
    with open(f"{work}/chunk_{i//size:03d}.jsonl", "w") as f:
        f.writelines(rows[i:i+size])
print(f"chunks={(len(rows)+size-1)//size} problems={len(rows)}")
PY

log "FULL RUN START split=${SPLIT} trials=${TRIALS} dataset=${DATASET} merged=${MERGED}"

for chunk in "$WORK"/chunk_*.jsonl; do
  name="$(basename "$chunk" .jsonl)"
  # Resume: drop case_ids already present in the merged output.
  todo="$WORK/${name}.todo.jsonl"
  uv run python - "$chunk" "$MERGED" "$todo" <<'PY'
import json, os, sys
chunk, merged, todo = sys.argv[1:4]
done = set()
if os.path.exists(merged):
    for l in open(merged):
        try: done.add(json.loads(l).get("case_id"))
        except json.JSONDecodeError: pass
rows = [l for l in open(chunk) if l.strip() and json.loads(l)["id"] not in done]
open(todo, "w").writelines(rows)
print(f"todo={len(rows)} done={len(done)}")
PY
  if [ ! -s "$todo" ]; then
    log "SKIP ${name} (already complete)"
    continue
  fi
  log "CHUNK START ${name} remaining=$(wc -l < "$todo")"
  out="$WORK/${name}.out.jsonl"
  if uv run python scripts/evaluate_math_agent.py \
      --dataset "$todo" \
      --trials "$TRIALS" \
      --pass-k 8 \
      --output "$out" >>"$LOG" 2>&1; then
    # Append case rows (skip the trailing summary row, which has no case_id).
    uv run python - "$out" "$MERGED" <<'PY'
import json, sys
src, dst = sys.argv[1:3]
with open(dst, "a") as f:
    for l in open(src):
        if l.strip() and json.loads(l).get("case_id"):
            f.write(l if l.endswith("\n") else l + "\n")
PY
    log "CHUNK DONE ${name}"
  else
    log "CHUNK FAIL ${name} exit=$? (rerun this script to resume)"
  fi
done

log "FULL RUN COMPLETE split=${SPLIT} results=${MERGED}"
uv run python - "$MERGED" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
cases = [r for r in rows if r.get("case_id")]
v = sum(1 for r in cases if r.get("verification_status") == "verified")
c = sum(1 for r in cases if r.get("correct"))
print(f"TOTAL cases={len(cases)} correct={c} verified={v} ({v/max(1,len(cases))*100:.1f}%)")
PY
