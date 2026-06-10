"""Regression: POST /api/email/ai-reply must not crash on JSON null model/session_id.

The handler read two optional fields with ``data.get("model", "").strip()`` /
``data.get("session_id", "").strip()``. ``dict.get`` returns the default only
when the key is ABSENT; a body like ``{"model": null}`` gives ``None``, so
``None.strip()`` raised ``AttributeError``. That was swallowed by the handler's
outer ``try/except`` and reported to the user as the generic "Mail operation
failed", masking a null-deref instead of treating a null model as "use the
default" (which the empty-string default already does).

Tellingly, the three sibling fields right below (message_id, uid, folder) were
already guarded with ``(data.get(k) or "")`` — only model/session_id were not.
Both are now guarded the same way. Same class as the contacts ``None.strip()``
fix (#1544).
"""
import asyncio

import pytest

import routes.email_routes as er


def _ai_reply_handler():
    router = er.setup_email_routes()
    for r in router.routes:
        if getattr(r, "path", "").endswith("/ai-reply") and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("ai-reply route not found")


@pytest.mark.parametrize("body", [
    {"model": None, "original_body": ""},
    {"session_id": None, "original_body": ""},
    {"model": None, "session_id": None, "original_body": ""},
])
def test_null_model_or_session_does_not_crash(body):
    handler = _ai_reply_handler()
    result = asyncio.run(handler(body, owner="tester"))
    # A null model/session must fall through to the intended early-return
    # validation (which lives AFTER the .strip() calls), NOT crash into the
    # catch-all "Mail operation failed". Reaching "No email body provided"
    # proves the null no longer raises at the strip.
    assert result == {"success": False, "error": "No email body provided"}
