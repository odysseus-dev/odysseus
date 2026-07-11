"""Regression tests for explicit email-account ownership in scheduled tasks."""

import pytest


@pytest.mark.asyncio
async def test_email_task_rejects_foreign_explicit_account(monkeypatch):
	from routes import email_pollers

	imap_calls = []

	monkeypatch.setattr(
		email_pollers,
		"_load_settings",
		lambda: {"email_auto_summarize": True},
	)
	monkeypatch.setattr(
		email_pollers,
		"_owner_for_email_account",
		lambda account_id: "bob",
	)

	def fake_imap_connect(account_id=None, owner=""):
		imap_calls.append((account_id, owner))
		raise AssertionError(
			"IMAP must not be opened for a foreign account"
		)

	monkeypatch.setattr(
		email_pollers,
		"_imap_connect",
		fake_imap_connect,
	)

	with pytest.raises(
		PermissionError,
		match="not available to this task owner",
	):
		await email_pollers._auto_summarize_pass_single(
			account_id="bob-account",
			owner="alice",
		)

	assert imap_calls == []
