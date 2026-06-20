# Odysseus Helm chart

Deploys Odysseus and its sidecars into a **single Kubernetes namespace**, mirroring
`docker-compose.yml`:

| Component | Image | Port | Persistence |
|-----------|-------|------|-------------|
| odysseus  | built from repo `Dockerfile` | 7000 | `data` + `logs` PVCs |
| chromadb  | `chromadb/chroma` | 8000 | PVC `/chroma/chroma` |
| searxng   | `searxng/searxng` (pinned) | 8080 | PVC `/etc/searxng` |
| ntfy      | `binwiederhier/ntfy` | 80 | PVC `/var/cache/ntfy` |

Service discovery (`SEARXNG_INSTANCE`, `CHROMADB_HOST`/`PORT`) is injected from the
in-cluster service names, so the app always points at what the chart deploys.

## Prerequisites

- A namespace (everything lands in the release namespace — `helm install -n <ns>`).
- A default StorageClass that supports `ReadWriteOnce` (or set `storageClass`/`existingClaim`).
- An Odysseus image. The chart defaults to `ghcr.io/pewdiepie-archdaemon/odysseus:latest`,
  published by `.github/workflows/docker-publish.yml`. For a fork, build and push
  your own and override `odysseus.image.repository`/`tag`:

  ```sh
  docker build -t <registry>/odysseus:<tag> .
  docker push <registry>/odysseus:<tag>
  ```

## Install

```sh
helm install ody charts/odysseus \
  --namespace odysseus --create-namespace \
  --set odysseus.secrets.ODYSSEUS_ADMIN_PASSWORD=<password>
```

For a fork image, add `--set odysseus.image.repository=<registry>/odysseus --set odysseus.image.tag=<tag>`.

Keep secrets out of git by using `--set`, a private values file, or
`odysseus.secrets.existingSecret` to reference a Secret you created.

## Notes

- `odysseus.replicaCount` stays at 1: SQLite + `ReadWriteOnce` PVCs allow one writer.
  To scale, move `DATABASE_URL` to an external DB and the data dirs to RWX storage.
- The app container starts as root by design — its entrypoint drops to
  `odysseus.puid`/`pgid` via gosu and chowns the mounted dirs. Don't force a
  non-root `runAsUser` unless you rebuild the image without gosu.
- SearXNG auto-generates a secret on first boot if `searxng.secretKey` is unset;
  it persists in the PVC. Set `searxng.secretKey` for a fixed, managed value.
- Expose the app via `odysseus.ingress.*` or `kubectl port-forward`.

See also the [setup guide](../../docs/setup.md#kubernetes-helm) and
[backup notes](../../docs/backup-restore.md#docker-vs-native-vs-kubernetes-installs).
