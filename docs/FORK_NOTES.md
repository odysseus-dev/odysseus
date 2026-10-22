# Fork Notes

Fixes and changes carried by the `paulorochagodoi/odysseus` fork on top of
upstream `odysseus-dev/odysseus` (`dev`). Each section names the symptom, the
root cause, and the regression test, so it can be dropped once upstream has
an equivalent fix.

## Codex email API returned 401 for valid tokens

**Symptom.** `GET /api/codex/emails?limit=3` with a valid `email:read` bearer
token (`ody_...`) returned `401 {"error": "Invalid API token"}` and logged
`API token auth error`. With the same token, `/api/codex/capabilities` and
`/api/codex/todos` returned 200.

**Root cause.** There were two bugs:

1. `routes/codex_routes.py` calls the email route handlers directly as Python
   functions. `list_emails` did not pass `date_from`, `date_to`,
   `cached_only` or `refresh`, so they kept their FastAPI defaults:
   `Query(None)`/`Query(0)` objects, which are truthy. The handler then called
   `datetime.strptime(<Query>, ...)`, which raised `TypeError`. `read_email`
   also leaked `full` as a `Query(False)` object; that was harmless only by
   accident.
2. In `app.py`, `AuthMiddleware` wrapped `await call_next(request)` in the
   same `try/except Exception` as bearer-token verification. Any downstream
   route exception was therefore reported as `401 Invalid API token`.

**Fix.**

- Codex routes call every borrowed handler through `_call_handler()`. It
  replaces each unpassed FastAPI marker default with the real default value,
  and raises `TypeError` for an unresolved `Depends()` or a required
  parameter. Email list/read now also pass every parameter explicitly.
- `AuthMiddleware` returns 401 only when token verification fails. Route
  errors after a successful auth are logged with the path and token id, then
  re-raised so normal error handling returns a 500.

**Tests.** `tests/test_codex_email_api_token.py`.
