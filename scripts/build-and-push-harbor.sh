#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/build-and-push-harbor.sh [tag]

Builds, pushes, and deploys the Odysseus image to the homelab Harbor registry and k8s cluster.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image_repo="harbor.homelab/atomic/odysseus"
# Default tag must be unique per build so repeated same-day deploys produce a
# new image ref — otherwise `kubectl set image` sees no change and never rolls
# out. Timestamp to the second guarantees uniqueness; the short git SHA (when
# available) makes a deployed tag traceable back to a commit. Override with [tag].
git_sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || true)"
tag="${1:-homelab-$(date +%Y%m%d-%H%M%S)${git_sha:+-$git_sha}}"
image_ref="${image_repo}:${tag}"
registry_host="harbor.homelab"
kube_secret_ns="${HARBOR_SECRET_NAMESPACE:-odysseus}"
kube_secret_name="${HARBOR_SECRET_NAME:-harbor-robot}"

echo "Image: $image_ref"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker CLI is not available." >&2
  exit 1
fi

docker_ready="false"
for _ in 1 2 3 4 5; do
  if docker version >/dev/null 2>&1; then
    docker_ready="true"
    break
  fi
  sleep 2
done
if [[ "$docker_ready" != "true" ]]; then
  echo "Error: Docker is installed but not usable from this shell." >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "Error: docker buildx is required for Harbor push." >&2
  exit 1
fi

registry_auth=""
if command -v kubectl >/dev/null 2>&1; then
  registry_auth="$(
    kubectl -n "$kube_secret_ns" get secret "$kube_secret_name" \
      -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null \
      | base64 -d 2>/dev/null || true
  )"
fi

if [[ -z "$registry_auth" ]]; then
  echo "Warning: Could not extract Harbor credentials from $kube_secret_ns/$kube_secret_name" >&2
  echo "Falling back to existing Docker auth for $registry_host." >&2
else
  temp_docker_config_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_docker_config_dir"' EXIT
  printf '%s\n' "$registry_auth" > "$temp_docker_config_dir/config.json"
  export DOCKER_CONFIG="$temp_docker_config_dir"
  echo "Using Docker auth from Kubernetes secret $kube_secret_ns/$kube_secret_name"
fi

docker buildx build \
  --platform linux/amd64 \
  --output "type=registry,name=${image_ref},push=true,registry.insecure=true" \
  "$repo_root"

echo "$image_ref"

kube_deploy_ns="${DEPLOY_NAMESPACE:-odysseus}"
kube_deploy_name="${DEPLOY_NAME:-odysseus}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "Warning: kubectl not found — skipping deploy." >&2
  exit 0
fi

echo "Deploying $image_ref to $kube_deploy_ns/$kube_deploy_name ..."
kubectl -n "$kube_deploy_ns" set image \
  "deployment/$kube_deploy_name" \
  "${kube_deploy_name}=${image_ref}"

kubectl -n "$kube_deploy_ns" rollout status \
  "deployment/$kube_deploy_name" \
  --timeout=300s
