# Homelab Operations

The Homelab Operations layer in Odysseus provides a secure, read-only interface for discovering and monitoring your personal homelab services. This allows Odysseus (and connected clients like OpenClaw) to act as a centralized dashboard for service health.

*Note: Phase 1 provides read-only discovery and health checks. Restarting or managing containers is not yet supported.*

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
- `restart_allowed`: Boolean indicating if restarting the service via Odysseus is allowed (Currently unused in Phase 1).

## Security and Scopes

Access to the Homelab API is protected by the `homelab:read` scope. API tokens without this scope will receive a `403 Forbidden` response.

- Container health checks use secure, structured commands (`docker inspect`) with strict shell execution disabled to prevent arbitrary code injection.
- Odysseus will only query containers explicitly listed in your `homelab_services.json` registry.

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
