# Odysseus Helm Chart

This chart deploys Odysseus plus optional in-cluster ChromaDB, SearXNG, and
ntfy services.

## Install

Build and push an Odysseus image first, then point the chart at it:

```bash
helm install odysseus ./charts/odysseus \
  --set image.repository=ghcr.io/YOUR_OWNER/odysseus \
  --set image.tag=latest
```

For local testing with the default ClusterIP service:

```bash
kubectl port-forward svc/odysseus 7000:7000
```

Then open `http://127.0.0.1:7000`.

## Secrets

Pass sensitive values through `secretEnv.values`:

```bash
helm install odysseus ./charts/odysseus \
  --set image.repository=ghcr.io/YOUR_OWNER/odysseus \
  --set image.tag=latest \
  --set secretEnv.values.OPENAI_API_KEY=sk-... \
  --set secretEnv.values.ODYSSEUS_ADMIN_PASSWORD=change-me
```

Or point at an existing Kubernetes Secret:

```yaml
secretEnv:
  existingSecret: odysseus-env
```

## Bundled Services

By default:

- `chromadb.enabled=true`
- `searxng.enabled=true`
- `ntfy.enabled=false`

When ChromaDB or SearXNG are enabled, the chart automatically sets
`CHROMADB_HOST`, `CHROMADB_PORT`, and `SEARXNG_INSTANCE` for the Odysseus pod.
Disable either service if you want to use an external instance and set the
corresponding values under `env`.

## Persistence

The app uses one PVC and mounts separate subpaths for:

- `/app/data`
- `/app/logs`
- `/app/.ssh`
- `/app/.cache/huggingface`
- `/app/.local`

The bundled ChromaDB, SearXNG, and ntfy deployments each have their own PVCs.

## First Run Setup

`setup.enabled=true` runs `uv run setup.py` as an init container before the app
starts. This initializes the SQLite database and creates the first admin user if
`auth.json` does not already exist.

If you do not provide `ODYSSEUS_ADMIN_PASSWORD`, setup generates a temporary
password in the init container logs:

```bash
kubectl logs deploy/odysseus -c setup
```

## Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: odysseus.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: odysseus-tls
      hosts:
        - odysseus.example.com
```
