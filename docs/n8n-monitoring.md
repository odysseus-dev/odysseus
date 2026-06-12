# n8n Workflow Monitoring

Odysseus provides first-class read-only monitoring for n8n. This allows the OpenClaw agent and homelab dashboard to track workflow execution failures automatically, surfacing them as durable events.

## Configuration

Set the following environment variables in your Odysseus `.env` file or `docker-compose.yml`:

```bash
# The internal Docker network or external URL to your n8n instance.
# Example: http://n8n:5678
N8N_BASE_URL=

# Create a User API token inside the n8n UI (Settings -> n8n API)
N8N_API_KEY=

# Optional: HTTP timeout for n8n API requests
N8N_TIMEOUT_SECONDS=10
```

If `N8N_BASE_URL` is omitted, the integration gracefully degrades and reports `configured: false` on the health endpoints without crashing.

## Required Scopes

To access the n8n monitoring API via an Odysseus token, the token must be granted the following scopes:

- `n8n:read` — Allows listing workflows, checking health, and reading execution summaries.
- `n8n:events` — Allows triggering the `record-events` endpoint to durably record failed executions into the Odysseus Event Store.

These scopes are automatically included in the `openclaw_bridge` token profile.

## API Routes

The integration provides the following standard JSON routes:

- `GET /api/n8n/health` — Checks if n8n is reachable.
- `GET /api/n8n/workflows` — Lists all configured workflows.
- `GET /api/n8n/executions?status=error&limit=10` — Lists recent executions (limit max 100).
- `GET /api/n8n/executions/summary` — Returns a compact summary of failed executions.
- `POST /api/n8n/executions/record-events` — Converts recent failed executions into durable Odysseus events. Deduplicated by `n8n:<workflow_id>:failed`.

### OpenClaw Envelopes

For agents like Slack/OpenClaw, wrapped routes are provided:

- `GET /api/openclaw/n8n/health`
- `GET /api/openclaw/n8n/failures`
- `POST /api/openclaw/n8n/failures/record`

## Event Structure

When a failed execution is recorded as an event, it contains:

- `source`: `n8n` (or `openclaw_n8n`)
- `service`: `n8n`
- `severity`: `warning`
- `title`: `n8n workflow failed`
- `summary`: Includes the workflow name, execution ID, and error message.
- `metadata`: Contains `workflow_id`, `execution_id`, `started_at`, and `stopped_at`.

### Suggested Actions

n8n events emit the following safe read-only UI actions:
- `view_workflow`
- `view_execution`
- `ack`
- `investigate`

**Important:** Destructive actions like `delete`, `disable`, `restart`, or `retry` are strictly prohibited by the OpenClaw safety allowlist and will never be returned to the agent.
