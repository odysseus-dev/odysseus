import pytest

from mcp_servers import email_server


@pytest.mark.asyncio
@pytest.mark.parametrize('scope,expected', [
    ({'folder': 'Archive'}, ['Archive']),
    ({'folder': 'Finance/2026'}, ['Finance/2026']),
    ({'folders': ['INBOX', 'Sent']}, ['INBOX', 'Sent']),
    ({'folders': ['Sent'], 'folder': 'Archive'}, ['Sent']),
    ({}, None),
])
async def test_search_honors_advertised_folder(monkeypatch, scope, expected):
    calls = []
    monkeypatch.setattr(email_server, '_read_accounts_from_db', lambda: [])
    monkeypatch.setattr(email_server, '_mcp_owner_required', lambda accounts: False)

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return []

    monkeypatch.setattr(email_server, '_search_emails', search)
    await email_server.call_tool('search_emails', {
        'query': 'design review', 'account': 'fixture@example.com', **scope,
    })
    assert len(calls) == 1
    assert calls[0][1]['folders'] == expected
    assert calls[0][1]['account'] == 'fixture@example.com'
