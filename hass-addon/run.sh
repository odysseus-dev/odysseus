#!/bin/bash
# Odysseus Home Assistant add-on entrypoint.
#
# Translates /data/options.json into the environment variables Odysseus reads,
# then starts the server. Home Assistant runs add-ons as root and maps the
# persistent volume at /data; all Odysseus state is redirected there.
set -euo pipefail

OPTIONS=/data/options.json
mkdir -p /data

log() { echo "[odysseus] $*"; }

# --- options.json helpers ---------------------------------------------------
_opt() {
    # Print a scalar option or the empty string when absent/null.
    jq -r --arg k "$1" '.[$k] // empty' "$OPTIONS" 2>/dev/null || true
}

_flag() {
    # Print "true"/"false" for a boolean option, falling back to $2 when the
    # option is absent (defaults match config.yaml).
    local value
    value="$(jq -r --arg k "$1" --arg d "$2" \
        'if .[$k] == null then $d else (.[$k] | tostring) end' \
        "$OPTIONS" 2>/dev/null || echo "$2")"
    [ "$value" = "true" ] && echo "true" || echo "false"
}

_list() {
    # Print a newline-separated list option.
    jq -r --arg k "$1" '(. [$k] // []) | .[]' "$OPTIONS" 2>/dev/null || true
}

# --- core paths -------------------------------------------------------------
export ODYSSEUS_DATA_DIR=/data
export HOME=/data
export APP_PORT=7000
export LOCALHOST_BYPASS=false
export ODYSSEUS_SKIP_ADMIN_PROMPT=1

# --- Agent safety defaults (2026-10 dev line) --------------------------------
# The add-on runs unattended, so keep the post-external-context tool approval
# gate on unless the operator explicitly disables it through extra_env.
export ODYSSEUS_TOOL_APPROVAL_GATE="${ODYSSEUS_TOOL_APPROVAL_GATE:-1}"

# Editor drafts and image ops write large payloads; keep the upstream default
# cap explicit so a stored draft cannot silently grow to fill /data.
export ODYSSEUS_EDITOR_DRAFT_MAX_BYTES="${ODYSSEUS_EDITOR_DRAFT_MAX_BYTES:-268435456}"

# --- Odysseus native authentication -----------------------------------------
export AUTH_ENABLED="$(_flag auth_enabled true)"

# --- Home Assistant credentials ---------------------------------------------
# ODYSSEUS_HASS_AUTH lets the login form verify username/password pairs with
# the Supervisor auth API (config.yaml auth_api). Home Assistant injects
# SUPERVISOR_TOKEN into this container automatically; the app refuses to
# enable the feature when it is missing. extra_env below can still override.
if [ "$(_flag hass_auth true)" = "true" ]; then
    export ODYSSEUS_HASS_AUTH="${ODYSSEUS_HASS_AUTH:-1}"
fi

ADMIN_USER="$(_opt admin_user)"
ADMIN_PASSWORD="$(_opt admin_password)"
if [ -n "$ADMIN_USER" ]; then export ODYSSEUS_ADMIN_USER="$ADMIN_USER"; fi
if [ -n "$ADMIN_PASSWORD" ]; then export ODYSSEUS_ADMIN_PASSWORD="$ADMIN_PASSWORD"; fi

# --- Model / network options ------------------------------------------------
LLM_HOST="$(_opt llm_host)"
[ -n "$LLM_HOST" ] && export LLM_HOST="$LLM_HOST"
OLLAMA_BASE_URL="$(_opt ollama_base_url)"
[ -n "$OLLAMA_BASE_URL" ] && export OLLAMA_BASE_URL="$OLLAMA_BASE_URL"

# Provider dropdown + API key. The seed script (run after DB init) turns these
# into a ready-to-use model endpoint; openai additionally feeds OPENAI_API_KEY
# so model discovery sees it immediately.
SEED_PROVIDER="$(_opt llm_provider)"
SEED_KEY="$(_opt llm_api_key)"
SEED_MODEL="$(_opt llm_model)"
if [ -n "$SEED_PROVIDER" ] && [ "$SEED_PROVIDER" != "none" ] && [ -n "$SEED_KEY" ]; then
    export ODYSSEUS_SEED_PROVIDER="$SEED_PROVIDER"
    export ODYSSEUS_SEED_API_KEY="$SEED_KEY"
    export ODYSSEUS_SEED_MODEL="$SEED_MODEL"
    if [ "$SEED_PROVIDER" = "openai" ]; then
        export OPENAI_API_KEY="$SEED_KEY"
    fi
fi

# --- Database ---------------------------------------------------------------
# DATABASE_URL must be exported before the bootstrap import below. sqlite (the
# default) stays on the /data volume; postgres is built from the parts so that
# a password containing @ / : is percent-encoded correctly.
DB_TYPE="$(_opt db_type)"
DB_URL="$(_opt db_url)"
if [ -n "$DB_URL" ]; then
    export DATABASE_URL="$DB_URL"
    log "Using custom DATABASE_URL"
elif [ "$DB_TYPE" = "postgres" ]; then
    export ODYSSEUS_DB_HOST="$(_opt db_host)"
    export ODYSSEUS_DB_PORT="$(_opt db_port)"
    export ODYSSEUS_DB_NAME="$(_opt db_name)"
    export ODYSSEUS_DB_USER="$(_opt db_user)"
    export ODYSSEUS_DB_PASSWORD="$(_opt db_password)"
    export DATABASE_URL="$(python /db_url.py)"
    unset ODYSSEUS_DB_PASSWORD
    log "Using PostgreSQL database"
fi

# --- Web search -------------------------------------------------------------
# The provider is an app setting, seeded into settings.json after DB init. The
# API key is handed over as the environment variable the search layer reads.
SEARCH_PROVIDER="$(_opt search_provider)"
SEARCH_URL="$(_opt search_url)"
SEARCH_KEY="$(_opt search_api_key)"
if [ -n "$SEARCH_URL" ]; then
    export SEARXNG_INSTANCE="$SEARCH_URL"
fi
case "$SEARCH_PROVIDER" in
    brave)
        if [ -n "$SEARCH_KEY" ]; then export DATA_BRAVE_API_KEY="$SEARCH_KEY"; fi
        ;;
    tavily)
        if [ -n "$SEARCH_KEY" ]; then export TAVILY_API_KEY="$SEARCH_KEY"; fi
        ;;
    serper)
        if [ -n "$SEARCH_KEY" ]; then export SERPER_API_KEY="$SEARCH_KEY"; fi
        ;;
    google_pse)
        if [ -n "$SEARCH_KEY" ]; then export GOOGLE_API_KEY="$SEARCH_KEY"; fi
        ;;
esac
export ODYSSEUS_SEED_SEARCH_PROVIDER="$SEARCH_PROVIDER"
export ODYSSEUS_SEED_SEARCH_URL="$SEARCH_URL"

ALLOWED_ORIGINS="$(jq -r '(.allowed_origins // []) | join(",")' "$OPTIONS" 2>/dev/null || true)"
[ -n "$ALLOWED_ORIGINS" ] && export ALLOWED_ORIGINS="$ALLOWED_ORIGINS"

# --- Agent safety / transport ------------------------------------------------
if [ "$(_flag tool_approval_gate true)" = "true" ]; then
    export ODYSSEUS_TOOL_APPROVAL_GATE=1
else
    export ODYSSEUS_TOOL_APPROVAL_GATE=0
fi

if [ "$(_flag response_compression true)" = "true" ]; then
    export RESPONSE_COMPRESSION_ENABLED=true
else
    export RESPONSE_COMPRESSION_ENABLED=false
fi

# --- Bind address -----------------------------------------------------------
# Default to IPv4 (0.0.0.0). There are two reasons this is the safe default
# and NOT "::":
#
#  1. Supervisor's ingress proxy (the sidebar panel) connects to the add-on
#     over IPv4 at app.ip_address, e.g. http://172.30.33.5:7000. If the
#     listener is IPv6-only, every panel request fails with
#     "Ingress error: Cannot connect to host 172.30.33.5:7000" → 502 in the
#     panel, even while the host port-map still works.
#  2. Binding "::" only accepts IPv4 when net.ipv6.bindv6only=0. Many
#     container netns have it set to 1, and uvicorn does not force
#     IPV6_V6ONLY=0 on the socket it creates — so "::" silently becomes
#     IPv6-only. A probe can't fix that reliably: the flag it sets on a test
#     socket does not carry over to uvicorn's own listener.
#
# Operators who need IPv6 can set bind_address explicitly ("::" for a
# dual-stack-capable netns). Everything else (browsers, Supervisor, tunnels)
# reaches us over IPv4.
BIND_ADDRESS="$(_opt bind_address)"
[ -z "$BIND_ADDRESS" ] && BIND_ADDRESS="0.0.0.0"

# --- Free-form extra environment -------------------------------------------
while IFS= read -r line; do
    [ -n "$line" ] && export "$line"
done < <(_list extra_env)

# --- First-time setup -------------------------------------------------------
# Importing the database layer creates the data directory and runs
# create_all() so the app never boots against a missing schema.
python - <<'PY' || log "database bootstrap skipped"
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("ODYSSEUS_DATA_DIR", "/data")
import core.database  # noqa: F401  (import side effect: create_all)
PY

# Seed the provider model endpoint chosen in the add-on options.
if [ -n "${ODYSSEUS_SEED_PROVIDER:-}" ]; then
    python /seed_provider.py || log "provider seed failed (continuing)"
fi

# Apply the search provider setting chosen in the add-on options.
if [ -n "${ODYSSEUS_SEED_SEARCH_PROVIDER:-}" ] || [ -n "${ODYSSEUS_SEED_SEARCH_URL:-}" ]; then
    python /seed_settings.py || log "search settings seed failed (continuing)"
fi

# Create the initial admin only when the operator supplied credentials.
if [ -n "$ADMIN_USER" ] && [ -n "$ADMIN_PASSWORD" ]; then
    python /app/setup.py || log "setup.py reported an error (continuing)"
fi

# --- Vector store (ChromaDB) -----------------------------------------------
# Upstream runs ChromaDB as a separate docker-compose service; an add-on is a
# single container, so the server (installed in the image) runs here, inside,
# loopback-only. Persisted under /data so collections survive rebuilds and are
# included in add-on backups. The app retries lazily anyway; the wait just
# avoids the noisy startup errors in the log.
export CHROMADB_HOST=127.0.0.1
export CHROMADB_PORT=8100
mkdir -p /data/chromadb /data/logs
if command -v chromadb >/dev/null 2>&1; then
    nohup chromadb run --path /data/chromadb --host 127.0.0.1 --port 8100 \
        >/data/logs/chromadb.log 2>&1 &
    for _ in $(seq 1 30); do
        curl -fsS "http://127.0.0.1:8100/api/v2/heartbeat" >/dev/null 2>&1 && break
        sleep 1
    done
else
    log "chromadb not found — vector memory/RAG will stay degraded"
fi

log "Starting Odysseus on ${BIND_ADDRESS}:7000 (data=/data, AUTH_ENABLED=${AUTH_ENABLED})"
exec python -m uvicorn app:app --host "$BIND_ADDRESS" --port 7000