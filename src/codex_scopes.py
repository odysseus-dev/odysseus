"""Centralized Codex/Claude API token scope constants.

Imported by routes/codex_routes.py to enforce per-action scope checks and
documented in integrations/*/skills/*/SKILL.md so the LLM-facing description
stays in sync with the server's source of truth.

If you add a new scope:
  1. Add it to one of the *_SCOPES sets below.
  2. Add a corresponding branch in /api/codex/capabilities.
  3. Update SKILL.md so the agent knows it exists.
"""

# ── Todos ─────────────────────────────────────────────────────────────
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}

# ── Email ─────────────────────────────────────────────────────────────
EMAIL_READ_SCOPES = {"email:read", "email:draft", "email:send"}
EMAIL_DRAFT_SCOPES = {"email:draft", "email:send"}
EMAIL_SEND_SCOPES = {"email:send"}

# ── Memory ────────────────────────────────────────────────────────────
MEMORY_READ_SCOPES = {"memory:read", "memory:write"}
MEMORY_WRITE_SCOPES = {"memory:write"}

# ── Calendar ──────────────────────────────────────────────────────────
CALENDAR_READ_SCOPES = {"calendar:read", "calendar:write"}
CALENDAR_WRITE_SCOPES = {"calendar:write"}

# ── Documents ─────────────────────────────────────────────────────────
DOCS_READ_SCOPES = {"documents:read", "documents:write"}
DOCS_WRITE_SCOPES = {"documents:write"}


# Todo actions that require a write scope. Anything not in this set is
# treated as read-only at the scope layer.
WRITE_ACTIONS = {
    "add", "create", "new", "save", "remind",
    "update", "delete", "toggle_item", "remove", "remove_item",
}

# Body fields accepted by /api/codex/todos POST. Anything else is dropped
# before being forwarded to do_manage_notes — defense in depth against a
# future codex client smuggling in a key the backend interprets differently.
KNOWN_TODO_FIELDS = {
    "title", "id", "priority", "due_date", "label", "archived", "notes",
    "action",  # the action field itself is set explicitly by the route
}
