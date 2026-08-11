# Contributing

Contributions should keep the single production solve path built around
`SupervisorAgent` and `ReActAgent`, preserve verification-status semantics, and
include tests proportional to the behavioral change.

## Development setup

```bash
uv sync --frozen --extra dev
uv run ruff check math_agent scripts tests
uv run mypy
uv run pytest -q

cd math_agent/web/frontend
npm ci
npm run typecheck
npm test -- --run
npm run build
```

Do not commit `.env`, `config.toml`, generated benchmark JSONL files, runtime
logs, user material, or local agent/editor configuration.

## Pull requests

- explain the user-visible or trust-boundary change;
- add regression tests for fixes;
- keep dependency and lock-file changes together;
- confirm that `pip-audit` and `npm audit --audit-level=low` pass;
- regenerate frontend assets and `THIRD_PARTY_FRONTEND_LICENSES.txt` when the
  frontend or its dependencies change.

Report security issues through the private process in `SECURITY.md`.
