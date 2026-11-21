"""Strict-server regressions for UID STORE on flag and delete/move paths."""
import imaplib

import pytest

from routes.email import email_helpers, email_routes
from tests.helpers.strict_imap_store import StrictImapStore


@pytest.fixture
def mailbox():
    server = StrictImapStore().start()
    try:
        yield server
    finally:
        server.stop()


def connect(mailbox):
    client = imaplib.IMAP4(*mailbox.server_address, timeout=5)
    client.login('fixture', 'fixture')
    client.select('INBOX')
    return client


@pytest.mark.parametrize('flag', [r'\Seen', r'\Flagged', r'\Deleted'])
@pytest.mark.parametrize('add', [True, False])
def test_uid_store_serializes_a_flag_list_and_changes_the_requested_flag(mailbox, flag, add):
    if not add:
        mailbox.flags.add(flag.encode())
    with connect(mailbox) as client:
        assert email_routes._store_email_flag(client, '89773', flag, add=add)
    assert (flag.encode() in mailbox.flags) == add
    stores = [line for line in mailbox.commands if line.startswith(b'UID STORE')]
    assert len(stores) == 1
    assert stores[0].endswith(b'('+flag.encode()+b')')


@pytest.mark.parametrize('move', ['move-fallback', 'copy-delete', 'background-move'])
def test_copy_delete_paths_can_expunge_on_a_strict_server(mailbox, monkeypatch, move):
    if move == 'background-move':
        monkeypatch.setattr(email_helpers, '_imap_connect', lambda *args, **kwargs: connect(mailbox))
        assert email_helpers._imap_move('89773', 'Trash', owner='alice')
    else:
        with connect(mailbox) as client:
            action = email_routes._move_email_message if move == 'move-fallback' else email_routes._copy_and_delete_email_message
            assert action(client, '89773', 'Trash', role='trash')
    assert mailbox.copies
    assert mailbox.present is False


@pytest.mark.parametrize('bulk', [False, True])
@pytest.mark.parametrize('flag', [r'\Seen', r'\Flagged', r'\Answered', r'\Deleted'])
@pytest.mark.parametrize('add', [True, False])
def test_mcp_flag_paths_use_the_same_wire_contract(mailbox, monkeypatch, bulk, flag, add):
    from mcp_servers import email_server
    monkeypatch.setattr(email_server, '_fixture_email_enabled', lambda: False)
    monkeypatch.setattr(email_server, '_imap_connect', lambda *args, **kwargs: connect(mailbox))
    if not add:
        mailbox.flags.add(flag.encode())
    if bulk:
        assert email_server._bulk_set_flag(['89773'], 'INBOX', flag, add=add) == 1
    else:
        assert email_server._set_flag('89773', 'INBOX', flag, add=add)
    assert (flag.encode() in mailbox.flags) == add


@pytest.mark.parametrize('bulk', [False, True])
def test_mcp_move_fallbacks_remove_the_source_message(mailbox, monkeypatch, bulk):
    from mcp_servers import email_server
    monkeypatch.setattr(email_server, '_fixture_email_enabled', lambda: False)
    monkeypatch.setattr(email_server, '_imap_connect', lambda *args, **kwargs: connect(mailbox))
    monkeypatch.setattr(email_server, '_email_index_delete_uids', lambda *args: None)
    if bulk:
        assert email_server._bulk_move(['89773'], 'INBOX', 'Trash', role='trash') == 1
    else:
        assert email_server._move_message('89773', 'INBOX', 'Trash', role='trash')
    assert mailbox.present is False
