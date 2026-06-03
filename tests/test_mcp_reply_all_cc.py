"""Reply-all Cc must exclude the user's own address and dedup case-insensitively.

The MCP reply-all Cc builder only excluded the original sender, and did so
case-sensitively, never excluding the mailbox owner — so reply-all CC'd the
user themselves and kept duplicate / different-case addresses across To+Cc.
The frontend reply-all builder was hardened for this; the MCP path was not.
"""
import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


def test_excludes_owner_and_sender_and_dedups():
    out = es._reply_all_cc(
        to_header="me@myco.com, Bob <bob@y.com>",
        cc_header="Bob <BOB@y.com>, Carol <carol@z.com>",
        sender_addr="alice@x.com",
        own_addrs=["me@myco.com", "me@myco.com"],
    )
    assert out == ["bob@y.com", "carol@z.com"]


def test_excludes_sender_case_insensitively():
    out = es._reply_all_cc(
        to_header="ALICE@x.com, Bob <bob@y.com>",
        cc_header="",
        sender_addr="alice@x.com",
        own_addrs=[],
    )
    assert out == ["bob@y.com"]


def test_no_other_recipients_yields_empty():
    out = es._reply_all_cc("me@myco.com", "", "alice@x.com", ["me@myco.com"])
    assert out == []
