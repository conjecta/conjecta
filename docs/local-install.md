# Local installation

This guide sets up the Conjecta math agent on your machine. One install gives
you both the web UI (chat app + project page) and the `math-agent` CLI. The
chat frontend ships prebuilt with the repository — no Node.js or build step is
needed.

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Python 3.10+** | CI runs 3.10 and 3.11. Check with `python3 --version` |
| **Git** | To clone the repository |
| **API key + endpoint** | An OpenAI-compatible endpoint serving `gpt-5.6-sol`; see [endpoint setup](openai-setup.md) |

Optional:

| Component | Purpose |
|-----------|---------|
| **Lean 4** (via `elan`) | Formal proof verification, disabled by default. The agent runs fine without it — see [step 6](#6-lean-4-optional) |
| **Poppler** (`pdfinfo`, `pdftoppm`) | Page-image rendering for uploaded PDFs; on Ubuntu install `poppler-utils` |
| **Node.js 20+** | Only if you modify the frontend source — see [Working on the frontend](#working-on-the-frontend) |

## 1. Clone the repository

```bash
git clone https://github.com/conjecta/conjecta.git
cd conjecta
```

All commands below are run from the repository root — the app resolves
`config.toml`, `logs/`, and the project page relative to the checkout.

## 2. Install dependencies

Choose **one** of the following methods.

### Option A — uv (preferred)

[uv](https://docs.astral.sh/uv/) installs the exact locked dependency tree
that CI and production use (`uv.lock`):

```bash
uv sync --frozen
```

For development and tests, add the `dev` extra:

```bash
uv sync --frozen --extra dev
```

uv creates `.venv/` for you. Either prefix commands with `uv run`
(e.g. `uv run math-agent-web`) or activate the environment:

```bash
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### Option B — pip + venv

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .                 # editable install from pyproject.toml
```

For development and tests:

```bash
pip install -e ".[dev]"
```

### Option C — requirements file

Use this if you prefer an explicit `requirements.txt` (e.g. for auditing or
mirroring in another environment):

```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install -e . --no-deps
```

The `--no-deps` step registers the `math-agent` and `math-agent-web` commands
without reinstalling packages already listed in the requirements file.

## 3. Configure

```bash
cp config.example.toml config.toml      # Windows: copy config.example.toml config.toml
```

`config.toml` is gitignored. Edit the `[llm]` section to point at your
endpoint (`base_url`); the public Web application is fixed to
`openai/gpt-5.6-sol`. Put your key in an environment variable —
`OPENAI_API_KEY` — not in the config file:

```bash
export OPENAI_API_KEY="your-api-key"    # Windows: set OPENAI_API_KEY=your-api-key
```

See [endpoint setup](openai-setup.md) and [API key setup](api-key-setup.md)
for details, including environment-variable overrides
(`CONJECTA_LLM_BASE_URL`, etc.) and the optional API-key dialog for
authenticated deployments.

Optional local convenience file:

```bash
cp .env.example .env
```

Uncomment `CONJECTA_DISABLE_QUOTA=1` in `.env` to skip the platform free-tier
daily token cap on a trusted, single-user local install. The
Supabase/phone-auth values in `.env.example` are only needed for the
friends/collaboration features — see [local friends setup](local-friends-setup.md).

## 4. Start the web UI

```bash
math-agent-web          # or: uv run math-agent-web
```

Then open:

- Chat app: <http://127.0.0.1:8000/app>
- Project page: <http://127.0.0.1:8000/>

First solve in the UI:

1. Confirm the server has `OPENAI_API_KEY` and the configured endpoint.
2. Start or open a project in the sidebar.
3. Ask a math question, inspect a URL, or upload a PDF/text file.

The server binds to `127.0.0.1:8000`. If that port is taken, run
`uvicorn math_agent.web.app:app --port 8001` instead.

## 5. Run the CLI

**macOS / Linux:**

```bash
export OPENAI_API_KEY=your-api-key
math-agent "Prove that sqrt(2) is irrational"
```

**Windows (Command Prompt):**

```cmd
set OPENAI_API_KEY=your-api-key
math-agent "Prove that sqrt(2) is irrational"
```

## 6. Lean 4 (optional)

Formal verification (`lean_check`, `tactic_search`, lemma-decomposition
proofs) needs a Lean 4 toolchain and is **disabled by default**
(`[lean] enabled = false` in `config.example.toml`). Everything else works
without it.

To enable:

1. Install `elan` (the Lean version manager) from
   <https://lean-lang.org/install/> — on macOS/Linux it is
   `curl https://elan.lean-lang.org/elan-init.sh -sSf | sh`.
2. Prefetch the pinned toolchain and the mathlib cache (first run takes a
   while):

   ```bash
   math-agent-lean-setup
   ```

   This prepares `.lean_workspace/` using the toolchain pinned in
   `config.example.toml` (`leanprover/lean4:v4.30.0`).
3. Turn it on in `config.toml`:

   ```toml
   [lean]
   enabled = true
   ```

### Optional: Lean REPL for fast tactic search

With Lean enabled, the agent can drive a long-running
[Lean REPL](https://github.com/leanprover-community/repl) instead of a full
batch compile per tactic step — structured proof states and much faster
search. To opt in, set `repl_enabled = true` under `[lean]` in `config.toml`,
then build the binary once after `math-agent-lean-setup`:

```bash
cd .lean_workspace
lake update repl && lake build repl
```

The agent auto-detects `.lake/packages/repl/.lake/build/bin/repl` and falls
back to batch compilation when it is absent. Keep using *precise* imports
(`import Mathlib.Algebra...`); the umbrella `import Mathlib` does not fit
small-RAM hosts, in either mode.

## 7. Verify the installation

```bash
pytest          # requires the dev extra
```

Smoke tests run without live API calls. You should see all tests pass.

Quick manual check for PDF support:

```bash
python -c "from pypdf import PdfReader; print('pypdf OK')"
```

## Working on the frontend

The chat app (`/app`) is a React/Vite frontend whose built bundle is committed
under `math_agent/web/static/`. You only need Node.js 20+ when you modify the
source in `math_agent/web/frontend/`:

```bash
cd math_agent/web/frontend
npm ci
npm run build    # outputs to math_agent/web/static/
```

## PDF uploads

Text extraction from PDF URLs and uploads uses
[pypdf](https://pypi.org/project/pypdf/) (`pypdf>=4.0`), installed as a
regular dependency. Page-image rendering uses `pdf2image`, which needs
Poppler on the host (Ubuntu: `sudo apt install poppler-utils`).

**Limitations:**

- Text-based PDFs (papers, preprints) work best.
- Scanned or image-only PDFs often return little or no text unless OCR is
  added separately.
- Very large PDFs are truncated server-side for safety.

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `math-agent-web` not found | Activate `.venv` (or use `uv run math-agent-web`) and reinstall |
| Chat app loads a blank page | Pull the latest repo; if you edited the frontend, rebuild it (see above) |
| `uv sync --frozen` reports a stale lock | Pull the latest repo — `uv.lock` is the authoritative dependency tree |
| API errors in the UI | Check `OPENAI_API_KEY`, `[llm].base_url`, and [endpoint setup](openai-setup.md) |
| Lean tools report `lake not found` | Expected while `[lean] enabled = false`; to enable Lean see step 6 |
| `pypdf is not installed` | `pip install "pypdf>=4.0"` |
| PDF URL returns no text | PDF may be scanned; try a text/HTML source |
| Port 8000 in use | Stop the other process or run `uvicorn math_agent.web.app:app --port 8001` |

Logs are written to `logs/math-agent.log` and per-session files under
`logs/sessions/`.

## Dependency summary

| Package | Role |
|---------|------|
| `fastapi`, `uvicorn`, `websockets` | Web UI and streaming |
| `httpx` | Fetch URLs for inspection |
| `pypdf`, `pdf2image` | Extract text / render pages from PDF URLs and uploads |
| `openai` | OpenAI-compatible `gpt-5.6-sol` backend |
| `sympy` | Symbolic computation tool |
| `pytest`, `pytest-asyncio` | Tests (dev only) |

## Next steps

- [API key setup](api-key-setup.md) — where to create keys and where to paste/export them
- [GPT-5.6 Sol endpoint setup](openai-setup.md) — key, base URL, and troubleshooting
- [README](../README.md) — architecture overview and configuration table
