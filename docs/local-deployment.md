# Conjecta Math Agent · Local Deployment Tutorial

> A friendly, end-to-end guide for getting the Conjecta math agent running on your own
> computer — both the **web chat UI** and the **command-line tool**. Just follow it top to bottom.
>
> Every step here was tested and verified on **Windows 11 + Python 3.13**. The macOS / Linux
> equivalents are included throughout.

---

## 0. What is this?

Conjecta is an agent that solves math problems *and* checks its own work step by step:

1. The LLM generates **one reasoning step at a time**.
2. Each step is verified by a "critic" model (and optionally by Lean 4 for formal proofs).
3. Failed steps are auto-rewritten up to 3 times.
4. The web UI streams every step, its verification status, and the final answer to your
   browser in real time over WebSocket.

All you need to provide is **Python** and **one LLM API key** (DeepSeek recommended).

---

## 1. Prerequisites

| Requirement | Why | How to check |
|-------------|-----|--------------|
| **Python 3.11+** | The runtime (3.13 verified) | Run `python --version` |
| **Git** | To clone the code | Run `git --version` |
| **An LLM API key** | [DeepSeek](https://platform.deepseek.com/api_keys) recommended; OpenAI also supported | See Step 2 |
| **A modern browser** | Chrome / Edge / Firefox | — |

> **Optional:** Lean 4. It enables *formal* proof verification but is a heavy install.
> It is **disabled by default** in this tutorial — beginners can skip it.

---

## 2. Get an API key (required)

The agent does not bundle an LLM; you supply a key so it can call a cloud model.


> ⚠️ Treat the key like a password: never commit it to Git, post it in chats, or put it in screenshots.
>
> Prefer OpenAI? Create a key at <https://platform.openai.com/api-keys> and use the
> environment variable `OPENAI_API_KEY` instead.

---

## 3. Get the code

```bash
git clone https://github.com/wangt1anyu/Conjecta-v0.git
cd Conjecta-v0
```

> 💡 **Heads-up:** After cloning, always `cd Conjecta-v0` before running the next commands, and
> confirm you are in the project root (you should see `pyproject.toml` and `README.md` there).

---

## 4. Create a virtual environment

A virtual environment keeps the dependencies isolated from your system Python.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> If PowerShell blocks the activation script, run this once and retry:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

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

When activated, your prompt shows `(.venv)`.

---

## 5. Install the project

From the project root, with the virtual environment active:

```bash
python -m pip install --upgrade pip
pip install -e .
```

> For tests / development, use: `pip install -e ".[dev]"`

This registers three commands: `math-agent` (CLI), `math-agent-web` (web UI), and
`math-agent-lean-setup` (Lean installer helper).

---

## 6. Create a config file (recommended)

For a **clean, warning-free** startup, create a `config.toml` in the project root that turns
off the heavy Lean dependency:

```toml
[llm]
provider = "deepseek"
model = "deepseek-v4-pro"
temperature = 0.7

[llm.critic]
provider = "deepseek"
model = "deepseek-v4-flash"
temperature = 0.2

[agent]
max_react_steps = 20
max_scheduler_iterations = 100
max_retries_per_stage = 3
tools = ["compute", "search", "fetch_url", "searching"]
reviewers_enabled = ["critic", "knowledge"]
memory_consolidation_enabled = true
artifact_root = "data/artifacts"

[verifier]
strictness = "high"
require_lean_for_theorems = false
prefer_lean = false
fallback_to_human = true

[lean]
enabled = false
mathlib_dep = false

[logging]
enabled = true
level = "INFO"
dir = "logs"
```

> Why: if you skip `config.toml`, the app falls back to the bundled `config.example.toml`,
> which has `lean.enabled = true` and will try to install Lean/mathlib on startup. Without Lean
> installed it **won't crash** (you just get a warning in the logs), but turning it off as above
> avoids confusion. `config.toml` is gitignored, so your local settings stay private.

---

## 7. Run the web UI (recommended)

```bash
math-agent-web
```

Success looks like:

```text
Uvicorn running on http://127.0.0.1:8000
```

Then open in your browser:

- **Project page:** <http://127.0.0.1:8000/>
- **Chat app:** <http://127.0.0.1:8000/app>

In the chat app:

1. Open **Usage & API Key** from the user menu.
2. Enter a public HTTPS OpenAI-compatible Base URL and its API Key, then click **Save**.
   The server stores them together in the user's encrypted Supabase record and never returns the key.
3. User-endpoint requests always use `gpt-5.6-sol`.
4. Type a math question and press Enter to watch the step-by-step reasoning and verification.

> Stop the server: press `Ctrl + C` in the terminal running `math-agent-web`.

---

## 8. Use the CLI (optional)

When you'd rather skip the browser, ask questions directly from the terminal. Set the key first:

**Windows (PowerShell):**

```powershell
$env:DEEPSEEK_API_KEY = "sk-your-key"
math-agent "Prove that sqrt(2) is irrational"
```

**Windows (CMD):**

```cmd
set DEEPSEEK_API_KEY=sk-your-key
math-agent "Prove that sqrt(2) is irrational"
```

**macOS / Linux:**

```bash
export DEEPSEEK_API_KEY=sk-your-key
math-agent "Prove that sqrt(2) is irrational"
```

Each reasoning step, the critic's feedback, and the final summary print to the terminal.

---

## 9. Verify the installation

```bash
pip install -e ".[dev]"   # if you haven't installed the dev extras yet
pytest
```

> On Windows, **one test in `tests/test_web_logs.py` may fail**. The cause: Windows auto-converts
> the newline `\n` into `\r\n`, so the test's string comparison is off by one `\r`. This is a
> cross-platform quirk of the test itself and **does not affect functionality** — if every other
> test passes, your install is good.

Quick PDF-support check:

```bash
python -c "from pypdf import PdfReader; print('pypdf OK')"
```

---

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `math-agent-web` not found | Make sure the venv is active (prompt shows `(.venv)`), then re-run `pip install -e .` |
| Port 8000 already in use | Run on another port: `uvicorn math_agent.web.app:app --port 8001`, then browse to `:8001` |
| Browser asks you to rebind the API endpoint | Open **Usage & API Key** and enter both a public HTTPS Base URL and API Key |
| `401` authentication error | Key is wrong / revoked / pasted with stray spaces — create a new one |
| `429` or quota error | Check your DeepSeek balance and rate limits, or switch to the cheaper **V4 Flash** |
| Browser `Connection error` | Confirm `math-agent-web` is still running and you're on `http://127.0.0.1:8000/app` |
| Slow or empty answers | V4 models use "thinking mode"; first token is slower. Set log `level` to `DEBUG` for detail |
| `pypdf is not installed` | Run `pip install "pypdf>=4.0"` |

Logs live at `logs/math-agent.log`, with per-session files under `logs/sessions/`.

---

## 11. Security notes

- The server listens on `127.0.0.1` only and is meant for **local development**. Do not expose
  port 8000 to the internet without adding authentication and HTTPS first.
- Never commit your API key, post it in chats, or leak it in screenshots. Use a dedicated key for
  Conjecta so it can be revoked independently.
- Server logs automatically redact key-shaped strings.

---

## 12. Next steps

- Enable Lean 4 formal verification: set `[lean] enabled = true` in `config.toml`, then run
  `math-agent-lean-setup`.
- Models and billing details: see [docs/deepseek-setup.md](deepseek-setup.md) and
  [docs/openai-setup.md](openai-setup.md).
- How it works under the hood: see [docs/workflow.mmd](workflow.mmd) and [README.md](../README.md).

Happy proving with Conjecta! 🎉
