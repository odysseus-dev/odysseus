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

# --- Odysseus native authentication -----------------------------------------
export AUTH_ENABLED="$(_flag auth_enabled true)"

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

# --- Bind address -----------------------------------------------------------
# Prefer a dual-stack bind. When the host advertises AAAA records, browsers try
# IPv6 first; an IPv4-only listener (0.0.0.0) makes those clients fail instead
# of reaching the app. Binding "::" accepts IPv6 and (on Linux, v6only=0)
# IPv4-mapped connections. Falls back to IPv4 without an IPv6 stack.
BIND_ADDRESS="$(_opt bind_address)"
if [ -z "$BIND_ADDRESS" ]; then
    if python -c 'import socket; s = socket.socket(socket.AF_INET6); s.bind(("::", 0)); s.close()' 2>/dev/null; then
        BIND_ADDRESS="::"
    else
        BIND_ADDRESS="0.0.0.0"
    fi
fi

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

log "Starting Odysseus on ${BIND_ADDRESS}:7000 (data=/data, AUTH_ENABLED=${AUTH_ENABLED})"
exec python -m uvicorn app:app --host "$BIND_ADDRESS" --port 7000