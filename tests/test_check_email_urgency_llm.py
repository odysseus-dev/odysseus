"""Regression tests for issue #5506: the Email Tags task
(`check_email_urgency`) must actually call the configured LLM to triage
fresh unread mail — honouring `urgent_email_prompt` — instead of silently
short-circuiting to the keyword heuristic, and it must give reasoning models
enough token budget to emit their JSON verdict.
"""

from types import SimpleNamespace

import pytest


class _Column:
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return True


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _Db:
    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model
        self.closed = False

    def query(self, model):
        return _Query(self._rows_by_model.get(model, []))

    def close(self):
        self.closed = True


class _FakeImap:
    """Minimal IMAP stub yielding one fresh, unread inbox message."""

    def __init__(self):
        self.logged_out = False

    def select(self, *_args, **_kwargs):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"5"]
        if command == "FETCH":
            raw = (
                b"From: Alice Manager <alice@example.com>\r\n"
                b"Subject: Need your sign-off on the Q3 budget today\r\n"
                b"Message-ID: <m5@example.com>\r\n"
                b"\r\n"
                b"Please approve the Q3 budget by 5pm; the release is blocked until you do."
            )
            # FLAGS () => no \\Seen => unread.
            return "OK", [(b"5 (UID 5 FLAGS () RFC822.HEADER", raw)]
        return "OK", []

    def logout(self):
        self.logged_out = True


async def _run_urgency_capturing_llm(monkeypatch, tmp_path, llm_raises=False):
    """Drive action_check_email_urgency over one fresh unread email and return
    the list of max_tokens values the LLM classifier was invoked with. When
    llm_raises is True the fake LLM raises, exercising the heuristic fallback."""
    from core import database
    from routes import email_helpers
    from src import builtin_actions, llm_core, settings, task_endpoint

    class FakeEmailAccount:
        enabled = _Column()
        owner = _Column()
        imap_user = _Column()
        from_address = _Column()

    account = SimpleNamespace(
        id="acc1",
        enabled=True,
        owner="alice",
        imap_user="alice",
        from_address="alice",
    )
    db = _Db({FakeEmailAccount: [account]})

    monkeypatch.setattr(database, "EmailAccount", FakeEmailAccount)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        settings,
        "load_settings",
        lambda: {"urgent_email_prompt": "Flag anything my manager sends."},
    )
    monkeypatch.setattr(
        task_endpoint,
        "resolve_task_candidates",
        lambda *args, **kwargs: [("http://llm", "alice-model", {})],
    )
    monkeypatch.setattr(
        email_helpers, "_imap_connect", lambda *_a, **_k: _FakeImap()
    )

    # Keep all state file writes inside the tmp dir.
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        builtin_actions, "EMAIL_URGENCY_CACHE_DIR", str(tmp_path / "urgency_cache")
    )
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", tmp_path / "scheduled_emails.db")
    email_helpers._init_scheduled_db()

    max_tokens_seen = []

    async def fake_llm(_candidates, _messages, **kwargs):
        max_tokens_seen.append(kwargs.get("max_tokens"))
        if llm_raises:
            raise RuntimeError("endpoint unreachable")
        # Score < 2 so the notify path stays silent (no SMTP in the test).
        return '{"score":1,"tags":["action-needed"],"spam":false,"reason":"manager asked for sign-off"}'

    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", fake_llm)

    from src.builtin_actions import action_check_email_urgency

    await action_check_email_urgency("alice")
    return max_tokens_seen


@pytest.mark.asyncio
async def test_urgency_calls_llm_for_fresh_unread_email(monkeypatch, tmp_path):
    """The LLM classifier must be invoked for a fresh unread email; on `dev`
    the hardcoded `continue` makes the LLM block unreachable, so this is red
    until the short-circuit is removed."""
    max_tokens_seen = await _run_urgency_capturing_llm(monkeypatch, tmp_path)
    assert max_tokens_seen, (
        "LLM classifier was never called — check_email_urgency short-circuited "
        "to the keyword heuristic and ignored urgent_email_prompt (#5506)"
    )


@pytest.mark.asyncio
async def test_urgency_gives_reasoning_models_enough_tokens(monkeypatch, tmp_path):
    """Reasoning models spend tokens thinking before emitting JSON; a 220-token
    cap yields an empty completion. The classify call must request a budget
    large enough to return the verdict (#5506, second defect)."""
    max_tokens_seen = await _run_urgency_capturing_llm(monkeypatch, tmp_path)
    assert max_tokens_seen, "LLM classifier was never called (#5506)"
    assert max_tokens_seen[0] is not None and max_tokens_seen[0] >= 1024, (
        f"classify call requested only {max_tokens_seen[0]} max_tokens; reasoning "
        "models need room to emit their JSON verdict (#5506)"
    )


@pytest.mark.asyncio
async def test_urgency_retries_llm_next_scan_after_transient_failure(monkeypatch, tmp_path):
    """A transient LLM failure must not stick the heuristic fallback in the
    cache: the LLM is attempted, and the uid is left uncached so the next
    hourly scan re-classifies it instead of skipping it as already-triaged."""
    import json

    max_tokens_seen = await _run_urgency_capturing_llm(
        monkeypatch, tmp_path, llm_raises=True
    )
    assert max_tokens_seen, "LLM classifier was never even attempted (#5506)"

    cache_file = tmp_path / "urgency_cache" / "acc1.json"
    cached_uids = {}
    if cache_file.exists():
        cached_uids = json.loads(cache_file.read_text()).get("uids", {})
    assert "5" not in cached_uids, (
        "heuristic fallback was cached at the current triage version after an "
        "LLM failure, so the next scan would skip re-classification instead of "
        "retrying the LLM"
    )
