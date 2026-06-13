# Homelab Operations

The Homelab Operations layer in Odysseus provides a secure, read-only interface for discovering and monitoring your personal homelab services. This allows Odysseus (and connected clients like OpenClaw) to act as a centralized dashboard for service health.

*Note: Phase 1-3 provided read-only discovery, health checks, and event monitoring. Phase 4 introduces strict, scoped write operations (like restarting containers or resolving events), protected by explicit confirmation flags.*

## Service Registry

Homelab services are defined in a JSON registry file.

1. Copy the example configuration:
   ```bash
   cp config/homelab_services.example.json config/homelab_services.json
   ```
2. Edit `config/homelab_services.json` to add your services.

### Configuration Fields

- `name`: Unique identifier for the service (e.g., `"pihole"`).
- `display_name`: Human-readable name.
- `tier`: Classification of the service (e.g., `"core"`, `"media"`).
- `host`: The IP address or hostname where the service runs.
- `container`: (Optional) The Docker container name. If provided, Odysseus will check its running status.
- `url`: (Optional) The main URL to access the service.
- `health_url`: (Optional) A specific API endpoint or URL to verify the service is up. Odysseus will perform an HTTP GET request against this URL or `url` if `health_url` is absent.
- `tags`: An array of descriptive tags.
- `restart_allowed`: Boolean indicating if restarting the service via Odysseus is allowed via the `/ops/docker-restart` API.

## Security and Scopes

Access to the Homelab API is protected by several scopes. Read-only APIs require `homelab:read`.
Operations that mutate state require `homelab:write` (or `n8n:write` / `events:write` / `events:resolve`).

- Container health checks use secure, structured commands (`docker inspect`) with strict shell execution disabled to prevent arbitrary code injection.
- Odysseus will only query or restart containers explicitly listed in your `homelab_services.json` registry.
- All write operations require a strict `--confirm` gate to protect against accidental LLM hallucinations.

## Write Operations (Phase 4)
Write operations provide restricted abilities to mutate homelab state. These APIs are exposed via the OpenClaw bridge rather than the core routes.
- **Docker Restart**: Restarts a container associated with a registered homelab service. Requires `homelab:write`.
- **N8N Rerun**: Reruns a failed N8N execution by ID. Requires `n8n:write`.
- **Redmine Ticket**: Creates a Redmine ticket tracking a homelab event. Requires `homelab:write`.

For comprehensive OpenClaw API examples, see `docs/openclaw-bridge.md`.

## API Routes

### 1. List All Services
Retrieve the full registry of homelab services.

**Route:** `GET /api/homelab/services`
**Requires:** `homelab:read`

**Example:**
```bash
curl -H "Authorization: Bearer <token>" http://localhost:7000/api/homelab/services
```

### 2. Get Specific Service
Retrieve the configuration details for a single service.

**Route:** `GET /api/homelab/services/{name}`
**Requires:** `homelab:read`

**Example:**
```bash
curl -H "Authorization: Bearer <token>" http://localhost:7000/api/homelab/services/pihole
```

### 3. Check Homelab Health
Perform non-destructive health checks against all registered services. This endpoint queries Docker statuses (if `container` is provided) and HTTP statuses (if `health_url` or `url` is provided).

**Route:** `GET /api/homelab/health`
**Requires:** `homelab:read`

**Example:**
```bash
curl -H "Authorization: Bearer <token>" http://localhost:7000/api/homelab/health
```

**Response Format:**
```json
{
  "status": "ok",
  "services": [
    {
      "name": "pihole",
      "status": "ok",
      "container_status": "running",
      "http_status": 200
    }
  ]
}
```
*Note: The top-level `status` can be `"ok"`, `"degraded"`, or `"error"` depending on the individual service statuses.*

## Performance & Concurrency (Phase 2.1)

### Concurrent health checks

All service checks run concurrently using `asyncio.gather()` rather than sequentially. A semaphore limits the number of checks in flight at once.

**Environment variable:** `HOMELAB_HEALTH_CONCURRENCY`

| Value | Behaviour |
|---|---|
| Unset | Default: **5** concurrent checks |
| Valid integer (1–50) | Uses that value |
| `0` or negative | Clamped to **1** |
| `> 50` | Clamped to **50** |
| Non-numeric | Logs a warning and falls back to **5** |

```bash
# Example: allow up to 10 parallel checks
HOMELAB_HEALTH_CONCURRENCY=10
```

### Shared HTTP client

A single `httpx.AsyncClient` is created per health request and reused across all service checks, avoiding repeated TLS handshakes and connection overhead.

### Non-blocking Docker checks

`docker inspect` is executed in a thread pool via `asyncio.to_thread()` so it cannot block the event loop. `shell=False` is always enforced; the container name comes only from the registry config, never from user input.

### Serial event recording

Even though health checks run concurrently, **event recording is always serial**. After all checks complete, results are looped in order and written one at a time to `data/homelab_events.json`. This prevents concurrent writes that could corrupt the JSON store and ensures deduplication works correctly.

```
Concurrent phase:  [check s1] [check s2] [check s3]  ← asyncio.gather
                        ↓
Serial phase:      record s1 → record s2 → record s3  ← sequential loop
```

