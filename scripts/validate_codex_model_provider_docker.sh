#!/usr/bin/env bash
# Validate the experimental Codex model-provider status probe in a real Docker
# deployment. Run this on the deployment host, usually:
#
#   cd /opt/odysseus
#   ODYSSEUS_ADMIN_USER=admin ODYSSEUS_ADMIN_PASSWORD='...' \
#     scripts/validate_codex_model_provider_docker.sh
#
# The script edits only the deployment .env file, backs it up, and restores it
# before exit. It never reads Codex credential files and redirects CLI logout
# output so token-like material is not printed.

set -euo pipefail

APP_DIR="${1:-/opt/odysseus}"
SERVICE="${ODYSSEUS_DOCKER_SERVICE:-odysseus}"
APP_PORT="${APP_PORT:-7000}"
BASE_URL="${ODYSSEUS_BASE_URL:-http://127.0.0.1:${APP_PORT}}"
FLAG_KEY="ODYSSEUS_CODEX_MODEL_PROVIDER_ENABLED"
MODEL_ID="codex-cli/chatgpt-experimental"
COOKIE_JAR=""
ENV_BACKUP=""
RESTORE_DONE=0

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

cleanup() {
  local rc=$?
  if [ -n "${COOKIE_JAR}" ] && [ -f "${COOKIE_JAR}" ]; then
    rm -f "${COOKIE_JAR}"
  fi
  if [ "${RESTORE_DONE}" = "0" ] && [ -n "${ENV_BACKUP}" ] && [ -f "${ENV_BACKUP}" ]; then
    log "Restoring original .env feature-flag state"
    cp "${ENV_BACKUP}" "${APP_DIR}/.env"
    rm -f "${ENV_BACKUP}"
    (cd "${APP_DIR}" && docker compose up -d --force-recreate "${SERVICE}" >/dev/null)
    RESTORE_DONE=1
  fi
  exit "${rc}"
}
trap cleanup EXIT

json_get() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
cur = data
for part in key.split("."):
    if isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
        break
print("" if cur is None else cur)
PY
}

assert_status_payload() {
  local file="$1"
  local expected_status="$2"
  local expected_feature="$3"
  python3 - "$file" "$expected_status" "$expected_feature" "$MODEL_ID" <<'PY'
import json, sys
path, expected_status, expected_feature, model_id = sys.argv[1:5]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

def fail(msg):
    raise SystemExit(msg)

if data.get("status") != expected_status:
    fail(f"expected status={expected_status!r}, got {data.get('status')!r}: {data}")
if bool(data.get("feature_enabled")) != (expected_feature == "true"):
    fail(f"feature_enabled mismatch: {data}")
if expected_status == "available":
    if data.get("chat_supported") is not True:
        fail(f"chat_supported must be true only for safe available provider: {data}")
else:
    if data.get("chat_supported") is not False:
        fail(f"chat_supported must remain false for status {expected_status}: {data}")
for key in ("streaming_supported", "session_resume_supported", "tool_execution_allowed"):
    if data.get(key) is not False:
        fail(f"{key} must remain false: {data}")
dump = json.dumps(data).lower()
for forbidden in ("access_token", "refresh_token", "id_token", "secret", "bearer "):
    if forbidden in dump:
        fail(f"response contains forbidden token-like field/text: {forbidden}")
models = data.get("models") or []
if expected_status == "available":
    ids = [m.get("id") for m in models if isinstance(m, dict)]
    if model_id not in ids:
        fail(f"missing experimental model {model_id}: {data}")
else:
    if models:
        fail(f"models should be empty for status {expected_status}: {data}")
print("ok")
PY
}

assert_test_chat_payload() {
  local file="$1"
  python3 - "$file" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

def fail(msg):
    raise SystemExit(msg)

if data.get("ok") is not True:
    fail(f"expected ok=true from test-chat: {data}")
message = data.get("message")
if not isinstance(message, str) or not message.strip():
    fail(f"test-chat response missing assistant message: {data}")
for key in ("streaming_supported", "session_resume_supported", "tool_execution_allowed"):
    if data.get(key) is not False:
        fail(f"{key} must remain false: {data}")
dump = json.dumps(data).lower()
for forbidden in ("access_token", "refresh_token", "id_token", "secret", "bearer "):
    if forbidden in dump:
        fail(f"response contains forbidden token-like field/text: {forbidden}")
print("ok")
PY
}

set_flag() {
  local value="$1"
  python3 - "${APP_DIR}/.env" "${FLAG_KEY}" "${value}" <<'PY'
import os, sys
path, key, value = sys.argv[1:4]
lines = []
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
out = []
seen = False
for line in lines:
    if line.startswith(key + "=") or line.startswith("#" + key + "="):
        out.append(f"{key}={value}")
        seen = True
    else:
        out.append(line)
if not seen:
    if out and out[-1].strip():
        out.append("")
    out.append(f"{key}={value}")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
PY
}

restart_app() {
  (cd "${APP_DIR}" && docker compose up -d --build --force-recreate "${SERVICE}" >/dev/null)
  for _ in $(seq 1 60); do
    if curl -fsS "${BASE_URL}/api/auth/status" >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done
  die "app did not become reachable at ${BASE_URL}"
}

login_admin() {
  local body login_file
  if [ -n "${COOKIE_JAR}" ] && [ -f "${COOKIE_JAR}" ]; then
    rm -f "${COOKIE_JAR}"
  fi
  COOKIE_JAR="$(mktemp)"
  login_file="$(mktemp)"
  body="$(python3 - <<'PY'
import json, os
body = {
    "username": os.environ["ODYSSEUS_ADMIN_USER"],
    "password": os.environ["ODYSSEUS_ADMIN_PASSWORD"],
    "remember": False,
}
totp = os.environ.get("ODYSSEUS_ADMIN_TOTP")
if totp:
    body["totp_code"] = totp
print(json.dumps(body))
PY
)"
  local code
  code="$(curl -sS -o "${login_file}" -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -c "${COOKIE_JAR}" \
    -X POST "${BASE_URL}/api/auth/login" \
    --data "${body}")"
  if [ "${code}" != "200" ]; then
    rm -f "${login_file}"
    die "admin login failed with HTTP ${code}"
  fi
  if [ "$(json_get "${login_file}" ok)" != "True" ]; then
    rm -f "${login_file}"
    die "admin login did not return ok=true"
  fi
  rm -f "${login_file}"
}

request_status() {
  local file="$1"
  curl -fsS -b "${COOKIE_JAR}" "${BASE_URL}/api/codex-model-provider/status" > "${file}"
}

request_codex_auth() {
  local method="$1"
  local path="$2"
  local file="$3"
  curl -fsS -b "${COOKIE_JAR}" -X "${method}" "${BASE_URL}/api/codex-auth/${path}" > "${file}"
}

request_test_chat() {
  local file="$1"
  curl -fsS -b "${COOKIE_JAR}" \
    -H "Content-Type: application/json" \
    -X POST "${BASE_URL}/api/codex-model-provider/test-chat" \
    --data '{"prompt":"Reply with exactly: codex provider test ok","timeout_seconds":180}' \
    > "${file}"
}

poll_until_codex_authenticated() {
  local status_file
  status_file="$(mktemp)"
  for _ in $(seq 1 310); do
    request_codex_auth GET status "${status_file}"
    if [ "$(json_get "${status_file}" authenticated)" = "True" ] || [ "$(json_get "${status_file}" codex_authenticated)" = "True" ]; then
      rm -f "${status_file}"
      return
    fi
    sleep 3
  done
  rm -f "${status_file}"
  die "Codex did not become authenticated before timeout"
}

main() {
  need docker
  need curl
  need python3
  [ -d "${APP_DIR}" ] || die "deployment path not found: ${APP_DIR}"
  [ -f "${APP_DIR}/docker-compose.yml" ] || die "docker-compose.yml not found under ${APP_DIR}"
  [ -n "${ODYSSEUS_ADMIN_USER:-}" ] || die "set ODYSSEUS_ADMIN_USER in the environment"
  [ -n "${ODYSSEUS_ADMIN_PASSWORD:-}" ] || die "set ODYSSEUS_ADMIN_PASSWORD in the environment"

  local branch
  branch="$(git -C "${APP_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ "${SKIP_GIT_BRANCH_CHECK:-0}" != "1" ] && [ "${branch}" != "codex-model-provider-draft" ]; then
    die "expected ${APP_DIR} to be on codex-model-provider-draft, got ${branch:-unknown}"
  fi
  [ -f "${APP_DIR}/src/codex_model_provider.py" ] || die "provider probe source is missing"

  ENV_BACKUP="${APP_DIR}/.env.codex-provider-validation.$(date +%Y%m%d%H%M%S).bak"
  if [ -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/.env" "${ENV_BACKUP}"
  else
    : > "${ENV_BACKUP}"
    : > "${APP_DIR}/.env"
  fi

  log "State 1: feature flag disabled"
  set_flag false
  restart_app
  login_admin
  local unauth_code disabled_file logged_out_file logged_in_file test_chat_file after_logout_file start_file logout_file
  disabled_file="$(mktemp)"
  unauth_code="$(curl -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/api/codex-model-provider/status")"
  [ "${unauth_code}" = "403" ] || die "admin gate check failed: unauthenticated status returned HTTP ${unauth_code}, expected 403"
  request_status "${disabled_file}"
  assert_status_payload "${disabled_file}" disabled false >/dev/null
  rm -f "${disabled_file}"

  log "State 2: feature flag enabled, Codex logged out"
  set_flag true
  restart_app
  login_admin
  (cd "${APP_DIR}" && docker compose exec -T "${SERVICE}" codex logout >/dev/null 2>&1 || true)
  restart_app
  login_admin
  logged_out_file="$(mktemp)"
  request_status "${logged_out_file}"
  assert_status_payload "${logged_out_file}" sign_in_required true >/dev/null
  rm -f "${logged_out_file}"

  log "State 3: feature flag enabled, Codex logged in"
  start_file="$(mktemp)"
  request_codex_auth POST start "${start_file}"
  local st
  st="$(json_get "${start_file}" status)"
  if [ "${st}" = "pending" ] || [ "${st}" = "starting" ]; then
    local url code
    url="$(json_get "${start_file}" verification_url)"
    code="$(json_get "${start_file}" user_code)"
    if [ -n "${url}" ] && [ -n "${code}" ]; then
      printf 'Open this URL and enter the one-time code:\n%s\n%s\n' "${url}" "${code}"
    else
      printf 'Codex login started. Complete verification in the browser.\n'
    fi
    poll_until_codex_authenticated
  elif [ "${st}" = "already_authenticated" ] || [ "$(json_get "${start_file}" authenticated)" = "True" ]; then
    :
  else
    rm -f "${start_file}"
    die "Codex auth start failed with status ${st}"
  fi
  rm -f "${start_file}"
  logged_in_file="$(mktemp)"
  request_status "${logged_in_file}"
  assert_status_payload "${logged_in_file}" available true >/dev/null
  rm -f "${logged_in_file}"
  test_chat_file="$(mktemp)"
  request_test_chat "${test_chat_file}"
  assert_test_chat_payload "${test_chat_file}" >/dev/null
  rm -f "${test_chat_file}"

  log "State 4: logout after login"
  logout_file="$(mktemp)"
  request_codex_auth POST logout "${logout_file}"
  rm -f "${logout_file}"
  after_logout_file="$(mktemp)"
  request_status "${after_logout_file}"
  assert_status_payload "${after_logout_file}" sign_in_required true >/dev/null
  rm -f "${after_logout_file}"

  log "Restoring original .env"
  cp "${ENV_BACKUP}" "${APP_DIR}/.env"
  rm -f "${ENV_BACKUP}"
  restart_app
  RESTORE_DONE=1

  log "Codex model-provider Docker validation passed"
}

main "$@"
