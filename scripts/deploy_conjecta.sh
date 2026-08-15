#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

log() {
  printf '[conjecta-deploy] %s\n' "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  local help_text="${2:-Install ${command_name} before deploying.}"
  command -v "$command_name" >/dev/null 2>&1 || die "$help_text"
}

# Live deployment path on the production host; override for other checkouts
# via the CONJECTA_REPO_DIR environment variable.
REPO_DIR="${CONJECTA_REPO_DIR:-/opt/conjecta/Conjecta-v0}"
SERVICE_NAME="${CONJECTA_SERVICE_NAME:-conjecta.service}"
DEPLOY_REF="${1:-${CONJECTA_DEPLOY_REF:-main}}"
HEALTH_URL="${CONJECTA_HEALTH_URL:-http://127.0.0.1:8010/healthz}"
# Backend port the Nginx vhost must proxy to; derived from the health URL so a
# parallel staging stack (e.g. port 8011) validates its own vhost.
BACKEND_PORT="${CONJECTA_BACKEND_PORT:-$(printf '%s' "$HEALTH_URL" | sed -E 's|^https?://[^/:]+:([0-9]+)/.*$|\1|')}"
LOCK_FILE="${CONJECTA_DEPLOY_LOCK:-/var/lock/conjecta-deploy.lock}"
UV_BIN="${CONJECTA_UV_BIN:-/usr/local/bin/uv}"
STABLE_PYTHON="${CONJECTA_PYTHON_BIN:-/usr/bin/python3.10}"
RUNTIME_ARCHIVE_DIR="${CONJECTA_RUNTIME_ARCHIVE_DIR:-$(dirname "$REPO_DIR")/conjecta-runtime-archives}"
NGINX_SITE="${CONJECTA_NGINX_SITE:-}"
ENV_FILE="${CONJECTA_ENV_FILE:-/opt/conjecta/conjecta.env}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STASH_COMMIT=""
VENV_RELEASE=""
VENV_ACTIVE_TARGET=""
VENV_BACKUP=""
STATIC_BACKUP=""
STATIC_RELEASE=""
VENV_FAILED=""
STATIC_FAILED=""
VENV_SWAPPED=0
STATIC_SWAPPED=0
RUNTIME_SWAP_STARTED=0
PREVIOUS_COMMIT=""

write_deployment_version() {
  local commit="$1"
  [[ "$commit" =~ ^[0-9a-f]{7,40}$ ]] || die "Refusing to write invalid deployment version."
  if [[ -f "$ENV_FILE" ]] && grep -q '^CONJECTA_DEPLOYMENT_VERSION=' "$ENV_FILE"; then
    sed -i "s|^CONJECTA_DEPLOYMENT_VERSION=.*|CONJECTA_DEPLOYMENT_VERSION=${commit}|" "$ENV_FILE"
  else
    # Ensure we never corrupt a file missing a trailing newline.
    if [[ -f "$ENV_FILE" && -n "$(tail -c 1 "$ENV_FILE")" ]]; then
      printf '\n' >> "$ENV_FILE"
    fi
    printf 'CONJECTA_DEPLOYMENT_VERSION=%s\n' "$commit" >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
}

restore_previous_runtime() {
  set +e
  log "Restoring the previous static assets and virtualenv after failed activation."
  systemctl stop "$SERVICE_NAME" >/dev/null 2>&1

  if ((STATIC_SWAPPED == 1)) && [[ -d "$STATIC_DIR" ]]; then
    STATIC_FAILED="${RUNTIME_ARCHIVE_DIR}/static.failed-${TIMESTAMP}-$$"
    mv "$STATIC_DIR" "$STATIC_FAILED"
  fi
  if [[ -n "$STATIC_BACKUP" && -d "$STATIC_BACKUP" ]]; then
    mv "$STATIC_BACKUP" "$STATIC_DIR"
  fi

  if ((VENV_SWAPPED == 1)) && [[ -L "${REPO_DIR}/.venv" ]]; then
    unlink "${REPO_DIR}/.venv"
    VENV_FAILED="${RUNTIME_ARCHIVE_DIR}/.venv.failed-${TIMESTAMP}-$$"
    if [[ -n "$VENV_ACTIVE_TARGET" && -d "$VENV_ACTIVE_TARGET" ]]; then
      mv "$VENV_ACTIVE_TARGET" "$VENV_FAILED"
    fi
  elif ((VENV_SWAPPED == 1)) && [[ -d "${REPO_DIR}/.venv" ]]; then
    VENV_FAILED="${RUNTIME_ARCHIVE_DIR}/.venv.failed-${TIMESTAMP}-$$"
    mv "${REPO_DIR}/.venv" "$VENV_FAILED"
  fi
  if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
    mv "$VENV_BACKUP" "${REPO_DIR}/.venv"
  fi

  if [[ -n "$PREVIOUS_COMMIT" ]]; then
    write_deployment_version "$PREVIOUS_COMMIT" >/dev/null 2>&1
  fi
  systemctl restart "$SERVICE_NAME" >/dev/null 2>&1
  set -e
}

on_exit() {
  local status=$?
  trap - EXIT
  if ((status != 0)); then
    log "Deployment failed with status ${status}; the service may still be on the previous release."
    if ((RUNTIME_SWAP_STARTED == 1)); then
      restore_previous_runtime
    fi
  fi
  if [[ -n "$STASH_COMMIT" ]]; then
    log "Preserved pre-deploy changes in stash commit ${STASH_COMMIT}."
    log "Restore deliberately with: cd ${REPO_DIR} && git stash apply ${STASH_COMMIT}"
  fi
  if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
    log "Previous virtualenv remains archived at ${VENV_BACKUP}."
  fi
  if [[ -n "$STATIC_BACKUP" && -d "$STATIC_BACKUP" ]]; then
    log "Previous static assets remain archived at ${STATIC_BACKUP}."
  fi
  if [[ -n "$VENV_RELEASE" && -d "$VENV_RELEASE" ]]; then
    log "Unactivated release virtualenv remains at ${VENV_RELEASE}."
  fi
  if [[ -n "$STATIC_RELEASE" && -d "$STATIC_RELEASE" ]]; then
    log "Unactivated static release remains at ${STATIC_RELEASE}."
  fi
  if [[ -n "$VENV_FAILED" && -d "$VENV_FAILED" ]]; then
    log "Failed release virtualenv remains archived at ${VENV_FAILED}."
  fi
  if [[ -n "$STATIC_FAILED" && -d "$STATIC_FAILED" ]]; then
    log "Failed static release remains archived at ${STATIC_FAILED}."
  fi
  exit "$status"
}
trap on_exit EXIT

[[ "$SERVICE_NAME" =~ ^[A-Za-z0-9_.@-]+$ ]] || die "Invalid systemd service name."
[[ "$DEPLOY_REF" =~ ^[A-Za-z0-9._/-]+$ && "$DEPLOY_REF" != -* ]] || die "Invalid deploy ref."
[[ "$HEALTH_URL" == http://127.0.0.1:*/* || "$HEALTH_URL" == http://localhost:*/* ]] \
  || die "Health URL must target the local service."
((EUID == 0)) || die "Run deployment as root so systemd and runtime ownership can be managed."

for command_name in git npm nginx systemctl curl flock cmp chown chmod id getent dirname; do
  require_command "$command_name"
done
require_command pdfinfo "Missing pdfinfo; install Ubuntu package poppler-utils before deploying."
require_command pdftoppm "Missing pdftoppm; install Ubuntu package poppler-utils before deploying."
pdfinfo -v >/dev/null 2>&1 || die "pdfinfo version smoke check failed (install poppler-utils)."
pdftoppm -v >/dev/null 2>&1 || die "pdftoppm version smoke check failed (install poppler-utils)."

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || die "Another Conjecta deployment holds ${LOCK_FILE}."

[[ -d "$REPO_DIR/.git" ]] || die "Repository not found at ${REPO_DIR}."
mkdir -p "$RUNTIME_ARCHIVE_DIR"
REPO_DIR="$(cd "$REPO_DIR" && pwd -P)"
RUNTIME_ARCHIVE_DIR="$(cd "$RUNTIME_ARCHIVE_DIR" && pwd -P)"
[[ "$RUNTIME_ARCHIVE_DIR" != "$REPO_DIR" && "$RUNTIME_ARCHIVE_DIR" != "$REPO_DIR/"* ]] \
  || die "Runtime archive directory must be outside the Git checkout."

SERVICE_USER="$(systemctl show "$SERVICE_NAME" --property=User --value)"
SERVICE_GROUP="$(systemctl show "$SERVICE_NAME" --property=Group --value)"
[[ "$SERVICE_USER" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ && "$SERVICE_USER" != "root" ]] \
  || die "${SERVICE_NAME} must declare a safe non-root User."
if [[ -z "$SERVICE_GROUP" ]]; then
  SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
fi
[[ "$SERVICE_GROUP" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ && "$SERVICE_GROUP" != "root" ]] \
  || die "${SERVICE_NAME} must use a safe non-root Group."
id -u "$SERVICE_USER" >/dev/null 2>&1 || die "Configured service user does not exist."
getent group "$SERVICE_GROUP" >/dev/null 2>&1 || die "Configured service group does not exist."
chown "root:${SERVICE_GROUP}" "$RUNTIME_ARCHIVE_DIR"
chmod 0750 "$RUNTIME_ARCHIVE_DIR"

# Uvicorn must leave the ASGI client as the Nginx socket peer. The application
# itself accepts only Nginx-overwritten X-Real-IP/X-Forwarded-Proto from
# CONJECTA_TRUSTED_PROXY_CIDRS.
service_exec_start="$(systemctl show "$SERVICE_NAME" --property=ExecStart --value)"
[[ "$service_exec_start" == *"--no-proxy-headers"* ]] \
  || die "${SERVICE_NAME} must start Uvicorn with --no-proxy-headers."
[[ "$service_exec_start" =~ --ws-max-size(=|[[:space:]])16777216 ]] \
  || die "${SERVICE_NAME} must set --ws-max-size 16777216."
unset service_exec_start

# Validate only the Nginx server blocks that proxy to Conjecta. nginx -T dumps
# every included file, so grepping the whole output would assert settings from
# unrelated vhosts while the real Conjecta vhost goes unchecked.
extract_conjecta_server_blocks() {
  awk -v port="$BACKEND_PORT" '
    function flush_block() {
      if (block ~ ("proxy_pass[[:space:]]+https?://(127\\.0\\.0\\.1|localhost):" port "([/;[:space:]]|$)")) {
        printf "%s", block
      }
      in_block = 0
      depth = 0
      block = ""
    }
    {
      line = $0
      countline = line
      sub(/#.*/, "", countline)
      braces = countline
      opens = gsub(/\{/, "{", braces)
      closes = gsub(/\}/, "}", braces)
      if (in_block) {
        block = block line "\n"
        depth += opens - closes
        if (depth <= 0) {
          flush_block()
        }
      } else if (countline ~ /^[[:space:]]*server[[:space:]]*\{/) {
        in_block = 1
        depth = opens - closes
        block = line "\n"
        if (depth <= 0) {
          flush_block()
        }
      }
    }
  '
}

# Convert an Nginx size value (plain bytes, k/K, m/M, g/G) to bytes.
nginx_size_to_bytes() {
  local raw="$1"
  [[ "$raw" =~ ^([0-9]+)([kKmMgG]?)$ ]] || return 1
  local number="${BASH_REMATCH[1]}"
  case "${BASH_REMATCH[2],,}" in
    "") printf '%s\n' "$number" ;;
    k) printf '%s\n' "$((number * 1024))" ;;
    m) printf '%s\n' "$((number * 1048576))" ;;
    g) printf '%s\n' "$((number * 1073741824))" ;;
  esac
}

# Convert an Nginx time value (seconds by default, s/m/h/d suffix) to seconds.
nginx_time_to_seconds() {
  local raw="$1"
  [[ "$raw" =~ ^([0-9]+)([smhdSMHD]?)$ ]] || return 1
  local number="${BASH_REMATCH[1]}"
  case "${BASH_REMATCH[2],,}" in
    "" | s) printf '%s\n' "$number" ;;
    m) printf '%s\n' "$((number * 60))" ;;
    h) printf '%s\n' "$((number * 3600))" ;;
    d) printf '%s\n' "$((number * 86400))" ;;
  esac
}

# Print the first value of an Nginx directive found on stdin; empty if absent.
first_nginx_directive_value() {
  grep -Eio "^[[:space:]]*${1}[[:space:]]+[^;[:space:]]+" | head -n 1 | awk '{print $NF}' || true
}

# Uvicorn starts with --ws-max-size 16777216, so the Nginx upload envelope must
# not exceed 16 MiB or 16-25 MB uploads pass Nginx and die at the WebSocket layer.
CONJECTA_NGINX_MAX_BODY_BYTES=16777216
# Long solves can run for hours (deep_search_wall_seconds / formal escalation
# budgets in math_agent/config.py); Nginx must not cut the proxied connection
# before those budgets expire.
CONJECTA_NGINX_MIN_PROXY_READ_TIMEOUT=7200

assert_conjecta_nginx_config() {
  local candidate="$1"
  local server_blocks=""
  server_blocks="$(extract_conjecta_server_blocks <<<"$candidate")"
  [[ -n "$server_blocks" ]] \
    || die "No Nginx server block proxies to Conjecta (proxy_pass to 127.0.0.1:${BACKEND_PORT} or localhost:${BACKEND_PORT}); refusing to deploy against an unverifiable vhost."

  local body_size_raw="" body_size_source=""
  body_size_raw="$(first_nginx_directive_value client_max_body_size <<<"$server_blocks")"
  if [[ -n "$body_size_raw" ]]; then
    body_size_source="the Conjecta server block"
  else
    body_size_raw="$(first_nginx_directive_value client_max_body_size <<<"$candidate")"
    body_size_source="the inherited http-level configuration"
  fi
  [[ -n "$body_size_raw" ]] \
    || die "Nginx sets no client_max_body_size for the Conjecta vhost; the 1m default is below the 16 MiB upload envelope."
  local body_size_bytes=""
  body_size_bytes="$(nginx_size_to_bytes "$body_size_raw")" \
    || die "Unrecognized client_max_body_size value '${body_size_raw}' in ${body_size_source}."
  ((body_size_bytes > 0 && body_size_bytes <= CONJECTA_NGINX_MAX_BODY_BYTES)) \
    || die "client_max_body_size ${body_size_raw} in ${body_size_source} exceeds 16m (${CONJECTA_NGINX_MAX_BODY_BYTES} bytes), the envelope accepted by Uvicorn --ws-max-size 16777216."

  local read_timeout_raw="" read_timeout_source=""
  read_timeout_raw="$(first_nginx_directive_value proxy_read_timeout <<<"$server_blocks")"
  if [[ -n "$read_timeout_raw" ]]; then
    read_timeout_source="the Conjecta server block"
  else
    read_timeout_raw="$(first_nginx_directive_value proxy_read_timeout <<<"$candidate")"
    read_timeout_source="the inherited http-level configuration"
  fi
  [[ -n "$read_timeout_raw" ]] \
    || die "Nginx sets no proxy_read_timeout for the Conjecta vhost; the 60s default cuts long solves (need >= ${CONJECTA_NGINX_MIN_PROXY_READ_TIMEOUT}s for deep-search / formal-escalation budgets)."
  local read_timeout_seconds=""
  read_timeout_seconds="$(nginx_time_to_seconds "$read_timeout_raw")" \
    || die "Unrecognized proxy_read_timeout value '${read_timeout_raw}' in ${read_timeout_source}."
  ((read_timeout_seconds >= CONJECTA_NGINX_MIN_PROXY_READ_TIMEOUT)) \
    || die "proxy_read_timeout ${read_timeout_raw} in ${read_timeout_source} is below ${CONJECTA_NGINX_MIN_PROXY_READ_TIMEOUT}s; long solves (deep-search / formal-escalation budgets) would be cut by Nginx."

  grep -Eq 'proxy_set_header[[:space:]]+X-Real-IP[[:space:]]+\$remote_addr[[:space:]]*;' <<<"$server_blocks" \
    || die "The Conjecta Nginx vhost must overwrite X-Real-IP with \$remote_addr."
  grep -Eq 'proxy_set_header[[:space:]]+X-Forwarded-For[[:space:]]+\$remote_addr[[:space:]]*;' <<<"$server_blocks" \
    || die "The Conjecta Nginx vhost must overwrite X-Forwarded-For with \$remote_addr."
  grep -Eq 'proxy_set_header[[:space:]]+X-Forwarded-Proto[[:space:]]+(\$scheme|https)[[:space:]]*;' <<<"$server_blocks" \
    || die "The Conjecta Nginx vhost must overwrite X-Forwarded-Proto with \$scheme or fixed https."
}

nginx_config="$(nginx -T 2>/dev/null)" || die "Unable to read a valid Nginx configuration."
if [[ -n "$NGINX_SITE" ]]; then
  [[ -r "$NGINX_SITE" ]] || die "Configured Conjecta Nginx site is not readable."
  nginx_candidate="$(<"$NGINX_SITE")"
else
  nginx_candidate="$nginx_config"
fi
assert_conjecta_nginx_config "$nginx_candidate"
unset nginx_config nginx_candidate

cd "$REPO_DIR"

PREVIOUS_COMMIT="$(git rev-parse HEAD)"

dirty_state="$(git status --porcelain --untracked-files=all)"
if [[ -n "$dirty_state" ]]; then
  before_stash="$(git rev-parse --verify -q refs/stash || true)"
  git stash push --include-untracked --message "conjecta-deploy-${TIMESTAMP}"
  STASH_COMMIT="$(git rev-parse --verify refs/stash)"
  [[ "$STASH_COMMIT" != "$before_stash" ]] || die "Dirty state was not preserved in a new stash."
  log "Preserved dirty tracked/untracked state before updating code."
fi
unset dirty_state

git fetch --prune --tags origin

resolve_target_commit() {
  local requested_ref="$1"
  local candidate=""
  if [[ "$requested_ref" == "main" ]]; then
    candidate="refs/remotes/origin/main"
  elif git rev-parse --verify -q "refs/remotes/origin/${requested_ref}^{commit}" >/dev/null; then
    candidate="refs/remotes/origin/${requested_ref}"
  elif git rev-parse --verify -q "refs/tags/${requested_ref}^{commit}" >/dev/null; then
    candidate="refs/tags/${requested_ref}"
  else
    candidate="$requested_ref"
  fi
  git rev-parse --verify "${candidate}^{commit}"
}

target_commit="$(resolve_target_commit "$DEPLOY_REF")" \
  || die "Requested ref ${DEPLOY_REF} is not available after fetch."

if git show-ref --verify --quiet refs/heads/main; then
  git switch main
else
  git switch --track -c main refs/remotes/origin/main
fi
git merge --ff-only "$target_commit"
[[ "$(git rev-parse HEAD)" == "$target_commit" ]] \
  || die "HEAD does not equal the exact requested deployment commit."

# Git creates files with this script's restrictive umask and root ownership.
# Keep source immutable by the service account while ensuring its group can
# traverse newly created package directories and import every tracked file.
make_tracked_tree_service_readable() {
  local tracked_path=""
  local parent=""

  while IFS= read -r -d '' tracked_path; do
    if [[ -L "$tracked_path" ]]; then
      chown -h "root:${SERVICE_GROUP}" "$tracked_path"
    else
      chown "root:${SERVICE_GROUP}" "$tracked_path"
      chmod g+r "$tracked_path"
    fi

    parent="$(dirname -- "$tracked_path")"
    while [[ "$parent" != "." ]]; do
      chown "root:${SERVICE_GROUP}" "$parent"
      chmod g+rx "$parent"
      parent="$(dirname -- "$parent")"
    done
  done < <(git ls-files -z)
}

make_tracked_tree_service_readable

# Build dependencies beside the live virtualenv. The runtime swap happens only
# after every install/build/preflight succeeds and the service has stopped.
VENV_RELEASE="${RUNTIME_ARCHIVE_DIR}/.venv.release-${TIMESTAMP}"
VENV_BACKUP="${RUNTIME_ARCHIVE_DIR}/.venv.before-${TIMESTAMP}"
[[ ! -e "$VENV_RELEASE" && ! -e "$VENV_BACKUP" && ! -L "$VENV_BACKUP" ]] \
  || die "Virtualenv release/archive path already exists."

# Prefer the repository lock and stable production Python. Other hosts retain a
# standard-library venv/pip fallback and do not require uv.
if [[ -x "$UV_BIN" && -f uv.lock && -x "$STABLE_PYTHON" ]]; then
  UV_PROJECT_ENVIRONMENT="$VENV_RELEASE" \
    "$UV_BIN" sync --frozen --no-dev --python "$STABLE_PYTHON"
else
  require_command python3 "Python 3 is required when uv/Python 3.10 is unavailable."
  python3 -m venv "$VENV_RELEASE"
  PYTHON="${VENV_RELEASE}/bin/python"
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -e .
fi
PYTHON="${VENV_RELEASE}/bin/python"
[[ -x "$PYTHON" ]] || die "Release virtualenv was not created."

FRONTEND_DIR="${REPO_DIR}/math_agent/web/frontend"
STATIC_DIR="${REPO_DIR}/math_agent/web/static"
STATIC_RELEASE="${RUNTIME_ARCHIVE_DIR}/static.release-${TIMESTAMP}"
STATIC_BACKUP="${RUNTIME_ARCHIVE_DIR}/static.before-${TIMESTAMP}"
[[ ! -e "$STATIC_RELEASE" && ! -e "$STATIC_BACKUP" ]] \
  || die "Static release/archive path already exists."

(
  cd "$FRONTEND_DIR"
  npm ci
  CONJECTA_FRONTEND_OUT_DIR="$STATIC_RELEASE" npm run build
)

verify_static_assets() {
  local static_root="$1"
  "$PYTHON" - "$static_root" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

root = Path(sys.argv[1]).resolve()
index = root / "index.html"
if not index.is_file():
    raise SystemExit("built static index.html is missing")
html = index.read_text(encoding="utf-8")
references = set(
    re.findall(r'''(?:src|href)=["'](/static/[^"'?#]+)''', html)
)
if not references:
    raise SystemExit("static index.html contains no /static/ asset references")
for reference in references:
    relative = Path(unquote(reference.removeprefix("/static/")))
    asset = (root / relative).resolve()
    if root not in asset.parents or not asset.is_file():
        raise SystemExit(f"referenced static asset is missing: {reference}")
PY
}

verify_static_assets "$STATIC_RELEASE"
[[ -f "$STATIC_DIR/index.html" ]] || die "Tracked static/index.html is missing."
cmp --silent "$STATIC_DIR/index.html" "$STATIC_RELEASE/index.html" \
  || die "Built static/index.html differs from the tracked deterministic entrypoint."
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  die "Frontend build differs from tracked static assets; commit the deterministic build first."
fi
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$VENV_RELEASE" "$STATIC_RELEASE"

nginx -t
RUNTIME_SWAP_STARTED=1
systemctl stop "$SERVICE_NAME"

if [[ -e "${REPO_DIR}/.venv" || -L "${REPO_DIR}/.venv" ]]; then
  mv "${REPO_DIR}/.venv" "$VENV_BACKUP"
fi
ln -s "$VENV_RELEASE" "${REPO_DIR}/.venv"
VENV_ACTIVE_TARGET="$VENV_RELEASE"
VENV_RELEASE=""
VENV_SWAPPED=1

if [[ -d "$STATIC_DIR" ]]; then
  mv "$STATIC_DIR" "$STATIC_BACKUP"
fi
mv "$STATIC_RELEASE" "$STATIC_DIR"
STATIC_RELEASE=""
STATIC_SWAPPED=1
verify_static_assets "$STATIC_DIR"

PYTHON="${REPO_DIR}/.venv/bin/python"
write_deployment_version "$target_commit"
systemctl restart "$SERVICE_NAME"

# Probe once. Retry noise is suppressed: a service that is still binding its
# port fails here several times by design, and leaking curl errors plus a JSON
# traceback per attempt made a normal startup wait look like a failed deploy.
# The last attempt's diagnostics are kept in $health_last_error for the
# failure path, so a genuinely broken deploy still reports why.
health_last_error=""
health_ready() {
  local body
  if ! body="$(curl --fail --silent --show-error --max-time 4 "$HEALTH_URL" 2>&1)"; then
    health_last_error="$body"
    return 1
  fi
  if ! printf '%s' "$body" \
    | "$PYTHON" -c 'import json,sys; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("ok") is True else 1)' 2>/dev/null; then
    health_last_error="unexpected health payload: ${body}"
    return 1
  fi
  health_last_error=""
}

healthy=0
for ((attempt = 1; attempt <= 20; attempt++)); do
  if health_ready; then
    healthy=1
    log "Health check passed at ${HEALTH_URL} (attempt ${attempt})."
    break
  fi
  sleep 2
done
if [[ "$healthy" != "1" ]]; then
  log "Last health probe error: ${health_last_error:-<none>}"
  die "Health check did not become ready at ${HEALTH_URL}."
fi
systemctl is-active --quiet "$SERVICE_NAME" || die "${SERVICE_NAME} is not active."
[[ "$(git rev-parse HEAD)" == "$target_commit" ]] || die "Deployed HEAD changed unexpectedly."

log "Deployment complete at commit ${target_commit}."
