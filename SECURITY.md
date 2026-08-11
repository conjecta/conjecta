# Security Policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting for the public repository. Do not
open a public issue with exploit details, credentials, user data, or a working
proof of concept. Include the affected commit, configuration, impact, and the
smallest reproduction that demonstrates the issue.

## Deployment boundary

Conjecta is safe by default for local, trusted-user development. A public or
multi-tenant deployment needs additional controls outside this repository:

- require authentication and enforce per-user quotas;
- run Lean, Python, plotting, and MCP processes in isolated containers or an
  equivalent restricted runtime with CPU, memory, filesystem, and network
  limits;
- keep MCP disabled unless every server and tool is explicitly reviewed and
  allowlisted;
- restrict outbound network access and place the application behind a trusted
  reverse proxy configured as described in the deployment documentation;
- use a dedicated database project or review every migration before applying
  it to a shared database.

The built-in Python and plotting sandboxes use process and syntax restrictions;
they are not a container boundary for hostile tenants.

## Secrets

Keep provider keys, JWT secrets, database service keys, SMS credentials, and
deployment tokens in environment variables or a secret manager. `.env` and
local configuration files are ignored by Git. Rotate a secret immediately if
it appears in a commit, issue, log, screenshot, or benchmark artifact.

## Supported versions

Security fixes are applied to the latest commit on `main`. Older snapshots are
not maintained unless a release announcement states otherwise.
