#!/usr/bin/env sh
set -eu

# Read-only Odysseus self-host vitals collector.
# It avoids log contents and secrets by default. Share output only after review.

APP_URL="${ODYSSEUS_URL:-}"
if [ -z "$APP_URL" ]; then
  APP_PORT="${APP_PORT:-7000}"
  APP_URL="http://127.0.0.1:${APP_PORT}"
fi

section() {
  printf '\n== %s ==\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run() {
  printf '$ %s\n' "$*"
  "$@" 2>&1 || printf '[exit %s]\n' "$?"
}

section "Odysseus Doctor Vitals"
printf 'privacy_note=%s\n' 'Review before sharing. This output avoids logs and masks configured endpoint values, but hostnames, paths, ports, and readiness errors may still reveal deployment details.'
printf 'app_url=%s\n' "$APP_URL"
printf 'cwd=%s\n' "$(pwd)"
printf 'date_utc='
date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || true

section "Host"
if have uname; then
  run uname -srm
else
  printf 'uname=not-found\n'
fi
if have python3; then
  run python3 --version
elif have python; then
  run python --version
else
  printf 'python=not-found\n'
fi

section "Safe Environment Summary"
for key in APP_BIND APP_PORT AUTH_ENABLED LOCALHOST_BYPASS SECURE_COOKIES DATABASE_URL CHROMADB_HOST CHROMADB_PORT SEARXNG_INSTANCE OLLAMA_BASE_URL ODYSSEUS_HOST; do
  eval "value=\${$key-}"
  if [ -n "${value:-}" ]; then
    case "$key" in
      DATABASE_URL|SEARXNG_INSTANCE|OLLAMA_BASE_URL|ODYSSEUS_HOST)
        printf '%s=%s\n' "$key" '[set]'
        ;;
      *)
        printf '%s=%s\n' "$key" "$value"
        ;;
    esac
  fi
done

section "HTTP Vitals"
if have curl; then
  run curl -sS -i --max-time 5 "${APP_URL}/api/health"
  run curl -sS -i --max-time 8 "${APP_URL}/api/ready"
  printf '$ %s\n' "curl -sS -i --max-time 5 ${APP_URL}/api/runtime | redact ollama_base_url"
  tmp_file="${TMPDIR:-/tmp}/odysseus-doctor-runtime.$$"
  if curl -sS -i --max-time 5 "${APP_URL}/api/runtime" >"$tmp_file" 2>&1; then
    sed 's#"ollama_base_url"[[:space:]]*:[[:space:]]*"[^"]*"#"ollama_base_url":"[redacted]"#g' "$tmp_file"
  else
    rc=$?
    cat "$tmp_file"
    printf '[exit %s]\n' "$rc"
  fi
  rm -f "$tmp_file"
else
  printf 'curl=not-found\n'
fi

section "Docker Compose"
if have docker; then
  run docker compose ps
else
  printf 'docker=not-found\n'
fi

section "Local Ports"
ports="7000 7860 11434"
case " $ports " in
  *" ${APP_PORT:-7000} "*) ;;
  *) ports="${APP_PORT:-7000} $ports" ;;
esac
if have lsof; then
  for port in $ports; do
    run lsof -nP -iTCP:"$port" -sTCP:LISTEN
  done
elif have ss; then
  run ss -ltn
else
  printf 'port_tool=not-found\n'
fi

section "Repository Files"
for path in .env docker-compose.yml data logs start-macos.sh scripts/check-docker-gpu.sh scripts/check-docker-amd-gpu.sh; do
  if [ -e "$path" ]; then
    printf '%s=present\n' "$path"
  else
    printf '%s=missing\n' "$path"
  fi
done
