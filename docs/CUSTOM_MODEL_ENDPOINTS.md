# Adding Custom Model Endpoints

This guide explains how to add custom or local model endpoints (like local vLLM servers, LM Studio instances, or custom LLM APIs) to Odysseus.

## Overview

Odysseus supports multiple LLM endpoints through the **Model Endpoints** admin panel. Each endpoint can serve multiple models and can optionally require authentication via API key.

## Quick Start

### Via Admin UI (Recommended)

1. **Open Odysseus** at `http://localhost:7000`
2. Navigate to **Admin → Model Endpoints**
3. Click **"Add Endpoint"**
4. Fill in the form:
   - **Name:** Descriptive label (e.g., "Local vLLM", "LM Studio", "Code Model")
   - **Base URL:** The `/v1` endpoint URL (e.g., `http://127.0.0.1:8000/v1`)
   - **API Key:** If required by your backend (leave empty for local Ollama/vLLM)
   - **Model Type:** `LLM` or `image` (usually `LLM`)
   - **Supports Tools:** Leave blank for auto-detection, or set `true`/`false` if you know the backend's capabilities
5. Click **Save**

Odysseus will automatically probe the endpoint and discover available models.

### Via REST API

If you prefer programmatic addition (e.g., in scripts or CI/CD):

```bash
curl -X POST http://localhost:7000/api/model-endpoints \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "name=Local%20Code%20Model" \
  -d "base_url=http://127.0.0.1:20128/v1" \
  -d "api_key=sk-your-api-key-here" \
  -d "model_type=llm" \
  -d "skip_probe=false"
```

**Note:** If your endpoint doesn't require authentication, omit the `api_key` parameter.

## Common Setups

### Local Ollama

```
Base URL: http://localhost:11434/v1
API Key: (leave empty)
```

Odysseus auto-detects Ollama on common ports, so this is often added automatically.

### Local vLLM Server

```
Base URL: http://127.0.0.1:8000/v1
API Key: (leave empty if no auth configured)
```

If vLLM is behind authentication:

```
Base URL: http://127.0.0.1:8000/v1
API Key: Bearer your-token-here (or sk-xxx depending on your auth)
```

### LM Studio (Local)

```
Base URL: http://localhost:1234/v1
API Key: (leave empty)
```

### Custom Local API (e.g., Combo)

```
Base URL: http://127.0.0.1:20128/v1
API Key: sk-576a1c43755b51a6-be3nal-aa9f6298 (or whatever your service requires)
```

## Authentication

Odysseus supports two authentication methods:

1. **Bearer Token** (standard OpenAI-compatible):
   ```
   Authorization: Bearer <your-api-key>
   ```

2. **Anthropic API Key** (if your endpoint mimics Anthropic's API):
   ```
   x-api-key: <your-api-key>
   ```

The system automatically detects the endpoint provider (OpenAI, Anthropic, custom, etc.) and uses the appropriate header format.

## Troubleshooting

### "API key rejected" / "Missing API key"

If you see errors like **"local endpoint rejected the API key — Missing API key"**:

1. **Verify the key is saved correctly:**
   - Go to Admin → Model Endpoints
   - Click the endpoint to edit it
   - Paste the API key again and save
   - The system will re-probe and may discover models if the key is now valid

2. **Check the endpoint format:**
   - Base URL should end in `/v1` (for OpenAI-compatible APIs)
   - Some endpoints require `/api/v1` or other paths — check your service documentation

3. **Verify the endpoint is online:**
   - Test from terminal: `curl http://127.0.0.1:20128/v1/models -H "Authorization: Bearer <your-key>"`
   - The endpoint should return a JSON list of models

4. **Check endpoint requirements:**
   - Some services require specific headers or authentication schemes
   - Consult your API provider's documentation

### Endpoint appears "offline" but the server is running

1. Increase the probe timeout (Odysseus waits 1–3 seconds by default)
2. Check that the `/v1/models` endpoint exists and returns valid JSON
3. Verify firewall rules if the endpoint is on a different machine

### Models not appearing after adding the endpoint

1. **Endpoint must support OpenAI-compatible `/v1/models`:**
   ```json
   {
     "object": "list",
     "data": [
       {"id": "model-name", "object": "model", "owned_by": "provider"},
       ...
     ]
   }
   ```

2. **Or Ollama format (for Ollama-only endpoints):**
   ```json
   {
     "models": [
       {"name": "model-name", "size": 4700000000, ...},
       ...
     ]
   }
   ```

3. If your endpoint has a different response format, manually pin model IDs:
   - Edit the endpoint
   - Under "Pinned Models", enter the exact model IDs your endpoint serves
   - Click Save

## Advanced Configuration

### Skip Initial Probe

If you know your models ahead of time and want to avoid the initial probe timeout:

```bash
curl -X POST http://localhost:7000/api/model-endpoints \
  -d "name=My%20Endpoint" \
  -d "base_url=http://127.0.0.1:8000/v1" \
  -d "api_key=sk-xxx" \
  -d "skip_probe=true" \
  -d "pinned_models=model-name-1,model-name-2"
```

### Updating an Existing Endpoint

To change the API key, base URL, or other settings without deleting and re-adding:

```bash
curl -X PATCH http://localhost:7000/api/model-endpoints/{endpoint_id} \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "new-key-here",
    "base_url": "http://new-host:port/v1"
  }'
```

### Per-User Endpoints (Admin Only)

By default, new endpoints are visible to all users. To scope an endpoint to yourself:

```bash
curl -X POST http://localhost:7000/api/model-endpoints \
  -d "name=My%20Personal%20Endpoint" \
  -d "base_url=http://127.0.0.1:8000/v1" \
  -d "shared=false"
```

Set `shared=false` to restrict the endpoint to your account only (admins always see all endpoints).

## Database Direct Access (Advanced)

If the UI or API doesn't work, you can add an endpoint directly to the database:

```python
import uuid
import json
from core.database import SessionLocal, ModelEndpoint

ep_id = str(uuid.uuid4())[:8]
db = SessionLocal()
try:
    ep = ModelEndpoint(
        id=ep_id,
        name="Local Code Model",
        base_url="http://127.0.0.1:20128/v1",
        api_key="sk-576a1c43755b51a6-be3nal-aa9f6298",  # Auto-encrypted
        is_enabled=True,
        model_type="llm",
        cached_models=json.dumps(["code", "Github"]),  # Optional: pre-populate models
    )
    db.add(ep)
    db.commit()
    print(f"Endpoint added: {ep_id}")
finally:
    db.close()
```

**Note:** API keys are encrypted at rest using Fernet (see `src/secret_storage.py`).

## Environment Variables

You can also configure default LLM endpoints via environment variables in `.env`:

```bash
# Ollama endpoint (auto-discovered on common ports, but can be overridden)
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1

# LM Studio endpoint (auto-discovered)
LM_STUDIO_URL=http://host.docker.internal:1234

# Additional hosts to scan for models (comma-separated)
LLM_HOSTS=llm-host.local,backup-llm.local
```

These environment variables set up default/fallback endpoints that are auto-discovered during startup. For custom/static endpoints, use the Admin UI or API instead.

## See Also

- [Model Endpoint Schema](../core/database.py#L326) — SQLAlchemy model definition
- [Endpoint Resolver](../src/endpoint_resolver.py) — How Odysseus resolves and probes endpoints
- [LLM Core](../src/llm_core.py) — Chat and model invocation logic
- [Model Routes](../routes/model_routes.py) — Admin API endpoints
