# Local installation

This guide covers setting up Conjecta math-agent on your machine for the web UI and CLI.

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Python 3.11+** | Check with `python --version` or `python3 --version` |
| **Git** | To clone the repository |
| **LLM API key** | [DeepSeek](deepseek-setup.md) (recommended) or [OpenAI](openai-setup.md) |

Optional:

| Component | Purpose |
|-----------|---------|
| **Lean 4** | Formal proof verification (disabled by default in `config.example.toml`) |
| **Modern browser** | Web UI; Chrome/Edge/Firefox for folder picker features |

## 1. Clone and enter the repo

```bash
git clone https://github.com/wangt1anyu/math-agent.git
cd math_agent
```

## 2. Create a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your shell prompt should show `(.venv)` when the environment is active.

## 3. Install dependencies

Choose **one** of the following methods.

### Option A — Editable install (recommended)

Installs the package and all runtime dependencies (including the PDF parser) from `pyproject.toml`:

```bash
pip install --upgrade pip
pip install -e .
```

For development and tests:

```bash
pip install -e ".[dev]"
```

### Option B — Requirements file

Use this if you prefer an explicit `requirements.txt` (e.g. for auditing or mirroring in another environment):

```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install -e . --no-deps
```

For development:

```bash
pip install -r requirements-dev.txt
pip install -e . --no-deps
```

The `--no-deps` step registers the `math-agent` and `math-agent-web` CLI commands without reinstalling packages already listed in the requirements file.

## 4. PDF parsing (`pypdf`)

URL inspection and uploaded materials can include **PDF** documents. Text extraction uses **[pypdf](https://pypi.org/project/pypdf/)** (`pypdf>=4.0`), which is included in both `pyproject.toml` and `requirements.txt`.

If PDF support is missing, the web UI will report an error like:

```text
pypdf is not installed; run `pip install pypdf` to enable PDF parsing.
```

Fix it with:

```bash
pip install "pypdf>=4.0"
```

**Limitations:**

- Text-based PDFs (papers, preprints) work best.
- Scanned or image-only PDFs often return little or no text unless OCR is added separately.
- Very large PDFs are truncated server-side for safety.

## 5. Configuration (optional for web UI)

Copy the example config if you use the **CLI** or want server-side defaults:

```bash
# Windows
copy config.example.toml config.toml

# macOS / Linux
cp config.example.toml config.toml
```

Edit `config.toml` for provider, Lean, and logging. The **web UI** can store API keys in the browser instead — see [DeepSeek setup](deepseek-setup.md) or [OpenAI setup](openai-setup.md).

### Optional: Lean REPL for fast tactic search

With Lean 4 enabled (`[lean] enabled = true`, `mathlib_dep = true`), the agent
can drive a long-running [Lean REPL](https://github.com/leanprover-community/repl)
instead of a full batch compile per tactic step — structured proof states and
an order of magnitude faster search. To enable:

```toml
[lean]
repl_enabled = true
```

then build the binary once (must match the pinned toolchain/mathlib rev):

```bash
cd .lean_workspace
lake update repl && lake build repl
```

The agent auto-detects `.lake/packages/repl/.lake/build/bin/repl` and falls
back to batch compilation when it is absent. Note: keep using *precise*
imports (`import Mathlib.Algebra...`); the umbrella `import Mathlib` does not
fit small-RAM hosts, in either mode.

## 6. Run the web UI

```bash
math-agent-web
```

Open [http://127.0.0.1:8000/app](http://127.0.0.1:8000/app) for the chat UI. The project overview is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

1. Click **API Keys** and paste your provider key.
2. Start or open a project in the sidebar.
3. Ask a math question, inspect a URL, or upload a PDF/text file.

## 7. Run the CLI

**Windows:**

```cmd
set DEEPSEEK_API_KEY=sk-your-key-here
math-agent "Prove that sqrt(2) is irrational"
```

**macOS / Linux:**

```bash
export DEEPSEEK_API_KEY=sk-your-key-here
math-agent "Prove that sqrt(2) is irrational"
```

## 8. Verify the installation

```bash
pytest
```

Smoke tests run without live API calls. You should see all tests pass.

Quick manual check for PDF support:

```bash
python -c "from pypdf import PdfReader; print('pypdf OK')"
```

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `math-agent-web` not found | Activate `.venv` and run `pip install -e .` again |
| `pypdf is not installed` | `pip install "pypdf>=4.0"` |
| PDF URL returns no text | PDF may be scanned; try a text/HTML source |
| API errors in the UI | Set key under **API Keys**; see [deepseek-setup.md](deepseek-setup.md) or [openai-setup.md](openai-setup.md) |
| Port 8000 in use | Stop the other process or run `uvicorn math_agent.web.app:app --port 8001` |

Logs are written to `logs/math-agent.log` and per-session files under `logs/sessions/`.

## Dependency summary

| Package | Role |
|---------|------|
| `fastapi`, `uvicorn`, `websockets` | Web UI and streaming |
| `httpx` | Fetch URLs for inspection |
| **`pypdf`** | **Extract text from PDF URLs and uploads** |
| `openai` | LLM backend (DeepSeek also uses the OpenAI-compatible client) |
| `sympy` | Symbolic computation tool |
| `pytest`, `pytest-asyncio` | Tests (dev only) |

## Next steps

- [API key setup](api-key-setup.md) — where to create keys and where to paste/export them
- [DeepSeek API setup](deepseek-setup.md) — keys, models, troubleshooting
- [OpenAI API setup](openai-setup.md) — ChatGPT API keys and models
- [README](../README.md) — architecture overview and configuration table
