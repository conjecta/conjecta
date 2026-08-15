# OpenAI (ChatGPT) API Setup Guide

This tutorial walks you through connecting the Conjecta math agent to the [OpenAI API](https://platform.openai.com). OpenAI models work well for general reasoning and are a good alternative when you already have ChatGPT API access.

For cost-effective math reasoning, [DeepSeek](deepseek-setup.md) is the recommended default provider.

## Prerequisites

- Python 3.11 or newer
- The math-agent package installed (`pip install -e .` from the repo root)
- An OpenAI account with API billing enabled

## Step 1 — Create an OpenAI API key

1. Go to [https://platform.openai.com](https://platform.openai.com) and sign in (or create an account).
2. Open **API keys** in the dashboard: [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
3. Click **Create new secret key**, give it a name (e.g. `conjecta-local`), and copy the key.
4. Store the key somewhere safe. OpenAI shows it only once.

Your key looks like `sk-proj-...` or `sk-...`. Treat it like a password — do not commit it to git or share it publicly.

### Enable billing

OpenAI requires a payment method for API usage:

1. Open [Billing](https://platform.openai.com/settings/organization/billing) in the dashboard.
2. Add a payment method and set usage limits if desired.

Without billing, requests will fail with `429` or `insufficient_quota` errors.

## Step 2 — Choose your setup path

| Path | Best for | Key storage |
|------|----------|-------------|
| **Web UI** | Hosted interactive use with fixed `gpt-5.6-sol` | Encrypted Supabase user record |
| **CLI + config.toml** | Scripts, automation, terminal workflows | Environment variable or shell profile |

You can use both; the web UI overrides config when you paste a key in the browser.

---

## Path A — Web UI setup (recommended)

### 1. Start the server

From the repo root with your virtual environment activated:

```bash
math-agent-web
```

You should see:

```
Uvicorn running on http://127.0.0.1:8000
```

### 2. Open the chat UI

Visit [http://127.0.0.1:8000/app](http://127.0.0.1:8000/app) in your browser. The project overview is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

### 3. Add your API key

1. Open **Usage & API Key** from the user menu.
2. Enter `https://api.openai.com/v1` as the Base URL and paste your API Key.
3. Click **Save**.

The Base URL and key are encrypted and stored in the signed-in user's Supabase record. The key is never returned to the browser after it is saved.

### 4. Select a model

The user-endpoint flow has no model selector. Conjecta always requests `gpt-5.6-sol`, so the account and endpoint must expose that exact model ID.

### 5. Start your math research with Conjecta

Ask a question, inspect a paper URL, or upload a source — Conjecta will reason step by step and verify each step as it goes.

---

## Path B — CLI setup

### 1. Set the environment variable

**Windows (PowerShell):**

```powershell
$env:OPENAI_API_KEY = "sk-your-key-here"
```

**Windows (Command Prompt):**

```cmd
set OPENAI_API_KEY=sk-your-key-here
```

**macOS / Linux:**

```bash
export OPENAI_API_KEY=sk-your-key-here
```

To make this permanent, add the `export` line to `~/.bashrc`, `~/.zshrc`, or your shell profile.

### 2. Create `config.toml`

Copy the example config:

```bash
cp config.example.toml config.toml
```

Edit `[llm]` and `[llm.critic]` for OpenAI:

```toml
[llm]
provider = "openai"
model = "gpt-4o"
temperature = 0.7
# API key from OPENAI_API_KEY environment variable

[llm.critic]
provider = "openai"
model = "gpt-4o-mini"    # faster/cheaper model for verification
temperature = 0.2
```

`config.toml` is gitignored so your local settings stay private.

### 3. Run the agent

```bash
math-agent "Prove that the sum of two even integers is even"
```

Output streams to the terminal: each reasoning step, critic feedback, and the final summary.

---

## Model reference

| UI / config name | API model ID | Notes |
|------------------|--------------|-------|
| GPT-4o | `gpt-4o` | Default for hard problems |
| GPT-4o Mini | `gpt-4o-mini` | Faster, lower cost |
| o3-mini | `o3-mini` | Reasoning model |

**API endpoint:** `https://api.openai.com/v1` (OpenAI Chat Completions).

**Official docs:** [https://platform.openai.com/docs](https://platform.openai.com/docs)

Model availability and pricing change over time — check the [OpenAI pricing page](https://openai.com/api/pricing/) before running large benchmarks.

---

## Troubleshooting

### `OpenAI API key required`

- Web UI: open **Usage & API Key** and save both `https://api.openai.com/v1` and the key.
- CLI: confirm `echo $OPENAI_API_KEY` (or `echo %OPENAI_API_KEY%` on Windows) prints your key.

### `401` / authentication errors

- Key may be wrong or revoked — create a new key on the OpenAI dashboard.
- Check for extra spaces when pasting.
- Project-scoped keys (`sk-proj-...`) must belong to the project with billing enabled.

### `429` / rate limits or quota errors

- Review usage at [https://platform.openai.com/usage](https://platform.openai.com/usage).
- Add or update your payment method under Billing.
- Switch to **GPT-4o Mini** for lower cost per request.
- Reduce concurrency if running benchmarks.

### `Connection error` in the browser

- Ensure `math-agent-web` is running.
- Use `http://127.0.0.1:8000/app` for the chat UI, not a stale tab from a previous session.

### Slow or empty responses

- Reasoning models (o3-mini) can take longer before the first visible token.
- Check `logs/math-agent.log` for request/response and tool-call details.
- Per-session logs live in `logs/sessions/`.

### Enable debug logging

In `config.toml`:

```toml
[logging]
enabled = true
level = "DEBUG"
dir = "logs"
```

At **INFO** level (default), tool calls (`compute`, `search`, `web_fetch`) and web planner actions are already recorded. Use **DEBUG** for full LLM request/response details.

Restart the server or CLI after changing config.

---

## Security notes

- **Authenticated storage:** The hosted Web UI encrypts the Base URL and API key in the signed-in user's Supabase record. Use HTTPS and authentication for any public deployment.
- **Never commit keys:** `config.toml` and `.env` are gitignored. Use environment variables in CI.
- **Key redaction:** Server logs redact API key values automatically.

---

## Verify your installation

Run the smoke tests (no real API calls):

```bash
pip install -e ".[dev]"
pytest tests/test_smoke.py -v
```

Manual check without a key:

```bash
python -c "from math_agent.llm.openai import OpenAICompatibleBackend; OpenAICompatibleBackend(api_key='sk-test')"
```

Should print no error and create a backend with `model=gpt-4o` (default model passed explicitly if needed).

---

## Next steps

- Compare with [DeepSeek setup](deepseek-setup.md) for a lower-cost alternative.
- Read the [workflow diagram](workflow.mmd) to understand the agent loop.
- Try harder problems: olympiad-style inequalities, number theory, combinatorics.
