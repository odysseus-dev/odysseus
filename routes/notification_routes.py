"""Routes for durable notification events and inbox items."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import require_user
from src.notifications import (
    archive_notification,
    count_unread_notifications,
    dismiss_notification,
    list_inbox_notifications,
    list_notification_events,
    mark_notification_read,
)


class NotificationReadRequest(BaseModel):
    read: bool = True


def setup_notification_routes() -> APIRouter:
    router = APIRouter(prefix="/api/notifications", tags=["notifications"])

    @router.get("")
    async def list_notifications(
        request: Request,
        limit: int = 50,
        include_archived: bool = False,
        include_dismissed: bool = False,
    ):
        owner = require_user(request)
        return {
            "notifications": list_inbox_notifications(
                owner=owner,
                limit=limit,
                include_archived=include_archived,
                include_dismissed=include_dismissed,
            )
        }

    @router.get("/count")
    async def notification_count(request: Request):
        owner = require_user(request)
        return {"unread": count_unread_notifications(owner=owner)}

    @router.get("/events")
    async def notification_events(
        request: Request,
        limit: int = 100,
        event_class: Optional[str] = None,
    ):
        owner = require_user(request)
        return {
            "events": list_notification_events(
                owner=owner,
                limit=limit,
                event_class=event_class,
            )
        }

    @router.post("/{item_id}/read")
    async def read_notification(request: Request, item_id: str, body: NotificationReadRequest):
        owner = require_user(request)
        item = mark_notification_read(item_id=item_id, owner=owner, read=body.read)
        if not item:
            raise HTTPException(404, "Notification not found")
        return item

    @router.post("/{item_id}/dismiss")
    async def dismiss_inbox_notification(request: Request, item_id: str):
        owner = require_user(request)
        item = dismiss_notification(item_id=item_id, owner=owner)
        if not item:
            raise HTTPException(404, "Notification not found")
        return item

    @router.post("/{item_id}/archive")
    async def archive_inbox_notification(request: Request, item_id: str):
        owner = require_user(request)
        item = archive_notification(item_id=item_id, owner=owner)
        if not item:
            raise HTTPException(404, "Notification not found")
        return item

    return router
