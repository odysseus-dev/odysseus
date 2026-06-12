from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from src.event_store import EventStore
from routes.homelab_routes import _scope_owner

EVENTS_READ_SCOPES = {"events:read"}
EVENTS_ACK_SCOPES = {"events:ack"}
EVENTS_RESOLVE_SCOPES = {"events:resolve"}

def setup_event_routes() -> APIRouter:
    router = APIRouter(prefix="/api/events", tags=["events"])

    @router.get("")
    async def list_events(request: Request, status: Optional[str] = None, limit: Optional[int] = Query(None, ge=1, le=100)):
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        try:
            events = store.get_events(status=status, limit=limit)
            return {"status": "ok", "events": events}
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    @router.get("/summary")
    async def get_events_summary(request: Request):
        # Alias for compact open events list
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        try:
            events = store.get_events(status="open", limit=10)
            # Make it compact
            compact = []
            for e in events:
                compact.append({
                    "id": e["id"],
                    "service": e["service"],
                    "severity": e["severity"],
                    "status": e["status"],
                    "title": e["title"],
                    "summary": e["summary"],
                    "count": e["count"],
                    "first_seen": e["first_seen"],
                    "last_seen": e["last_seen"],
                    "suggested_actions": e.get("suggested_actions", [])
                })
            return {"status": "ok", "events": compact}
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    @router.get("/{event_id}")
    async def get_event(request: Request, event_id: str):
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        event = store.get_event(event_id)
        if not event:
            raise HTTPException(404, "Event not found")
        return {"status": "ok", "event": event}

    @router.post("/{event_id}/ack")
    async def ack_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, "acknowledged", owner)
            if not event:
                raise HTTPException(404, "Event not found")
            return {"status": "ok", "event": event}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    @router.post("/{event_id}/investigate")
    async def investigate_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_ACK_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, "investigating", owner)
            if not event:
                raise HTTPException(404, "Event not found")
            return {"status": "ok", "event": event}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    @router.post("/{event_id}/resolve")
    async def resolve_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, "resolved", owner)
            if not event:
                raise HTTPException(404, "Event not found")
            return {"status": "ok", "event": event}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    @router.post("/{event_id}/ignore")
    async def ignore_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        try:
            event = store.update_status(event_id, "ignored", owner)
            if not event:
                raise HTTPException(404, "Event not found")
            return {"status": "ok", "event": event}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Persistence error: {str(e)}")

    return router
