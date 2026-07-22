"""Regression coverage for passwordless Google OAuth reminder senders."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from routes.note_routes import dispatch_reminder


_REPO = Path(__file__).resolve().parents[1]


def test_dispatch_reminder_sends_with_google_oauth_without_smtp_password():
    cfg = {
        "account_name": "Workspace",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": "alice@example.edu",
        "smtp_password": "",
        "from_address": "alice@example.edu",
        "oauth_provider": "google",
    }
    sent = []

    def fake_send(actual_cfg, sender, recipients, message):
        sent.append((actual_cfg, sender, recipients, message))

    with (
        patch("src.settings.load_settings", return_value={}),
        patch("routes.email_routes._get_email_config", return_value=cfg),
        patch("routes.email_helpers._send_smtp_message", side_effect=fake_send),
        patch("core.database.SessionLocal", side_effect=AssertionError("fallback lookup is not needed")),
    ):
        result = asyncio.run(dispatch_reminder(
            "Reminder: Submit report",
            "The report is due today.",
            note_id="",
            owner="alice@example.edu",
            queue_browser=False,
            settings_override={
                "reminder_channel": "email",
                "reminder_llm_synthesis": False,
            },
        ))

    assert result["email_sent"] is True
    assert result["email_error"] == ""
    assert len(sent) == 1
    actual_cfg, sender, recipients, message = sent[0]
    assert actual_cfg is cfg
    assert sender == "alice@example.edu"
    assert recipients == ["alice@example.edu"]
    assert "Subject: Reminder (Odysseus): Submit report" in message


def test_reminder_settings_offer_oauth_smtp_accounts():
    source = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    helper = source[source.index("const smtpAccountReady"):source.index("const smtpAccountReady") + 260]

    assert "account.has_smtp_password || account.oauth_provider === 'google'" in helper
    assert source.count(".filter(smtpAccountReady)") == 2
