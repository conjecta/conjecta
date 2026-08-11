# Conjecta production workflow

This document describes the active production architecture. It is a contract,
not a catalog of every historical module still present in the repository.

## One solve engine

Both `math_agent/main.py` and `math_agent/web/solve_session.py` construct a
`SupervisorAgent`. The supervisor always delegates mathematical execution to
`ReActAgent`.

```text
CLI ───────────────┐
HTTP /api/solve ───┼─> SupervisorAgent
WS /ws/solve ──────┘      |
                           |-- exact checkpoint hydration
                           |-- SupervisorIntake (intent/source/search)
                           |-- bounded ContextAugmentor retrieval
                           `-- ReActAgent
                                  |-- Thought / Action / Observation
                                  |-- ToolRegistry
                                  |-- bounded reviewer panel
                                  `-- conclude / best effort / blocked
```

The accepted transport modes are `auto` and `react` only. Both run the same
ReAct engine; `react` remains an API-compatible alias for ordinary solve.
Formal-first behavior is **not** a mode: when the problem requires formal
verification and the first round does not reach `verified`, the supervisor runs
bounded replan rounds under larger budgets (`escalation_*`), with planning and
review forced on and Lean failure diagnostics injected as context. Intake may
identify a clarifying follow-up and reduce steps/reviewers in the ordinary
engine. A matching checkpoint hydrates the actual serialized trace, plan,
completed actions, next step, and remaining budgets before intake. Historical
`strategy == "research"` checkpoints may still resume for old sessions.

Historical CoT, staged scheduler, geeky, and standalone research modules are
not active production solve routes. Do not add a second, parallel, or legacy solve path.
New behavior belongs in explicit `SupervisorAgent`
intake/context/resume/escalation seams, `ReActAgent`, `ToolRegistry`,
reviewers, or their configured budgets.

## Request and stream lifecycle

1. HTTP middleware or WebSocket pre-accept checks authentication.
2. A valid tenant is resolved before the WebSocket is accepted and before an
   agent/store is constructed.
3. Intake classifies conversation intent and performs bounded source/search
   preparation. Untrusted source text remains data, never instructions.
4. Trusted project memory and plan memory are retrieved with fixed limits and
   positive relevance thresholds.
5. ReAct executes with wall-time, step, action, model-call, tool-call, retry,
   reviewer, and token budgets.
6. Reviewers inspect tool actions and the conclusion. Formal claims may require
   an actual successful Lean observation. Each formal observation receives a
   claim-bound evidence ID; a conclusion can only use evidence from its current
   revision scope.
7. `stream_solve_events()` owns the single terminal event. WebSocket and HTTP
   disconnects close the same generator and cancel the same solve task.
8. The completed turn is persisted before `done`; bounded post-solve memory
   work is scheduled in the retained task registry and cannot hold the response
   open.

## Durable human-in-the-loop boundary

HITL pauses do not keep an HTTP connection, asyncio task, or model call alive.
Before pausing, the runtime writes checkpoint schema v4 with a JSON-serializable
`pending_interaction` containing its stable request id, stage, decision set, and
the exact proposed action or proof graph. The solve stream then ends with
`human_input_required`, not `done` and not `error`.

The browser submits a decision to
`POST /api/solve/{checkpoint_id}/decisions/stream`. The tenant-local store
claims that request once before execution, the supervisor hydrates the original
trace, and the engine applies approve/reject/edit/respond without regenerating
an approved tool call. A submitted decision is also kept in the checkpoint so
an interrupted resume can recover it. Completed-turn persistence and post-solve
memory work run only after a real `done` result.

Configured approval boundaries are selective: read-only computation, search,
source reads, and Lean checks may run automatically; project-memory writes can
require approval. Long formal solves may additionally offer proof-graph review
and escalate concrete counterexamples. Nested lemma/route workers keep HITL
disabled so nested pauses cannot disappear inside parallel attempt collection;
their reviewed results return to the outer durable boundary.

Independent ready DAG nodes may still be attacked concurrently inside
`prove_by_lemmas` / lemma execution (bounded by `lean.lemma_max_parallel`).
Every worker receives an isolated context; proof-graph mutations are committed
serially in deterministic goal order. A counterexample replan is a graph
transaction and invalidates other uncommitted results in that batch. Lean
checks remain bounded globally by `lean.max_concurrent_checks` and use
UUID-named job files.

Within a leaf goal, independently reviewed routes form a cross-route quorum. A
formally `verified` route is accepted immediately — no quorum count and no
cross-check judge (two verified routes are compared on their stored
formal-evidence signatures instead). Escalation / formal-first rounds never
skip the reviewer panel (`force_review`), so a passing attempt always earns its
`reviewed`/`verified` status honestly; the skip-review shortcuts apply only to
ordinary informal solves. Failed routes leave a bounded, deduplicated finding
on the goal's issues, which later attempts, repair helpers, and decomposition
see through the proof-graph context block.

The trace contains a persistent proof-goal DAG alongside the readable current
goal. `set_goal` can declare stable goal IDs and dependencies; formal attempts
update the active node with attempts, issues, and accepted evidence. Optional
reviewer-ranked Best-of-N expands only informal conclusions and is controlled by
`conclusion_candidate_count` plus `candidate_search_min_turns`.

## Verification contract

`ReActSolution.verification_status` is intentionally not a Boolean:

- `verified`: the formal requirement succeeded and the accepted conclusion is
  supported by the matching successful formal tool observation;
- `reviewed`: the reviewer panel accepted the conclusion without claiming a
  Lean proof;
- `unreviewed`: the run completed but review was skipped (fast easy-answer path or no reviewer configured); not a claim of review or proof.
- `best_effort`: the bounded run returned its strongest supported answer but
  did not meet the stronger acceptance boundary;
- `blocked`: a required review/formal boundary rejected completion.

These labels derive from the four-dimension `VerificationOutcome` protocol in
`math_agent/agent/verification.py`.

Reviewer prose and model self-report cannot create `verified` status or trusted
Lean memory. Transport/UI code must display the status unchanged.

Required formal verification is controlled by one `[verifier].formal_policy`:
`explicit` (default), `all_theorems`, or `disabled`. The legacy
`require_lean_for_theorems=true` setting is treated as `all_theorems` rather
than existing as a disconnected preference.

Untrusted Lean source passes a verifier-first safety gate before any compiler
process starts. The gate rejects proof placeholders/axiom aliases,
compile-time IO and metaprogramming commands, and imports outside the approved
proof-library prefixes. Each check uses a unique source file, a process-wide
concurrency limit, a minimal secret-free environment, and a dedicated process
group killed on timeout/cancellation. Mathlib dependencies are shared
read-only; result caching is shared across request-local runners.

## Knowledge and memory authority

Supabase `KnowledgeStore` is authoritative when configured. Otherwise the
tenant-local `ProjectStore` is the single fallback. The evaluator is never
given two writers for the same extracted memory.

Memory lifecycle:

```text
agent extraction -> candidate -> approved
                              \-> verified (matching successful formal evidence)
                              \-> rejected
```

Only `approved`, `reviewed`, and `verified` records enter solve-time context
(see `KnowledgeTrustPolicy`). Administration
may list every status. Verified records cannot be revised or discarded by an
ordinary model evaluator. Plan memory and graph roots are tenant-local; the
shared seed plan file is read-only.

The cloud schema must be applied manually in this order:

1. `docs/supabase_knowledge_schema.sql`
2. `docs/supabase_tenant_schema.sql`

The migration step is deliberately outside deploy automation. Review and back
up the database first. The scripts are idempotent, preserve legacy knowledge as
explicitly approved, create `conjecta_users`/`conjecta_projects`, enable RLS,
and do not mutate unrelated generic tables.

## Authentication and proxy boundary

Browser phone authentication is cookie-only. The JWT lives in a `Secure`,
`HttpOnly`, `SameSite=Lax` cookie and is not persisted to browser
`localStorage`. Non-browser API clients may use the documented bearer path.

The application derives client identity from the ASGI socket peer. If and only
if that peer belongs to `CONJECTA_TRUSTED_PROXY_CIDRS`, it may consume Nginx's
overwritten `X-Real-IP` and `X-Forwarded-Proto`. It never uses raw
`X-Forwarded-For` for local authorization or rate-limit keys.

Therefore production Uvicorn must include:

```text
--no-proxy-headers --ws-max-size 16777216
```

`--no-proxy-headers` prevents Uvicorn from replacing the socket peer before the
application applies its own trust decision. The WebSocket envelope is 16 MiB;
the decoded application attachment cap must remain lower.

The Conjecta Nginx server block must include the equivalent of:

```nginx
client_max_body_size 16m;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
```

Nginx must overwrite those values rather than append client input. Direct-IP
HTTP should redirect to the canonical HTTPS host. Production also disables
unauthenticated access, SMS debug output, and API docs; uses a 32-byte-or-longer
JWT secret; and configures a nonzero request rate without logging secrets.

PDF processing additionally requires the Ubuntu `poppler-utils` package. Both
`pdfinfo -v` and `pdftoppm -v` must succeed before the service is released.

## CI boundary

`.github/workflows/ci.yml` grants only `contents: read` and runs three gates:

1. Python compile/tests across supported Python versions;
2. Node 20.19 frontend `npm ci`, typecheck, Vitest `--run`, temporary production
   build, and `npm audit --audit-level=low`;
3. a portable Lean source-safety scan using POSIX ERE token boundaries. It
   rejects proof placeholders, axiom aliases, and compile-time execution forms.
   Grep status 0
   means blocked tokens, 1 means no match, and values greater than 1 are CI
   errors rather than false passes.

## Deployment boundary

Run `scripts/deploy_conjecta.sh [ref]` only after the tested commit is published
and the two Supabase migrations have been reviewed/applied manually when
needed. Set `CONJECTA_NGINX_SITE` to the enabled Conjecta site file so the
preflight validates this application rather than another virtual host. The script:

1. verifies Poppler, systemd Uvicorn flags, the Nginx 16 MiB envelope, and takes
   a non-blocking deployment lock;
2. stores tracked/untracked dirty state in a timestamped, reversible stash;
3. fetches origin, resolves the exact requested commit, switches to `main`, and
   uses only `git merge --ff-only`, then makes root-created tracked source and
   package directories readable by the non-root service group;
4. prefers `uv sync --frozen --no-dev --python /usr/bin/python3.10` into a
   separate release virtualenv, with a venv/pip fallback on other hosts;
5. runs `npm ci`, builds to a release directory, checks every `/static/` asset
   referenced by `index.html`, and confirms the result matches tracked assets;
6. runs `nginx -t`, stops the service, swaps the prepared virtualenv/static
   directories, and restarts systemd;
7. retries `/healthz`, checks `systemctl is-active`, and asserts deployed `HEAD`
   equals the exact target commit.

Previous runtime directories and the stash remain timestamped and are reported
for recovery. If activation fails after a swap, the script restores the prior
virtualenv/static directories and restarts the service. It does not hard-reset,
clean the repository, print environment secrets, or execute database SQL.

## Tiered evaluation benchmark

A developer-run benchmark gate lives in `data/eval/` and `scripts/run_benchmark.sh`.
It is **not** executed by GitHub CI (cost, secrets, mathlib, non-determinism).

- `data/eval/fast.jsonl` — ~40 hand-authored cases, no Lean, `trials=1`.
  Run with `scripts/run_benchmark.sh` for a quick accuracy/latency/per-tag
  regression check. An optional `--fast-floor 0.55` enforces a minimum accuracy.
- `data/eval/formal.jsonl` — ~20 miniF2F-style formal cases, `trials=3`.
  Run separately because each case may invoke `lake build`. The script exits
  non-zero if any case is falsely verified.
- `data/eval/research.jsonl` — 24 cases split across `decompose`, `tool_heavy`,
  and `formal` tags. Run with `--mode auto` (formal escalation triggers when
  required); the report still tracks lemma success, counterexample/replan
  activity, peak parallelism, per-tool counts, and wall-time components.
  The old `--mode research` / `--research-max-parallel-goals` flags are gone
  (see `docs/research-mode.md`).
- Results are archived under `data/eval-results/` (gitignored), and a schema
  validation test in `tests/test_eval_datasets.py` runs in CI to guard malformed
  or duplicate-id rows.

Capture a new fast-tier baseline after any change that affects the ReAct core,
Lean pipeline, or evaluation judges. The formal tier is run on demand when the
verified/false-verified signal is specifically under review.

## Active code map

```text
math_agent/
├── main.py                         # CLI transport
├── web/
│   ├── app.py                      # FastAPI/auth/routes
│   ├── solve_session.py            # shared HTTP/WS solve stream
│   ├── security.py                 # tenant/proxy/rate boundary
│   └── attachments.py              # bounded image/PDF intake
├── agent/
│   ├── supervisor.py               # sole production orchestrator
│   ├── supervisor_intake.py        # bounded intent/source intake
│   ├── react_agent.py              # sole production solve engine
│   ├── subagent.py                 # bounded worker construction contract
│   ├── reviewers.py                # critic/formal/knowledge/fidelity
│   ├── tools.py                    # tool registry
│   ├── memory_consolidation.py     # candidate extraction/formal trust
│   └── plan_memory.py              # verified tenant-local plan retrieval
├── knowledge/supabase.py           # authoritative cloud knowledge store
├── verification/                   # shared verification reports/backends
└── lean/                           # Lean execution and placeholder gate
```
