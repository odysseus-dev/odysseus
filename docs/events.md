# Homelab Events

The Homelab Events API provides a durable event tracking layer for homelab service health checks. It converts transient health check errors into stateful incident reports that can be tracked, acknowledged, investigated, and resolved.

Events are stored persistently in `data/homelab_events.json`.

## Scopes

To access and manage events, API tokens require the following scopes:
- `events:read`: View events
- `events:write`: Trigger event creation (typically via `/health?record_events=true`)
- `events:ack`: Acknowledge or investigate an open event
- `events:resolve`: Mark an event as resolved or ignored

## Event Lifecycle

Events transition through various statuses:
1. **new**: The failure was just detected.
2. **acknowledged**: An operator has seen the event and acknowledged it.
3. **investigating**: Used when work is actively happening to fix the issue.
4. **resolved**: The service has recovered.
5. **ignored**: The failure is a false positive or not actionable.

### Deduplication
When `record_events=true` is used against the health API, Odysseus uses a stable `dedupe_key` to intelligently map repeated failures to an open event rather than spamming new incidents. 
Example deduplication keys:
- `homelab:pihole:health`
- `homelab:plex:health`

If an open event matches the `dedupe_key`, its `count` is incremented, and its `last_seen` timestamp is updated. Once an event is marked as `resolved` or `ignored`, it is closed, and a subsequent failure will trigger a fresh event.

## API Routes

### 1. List All Events
**Route:** `GET /api/events`
**Requires:** `events:read`
**Query Parameters:**
- `status`: Filter by status (e.g., `open`, `resolved`, `new`)
- `limit`: Limit the number of returned events

**Example:**
```bash
curl -H "Authorization: Bearer <token>" "http://localhost:7000/api/events?status=open&limit=10"
```

### 2. Get Events Summary
**Route:** `GET /api/events/summary`
**Requires:** `events:read`
Returns a compact summary of the top 10 open events, tailored for conversational UIs.

### 3. Get Specific Event
**Route:** `GET /api/events/{id}`
**Requires:** `events:read`

### 4. Acknowledge Event
**Route:** `POST /api/events/{id}/ack`
**Requires:** `events:ack`

### 5. Investigate Event
**Route:** `POST /api/events/{id}/investigate`
**Requires:** `events:ack`

### 6. Resolve Event
**Route:** `POST /api/events/{id}/resolve`
**Requires:** `events:resolve`

### 7. Ignore Event
**Route:** `POST /api/events/{id}/ignore`
**Requires:** `events:resolve`

## Integration with Homelab Health
To actually trigger events from homelab status checks, pass the `record_events=true` query parameter to the health endpoint. This requires the `events:write` scope.

**Example:**
```bash
curl -H "Authorization: Bearer <token>" "http://localhost:7000/api/homelab/health?record_events=true"
```

## Usage by Clients (Slack / OpenClaw)
Clients polling for homelab status should transition from simple `/api/homelab/health` read-only polling to triggering `/api/homelab/health?record_events=true`. Once an event is returned in the API, the client should query `/api/events` to build incident alerts.

Events return an array of `suggested_actions` such as `ack`, `investigate`, `resolve`, `ignore`, and `view_service` to help UIs render quick-action buttons.

### Recommended OpenClaw Commands
- `ops events` - Lists open events
- `ops event <id>` - Views a specific event
- `ops ack <id>` - Acknowledges an event
- `ops resolve <id>` - Resolves an event
- `ops ignore <id>` - Ignores an event
- `ops homelab health --record` - Runs health checks and records events
