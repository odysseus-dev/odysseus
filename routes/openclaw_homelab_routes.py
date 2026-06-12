"""OpenClaw homelab command-facing routes (Phase 3: read/incident-state only).

Provides a compact Slack-friendly JSON layer over the homelab health checks
and event lifecycle.  No restart actions, no shell execution.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from routes.homelab_routes import (
    HOMELAB_READ_SCOPES,
    EVENTS_WRITE_SCOPES,
    _load_services,
    execute_health_checks,
    _has_scope,
    _scope_owner,
)
from src.event_store import EventStore

logger = logging.getLogger(__name__)

EVENTS_READ_SCOPES = {'events:read'}
EVENTS_ACK_SCOPES = {'events:ack'}
EVENTS_RESOLVE_SCOPES = {'events:resolve'}

# Actions that may appear in OpenClaw responses.
_ALLOWED_ACTIONS = {
    'ack', 'investigate', 'resolve', 'ignore', 'view_service',
    'view_workflow', 'view_execution', 'record_event'
}

BASE_URL = '/api/openclaw/homelab'


def _safe_actions(actions: list[str]) -> list[str]:
    """Filter to only allowed actions before returning to OpenClaw."""
    return [a for a in actions if a in _ALLOWED_ACTIONS]

def _sanitize_dict(data: dict) -> dict:
    """Recursively redact sensitive keys from a dictionary."""
    redact_keys = {'token', 'secret', 'password', 'api_key', 'authorization', 'headers', 'auth'}
    clean = {}
    for k, v in data.items():
        # Normalize camelCase to snake_case before sensitive-key matching.
        k_norm = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', k).lower()
        if any(re.search(rf'(^|[-_]){re.escape(rk)}s?([-_]|$)', k_norm) for rk in redact_keys):
            clean[k] = '***REDACTED***'
        elif isinstance(v, dict):
            clean[k] = _sanitize_dict(v)
        elif isinstance(v, list):
            clean[k] = [_sanitize_dict(i) if isinstance(i, dict) else i for i in v]
        else:
            clean[k] = v
    return clean

def _sanitize_service(service: dict) -> dict:
    """Return a new dict with sensitive fields redacted."""
    return _sanitize_dict(service)


def _event_links(event_id: str) -> dict[str, str]:
    """Return canonical self/collection links for an event."""
    return {
        'self': f'{BASE_URL}/events/{event_id}',
        'events': f'{BASE_URL}/events',
        'ack': f'{BASE_URL}/events/{event_id}/ack',
        'investigate': f'{BASE_URL}/events/{event_id}/investigate',
        'resolve': f'{BASE_URL}/events/{event_id}/resolve',
        'ignore': f'{BASE_URL}/events/{event_id}/ignore',
    }


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a Slack-friendly subset of an event dict."""
    return {
        'id': event['id'],
        'service': event.get('service'),
        'severity': event.get('severity'),
        'status': event.get('status'),
        'title': event.get('title'),
        'summary': event.get('summary'),
        'count': event.get('count', 1),
        'first_seen': event.get('first_seen'),
        'last_seen': event.get('last_seen'),
        'owner': event.get('owner'),
        'suggested_actions': _safe_actions(event.get('suggested_actions', [])),
        'links': _event_links(event['id']),
    }


def _ok(*, message: str, event: dict | None = None, events: list | None = None,
        requires_approval: bool = False) -> dict[str, Any]:
    """Build a successful OpenClaw response envelope."""
    payload: dict[str, Any] = {
        'status': 'ok',
        'message': message,
        'requires_approval': requires_approval,
    }
    if event is not None:
        payload['event'] = event
    if events is not None:
        payload['events'] = events
    return payload


def setup_openclaw_homelab_routes() -> APIRouter:
    """Create and return the /api/openclaw/homelab router."""
    router = APIRouter(prefix=BASE_URL, tags=['openclaw-homelab'])

    # ------------------------------------------------------------------
    # Health routes
    # ------------------------------------------------------------------

    @router.get('/health')
    async def openclaw_homelab_health(request: Request) -> dict[str, Any]:
        """Return homelab health in compact Slack-friendly format.

        Requires: homelab:read
        """
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        results, _, overall = await execute_health_checks(
            services, record_events=False, owner=owner, source_name='openclaw_health'
        )

        unhealthy = [r['name'] for r in results if r.get('status') != 'ok']
        if unhealthy:
            msg = f"{len(unhealthy)} service(s) unhealthy: {', '.join(unhealthy)}"
        else:
            msg = f"All {len(results)} service(s) healthy."

        return _ok(
            message=msg,
            events=None,
            event=None,
        ) | {
            'overall_status': overall,
            'services': results,
            'links': {'health': f'{BASE_URL}/health', 'events': f'{BASE_URL}/events'},
        }

    @router.post('/health/record')
    async def openclaw_homelab_health_record(request: Request) -> dict[str, Any]:
        """Run health checks and record failures as durable events.

        Requires: homelab:read + events:write
        """
        owner = _scope_owner(request, HOMELAB_READ_SCOPES)
        if not _has_scope(request, EVENTS_WRITE_SCOPES):
            raise HTTPException(403, 'API token missing required scope: events:write')

        services = _load_services()
        results, raw_events, overall = await execute_health_checks(
            services, record_events=True, owner=owner, source_name='openclaw_health'
        )
        recorded = [_compact_event(e) for e in raw_events]

        msg = (
            f"{len(recorded)} event(s) recorded from {len(results)} service(s)."
            if recorded else
            f"All {len(results)} service(s) healthy — no events recorded."
        )

        return _ok(message=msg, events=recorded) | {
            'overall_status': overall,
            'services': results,
            'links': {'health': f'{BASE_URL}/health', 'events': f'{BASE_URL}/events'},
        }

    # ------------------------------------------------------------------
    # Service read routes
    # ------------------------------------------------------------------

    @router.get('/services')
    async def openclaw_list_services(request: Request) -> dict[str, Any]:
        """List homelab services in compact format.

        Requires: homelab:read
        """
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = [_sanitize_service(srv) for srv in _load_services()]
        msg = f"{len(services)} service(s) returned." if services else 'No services found.'
        return _ok(message=msg, events=None) | {
            'services': services,
            'links': {'services': f'{BASE_URL}/services', 'health': f'{BASE_URL}/health'},
        }

    @router.get('/services/{name}')
    async def openclaw_get_service(request: Request, name: str) -> dict[str, Any]:
        """Get a specific homelab service by name.

        Requires: homelab:read
        """
        _scope_owner(request, HOMELAB_READ_SCOPES)
        services = _load_services()
        for srv in services:
            if srv.get('name') == name:
                return _ok(message=f"Service {name}.", event=None) | {
                    'service': _sanitize_service(srv),
                    'links': {'services': f'{BASE_URL}/services', 'health': f'{BASE_URL}/health'},
                }
        raise HTTPException(404, 'Service not found')

    # ------------------------------------------------------------------
    # Event read routes
    # ------------------------------------------------------------------

    @router.get('/events')
    async def openclaw_list_events(
        request: Request,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List homelab events in compact Slack-friendly format.

        Requires: events:read
        """
        _scope_owner(request, EVENTS_READ_SCOPES)
        if limit is not None and not (1 <= limit <= 100):
            raise HTTPException(400, 'limit must be between 1 and 100')

        store = EventStore()
        try:
            events = store.get_events(status=status, limit=limit)
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

        compact = [_compact_event(e) for e in events]
        count = len(compact)
        msg = f"{count} event(s) returned." if count else 'No events found.'
        return _ok(message=msg, events=compact) | {
            'links': {'events': f'{BASE_URL}/events', 'health': f'{BASE_URL}/health'},
        }

    @router.get('/events/{event_id}')
    async def openclaw_get_event(request: Request, event_id: str) -> dict[str, Any]:
        """Get a single event by ID.

        Requires: events:read
        """
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        event = store.get_event(event_id)
        if not event:
            raise HTTPException(404, 'Event not found')
        return _ok(
            message=f"Event {event_id}.",
            event=_compact_event(event),
        )

    # ------------------------------------------------------------------
    # Event lifecycle mutation routes
    # ------------------------------------------------------------------

    @router.post('/events/{event_id}/ack')
    async def openclaw_ack_event(request: Request, event_id: str) -> dict[str, Any]:
        """Acknowledge an open event.

        Requires: events:ack
        """
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'acknowledged', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} acknowledged by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/investigate')
    async def openclaw_investigate_event(request: Request, event_id: str) -> dict[str, Any]:
        """Mark an event as being investigated.

        Requires: events:ack
        """
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'investigating', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} marked as investigating by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/resolve')
    async def openclaw_resolve_event(request: Request, event_id: str) -> dict[str, Any]:
        """Resolve an event.

        Requires: events:resolve
        """
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'resolved', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} resolved by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    @router.post('/events/{event_id}/ignore')
    async def openclaw_ignore_event(request: Request, event_id: str) -> dict[str, Any]:
        """Ignore an event.

        Requires: events:resolve
        """
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, 'ignored', owner)
            if not event:
                raise HTTPException(404, 'Event not found')
            return _ok(
                message=f"Event {event_id} ignored by {owner}.",
                event=_compact_event(event),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f'Persistence error: {e}')

    return router
