# PDV native feature proof

Validation date: 2026-08-01

This proof stays inside an isolated temporary Odysseus data directory. It does
not enter the application lifespan, start background jobs, contact an LLM, or
probe any provider endpoint.

## Production bearer path

`tests/test_pdv_native_integration_proof.py` imports the production `app.py` in
a child interpreter, creates bcrypt-hashed API-token rows in the real SQLite
schema, and sends requests through the real ASGI authentication middleware to
`/api/pdv/source` from a non-loopback client address.

Observed contract:

| Caller | Expected/observed status |
|---|---:|
| No credential | 401 |
| Invalid bearer | 401 |
| Admin-owned token, wrong scope | 403 |
| Non-admin-owned token, `pdv:read` | 403 |
| Admin-owned token, `pdv:read` | 200 |

The authorized response exposes canonical repository, upstream commit, and
AGPL license metadata. It does not contain token values or local data paths.

The same process generated production OpenAPI and confirmed 425 paths,
including chat, agents, research, documents, notes, tasks, scheduler,
providers, history, email, and calendar surfaces.

## Commands and results

```text
.venv\Scripts\python.exe -m pytest -q tests\test_pdv_native_integration_proof.py
1 passed, 1 warning in 12.31s

.venv\Scripts\python.exe -m pytest -q tests\test_api_chat_security.py tests\test_ai_interaction_owner_scope.py tests\test_research_owner_scope_routes.py tests\test_document_session_owner_scope.py tests\test_document_tool_owner_scope.py tests\test_manage_notes_owner_gate.py tests\test_notes_fail_closed_auth.py tests\test_manage_tasks_owner_scope.py tests\test_task_scheduler_cancel.py tests\test_scheduler_restart_doublefire.py tests\test_model_helper_owner_scope.py tests\test_provider_endpoints_normalization.py tests\test_history_topics_owner_scope.py tests\test_email_owner_scope.py tests\test_calendar_owner_scope.py tests\test_session_manager_persist_guard.py
110 passed, 15 warnings in 13.26s

.venv\Scripts\python.exe -m pytest -q tests\test_pdv_routes.py tests\test_api_token_routes.py tests\test_pdv_native_integration_proof.py
25 passed, 1 warning in 14.02s
```

The 110-test lane exercises owner isolation and persisted state for chat/agent
resolution, research, documents, notes, tasks and scheduler restart/cancel,
provider configuration, history, email SQLite indexes/scheduled rows, calendar,
and session messages. Tests use local stores and mocks; no model inference is a
condition of passing.

## Credential-dependent capabilities

Local calendar storage and routes are available without a remote CalDAV
account. CalDAV synchronization requires owner-supplied remote credentials.
Email routes and owner isolation are implemented and tested, but live IMAP,
SMTP, and OAuth operations remain `AUTH_REQUIRED` until the owner configures an
email account. No credential was invented or persisted by this proof.
