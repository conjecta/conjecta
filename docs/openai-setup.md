# GPT-5.6 Sol endpoint setup

Conjecta's public Web application is intentionally fixed to one model:
`openai/gpt-5.6-sol`. The endpoint must implement the OpenAI-compatible Chat
Completions API.

## Prerequisites

- Python 3.10 or newer
- an OpenAI-compatible endpoint that serves `gpt-5.6-sol`
- an API key accepted by that endpoint

## Configure the endpoint

Copy the example configuration:

```bash
cp config.example.toml config.toml
```

Set the endpoint in the `[llm]` section:

```toml
[llm]
provider = "openai"
model = "gpt-5.6-sol"
base_url = "https://your-openai-compatible-endpoint.example/v1"
temperature = 0.7

[llm.critic]
provider = "openai"
model = "gpt-5.6-sol"
temperature = 0.2
```

The critic inherits `[llm].base_url` when both roles use the same provider.
`config.toml` is ignored by Git.

Set the API key in the environment:

```bash
export OPENAI_API_KEY="your-api-key"
```

The same settings can be supplied entirely through environment variables:

```bash
export OPENAI_API_KEY="your-api-key"
export CONJECTA_LLM_PROVIDER="openai"
export CONJECTA_LLM_MODEL="gpt-5.6-sol"
export CONJECTA_LLM_BASE_URL="https://your-openai-compatible-endpoint.example/v1"
```

## Run Conjecta

```bash
math-agent-web
math-agent "Prove that the sum of two even integers is even"
```

- Project page: <http://127.0.0.1:8000/>
- Chat app: <http://127.0.0.1:8000/app>

The Web client always sends `openai/gpt-5.6-sol`; there is no client-side
model picker. Server-side platform requests and knowledge workflows use the
configured `[llm].base_url`.

## User API keys

Authenticated deployments may let a user bind an OpenAI-compatible API key
from the usage dialog. The public API accepts only the `openai` provider and
maps it to `gpt-5.6-sol`. Stored keys are encrypted with
`CONJECTA_API_KEY_ENCRYPTION_KEY`; see `.env.example` and `SECURITY.md`.

## Troubleshooting

### `OpenAI API key required`

Confirm that `OPENAI_API_KEY` is set in the process that starts Conjecta.

### `404` or unknown model

Confirm that the configured endpoint exposes the exact model ID
`gpt-5.6-sol` and that `base_url` includes the provider's required API prefix,
usually `/v1`.

### `401` or `403`

Check that the API key belongs to the configured endpoint and has permission
to call `gpt-5.6-sol`.

### Connection errors

Check DNS, TLS, proxy settings, and whether the endpoint is reachable from the
Conjecta server process. Enable debug logging in `config.toml` when diagnosing
request failures:

```toml
[logging]
enabled = true
level = "DEBUG"
dir = "logs"
```

Never commit API keys, `.env`, or `config.toml`.
