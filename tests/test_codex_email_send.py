"""Codex email send delivers through the request's background tasks."""

from fastapi import APIRouter, BackgroundTasks, FastAPI, Request
from fastapi.testclient import TestClient

from routes import codex_routes
from routes.email_routes import SendEmailRequest


def _email_router(delivered: list[tuple]):
    router = APIRouter()

    @router.post("/api/email/send")
    async def send_email(req: SendEmailRequest, background_tasks: BackgroundTasks, owner: str):
        background_tasks.add_task(delivered.append, (owner, req.to, req.subject))
        return {"success": True, "queued": True}

    return router


def _client(delivered: list[tuple], scopes: list[str]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def api_token(request: Request, call_next):
        request.state.current_user = "api"
        request.state.api_token = True
        request.state.api_token_owner = "alice"
        request.state.api_token_scopes = scopes
        return await call_next(request)

    app.include_router(codex_routes.setup_codex_routes(email_router=_email_router(delivered)))
    return TestClient(app)


def test_send_runs_the_queued_delivery():
    delivered = []

    response = _client(delivered, ["email:send"]).post(
        "/api/codex/emails/send",
        json={"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "queued": True}
    assert delivered == [("alice", "bob@example.com", "Hi")]


def test_send_without_send_scope_queues_nothing():
    delivered = []

    response = _client(delivered, ["email:draft"]).post(
        "/api/codex/emails/send",
        json={"to": "bob@example.com", "subject": "Hi", "body": "Hello"},
    )

    assert response.status_code == 403
    assert delivered == []
