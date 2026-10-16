"""#4908 — Google CalDAV/CardDAV reject app passwords, so Google-linked
accounts authenticate with the linked email account's OAuth bearer token."""
import types

import httpx

import core.database as database
import routes.email_helpers as email_helpers
from routes.calendar_routes import FALLBACK_OWNER
from src import caldav_sync


class _FakeDb:
    def __init__(self, row):
        self.row = row

    def get(self, _model, _id):
        return self.row

    def close(self):
        pass


def _row(owner="", provider="google"):
    return types.SimpleNamespace(owner=owner, oauth_provider=provider, imap_user="me@gmail.com",
                                 from_address="", oauth_access_token="enc", oauth_token_expiry="0")


def _token_for(monkeypatch, row, owner):
    monkeypatch.setattr(database, "SessionLocal", lambda: _FakeDb(row))
    monkeypatch.setattr(email_helpers, "_get_valid_google_token", lambda _id, _cfg: "tok")
    return caldav_sync._google_access_token("acc1", owner)


def test_google_token_requires_owned_google_account(monkeypatch):
    assert _token_for(monkeypatch, _row(owner="alice"), "alice") == "tok"
    assert _token_for(monkeypatch, _row(owner="alice"), "bob") == ""
    assert _token_for(monkeypatch, _row(provider=""), "alice") == ""
    # Single-user: calendar uses FALLBACK_OWNER, email rows have no owner.
    assert _token_for(monkeypatch, _row(owner=""), FALLBACK_OWNER) == "tok"


def test_caldav_credentials_uses_bearer_only_for_google_links(monkeypatch):
    monkeypatch.setattr(caldav_sync, "_google_access_token", lambda gid, owner: f"tok-{gid}")
    acc = {"url": " https://x/ ", "username": "me", "google_account_id": "g1"}
    assert caldav_sync.caldav_credentials(acc, "alice") == ("https://x/", "me", "tok-g1", "bearer")
    plain = {"url": "https://x/", "username": "me", "password": ""}
    assert caldav_sync.caldav_credentials(plain, "alice")[3] is None


def test_carddav_auth_uses_bearer_only_for_google_links(monkeypatch):
    import routes.contacts.contacts_routes as contacts
    monkeypatch.setattr(email_helpers, "google_oauth_token", lambda gid, owner: f"tok-{gid}")
    monkeypatch.setattr(email_helpers, "gmail_app_password", lambda gid, owner: None)
    auth = contacts._carddav_auth({"google_account_id": "g1", "username": "me", "password": ""})
    assert auth(httpx.Request("GET", "https://x")).headers["Authorization"] == "Bearer tok-g1"
    assert contacts._carddav_auth({"google_account_id": "", "username": "u", "password": "p"}) == ("u", "p")


def test_carddav_report_retries_with_fn_filter_when_empty(monkeypatch):
    import routes.contacts.contacts_routes as contacts
    empty = '<d:multistatus xmlns:d="DAV:"/>'
    card = ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav"><d:response>'
            '<d:href>/ab/1.vcf</d:href><d:propstat><d:prop><c:address-data>'
            'BEGIN:VCARD\nVERSION:3.0\nUID:1\nFN:Ada\nEND:VCARD</c:address-data></d:prop></d:propstat>'
            '</d:response></d:multistatus>')
    sent = []

    def fake_request(method, url, content=b"", **_kw):
        sent.append(content.decode())
        return httpx.Response(207, text=card if "prop-filter" in sent[-1] else empty)

    monkeypatch.setattr(contacts.httpx, "request", fake_request)
    out = contacts._fetch_via_report({"url": "https://x/ab"}, None)
    assert [c["name"] for c in out] == ["Ada"] and len(sent) == 2


# Gmail set up with an app password: the legacy CalDAV endpoint accepts it, so
# a CalDAV link reuses the mailbox credentials instead of asking again.

def _gmail_row(owner="", provider="", host="imap.gmail.com", password="enc-pw"):
    return types.SimpleNamespace(owner=owner, oauth_provider=provider, imap_host=host,
                                 imap_user="me@gmail.com", from_address="", imap_password=password)


def _app_password_for(monkeypatch, row, owner):
    monkeypatch.setattr(database, "SessionLocal", lambda: _FakeDb(row))
    monkeypatch.setattr(email_helpers, "_decrypt", lambda v: "app-pw" if v else "")
    return email_helpers.gmail_app_password("acc1", owner)


def test_gmail_app_password_only_for_owned_gmail_password_accounts(monkeypatch):
    assert _app_password_for(monkeypatch, _gmail_row(owner="alice"), "alice") == ("me@gmail.com", "app-pw")
    assert _app_password_for(monkeypatch, _gmail_row(host=" IMAP.Gmail.com "), "") == ("me@gmail.com", "app-pw")
    # Another tenant's mailbox must never leak its decrypted password.
    assert _app_password_for(monkeypatch, _gmail_row(owner="alice"), "bob") is None
    # Google-OAuth accounts authenticate with their token, not a password.
    assert _app_password_for(monkeypatch, _gmail_row(provider="google"), "") is None
    # Non-Gmail mailboxes have no Google calendar behind them.
    assert _app_password_for(monkeypatch, _gmail_row(host="imap.fastmail.com"), "") is None
    assert _app_password_for(monkeypatch, _gmail_row(password=""), "") is None
    monkeypatch.setattr(database, "SessionLocal", lambda: _FakeDb(None))
    assert email_helpers.gmail_app_password("missing", "") is None


def test_caldav_credentials_prefers_linked_app_password(monkeypatch):
    seen = []

    def fake_app_password(gid, owner):
        seen.append(owner)
        return ("me@gmail.com", "app-pw") if gid == "gmail" else None

    monkeypatch.setattr(email_helpers, "gmail_app_password", fake_app_password)
    monkeypatch.setattr(caldav_sync, "_google_access_token", lambda gid, owner: f"tok-{gid}")
    # The stored URL is ignored: only the legacy endpoint accepts app passwords.
    acc = {"url": "https://apidata.googleusercontent.com/caldav/v2/x/user", "username": "x",
           "google_account_id": "gmail"}
    assert caldav_sync.caldav_credentials(acc, FALLBACK_OWNER) == (
        "https://www.google.com/calendar/dav/me@gmail.com/user", "me@gmail.com", "app-pw", None)
    # Single-user: calendar uses FALLBACK_OWNER, email rows have no owner.
    assert seen == [""]
    oauth = {"url": "https://x/", "username": "me", "google_account_id": "oauth"}
    assert caldav_sync.caldav_credentials(oauth, "alice") == ("https://x/", "me", "tok-oauth", "bearer")


def _calendar_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.calendar_routes as calendar_routes

    monkeypatch.setattr(calendar_routes, "_require_user", lambda _r: "alice")
    monkeypatch.setattr(calendar_routes, "require_user", lambda _r: "alice")
    monkeypatch.setattr(email_helpers, "google_oauth_email", lambda gid, owner: "")
    monkeypatch.setattr(email_helpers, "gmail_app_password",
                        lambda gid, owner: ("me@gmail.com", "app-pw") if (gid, owner) == ("g1", "alice") else None)
    app = FastAPI()
    app.include_router(calendar_routes.setup_calendar_routes())
    return TestClient(app)


def test_link_gmail_app_password_account_saves_no_password(monkeypatch):
    import routes.prefs_routes as prefs_routes
    saved = {}
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: {})
    monkeypatch.setattr(prefs_routes, "_save_for_user", lambda owner, prefs: saved.update(owner=owner, prefs=prefs))
    monkeypatch.setattr(caldav_sync, "_load_caldav_accounts", lambda owner: [])
    client = _calendar_client(monkeypatch)

    r = client.post("/api/calendar/config/accounts", json={"label": "Personal", "google_account_id": "g1"})
    assert r.status_code == 200, r.text
    (acc,) = saved["prefs"]["caldav_accounts"]
    assert acc["url"] == "https://www.google.com/calendar/dav/me@gmail.com/user"
    assert acc["username"] == "me@gmail.com" and acc["google_account_id"] == "g1"
    # The secret stays on the email account; the link must not copy it.
    assert "password" not in acc

    r = client.post("/api/calendar/config/accounts", json={"google_account_id": "someone-elses"})
    assert r.status_code == 400


def test_connection_test_uses_basic_auth_for_app_password_link(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def request(self, method, url, **kw):
            captured.update(url=url, **kw)
            return httpx.Response(207)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(caldav_sync, "validate_caldav_url", lambda u: u)
    client = _calendar_client(monkeypatch)

    r = client.post("/api/calendar/test", json={"google_account_id": "g1"})
    assert r.json() == {"ok": True}
    assert captured["url"] == "https://www.google.com/calendar/dav/me@gmail.com/user"
    assert captured["auth"] == ("me@gmail.com", "app-pw")
    assert "Authorization" not in captured["headers"]


def test_carddav_auth_uses_linked_app_password(monkeypatch):
    import routes.contacts.contacts_routes as contacts
    monkeypatch.setattr(email_helpers, "gmail_app_password",
                        lambda gid, owner: ("me@gmail.com", "app-pw") if gid == "g1" else None)
    monkeypatch.setattr(email_helpers, "google_oauth_token", lambda gid, owner: "should-not-be-used")
    assert contacts._carddav_auth({"google_account_id": "g1", "username": "", "password": ""}) == (
        "me@gmail.com", "app-pw")


def test_link_contacts_to_gmail_app_password_account(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.contacts.contacts_routes as contacts
    import src.auth_helpers as auth_helpers

    saved = {}
    monkeypatch.setattr(contacts, "_load_settings", lambda: {"carddav_password": "old-enc"})
    monkeypatch.setattr(contacts, "_save_settings", lambda s: saved.update(s))
    monkeypatch.setattr(auth_helpers, "require_user", lambda _r: "alice")
    monkeypatch.setattr(email_helpers, "google_oauth_email", lambda gid, owner: "")
    monkeypatch.setattr(email_helpers, "gmail_app_password",
                        lambda gid, owner: ("me@gmail.com", "app-pw") if (gid, owner) == ("g1", "alice") else None)
    app = FastAPI()
    app.include_router(contacts.setup_contacts_routes())
    app.dependency_overrides[contacts.require_admin] = lambda: "alice"
    client = TestClient(app)

    r = client.put("/api/contacts/config", json={"carddav_google_account_id": "g1"})
    assert r.status_code == 200, r.text
    assert saved["carddav_url"] == "https://www.googleapis.com/carddav/v1/principals/me@gmail.com/lists/default"
    assert saved["carddav_username"] == "me@gmail.com"
    # The secret stays on the email account; any old CardDAV password is cleared.
    assert saved["carddav_password"] == ""

    r = client.put("/api/contacts/config", json={"carddav_google_account_id": "someone-elses"})
    assert r.status_code == 400
