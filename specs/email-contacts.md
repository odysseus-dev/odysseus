# Email And Contacts

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers mail and contacts in:

- app wiring in `app.py`;
- `core.database.EmailAccount`;
- `routes/email_routes.py`, `routes/email_helpers.py`, and `routes/email_pollers.py`;
- email threading in `src/email_thread_parser.py`;
- email MCP tools in `mcp_servers/email_server.py`;
- contact/CardDAV routes in `routes/contacts_routes.py`;
- Codex email bridge in `routes/codex_routes.py`;
- document signed-reply flows in `routes/document_routes.py` and document `source_email_*` fields;
- reminder/task email senders in `routes/note_routes.py` and `src/task_scheduler.py`;
- email/contact agent surfaces in `src/tool_implementations.py`, `src/tool_schemas.py`, `src/tool_index.py`, and `src/agent_loop.py`;
- CLI wrappers `scripts/odysseus-mail` and `scripts/odysseus-contacts`;
- frontend modules `static/js/emailInbox.js`, `static/js/emailLibrary.js`, `static/js/emailLibrary/*`, `static/js/document.js`, and `static/js/settings.js`;
- tests under `tests/test_email_*`, `tests/test_contacts_*`, `tests/test_mail_cli_*`, `tests/test_mcp_email_*`, `tests/test_schedule_email_*`, email/contact JS tests, and email security regressions.

## Current Call Sites Include

- browser email inbox/library, compose, schedule, account, and attachment actions;
- document-editor compose, recipient autocomplete, compose uploads, and signed-reply handoff;
- Codex email read/draft/send routes using API-token scopes;
- note reminder and task-output email delivery;
- built-in email summary/reply/calendar/urgency actions;
- scheduled email pollers and CLI one-shot pollers;
- MCP email tools;
- contact manager settings, compose contact autocomplete, agent contact tools, and contacts CLI.

## Email Accounts And Transport

`EmailAccount` rows own IMAP/SMTP configuration. Password fields are string columns containing encrypted ciphertext written with `src.secret_storage`; startup migrations handle legacy plaintext rows. Do not return decrypted credentials or write them to logs.

`routes.email_helpers` owns:

- account owner assertions and config fallback order;
- IMAP/SMTP connection helpers;
- IMAP/SMTP connection helpers and related transport utilities;
- SMTP security modes (`ssl`, `starttls`, `none`);
- envelope recipients and Odysseus headers;
- attachment extraction helpers;
- email pre-retrieval context for AI reply drafting;
- scheduled email, summary, reply, tag, calendar extraction, urgency, and signature-boundary side databases.

Email config can fall back to legacy `data/settings.json` or environment variables when no scoped account is configured. That fallback is compatibility-sensitive in multi-user contexts.

`routes.email_routes` owns the HTTP mail surface:

- account CRUD, test, default, and masked config reads;
- list, search, read, folders, and contacts;
- folder role resolution and UID fetch/search helpers used by the route surface;
- owner-scoped route caches and IMAP pool behavior;
- attachments and attachment-to-document flows;
- compose upload, draft/send, `wait_for_delivery`, Sent append, and source `\Answered` marking;
- schedule/list/delete scheduled emails;
- mark read/unread/answered, spam flags, move, archive, and delete.

MCP full-message read/reply/attachment fetches use IMAP `BODY.PEEK[]` rather than bare `RFC822`, so iCloud-style servers return the full body without marking messages seen. Poller UID handling must tolerate both bytes and string UIDs.

## Runtime And Pollers

Scheduled email rows live in `data/scheduled_emails.db` and are owner-scoped. Scheduled send times are normalized before storage.

`routes.email_pollers` owns the scheduled-send poller and single-shot/task/CLI automation passes. Only the scheduled-send poller starts in-process by default when `ODYSSEUS_INPROCESS_POLLERS` allows it; Docker forwards that gate. Native cron/systemd can drive one-shot pollers through `scripts/odysseus-mail`.

Transport degraded behavior:

- IMAP timeouts are clamped by configuration;
- providers can use implicit SSL, STARTTLS, or plain connections;
- poisoned IMAP sockets are reconnected around known provider failures;
- SMTP-capable account fallback is used where supported;
- route helpers, MCP, and CLI do not all share identical SMTP/IMAP parsing and security behavior today.

## Caching And Staleness

Email list/read behavior uses short route caches, longer read caches, capped warm prefetch, and owner/account-aware pool/cache keys. The frontend email library has its own session SWR cache, cache-buster refreshes, scheduled/search cache exclusions, and stale-row behavior when refresh fails.

List/read route caches are owner/account-aware. Helper-side summary, AI-reply, calendar-extraction, and urgency-alert tables carry owner columns and owner clauses. Thread-boundary rows and learned sender-signature rows are still keyed by message/sender shape rather than a full owner/account/mailbox key, so those caches remain cross-owner audit points when identical messages appear in multiple mailboxes.

## Attachments And Signed Replies

Compose uploads live under `ODYSSEUS_MAIL_ATTACHMENTS_DIR`; missing staged files are skipped with warnings. Attachment-to-document supports PDF, DOCX, TXT, and MD. DOCX depends on `python-docx`; PDF form/open-in-doc flows can depend on optional PyMuPDF.

Email attachment-as-document flows stamp `Document.source_email_*` provenance. `prepare-signed-reply` verifies document ownership, reconstructs reply headers, flattens/stages signed PDFs as compose uploads, and leaves final send/draft review to the compose flow.

Email bodies and attachments are untrusted model context.

## Threading And Rendering

`src.email_thread_parser` owns splitting plaintext/HTML email threads into quoted conversation parts. Frontend email library modules own reply-recipient logic, signature folding, local state, and rendering behavior.

Remote inbound email HTML is sanitized by frontend email-library utilities before `innerHTML` insertion. Server-side email routes sanitize composed/generated outbound HTML before draft/send. Both sides are part of the rendering invariant.

## MCP Email

`mcp_servers/email_server.py` exposes email tools for MCP/agent use. It has its own account discovery, IMAP/SMTP, attachment, cache, and send paths.

MCP email is a separate local/admin trust boundary. Public and non-admin users must not see or execute email MCP tools. If all-account admin MCP behavior remains intentional, it should be documented as such; otherwise MCP must become owner-aware and reuse route-level credential, attachment containment, sanitization, and transport rules.

## Contacts

`routes.contacts_routes.py` owns global/admin contacts and CardDAV behavior. It supports local contacts, CardDAV config, list/search/add/update/delete, VCF/CSV import/export, and clear.

Contact runtime behavior:

- contacts routes are admin-gated;
- local `data/contacts.json` is used when CardDAV is unconfigured;
- configured CardDAV uses REPORT with GET fallback and a short in-memory cache;
- configured-but-offline CardDAV can return cached reads but writes fail instead of falling back to local JSON;
- the native contacts CLI is CardDAV-oriented and does not fully match web JSON fallback behavior;
- agent contact tools reuse helper functions in-process because the HTTP routes require browser/admin auth.

Contacts are global admin-only data today. There is no per-user contact sharing model unless a future spec defines one.

## Security Policy

Email HTTP access is owner-scoped, including account selection, scheduled email rows, and attachment routes. Null-owner/single-user compatibility paths are security-sensitive and must not allow cross-user mailbox access.

Codex email routes are the scoped bearer-token email API. They enforce `email:read`, `email:draft`, and `email:send` scopes and use token-owner attribution before borrowing email route handlers.

Known security policy details:

- decrypted email credentials stay process-local;
- account/config reads mask passwords;
- SMTP/IMAP security mode behavior is part of the credential contract;
- scheduled emails must remain owner-scoped;
- email pre-retrieval contacts context is allowed only for admin/single-user situations;
- MCP attachment downloads need route-level path-containment parity; current MCP paths are separate from the HTTP compose/attachment helper path.

CardDAV credentials and URLs are security-sensitive. CardDAV URL setup and derived href writes/deletes pass through outbound URL validation; absolute hrefs from a CardDAV server are constrained back to the configured origin before credentials are reused. CardDAV password storage remains settings-based/plaintext, unlike encrypted CalDAV account storage.

## Degraded Behavior

- IMAP/SMTP providers can be slow or inconsistent; folder resolution, pooled connections, and reconnect behavior should fail with clear errors.
- Scheduled email delivery depends on `scheduled_emails.db`, poller runtime, and configured SMTP.
- Attachment handling must tolerate missing staged files, unsupported formats, and inaccessible remote messages.
- CardDAV local fallback applies only when CardDAV is unconfigured; configured CardDAV outages are not treated as local-write mode.
- Multi-account list/search behavior can be sequential and cache-sensitive.

## Testing Coverage

Existing coverage includes header decoding, envelope recipients, IMAP timeout, SMTP security, IMAP reconnect, iCloud-compatible MCP full-message fetch shape, owner scope, scheduled offset normalization, thread parsing, HTML sanitizer source checks, MCP header decoding, mail CLI behavior, contacts parsing/add basics, reply-recipient JS, signature folding, Gmail quote attribution, and selected security regressions.

Route-level and duplicate-path coverage is still thin for email list/read/search/mutations, account CRUD/security, send/draft security, attachments, scheduled-poller failures, contacts admin/CardDAV routes, MCP account/scope behavior, CardDAV degraded mode, and executable frontend behavior.

## Current Gaps

- Owner-keyed cache policy still needs an explicit decision for thread boundaries and learned sender signatures, plus migration/query audits for every email side table.
- CardDAV still needs encrypted credential storage, redirect/proxy policy, and route-level tests for URL validation, private-address blocking configuration, and same-origin href enforcement.
- MCP email needs an explicit owner/scope decision and route-helper parity for credentials, attachment path containment, sanitization, and transport behavior.
- CLI send/contact paths need parity decisions for SMTP security, recipient parsing, local fallback, and normalized contact shapes.
- Email HTTP route coverage is concentrated in scheduling/account-test helpers rather than full list/read/search/mutation/send/draft/account/attachment flows.
- Contacts coverage lacks admin-gate, config masking, import/export, CardDAV fallback, and CardDAV write-failure tests.
- Multi-account performance and cache staleness remain known audit areas.
