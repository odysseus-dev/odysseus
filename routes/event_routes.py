from fastapi import APIRouter, HTTPException, Request
from src.event_store import EventStore
from routes.homelab_routes import _scope_owner

router = APIRouter(prefix="/api/events", tags=["events"])
EVENTS_READ_SCOPES = {"events:read"}
EVENTS_ACK_SCOPES = {"events:ack"}
EVENTS_RESOLVE_SCOPES = {"events:resolve"}

def setup_event_routes() -> APIRouter:
    @router.get("")
    async def list_events(request: Request):
        _scope_owner(request, EVENTS_READ_SCOPES)
        store = EventStore()
        return {"status": "ok", "events": store.get_events()}

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
        event = store.update_status(event_id, "acknowledged", owner)
        if not event:
            raise HTTPException(404, "Event not found")
        return {"status": "ok", "event": event}

    @router.post("/{event_id}/resolve")
    async def resolve_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        event = store.update_status(event_id, "resolved", owner)
        if not event:
            raise HTTPException(404, "Event not found")
        return {"status": "ok", "event": event}

    @router.post("/{event_id}/ignore")
    async def ignore_event(request: Request, event_id: str):
        owner = _scope_owner(request, EVENTS_RESOLVE_SCOPES)
        store = EventStore()
        event = store.update_status(event_id, "ignored", owner)
        if not event:
            raise HTTPException(404, "Event not found")
        return {"status": "ok", "event": event}

    return router
