#!/usr/bin/env bash
# Exercise an already-built image through the normal Compose startup path.
set -euo pipefail

searxng_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
searxng_image=${1:?usage: validate-searxng-image.sh IMAGE}
[[ "$searxng_image" =~ ^[a-zA-Z0-9_./:@+-]+$ ]] || {
  echo "Invalid image reference" >&2
  exit 2
}
searxng_checks=$(mktemp -d "${TMPDIR:-/tmp}/searxng-ci-XXXXXXXX")
searxng_project=$(basename "$searxng_checks" | tr '[:upper:]' '[:lower:]')
export SEARXNG_VALIDATION_IMAGE="$searxng_image"
export SEARXNG_VALIDATION_CHECKS="$searxng_checks"
export SEARXNG_VALIDATION_VOLUME="${searxng_project}-settings"
# Never forward an operator's existing secret or load their .env for a CI probe.
export SEARXNG_SECRET=searxng-ci-validation-only
searxng_volume_created=0
searxng_started=0
searxng_compose=(docker compose --env-file /dev/null --project-name "$searxng_project"
  -f "$searxng_repo/docker-compose.yml"
  -f "$searxng_repo/.github/searxng-validation.compose.yml")

searxng_cleanup() {
  local searxng_exit=$?
  if [ "$searxng_started" = 1 ]; then
    "${searxng_compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [ "$searxng_volume_created" = 1 ]; then
    docker volume rm "$SEARXNG_VALIDATION_VOLUME" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$searxng_checks"
  return "$searxng_exit"
}
trap searxng_cleanup EXIT

install -m 0644 "$searxng_repo/scripts/check_ca_trust.py" \
  "$searxng_repo/docker/searxng/validate_image.py" "$searxng_checks/"
install -m 0644 "$searxng_repo/tests/fixtures/ca-trust/incident.json" "$searxng_checks/incident.json"
install -m 0644 "$searxng_repo/config/searxng/settings.yml" "$searxng_checks/settings.yml"
install -m 0644 "$searxng_repo/scripts/migrate_searxng_settings.py" "$searxng_checks/migrate_searxng_settings.py"
if docker volume inspect "$SEARXNG_VALIDATION_VOLUME" >/dev/null 2>&1; then
  echo "Refusing to reuse an existing validation volume" >&2
  exit 1
fi
docker volume create --label "odysseus.searxng-validation=$searxng_project" \
  "$SEARXNG_VALIDATION_VOLUME" >/dev/null
searxng_volume_created=1
searxng_started=1
if ! "${searxng_compose[@]}" up --detach --no-deps --no-build --pull never \
  --wait --wait-timeout 150 searxng; then
  "${searxng_compose[@]}" logs --no-color searxng >&2 || true
  exit 1
fi
"${searxng_compose[@]}" exec -T searxng /usr/local/searxng/.venv/bin/python \
  /opt/odysseus-checks/validate_image.py --running --live
