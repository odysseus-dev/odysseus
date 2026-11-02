from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_read_email_tool_links_include_mailbox_identity_in_live_and_saved_renderers():
    saved = (ROOT / "static/js/chatRenderer.js").read_text()
    live = (ROOT / "static/js/chat.js").read_text()

    assert "function _emailTarget" in saved
    assert "[clean(folder), uid, clean(account)]" in saved
    assert "agent-thread-summary agent-thread-summary-link" in saved
    assert "function _emailToolHash" in live
    assert "[clean(folder), uid, clean(account)]" in live
    assert "agent-thread-summary agent-thread-summary-link" in live


def test_email_deeplink_opens_exact_folder_uid_and_optional_account():
    source = (ROOT / "static/js/chatRenderer.js").read_text()

    assert "const parts = String(id || '').split(':')" in source
    assert "const opts = { folder: parts[0] || 'INBOX', uid: parts[1] }" in source
    assert "if (account) opts.account_id = account" in source
    assert "open(opts)" in source
