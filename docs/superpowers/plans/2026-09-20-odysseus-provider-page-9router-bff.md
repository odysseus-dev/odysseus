# Odysseus Provider Page → Stack 9router BFF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Odysseus Settings connects inference providers through an in-stack 9router BFF; Odysseus never stores provider secrets and never opens 9router’s dashboard.

**Architecture:** A GET-only metadata client stays for catalog/health/usage. A separate connect client POSTs API keys and OAuth codes to unpublished Compose 9router on the Docker network. Settings lists redacted rows and starts connect in Odysseus chrome. Opaque `ProviderAuthSession` projections (`connection_id`, `status`, `entitlement`, `label`, `owner`) are the only persistence.

**Tech Stack:** FastAPI Odysseus, `httpx`, existing `ProviderAuthSession` / `provision_connection`, vanilla Settings JS (`static/index.html`, `static/js/admin.js`, `static/js/providerDeviceFlow.js`), overlay Compose 9router on the Linux VM.

## Global Constraints

- Develop on the already-deployed Linux VM overlay: SSH `orchestration-vm`, checkout `/home/agent/work/odysseus`, compose `docker-compose.yml` + `docker-compose.openhands.yml` only.
- Stack 9router is unpublished. Reach it with `docker compose exec`, origin `http://9router:20128`. Odysseus uses `NINE_ROUTER_METADATA_URL=http://9router:20128`.
- Do not use a host-wide 9router as connect or inference. Do not open `/dashboard/providers` as the product path. Do not persist API keys or OAuth tokens on Odysseus.
- Do not embed Canvas or 9router UI. Do not route through HHPE. Do not call `/v1/chat/completions` from metadata or connect clients.
- If pinned overlay 9router has no sessionless connect API, **stop**. Fail closed. Do not revive dashboard hop. Do not persist keys.
- Fake tests may run in the Odysseus image. Live 9router tests run only on this guest overlay after `python3 scripts/openhands_probe.py stack`.
- After the feature works on the guest checkout, package and redeploy that tree to confirm ship. Daily work is not edit-elsewhere-and-rsync.
- Commit messages: `feat:` / `test:` / `fix:` / `docs:`. Do not push unless asked.
- Guest pytest via compose (image already has the app). Prefix:

```bash
ssh orchestration-vm
cd /home/agent/work/odysseus
export PATH="$HOME/.local/bin:$PATH"
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q <paths> --tb=short
```

## File map

- Create: `services/ninerouter/connect.py` — sessionless connect client + path allowlist (locked in Task 1)
- Create: `routes/ninerouter_connection_routes.py` — catalog / API-key connect / OAuth start+callback / disconnect
- Create: `tests/test_ninerouter_connect_client.py`
- Create: `tests/test_ninerouter_connection_routes.py`
- Modify: `app.py` — include the new router
- Modify: `routes/chatgpt_subscription_routes.py` — start returns upstream IdP URL, never `/dashboard/providers`
- Modify: `static/index.html` — replace Add API Models card with 9router connections panel
- Modify: `static/js/admin.js` — catalog + connect against `/api/ninerouter/connections*`
- Modify: `static/js/providerDeviceFlow.js` — still opens `authUrl` from start; start must be IdP
- Modify: `tests/test_provider_connection_projection.py` — ChatGPT start is not dashboard PKCE
- Modify: `tests/test_provider_device_flow_js.py` — IdP URL, not dashboard
- Modify: `tests/test_admin_device_flow_static.py` / `tests/test_setup_device_auth_static.py` as copy requires
- Modify: `docs/operations/linux-vm-openhands-overlay.md` — develop on guest; connect via Odysseus Settings

Do not vendor 9router source. Do not bump the overlay 9router image in this slice.

---

### Task 1: Prove sessionless connect on overlay 9router

**Files:**
- Create: `services/ninerouter/connect.py` (allowlist constants only after probe)
- Create: `tests/test_ninerouter_connect_client.py` (allowlist tests; methods land in Task 2)

**Interfaces:**
- Consumes: running overlay; `NineRouterMetadataError` pattern from `services/ninerouter/metadata.py`
- Produces: module-level allowlists used by `NineRouterConnectClient` in Task 2:
  - `CONNECT_GET_EXACT: frozenset[str]`
  - `CONNECT_POST_EXACT: frozenset[str]`
  - `CONNECT_DELETE_PREFIXES: tuple[str, ...]`
  - `CONNECT_GET_PREFIXES: tuple[str, ...]`
  - `CONNECT_POST_PREFIXES: tuple[str, ...]`
  - `normalize_connect_path(method: str, path: str) -> str` (raises `NineRouterMetadataError` if forbidden)

- [ ] **Step 1: Confirm overlay health on the guest**

Run:

```bash
ssh orchestration-vm
cd /home/agent/work/odysseus
export PATH="$HOME/.local/bin:$PATH"
python3 scripts/openhands_probe.py stack --json
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T 9router \
  wget -qO- http://127.0.0.1:20128/api/health
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -c "import urllib.request; print(urllib.request.urlopen('http://9router:20128/api/health', timeout=5).read())"
```

Expected: probe all-pass; health JSON from both 9router and Odysseus containers. Stop if Odysseus cannot reach `http://9router:20128`.

- [ ] **Step 2: Discover connect endpoints from inside the overlay**

From the Odysseus container, fetch OpenAPI or probe paths. Record status codes (401 with JSON is “exists”; 404 is not).

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus python - <<'PY'
import urllib.request, urllib.error
base = "http://9router:20128"
candidates = [
    "/openapi.json",
    "/api/docs",
    "/api/providers",
    "/api/oauth/openai/authorize",
    "/api/oauth/codex/authorize",
    "/api/oauth/github/authorize",
]
for path in candidates:
    try:
        req = urllib.request.Request(base + path, method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            print(r.status, path, r.headers.get_content_type(), r.read()[:200])
    except urllib.error.HTTPError as e:
        print(e.code, path, e.read()[:200])
    except Exception as e:
        print("ERR", path, type(e).__name__, e)
PY
```

Also try POST with a dummy key (must not persist a real secret in the plan transcript; use `sk-probe-invalid`):

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus python - <<'PY'
import json, urllib.request, urllib.error
body = json.dumps({"provider": "openai", "apiKey": "sk-probe-invalid"}).encode()
for path in ("/api/providers", "/api/providers/create", "/api/connections"):
    req = urllib.request.Request(
        "http://9router:20128" + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            print(r.status, path, r.read()[:300])
    except urllib.error.HTTPError as e:
        print(e.code, path, e.read()[:300])
    except Exception as e:
        print("ERR", path, type(e).__name__, e)
PY
```

**Pass:** at least one POST that accepts a provider+key without a 9router dashboard cookie, and an OAuth start that returns an **upstream IdP** URL (auth.openai.com, github.com, etc.), not `/dashboard/providers`.

**Stop:** no sessionless connect. Write a short note in the PR/commit message and halt. Do not implement Tasks 3–8 UI against dashboard PKCE.

- [ ] **Step 3: Write failing allowlist tests**

Create `tests/test_ninerouter_connect_client.py`:

```python
import pytest
from services.ninerouter.connect import normalize_connect_path
from services.ninerouter.metadata import NineRouterMetadataError


def test_connect_allowlist_rejects_inference_and_dashboard():
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("POST", "/v1/chat/completions")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/v1/models")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/dashboard/providers")


def test_connect_allowlist_accepts_probed_provider_paths():
    # Replace paths with the Task 1 probe results before committing.
    assert normalize_connect_path("GET", "/api/providers") == "/api/providers"
    assert normalize_connect_path("POST", "/api/providers") == "/api/providers"
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_ninerouter_connect_client.py --tb=short
```

Expected: FAIL (`ModuleNotFoundError: services.ninerouter.connect` or missing `normalize_connect_path`).

- [ ] **Step 5: Write allowlist-only `connect.py`**

Create `services/ninerouter/connect.py`. Fill the frozensets from **this guest’s probe**, not from guesswork. Keep `/v1` and `/dashboard` out.

```python
"""Sessionless 9router connect client (in-network only).

Odysseus may create/delete connections and start/complete OAuth. It must
never call inference (`/v1/*`) or send the browser to `/dashboard/providers`.
"""

from __future__ import annotations

from urllib.parse import urlparse

from services.ninerouter.metadata import NineRouterMetadataError, ninerouter_metadata_url, metadata_token

# Locked from Task 1 overlay probe. Update only if the guest API differs.
CONNECT_GET_EXACT: frozenset[str] = frozenset({"/api/providers"})
CONNECT_POST_EXACT: frozenset[str] = frozenset({"/api/providers"})
CONNECT_GET_PREFIXES: tuple[str, ...] = ("/api/oauth/",)
CONNECT_POST_PREFIXES: tuple[str, ...] = ("/api/oauth/",)
CONNECT_DELETE_PREFIXES: tuple[str, ...] = ("/api/providers/",)


def normalize_connect_path(method: str, path: str) -> str:
    """Return a cleaned path if (method, path) is on the connect allowlist."""
    verb = (method or "GET").upper().strip()
    raw = (path or "").strip() or "/"
    parsed = urlparse(raw if raw.startswith("/") else f"/{raw}")
    cleaned = parsed.path or "/"
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    if cleaned.startswith("/v1/") or cleaned == "/v1" or "chat/completions" in cleaned:
        raise NineRouterMetadataError("path is outside the 9router connect allowlist")
    if cleaned.startswith("/dashboard"):
        raise NineRouterMetadataError("path is outside the 9router connect allowlist")
    if verb == "GET" and (cleaned in CONNECT_GET_EXACT or cleaned.startswith(CONNECT_GET_PREFIXES)):
        return cleaned
    if verb == "POST" and (cleaned in CONNECT_POST_EXACT or cleaned.startswith(CONNECT_POST_PREFIXES)):
        return cleaned
    if verb == "DELETE" and cleaned.startswith(CONNECT_DELETE_PREFIXES) and cleaned != "/api/providers":
        return cleaned
    raise NineRouterMetadataError("path is outside the 9router connect allowlist")
```

Adjust exact/prefix sets to match Step 2. If OAuth lives at `/api/oauth/{provider}/authorize`, the `/api/oauth/` prefix is correct.

- [ ] **Step 6: Run tests to verify they pass**

Run the same pytest command as Step 4. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/ninerouter/connect.py tests/test_ninerouter_connect_client.py
git commit -m "feat: allowlist in-stack 9router connect paths"
```

---

### Task 2: NineRouterConnectClient (redacting, no inference)

**Files:**
- Modify: `services/ninerouter/connect.py`
- Modify: `tests/test_ninerouter_connect_client.py`

**Interfaces:**
- Consumes: `normalize_connect_path`, `ninerouter_metadata_url()`, `metadata_token()`, `redact_providers` from `services/ninerouter/metadata.py`
- Produces:
  - `class NineRouterConnectError(NineRouterMetadataError)`
  - `class NineRouterConnectClient`
  - `create_api_key(self, provider: str, api_key: str) -> dict`
  - `start_oauth(self, provider: str, redirect_uri: str) -> dict` returning `{"authorization_url": str}` (IdP only)
  - `complete_oauth(self, provider: str, code: str, state: str | None = None) -> dict`
  - `delete_connection(self, connection_id: str) -> None`
  - `list_providers(self) -> list[dict]` (redacted)
  - `_reject_dashboard_url(url: str) -> str`

Return dicts must already be passed through `redact_providers` / `_redact_mapping`. Never return `apiKey`, `access_token`, `refresh_token`, or `base_url`.

- [ ] **Step 1: Write failing client tests** (append to `tests/test_ninerouter_connect_client.py`)

```python
from services.ninerouter.connect import NineRouterConnectClient, NineRouterConnectError


def test_create_api_key_posts_then_drops_secret_from_result():
    seen = []

    def fetch(method, path, headers, json_body):
        seen.append((method, path, json_body, headers.get("Authorization")))
        return {"id": "conn-1", "apiKey": "sk-live-should-strip", "status": "usable", "name": "OpenAI"}

    client = NineRouterConnectClient(fetch=fetch)
    row = client.create_api_key("openai", "sk-live-should-strip")
    assert seen[0][0] == "POST"
    assert seen[0][1] == "/api/providers"
    assert "sk-live-should-strip" in str(seen[0][2])
    assert row["id"] == "conn-1"
    assert "apiKey" not in row
    assert "api_key" not in row


def test_start_oauth_rejects_dashboard_authorization_url():
    def fetch(method, path, headers, json_body):
        return {"authorization_url": "http://9router:20128/dashboard/providers"}

    client = NineRouterConnectClient(fetch=fetch)
    with pytest.raises(NineRouterConnectError, match="dashboard"):
        client.start_oauth("codex", "http://odysseus/api/ninerouter/connections/oauth/callback")


def test_start_oauth_returns_idp_url():
    def fetch(method, path, headers, json_body):
        return {"authorization_url": "https://auth.openai.com/authorize?client_id=x"}

    client = NineRouterConnectClient(fetch=fetch)
    out = client.start_oauth("codex", "http://odysseus/callback")
    assert out["authorization_url"].startswith("https://auth.openai.com/")


def test_complete_oauth_forwards_code_not_tokens_and_redacts():
    seen = []

    def fetch(method, path, headers, json_body):
        seen.append(json_body)
        return {"id": "conn-2", "access_token": "tok", "refresh_token": "rt", "status": "usable"}

    client = NineRouterConnectClient(fetch=fetch)
    row = client.complete_oauth("codex", "auth-code-1", state="s")
    assert seen[0]["code"] == "auth-code-1"
    assert "access_token" not in row
    assert row["id"] == "conn-2"


def test_connect_client_cannot_fetch_completions():
    client = NineRouterConnectClient(fetch=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")))
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client._request("POST", "/v1/chat/completions", json_body={"model": "x"})
```

If Task 1 locked a different POST path than `/api/providers`, change the assertion to that path. Request JSON key names (`apiKey` vs `api_key`) must match the probe.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_ninerouter_connect_client.py --tb=short
```

Expected: FAIL (`NineRouterConnectClient` missing).

- [ ] **Step 3: Implement the client**

Append to `services/ninerouter/connect.py` (keep allowlist functions). Use `httpx` like `NineRouterMetadataClient`. Inject `fetch(method, path, headers, json_body)` for tests.

```python
from typing import Any, Callable, Optional

import httpx

from services.ninerouter.metadata import (
    NineRouterMetadataError,
    metadata_token,
    ninerouter_metadata_url,
    redact_providers,
    _redact_mapping,
)


class NineRouterConnectError(NineRouterMetadataError):
    """Connect call failed or returned a dashboard URL."""


class NineRouterConnectClient:
    """POST/GET/DELETE 9router connect API; never inference."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        fetch: Callable[[str, str, dict[str, str], Optional[dict]], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or ninerouter_metadata_url()).rstrip("/")
        self.token = token if token is not None else metadata_token()
        self._fetch = fetch

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, json_body: dict | None = None) -> Any:
        cleaned = normalize_connect_path(method, path)
        if self._fetch is not None:
            return self._fetch(method.upper(), cleaned, self._headers(), json_body)
        try:
            response = httpx.request(
                method.upper(),
                f"{self.base_url}{cleaned}",
                headers=self._headers(),
                json=json_body,
                timeout=15.0,
            )
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        except NineRouterMetadataError:
            raise
        except Exception as exc:
            raise NineRouterConnectError(f"9router connect request failed: {exc}") from exc

    def _one_row(self, payload: Any) -> dict[str, Any]:
        rows = redact_providers(payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else [])
        if not rows and isinstance(payload, dict):
            cleaned = _redact_mapping(payload)
            return cleaned if isinstance(cleaned, dict) else {}
        return rows[0] if rows else {}

    def _reject_dashboard_url(self, url: str) -> str:
        raw = (url or "").strip()
        parsed = urlparse(raw)
        if not raw or "/dashboard" in (parsed.path or "") or (parsed.hostname or "") in {"9router", "localhost"}:
            raise NineRouterConnectError("authorization_url must be an upstream IdP, not 9router dashboard")
        return raw

    def list_providers(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/providers")
        return redact_providers(payload)

    def create_api_key(self, provider: str, api_key: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/api/providers",
            {"provider": provider, "apiKey": api_key},
        )
        return self._one_row(payload)

    def start_oauth(self, provider: str, redirect_uri: str) -> dict[str, str]:
        payload = self._request(
            "GET",
            f"/api/oauth/{provider}/authorize?redirect_uri={redirect_uri}",
        )
        if not isinstance(payload, dict):
            raise NineRouterConnectError("oauth start did not return JSON")
        url = payload.get("authorization_url") or payload.get("url") or payload.get("redirect_url") or ""
        return {"authorization_url": self._reject_dashboard_url(str(url))}

    def complete_oauth(self, provider: str, code: str, state: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"code": code}
        if state:
            body["state"] = state
        payload = self._request("POST", f"/api/oauth/{provider}/callback", body)
        return self._one_row(payload)

    def delete_connection(self, connection_id: str) -> None:
        cid = (connection_id or "").strip()
        if not cid:
            raise NineRouterConnectError("connection_id is required")
        self._request("DELETE", f"/api/providers/{cid}")
```

Wire GET-with-query: `normalize_connect_path` uses `urlparse` and drops query; pass path without requiring query in the allowlist. If `httpx.get` is needed for authorize, `_request` already uses `httpx.request`.

If probe JSON uses different field names, change only the body/path here and in tests.

- [ ] **Step 4: Run tests to verify they pass**

Same pytest command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/ninerouter/connect.py tests/test_ninerouter_connect_client.py
git commit -m "feat: add in-stack 9router connect client"
```

---

### Task 3: Odysseus BFF routes (no ModelEndpoint keys)

**Files:**
- Create: `routes/ninerouter_connection_routes.py`
- Create: `tests/test_ninerouter_connection_routes.py`
- Modify: `app.py` (include router next to `setup_chatgpt_subscription_routes`)

**Interfaces:**
- Consumes: `NineRouterConnectClient`, `chatgpt_subscription.provision_connection`, `get_current_user`
- Produces FastAPI routes:
  - `GET /api/ninerouter/connections` → `{ "ok": bool, "providers": list[dict], "error": str | None }`
  - `POST /api/ninerouter/connections` form `provider`, `api_key` → projection; never writes `ModelEndpoint`
  - `POST /api/ninerouter/connections/oauth/start` form `provider` → `{ "authorization_url", "poll_id"? }`
  - `GET /api/ninerouter/connections/oauth/callback` query `code`, `state`, `error`, `connection_id` (ignore tokens)
  - `DELETE /api/ninerouter/connections/{connection_id}`

When 9router is down, GET catalog returns `ok: false`, empty `providers`, Connect disabled by UI in Task 5.

- [ ] **Step 1: Write failing route tests**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint, ProviderAuthSession
import routes.ninerouter_connection_routes as ncr
import src.chatgpt_subscription as chatgpt_subscription


def _app(monkeypatch, connect):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(
        chatgpt_subscription,
        "_database_handles",
        lambda: (ProviderAuthSession, TestSessionLocal, lambda: None),
    )
    monkeypatch.setattr(ncr, "get_current_user", lambda _request: "alice")
    monkeypatch.setattr(ncr, "NineRouterConnectClient", lambda: connect)
    app = FastAPI()
    app.include_router(ncr.setup_ninerouter_connection_routes())
    return app, TestSessionLocal


class FakeConnect:
    def __init__(self, **hooks):
        self.hooks = hooks

    def list_providers(self):
        return self.hooks.get("list", [{"id": "conn-1", "name": "OpenAI", "status": "usable"}])

    def create_api_key(self, provider, api_key):
        assert api_key == "sk-once"
        return {"id": "conn-new", "status": "usable", "name": provider}

    def start_oauth(self, provider, redirect_uri):
        assert "oauth/callback" in redirect_uri
        return {"authorization_url": "https://auth.openai.com/authorize?x=1"}

    def complete_oauth(self, provider, code, state=None):
        return {"id": "conn-oauth", "status": "usable", "entitlement": provider}

    def delete_connection(self, connection_id):
        self.hooks["deleted"] = connection_id


def test_catalog_redacted_when_9router_ok(monkeypatch):
    app, _ = _app(monkeypatch, FakeConnect())
    data = TestClient(app).get("/api/ninerouter/connections").json()
    assert data["ok"] is True
    assert data["providers"][0]["id"] == "conn-1"


def test_catalog_unhealthy_when_9router_down(monkeypatch):
    class Down(FakeConnect):
        def list_providers(self):
            raise ncr.NineRouterConnectError("down")

    app, _ = _app(monkeypatch, Down())
    data = TestClient(app).get("/api/ninerouter/connections").json()
    assert data["ok"] is False
    assert data["providers"] == []


def test_api_key_connect_stores_projection_not_key(monkeypatch):
    app, Session = _app(monkeypatch, FakeConnect())
    res = TestClient(app).post(
        "/api/ninerouter/connections",
        data={"provider": "openai", "api_key": "sk-once"},
    )
    body = res.json()
    assert res.status_code == 200
    assert body["connection_id"] == "conn-new"
    assert "api_key" not in body
    db = Session()
    try:
        assert db.query(ModelEndpoint).count() == 0
        auth = db.query(ProviderAuthSession).one()
        assert auth.connection_id == "conn-new"
        assert auth.access_token is None
        assert auth.owner == "alice"
    finally:
        db.close()


def test_oauth_callback_discards_token_query_params(monkeypatch):
    app, Session = _app(monkeypatch, FakeConnect())
    res = TestClient(app).get(
        "/api/ninerouter/connections/oauth/callback",
        params={
            "code": "abc",
            "provider": "codex",
            "access_token": "sk-leak",
            "refresh_token": "rt-leak",
        },
    )
    assert res.status_code == 200
    assert "sk-leak" not in res.text
    db = Session()
    try:
        auth = db.query(ProviderAuthSession).one()
        assert auth.connection_id == "conn-oauth"
        assert auth.access_token is None
    finally:
        db.close()


def test_oauth_start_returns_idp_not_dashboard(monkeypatch):
    app, _ = _app(monkeypatch, FakeConnect())
    data = TestClient(app).post(
        "/api/ninerouter/connections/oauth/start",
        data={"provider": "codex"},
    ).json()
    assert "auth.openai.com" in data["authorization_url"]
    assert "/dashboard/providers" not in data["authorization_url"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_ninerouter_connection_routes.py --tb=short
```

Expected: FAIL (module missing).

- [ ] **Step 3: Implement routes and include them**

`routes/ninerouter_connection_routes.py` must:

- Use `Request` + `get_current_user` like `chatgpt_subscription_routes.py`.
- On API-key POST, call `create_api_key` then `chatgpt_subscription.provision_connection({"connection_id", "status", "entitlement", "label"}, owner)`.
- On callback, if query has `access_token` / `refresh_token` / `code_verifier`, log warning and ignore them (same as existing chatgpt callback).
- If `code` present, `complete_oauth` then provision. If 9router already returns `connection_id` without code, provision that id.
- Missing `connection_id` after exchange → HTTP 400, no DB write.
- Catalog: try `list_providers`; on `NineRouterConnectError` return `ok: False`.

In `app.py` next to chatgpt router:

```python
from routes.ninerouter_connection_routes import setup_ninerouter_connection_routes
app.include_router(setup_ninerouter_connection_routes())
```

- [ ] **Step 4: Run tests to verify they pass**

Same pytest. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/ninerouter_connection_routes.py tests/test_ninerouter_connection_routes.py app.py
git commit -m "feat: add Odysseus 9router connection BFF"
```

---

### Task 4: Retire ChatGPT dashboard PKCE hop

**Files:**
- Modify: `routes/chatgpt_subscription_routes.py`
- Modify: `tests/test_provider_connection_projection.py`
- Modify: `src/chatgpt_subscription.py` only if `ninerouter_public_url` becomes unused (leave the helper if still referenced)

**Interfaces:**
- Consumes: `NineRouterConnectClient.start_oauth`, existing poll → `provision_connection`
- Produces: `_start_device_flow` response `redirect_url` / `verification_uri` = IdP URL from connect client. `_NINEROUTER_PKCE_PATH` deleted.

- [ ] **Step 1: Rewrite the existing redirect test to the new contract**

In `tests/test_provider_connection_projection.py` replace `test_start_device_flow_redirects_to_9router_hosted_pkce` with:

```python
def test_start_device_flow_opens_upstream_idp_not_9router_dashboard(monkeypatch):
    monkeypatch.setattr(csr, "get_current_user", lambda _request: "alice")

    class Fake:
        def start_oauth(self, provider, redirect_uri):
            assert provider
            assert "callback" in redirect_uri
            return {"authorization_url": "https://auth.openai.com/authorize?client_id=x"}

    monkeypatch.setattr(csr, "NineRouterConnectClient", lambda: Fake())
    start = csr._start_device_flow(SimpleNamespace(), {})
    redirect = start.response.get("redirect_url") or start.response.get("verification_uri")
    assert redirect.startswith("https://auth.openai.com/")
    assert "/dashboard/providers" not in redirect
    assert start.pending["owner"] == "alice"
    assert "code_verifier" not in start.pending
```

Keep AE5 projection tests unchanged.

- [ ] **Step 2: Run the single test to verify it fails**

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_provider_connection_projection.py::test_start_device_flow_opens_upstream_idp_not_9router_dashboard --tb=short
```

Expected: FAIL (still `/dashboard/providers`).

- [ ] **Step 3: Change `_start_device_flow`**

Remove `_NINEROUTER_PKCE_PATH`. Import `NineRouterConnectClient`. Start OAuth with Odysseus callback URL built from the incoming `Request` `base_url` + `/api/ninerouter/connections/oauth/callback`. Put IdP URL in `redirect_url` and `verification_uri`. Poll can keep listing redacted providers via metadata GET.

- [ ] **Step 4: Run projection + chatgpt route tests**

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_provider_connection_projection.py tests/test_chatgpt_subscription_routes.py --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/chatgpt_subscription_routes.py tests/test_provider_connection_projection.py
git commit -m "feat: start ChatGPT connect at the IdP via stack 9router"
```

---

### Task 5: Settings connections panel

**Files:**
- Modify: `static/index.html` (Add API Models card ~2263–2340)
- Modify: `static/js/admin.js` (add-endpoint handler ~1092+)
- Modify: `tests/test_admin_device_flow_static.py`
- Create or modify a small static test that the card title and `/api/ninerouter/connections` appear and `/api/model-endpoints` POST is not used for 9router cloud connect

**Interfaces:**
- Consumes: `GET/POST/DELETE /api/ninerouter/connections*`, existing `runProviderDeviceFlow` for Copilot + ChatGPT
- Produces: panel `#adm-ninerouter-connections` listing rows; Connect; one-shot key field `#adm-nrApiKey` cleared after submit; no `#adm-epUrl` save for 9router-backed providers

Keep local/custom non-9router endpoints **unmodified** if they live elsewhere; this card is 9router-only per spec. Remove Base URL + persistent API key + proxy vs API(direct) from this card.

- [ ] **Step 1: Write failing static tests**

In `tests/test_admin_device_flow_static.py` (or new `tests/test_ninerouter_settings_static.py`):

```python
from pathlib import Path

_INDEX = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (Path(__file__).resolve().parent.parent / "static" / "js" / "admin.js").read_text(encoding="utf-8")


def test_settings_card_is_ninerouter_connections():
    assert "9router connections" in _INDEX
    assert "Add API Models" not in _INDEX
    assert 'id="adm-ninerouter-connections"' in _INDEX
    assert 'id="adm-nrApiKey"' in _INDEX


def test_admin_loads_ninerouter_catalog_not_model_endpoint_keys():
    assert "/api/ninerouter/connections" in _ADMIN
    assert "adm-ninerouter-connections" in _ADMIN
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_ninerouter_settings_static.py --tb=short
```

Expected: FAIL.

- [ ] **Step 3: Replace the card HTML**

Replace the Add API Models `admin-card` with a card titled `9router connections`, empty list `#adm-ninerouter-connections`, provider select of 9router-backed names (openai, anthropic, groq, chatgpt-subscription, copilot, …), `#adm-nrApiKey` password input, Connect button `#adm-nrConnectBtn`, status `#adm-nrMsg`. Do not include Base URL. Do not include proxy/direct menu.

- [ ] **Step 4: Wire `admin.js`**

On Settings open: `GET /api/ninerouter/connections`. If `ok` is false, show unhealthy and disable Connect. Render each provider row (label, opaque id, status). Connect:

- `chatgpt-subscription` / `copilot`: existing `runProviderDeviceFlow`.
- others: `POST /api/ninerouter/connections` with `provider` + `api_key`, then clear `#adm-nrApiKey`.

Do not `POST /api/model-endpoints` from this card.

- [ ] **Step 5: Run static tests**

Expected: PASS. Also run `tests/test_admin_device_flow_static.py` and fix copy assertions that still require the old ChatGPT option markup if you moved it.

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/js/admin.js tests/test_ninerouter_settings_static.py tests/test_admin_device_flow_static.py
git commit -m "feat: pipe Settings providers through stack 9router"
```

---

### Task 6: Device-flow JS must not open dashboard URLs

**Files:**
- Modify: `tests/test_provider_device_flow_js.py`
- Modify: `static/js/providerDeviceFlow.js` only if a guard is needed (prefer server-side Task 4)

**Interfaces:**
- Consumes: start payload `redirect_url` / `verification_uri`
- Produces: `openWindow` called with IdP URL

- [ ] **Step 1: Change the ChatGPT JS test**

Replace the fixture URL `http://9router.example:20128/dashboard/providers` with `https://auth.openai.com/authorize?client_id=x`. Assert `opened` is that IdP URL.

- [ ] **Step 2: Run the JS tests**

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml exec -T odysseus \
  python -m pytest -q tests/test_provider_device_flow_js.py --tb=short
```

Expected: FAIL until fixture+implementation agree; then PASS.

- [ ] **Step 3: Optional guard**

If you add a client guard, reject `authUrl` containing `/dashboard/providers` and surface `formatDeviceFlowError`. Prefer relying on Task 2 `_reject_dashboard_url`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_provider_device_flow_js.py static/js/providerDeviceFlow.js
git commit -m "test: ChatGPT device flow opens IdP not 9router dashboard"
```

---

### Task 7: Live connect on the guest overlay

**Files:** none required unless probe notes go in `docs/operations/linux-vm-openhands-overlay.md` (Task 8).

**Interfaces:** running Odysseus `:7000` + unpublished 9router. Operator uses Odysseus Settings in a browser tunneled to the guest, or `curl` to Odysseus (not to 9router’s published port).

- [ ] **Step 1: Rebuild Odysseus on the guest from this checkout**

```bash
ssh orchestration-vm
cd /home/agent/work/odysseus
export PATH="$HOME/.local/bin:$PATH"
docker compose -f docker-compose.yml -f docker-compose.openhands.yml build odysseus
docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait --pull never odysseus
python3 scripts/openhands_probe.py stack --json
```

Expected: probe pass.

- [ ] **Step 2: Catalog through Odysseus, not 9router dashboard**

```bash
curl -fsS -c /tmp/ody.ck -b /tmp/ody.ck http://127.0.0.1:7000/api/ninerouter/connections
```

(Use the guest’s real auth: login cookie or whatever `AUTH_ENABLED=true` requires. Do not call `http://127.0.0.1:20128`.)

Expected: JSON `ok` true or false with empty providers if none connected. Never an HTML dashboard.

- [ ] **Step 3: One real connect via Odysseus Settings**

Open Odysseus Settings (SSH tunnel to guest `7000` if needed). Connect one provider (API key or ChatGPT). Confirm:

- Settings row `usable`
- Odysseus DB projection has `connection_id` and null tokens (compose exec sqlite/inspect only if you already have a safe admin path; do not dump 9router DATA_DIR secrets into git)
- 9router has the credential; Odysseus response bodies do not include the key

- [ ] **Step 4: Token-successful completion probe**

Re-run the existing live native/OpenCode/Hermes overlay probes that previously failed closed on missing credentials. Expected: completion succeeds through stack 9router.

Stop if you had to publish 9router or use a host-wide 9router to make this pass.

- [ ] **Step 5: Commit only if docs or scripts changed**; otherwise note evidence in the Task 8 ops doc.

---

### Task 8: Ops doc + package/redeploy confirmation

**Files:**
- Modify: `docs/operations/linux-vm-openhands-overlay.md`

- [ ] **Step 1: Update the “After health” section**

Replace “Connect providers inside overlay 9router on the guest” with: connect from Odysseus Settings on this board; stack 9router stays unpublished; develop on `/home/agent/work/odysseus`; after the feature works, rebuild `odysseus` and `up -d --wait --pull never` to confirm the packaged image.

- [ ] **Step 2: Redeploy confirmation**

From the guest checkout that contains the working feature:

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml build odysseus
docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait --pull never
python3 scripts/openhands_probe.py stack --json
curl -fsS http://127.0.0.1:7000/api/ninerouter/connections
```

Expected: same catalog contract after rebuild.

- [ ] **Step 3: Commit**

```bash
git add docs/operations/linux-vm-openhands-overlay.md
git commit -m "docs: connect overlay providers from Odysseus Settings"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| Odysseus chrome, stack 9router secrets, opaque projection | 2, 3 |
| Develop on Linux VM overlay; package/redeploy later | 1, 7, 8 |
| No host-wide 9router; unpublished | 1, 7 |
| API key POST-once; OAuth IdP; discard tokens | 2, 3, 4 |
| Separate connect client; GET metadata unchanged | 1, 2 |
| Sessionless API missing → stop | 1 |
| Settings catalog/connect/disconnect | 5 |
| ChatGPT start not `/dashboard/providers` | 4, 6 |
| Fail closed catalog/connect | 3, 5, 7 |
| Fake tests + live guest tests | 2–7 |
| Local/custom non-9router endpoints deferred | 5 (card is 9router-only) |
