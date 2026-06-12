# Homelab Events

The Homelab Events API provides a durable event tracking layer for homelab service health checks. It converts transient health check errors into stateful incident reports that can be tracked, acknowledged, and resolved.

## Scopes

To access and manage events, API tokens require the following scopes:
- `events:read`: View events
- `events:write`: Trigger event creation (typically via `/health?record_events=true`)
- `events:ack`: Acknowledge an open event
- `events:resolve`: Mark an event as resolved or ignored

## Event Lifecycle

Events transition through various statuses:
1. **new**: The failure was just detected.
2. **acknowledged**: An operator has seen the event and acknowledged it.
3. **investigating**: Used when work is actively happening (not strictly used via default routes, but supported by the model).
4. **resolved**: The service has recovered.
5. **ignored**: The failure is a false positive or not actionable.

### Deduplication
When `record_events=true` is used against the health API, Odysseus uses a `dedupe_key` to intelligently map repeated failures to an open event rather than spamming new incidents. 
Example deduplication keys:
- `homelab:pihole:unreachable`
- `homelab:pihole:container:exited`
- `homelab:plex:http:503`

If an open event matches the `dedupe_key`, its `count` is incremented, and its `last_seen` timestamp is updated. Once an event is marked as `resolved` or `ignored`, it is closed, and a subsequent failure will trigger a fresh event.

## API Routes

### 1. List All Events
**Route:** `GET /api/events`
**Requires:** `events:read`

**Example:**
```bash
curl -H "Authorization: Bearer <token>" http://localhost:7000/api/events
```

### 2. Get Specific Event
**Route:** `GET /api/events/{id}`
**Requires:** `events:read`

**Example:**
```bash
curl -H "Authorization: Bearer <token>" http://localhost:7000/api/events/123e4567-e89b-12d3-a456-426614174000
```

### 3. Acknowledge Event
**Route:** `POST /api/events/{id}/ack`
**Requires:** `events:ack`

**Example:**
```bash
curl -X POST -H "Authorization: Bearer <token>" http://localhost:7000/api/events/123e4567-e89b-12d3-a456-426614174000/ack
```

### 4. Resolve Event
**Route:** `POST /api/events/{id}/resolve`
**Requires:** `events:resolve`

**Example:**
```bash
curl -X POST -H "Authorization: Bearer <token>" http://localhost:7000/api/events/123e4567-e89b-12d3-a456-426614174000/resolve
```

### 5. Ignore Event
**Route:** `POST /api/events/{id}/ignore`
**Requires:** `events:resolve`

**Example:**
```bash
curl -X POST -H "Authorization: Bearer <token>" http://localhost:7000/api/events/123e4567-e89b-12d3-a456-426614174000/ignore
```

## Integration with Homelab Health
To actually trigger events from homelab status checks, pass the `record_events=true` query parameter to the health endpoint. This requires the `events:write` scope.

**Example:**
```bash
curl -H "Authorization: Bearer <token>" "http://localhost:7000/api/homelab/health?record_events=true"
```

## Usage by Clients (Slack / OpenClaw)
Clients polling for homelab status should transition from simple `/api/homelab/health` read-only polling to triggering `/api/homelab/health?record_events=true`. Once an event is returned in the API, the client should query `/api/events` to build incident alerts, providing operators UI shortcuts to trigger the `/ack` or `/resolve` routes.
