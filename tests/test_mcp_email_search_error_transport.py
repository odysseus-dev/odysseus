import asyncio

from mcp_servers import email_server


def test_search_exception_uses_mcp_error_transport_prefix(monkeypatch):
    monkeypatch.setattr(email_server, "_read_accounts_from_db", lambda: [])
    monkeypatch.setattr(email_server, "_search_emails", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline")))

    result = asyncio.run(email_server.call_tool("search_emails", {"query": "fixture"}))

    assert len(result) == 1
    assert result[0].text == "Error: Search failed: offline"
