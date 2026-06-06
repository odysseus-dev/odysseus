#!/bin/sh
set -eu

if [ ! -s /etc/searxng/settings.yml ] || grep -q '__SEARXNG_SECRET__' /etc/searxng/settings.yml; then
  secret="${SEARXNG_SECRET:-}"
  if [ -z "$secret" ]; then
    secret="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  fi
  sed "s|__SEARXNG_SECRET__|$secret|g" /tmp/searxng-settings.yml.template > /etc/searxng/settings.yml
fi

exec /usr/local/searxng/entrypoint.sh
