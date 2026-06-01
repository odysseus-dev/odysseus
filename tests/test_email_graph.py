"""Tests for the Microsoft Graph email backend (src/email_graph.py).

Covers the Graph counterpart to the IMAP mail path:

- folder-name → Graph well-known id mapping
- address / date parsing helpers
- list & read dict shaping — must match the IMAP sync helpers' keys so the
  route handlers and frontend stay backend-agnostic
- query building for list (filters, $count) and the write ops
  (sendMail base64 MIME, move, delete, attachment fetch)
- GraphClient's 401 → force-refresh → retry behaviour

Graph operations are exercised against a recording fake client, so there's no
network and no FastAPI boot. `src.email_graph` only imports stdlib at module
load (the routes.email_helpers import is lazy), so these run standalone.
"""

import base64

import pytest

from src.email_graph import (
    GraphBackend,
    GraphClient,
    GraphError,
    _addr_pair,
    _join_addrs,
    _parse_dt,
)


class FakeClient:
    """Records Graph requests and replays canned responses."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        if self._responses:
            return self._responses.pop(0)
        return {}


def _backend(responses=None):
    gb = GraphBackend({"account_id": "acct-1"}, owner="user@x.com")
    gb.client = FakeClient(responses)
    return gb


# ── pure helpers ─────────────────────────────────────────────────

def test_folder_id_maps_well_known_names():
    gb = _backend()
    assert gb._folder_id("INBOX") == "inbox"
    assert gb._folder_id("Sent") == "sentitems"
    assert gb._folder_id("Sent Items") == "sentitems"
    assert gb._folder_id("Drafts") == "drafts"
    assert gb._folder_id("Trash") == "deleteditems"
    assert gb._folder_id("Bin") == "deleteditems"
    assert gb._folder_id("Spam") == "junkemail"
    assert gb._folder_id("Junk") == "junkemail"
    assert gb._folder_id("Archive") == "archive"


def test_addr_helpers():
    assert _addr_pair({"emailAddress": {"name": "Jane", "address": "j@x.com"}}) == ("Jane", "j@x.com")
    assert _addr_pair(None) == ("", "")
    joined = _join_addrs([
        {"emailAddress": {"address": "a@x.com"}},
        {"emailAddress": {"name": "B", "address": "b@x.com"}},
    ])
    assert joined == "a@x.com, B <b@x.com>"


def test_parse_dt_handles_graph_zulu():
    iso, disp, epoch = _parse_dt("2026-06-01T12:30:00Z")
    assert iso.startswith("2026-06-01T12:30:00")
    assert epoch > 0
    assert _parse_dt("") == ("", "", 0.0)


# ── list / read shaping ──────────────────────────────────────────

_LIST_KEYS = {
    "uid", "message_id", "subject", "from_name", "from_address", "to", "cc",
    "date", "date_display", "date_epoch", "size", "is_read", "is_answered",
    "is_flagged", "flags", "has_attachments", "tags", "is_spam_verdict",
}


def _sample_message():
    return {
        "id": "AAA",
        "internetMessageId": "<m1@x>",
        "subject": "Hi",
        "from": {"emailAddress": {"name": "Jane", "address": "j@x.com"}},
        "toRecipients": [{"emailAddress": {"address": "me@x.com"}}],
        "ccRecipients": [],
        "receivedDateTime": "2026-06-01T12:30:00Z",
        "isRead": False,
        "hasAttachments": True,
        "flag": {"flagStatus": "flagged"},
    }


def test_to_list_dict_matches_imap_shape():
    gb = _backend()
    d = gb._to_list_dict(_sample_message())
    assert set(d) == _LIST_KEYS
    assert d["uid"] == "AAA"
    assert d["message_id"] == "<m1@x>"
    assert d["from_name"] == "Jane"
    assert d["from_address"] == "j@x.com"
    assert d["is_read"] is False
    assert d["has_attachments"] is True
    assert d["is_flagged"] is True


def test_list_emails_builds_query_and_total():
    page = {"@odata.count": 7, "value": [_sample_message()]}
    gb = _backend([page])
    out = gb.list_emails("INBOX", limit=50, offset=0, filter_="unread", from_addr=None)
    assert out["total"] == 7
    assert out["folder"] == "INBOX"
    assert len(out["emails"]) == 1
    call = gb.client.calls[0]
    assert call["path"] == "/me/mailFolders/inbox/messages"
    assert call["params"]["$filter"] == "isRead eq false"
    assert call["count"] is True


def test_list_emails_filters_by_sender():
    gb = _backend([{"value": []}])
    gb.list_emails("INBOX", 50, 0, "all", from_addr="boss@x.com")
    assert "from/emailAddress/address eq 'boss@x.com'" in gb.client.calls[0]["params"]["$filter"]


def test_read_email_shapes_body_and_marks_seen():
    msg = dict(_sample_message(), body={"contentType": "html", "content": "<p>hi</p>"})
    gb = _backend([msg])  # GET message; hasAttachments triggers an attachments GET + a PATCH
    gb.client._responses.append({"value": []})   # attachments list
    gb.client._responses.append({})              # PATCH isRead
    out = gb.read_email("AAA", "INBOX", mark_seen=True)
    assert out["body_html"] == "<p>hi</p>"
    assert out["body"] == ""
    assert out["uid"] == "AAA"
    # mark-seen issued a PATCH because the message was unread
    assert any(c["method"] == "PATCH" for c in gb.client.calls)


# ── write ops ────────────────────────────────────────────────────

def test_send_mime_posts_base64_to_sendmail():
    gb = _backend([{}])
    gb.send_mime(b"From: a@x.com\r\nSubject: hi\r\n\r\nbody")
    call = gb.client.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/me/sendMail"
    assert call["content_type"] == "text/plain"
    assert base64.b64decode(call["data"]).startswith(b"From: a@x.com")


def test_move_resolves_destination_folder():
    gb = _backend([{}])
    gb.move("AAA", "Archive")
    call = gb.client.calls[0]
    assert call["path"] == "/me/messages/AAA/move"
    assert call["json"]["destinationId"] == "archive"


def test_delete_trash_vs_permanent():
    gb = _backend([{}, {}])
    gb.delete("AAA", permanent=False)
    gb.delete("BBB", permanent=True)
    assert gb.client.calls[0]["path"] == "/me/messages/AAA/move"
    assert gb.client.calls[0]["json"]["destinationId"] == "deleteditems"
    assert gb.client.calls[1]["method"] == "DELETE"
    assert gb.client.calls[1]["path"] == "/me/messages/BBB"


def test_get_attachment_decodes_content_bytes():
    listing = {"value": [{"id": "att1", "name": "f.txt", "contentType": "text/plain", "size": 3}]}
    full = {"contentBytes": base64.b64encode(b"abc").decode()}
    gb = _backend([listing, full])
    name, ctype, blob = gb.get_attachment("AAA", 0)
    assert name == "f.txt"
    assert ctype == "text/plain"
    assert blob == b"abc"


def test_get_attachment_out_of_range_raises():
    gb = _backend([{"value": []}])
    with pytest.raises(GraphError):
        gb.get_attachment("AAA", 0)


# ── GraphClient 401 retry ────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, content=b"{}", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload


def test_request_refreshes_token_once_on_401(monkeypatch):
    client = GraphClient("acct-1", owner="u")
    tokens = iter(["stale-token", "fresh-token"])
    used = []
    monkeypatch.setattr(client, "_token", lambda force_refresh=False: next(tokens))

    seq = [_Resp(401), _Resp(200, payload={"ok": True})]

    def fake_request(method, url, headers=None, **kwargs):
        used.append(headers["Authorization"])
        return seq.pop(0)

    import httpx
    monkeypatch.setattr(httpx, "request", fake_request)

    out = client.request("GET", "/me/messages")
    assert out == {"ok": True}
    # first attempt with stale token, retried with the refreshed one
    assert used == ["Bearer stale-token", "Bearer fresh-token"]


def test_request_raises_on_persistent_error(monkeypatch):
    client = GraphClient("acct-1", owner="u")
    monkeypatch.setattr(client, "_token", lambda force_refresh=False: "tok")

    import httpx
    monkeypatch.setattr(httpx, "request", lambda *a, **k: _Resp(500, content=b"boom"))
    with pytest.raises(GraphError):
        client.request("GET", "/me/messages")
