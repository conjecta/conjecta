# Security Policy

## Reporting a Vulnerability

Please do **not** open public GitHub issues for security vulnerabilities — do
not post exploit details, credentials, user data, or a working proof of
concept in a public channel.

- Preferred: report via
  [GitHub private security advisories](https://github.com/conjecta/conjecta/security/advisories/new)
  for this repository.
- Fallback contact: security@conjecta.example (placeholder — replace with the
  project security contact before publication).

Include the affected commit, configuration, impact, and the smallest
reproduction that demonstrates the issue. We aim to acknowledge reports
within 72 hours and to coordinate disclosure with the reporter once a fix is
available.

## Supported Versions

Only the latest commit on `main` receives security fixes. There are no
supported release branches at this time; self-hosters should track `main`
and rebuild on security-relevant changes. Older snapshots are not maintained
unless a release announcement states otherwise.

## Sandbox Security Model

The agent executes model-generated code in two sandbox tools
(`math_agent/tools/python_sandbox.py`, `math_agent/tools/plot_sandbox.py`).
Understand what each layer does — and does not — guarantee:

- **AST validation** (import/attribute/name blocklists) is a **first-layer
  UX / early-error check only**. It is *not* a security boundary: the allowed
  modules include native extensions (numpy, sympy, matplotlib), so static
  checks are bypassable in principle.
- **bubblewrap (bwrap) isolation** is the actual boundary for
  **single-tenant / self-hosted** deployments. Enabled via
  `CONJECTA_SANDBOX_ISOLATION=bwrap` (or `auto`, the default, when a `bwrap`
  binary is on PATH). It unshares all namespaces, drops all capabilities,
  removes the network, bind-mounts only the Python runtime read-only plus the
  sandbox output directory read-write, and clears the child environment.
- When bwrap is unavailable, execution falls back to rlimits + AST checks
  and logs a **WARNING once**. That fallback is defense-in-depth, **not**
  isolation, and is not acceptable for untrusted multi-tenant use.
- rlimits (CPU/address-space/file-descriptor caps) and process-group kill on
  timeout remain active as a second layer in all modes.

### Public / multi-tenant deployments

bwrap alone is **not sufficient** for a public multi-tenant service. Such a
deployment must run each execution in a rootless container or microVM
(gVisor, Firecracker, Kata) with default-deny egress, and must satisfy all of
the following acceptance criteria:

1. Sandboxed code cannot read host `.env`, project source, SSH keys, or any
   file outside its allowed set.
2. Sandboxed code cannot write outside its designated output directory.
3. Sandboxed code has no localhost, cloud-metadata (169.254.169.254), or
   internet access (default-deny egress; any allowlist enforced outside the
   guest).
4. No unbounded fork/CPU/memory: hard rlimits plus cgroup limits enforced by
   the runtime.
5. Full process-group cleanup on timeout or cancellation (no orphaned
   children).
6. The worker environment contains no LLM, database, or SMS credentials —
   secrets must never be present in the sandboxed process environment.

Beyond the code sandboxes, a public or multi-tenant deployment also needs
controls outside this repository:

- require authentication and enforce per-user quotas;
- run Lean, Python, plotting, and MCP processes in isolated containers or an
  equivalent restricted runtime with CPU, memory, filesystem, and network
  limits;
- restrict outbound network access and place the application behind a trusted
  reverse proxy configured as described in the deployment documentation;
- use a dedicated database project or review every migration before applying
  it to a shared database.

### MCP servers

MCP servers currently run **unisolated**, in the server process's trust
domain (residual finding H-06 in `docs/security-audit-2026-08.md`). For any
multi-tenant deployment they must be disabled or moved behind the same
isolation and egress controls as the code sandboxes; keep MCP disabled unless
every server and tool is explicitly reviewed and allowlisted.

## Secrets

Keep provider keys, JWT secrets, database service keys, SMS credentials, and
deployment tokens in environment variables or a secret manager. `.env` and
local configuration files are ignored by Git. Rotate a secret immediately if
it appears in a commit, issue, log, screenshot, or benchmark artifact.
