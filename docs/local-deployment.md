# Local deployment

This guide runs the Conjecta Web application behind a locally configured
OpenAI-compatible endpoint. The public Web model is fixed to
`openai/gpt-5.6-sol`.

## Requirements

- Python 3.10+
- Git
- an API key and an OpenAI-compatible endpoint serving `gpt-5.6-sol`
- a modern browser

Lean 4 is optional and disabled until explicitly installed.

## Install

```bash
git clone https://github.com/conjecta/conjecta.git
cd conjecta
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

For development and tests:

```bash
pip install -e ".[dev]"
```

## Configure

```bash
cp config.example.toml config.toml
```

Edit `[llm].base_url` in `config.toml`, then set the API key:

```bash
export OPENAI_API_KEY="your-api-key"
```

Equivalent environment overrides are supported:

```bash
export CONJECTA_LLM_PROVIDER="openai"
export CONJECTA_LLM_MODEL="gpt-5.6-sol"
export CONJECTA_LLM_BASE_URL="https://your-openai-compatible-endpoint.example/v1"
```

## Run

```bash
math-agent-web
```

- Project page: <http://127.0.0.1:8000/>
- Chat app: <http://127.0.0.1:8000/app>

For the CLI:

```bash
math-agent "Prove that sqrt(2) is irrational"
```

## Verify

```bash
pytest
```

## Production boundary

The default server binds to localhost. Before exposing it to untrusted users,
add HTTPS, authentication, request limits, isolated tool runtimes, and a
dedicated database. See [SECURITY.md](../SECURITY.md).

Never commit API keys, `.env`, or `config.toml`.
