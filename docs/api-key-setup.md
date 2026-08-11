# API key and endpoint setup

Conjecta's public Web application uses only `openai/gpt-5.6-sol` through an
OpenAI-compatible Chat Completions endpoint.

## Server configuration

Copy the example configuration and set your endpoint:

```bash
cp config.example.toml config.toml
```

```toml
[llm]
provider = "openai"
model = "gpt-5.6-sol"
base_url = "https://your-openai-compatible-endpoint.example/v1"
```

Set the endpoint's API key in the server environment:

```bash
export OPENAI_API_KEY="your-api-key"
```

For systemd, use environment variables or an `EnvironmentFile` outside the
repository:

```ini
[Service]
Environment="OPENAI_API_KEY=your-api-key"
Environment="CONJECTA_LLM_BASE_URL=https://your-openai-compatible-endpoint.example/v1"
```

## User-provided keys

Authenticated deployments may enable the API-key dialog. The public endpoint
accepts only the `openai` provider and always maps it to `gpt-5.6-sol`. Keys
stored by the server are encrypted with `CONJECTA_API_KEY_ENCRYPTION_KEY`.

## Security rules

- Never commit API keys, `.env`, or `config.toml`.
- Use a separate key for Conjecta so it can be revoked independently.
- Rotate a key immediately if it appears in a log, issue, screenshot, or commit.
- Use HTTPS and authentication before accepting user-provided keys.

See [openai-setup.md](openai-setup.md) for troubleshooting and
[SECURITY.md](../SECURITY.md) for the deployment boundary.
