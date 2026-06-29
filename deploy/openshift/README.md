# Odysseus on OpenShift

This directory contains an OpenShift-native deployment for Odysseus using
restricted-SCC-compatible images, OpenShift binary builds, ChromaDB, SearXNG,
persistent storage, and an edge-terminated Route.

The default shape is:

- `base/`: reusable resources for a namespace named `odysseus`
- `overlays/example/`: generic overlay showing namespace, route host, storage
  class, and private CA customization

## What Gets Deployed

- Odysseus web app on port `7000`
- ChromaDB on port `8000`
- SearXNG on port `8080`
- PVCs for app data, app logs, and ChromaDB data
- ImageStreams and binary BuildConfigs for `Dockerfile.openshift` and
  `Dockerfile.chroma-openshift`
- An OpenShift Route with edge TLS termination

The Odysseus container runs without root at start time. Runtime writes go under
`/app/data`, `/app/logs`, `/app/.cache`, and `/app/.local`.

## Important CA Rule

Use `LLM_CA_BUNDLE` only when Odysseus needs to trust a private CA for LLM
provider endpoints.

Do not set `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` in this deployment. Those
global variables also affect web fetch/search traffic and can break public
internet tooling when pointed at a private bundle. The application scopes
`LLM_CA_BUNDLE` to the LLM provider HTTP path.

The example overlay projects the `router-ca.crt` key from `router-ca` to a
generic in-container path and sets:

```text
LLM_CA_BUNDLE=/etc/odysseus/ca/router-ca.crt
```

Remove that patch or replace the ConfigMap name/path for clusters that do not
need private LLM route trust.

## Prerequisites

Install `oc` and log in to the target OpenShift cluster.

Create the target project:

```bash
oc new-project odysseus
```

Create the first-admin password secret:

```bash
oc -n odysseus create secret generic odysseus-admin \
  --from-literal=ODYSSEUS_ADMIN_PASSWORD='change-me'
```

If your LLM endpoints use a private route CA, create a ConfigMap containing that
CA bundle and add an overlay patch that mounts it and sets `LLM_CA_BUNDLE`.

## Deploy The Generic Base

From the repository root:

```bash
oc apply -k deploy/openshift/base
oc -n odysseus start-build odysseus-chromadb --from-dir=. --follow
oc -n odysseus start-build odysseus --from-dir=. --follow
oc -n odysseus rollout status deploy/odysseus-chromadb
oc -n odysseus rollout status deploy/searxng
oc -n odysseus rollout status deploy/odysseus
```

Get the generated route:

```bash
oc -n odysseus get route odysseus -o jsonpath='https://{.spec.host}{"\n"}'
```

## Deploy The Example Overlay

The example overlay expects these values to be changed before production use:

- namespace `odysseus-example`
- storage class `replace-me-rwo-storage-class`
- ConfigMap `router-ca` with key `router-ca.crt`, projected as
  `/etc/odysseus/ca/router-ca.crt`
- route host `odysseus.apps.example.com`

Create or refresh the admin secret:

```bash
oc apply -f deploy/openshift/overlays/example/namespace.yaml
oc -n odysseus-example create secret generic odysseus-admin \
  --from-literal=ODYSSEUS_ADMIN_PASSWORD='change-me' \
  --dry-run=client -o yaml | oc apply -f -
```

Apply, build, and wait:

```bash
oc apply -k deploy/openshift/overlays/example
oc -n odysseus-example start-build odysseus-chromadb --from-dir=. --follow
oc -n odysseus-example start-build odysseus --from-dir=. --follow
oc -n odysseus-example rollout status deploy/odysseus-chromadb
oc -n odysseus-example rollout status deploy/searxng
oc -n odysseus-example rollout status deploy/odysseus
```

## Validate

Set the namespace and route URL:

```bash
ODYSSEUS_NAMESPACE=odysseus
ODYSSEUS_URL="$(oc -n odysseus get route odysseus -o jsonpath='https://{.spec.host}')"
```

For the example overlay:

```bash
ODYSSEUS_NAMESPACE=odysseus-example
ODYSSEUS_URL=https://odysseus.apps.example.com
```

Run basic checks:

```bash
curl -ksS "${ODYSSEUS_URL}/api/health"
curl -ksS "${ODYSSEUS_URL}/api/ready"
oc -n "${ODYSSEUS_NAMESPACE}" exec deploy/searxng -- \
  wget -qO- 'http://127.0.0.1:8080/search?q=openshift&format=json'
```

Retrieve the admin password when needed:

```bash
oc -n "${ODYSSEUS_NAMESPACE}" extract secret/odysseus-admin \
  --keys=ODYSSEUS_ADMIN_PASSWORD --to=-
```

## Register Model Endpoints

Register OpenAI-compatible LLM endpoints after the model route and model id are
verified. The base URL should be the provider `/v1` base, not the route root.

Example form fields for an authenticated browser session or cookie jar:

```bash
curl -ksS -b /tmp/odysseus.cookies \
  -F 'name=Example vLLM endpoint' \
  -F 'base_url=https://example-model-route.example.com/v1' \
  -F 'api_key=dummy' \
  -F 'endpoint_kind=proxy' \
  -F 'require_models=true' \
  -F 'model_refresh_mode=manual' \
  -F 'model_refresh_timeout=60' \
  -F 'supports_tools=false' \
  -F 'pinned_models=example-model-id' \
  -F 'shared=true' \
  "${ODYSSEUS_URL}/api/model-endpoints"
```

Do not copy historical lab model routes into a new deployment without checking
that the backing InferenceService still exists and that `/v1/models` returns the
expected id.

## Troubleshooting

- Pod cannot start because `odysseus-admin` is missing: create the secret in the
  same namespace before rollout.
- Public web fetch/search fails with certificate errors: remove global
  `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` from the Deployment. Keep only
  scoped `LLM_CA_BUNDLE` for private LLM endpoints.
- SearXNG search fails: check `SEARXNG_INSTANCE=http://searxng:8080`, then test
  the SearXNG service from inside the namespace.
- Readiness fails but health succeeds: inspect `/api/ready`; it checks database
  connectivity, data directory writability, and local-first storage mode.
- Image pull fails: confirm the namespace in the Deployment image points at the
  same namespace used by the ImageStream. Overlays should patch both Odysseus
  image names when changing namespaces.
