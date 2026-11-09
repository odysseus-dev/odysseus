# Email And Contacts

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers mail and contacts in:

- app wiring in `app.py`;
- `core.database.EmailAccount`;
- canonical `routes/email/email_routes.py`, `routes/email/email_helpers.py`, and
  `routes/email/email_pollers.py`, plus their shims at the old flat paths;
- email threading in `src/email_thread_parser.py`;
- email MCP tools in `mcp_servers/email_server.py`;
- canonical contact/CardDAV routes in `routes/contacts/contacts_routes.py`,
  with `routes/contacts_routes.py` as a compatibility shim;
- Codex email bridge in `routes/codex_routes.py`;
- document signed-reply flows in canonical `routes/document/document_routes.py` and document `source_email_*` fields;
- reminder/task email senders in `routes/note_routes.py` and `src/task_scheduler.py`;
- email/contact agent surfaces in `src/tool_implementations.py`, `src/tool_schemas.py`, `src/tool_index.py`, and `src/agent_loop.py`;
- CLI wrappers `scripts/odysseus-mail` and `scripts/odysseus-contacts`;
- frontend modules `static/js/emailInbox.js`, `static/js/emailLibrary.js`, `static/js/emailLibrary/*`, `static/js/emailShared.js`, `static/js/chatStream.js`, `static/js/document.js`, and `static/js/settings.js`;
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

`EmailAccount` rows own IMAP/SMTP configuration. Password fields are string columns containing encrypted ciphertext written with `src.secret_storage`; startup migrations handle legacy plaintext rows. Google OAuth account rows also carry `oauth_provider`, encrypted access/refresh tokens, token expiry, and an optional outbound `display_name`. Do not return decrypted credentials or OAuth tokens, or write them to logs.

Exactly one default account per owner is enforced as a serialized database transition. Startup normalizes legacy duplicate defaults and installs a unique per-owner default constraint/index; first create, delete/promotion, set-default, demo teardown, and owner rename lock the relevant owner rows and commit atomically. Multi-owner rename acquires locks in canonical order so stale concurrent writers fail closed.

`routes.email.email_helpers` owns (flat imports remain compatibility aliases):

- account owner assertions and config fallback order;
- IMAP/SMTP connection helpers and related transport utilities;
- Google OAuth2 state signing/verification, token refresh, and XOAUTH2 framing;
- SMTP security modes (`ssl`, `starttls`, `none`);
- envelope recipients and Odysseus headers;
- attachment extraction helpers;
- email pre-retrieval context for AI reply drafting;
- scheduled email, summary, reply, tag, calendar extraction, urgency, and signature-boundary side databases.

Email config can fall back to legacy `data/settings.json` or environment variables when no scoped account is configured. Account discovery now owner-scopes the default/first-enabled fallback and can still match legacy account rows by IMAP username or from-address. That fallback remains compatibility-sensitive in multi-user contexts.

Email owner semantics are route-local and compatibility-sensitive:

- `routes.email_helpers._require_auth()` returns `""` in `AUTH_ENABLED=false` mode, rejects configured auth with no user, and only tolerates first-run anonymous loopback fallback.
- Empty owner is treated as single-user compatibility: account-ownership assertions no-op, default/first-enabled account fallback can be global, and email cache clauses include `owner = '' OR owner IS NULL`.
- Non-empty owners scope account/config/cache queries. Legacy ownerless account
  rows are visible to an authenticated owner only when the row's IMAP username or from-address matches that owner, so old unowned rows do not become global cross-user accounts in configured multi-user deployments.

`routes.email.email_routes` owns the HTTP mail surface:

- account CRUD, test, default, and masked config reads;
- Google OAuth authorize/callback for Workspace and .edu Gmail-style accounts;
- list, search, read, folders, and contacts;
- folder role resolution and UID fetch/search helpers used by the route surface;
- owner-scoped route caches and IMAP pool behavior;
- attachments, bulk attachment ZIP downloads, and attachment-to-document flows;
- compose upload, draft/send, `wait_for_delivery`, Sent append, and source `\Answered` marking;
- schedule/list/delete scheduled emails;
- pending agent-draft approval/cancel flows;
- mark read/unread/answered, spam flags, move, archive, and delete. IMAP move/delete/archive operations use UID commands for message identity and fail safe when the requested UID no longer exists; they never reinterpret a missing UID as a sequence number, which could mutate or expunge an unrelated message.

Google OAuth behavior is account-owned:

- `/api/email/oauth/google/authorize` requires an authenticated owner, checks account ownership, HMAC-signs state with account id, owner, and nonce, and redirects to Google with mail/userinfo scopes;
- `/api/email/oauth/google/callback` verifies signed state before token exchange, re-checks the target account owner before writing tokens, stores access/refresh tokens encrypted, stores token expiry as a timestamp, and redirects with generic success/error codes rather than raw provider errors;
- token refresh uses `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, stores refreshed access tokens encrypted, and logs only generic/account-id context on failures;
- SMTP and IMAP use XOAUTH2 when `oauth_provider == "google"`; OAuth accounts are send-capable without an SMTP password when host and user are configured;
- outbound mail formats the `From` header with `display_name` when present.
- authorize/callback redirect URIs derive their scheme and host from the mounted request unless `GOOGLE_OAUTH_REDIRECT_URI` explicitly pins a value; the browser preserves the selected SMTP security mode during connect and reopens Settings after the callback.

MCP full-message read/reply/attachment fetches use IMAP `BODY.PEEK[]` rather than bare `RFC822`, so iCloud-style servers return the full body without marking messages seen. Poller UID handling must tolerate both bytes and string UIDs. Built-in signature-learning and daily-brief actions also use UID SEARCH/FETCH rather than sequence-number commands.

IMAP helpers quote mailbox names, raise the Python IMAP line cap for large messages, close sockets after connect/login failures, and preserve Gmail FETCH attributes that follow header literals so unread flag state is not lost. Browser list routes offload blocking IMAP work from async handlers; browser search runs in FastAPI's threadpool, rejects CRLF query input, tokenizes quoted phrases/terms, searches FROM/TO/CC/SUBJECT/TEXT, can search Gmail All Mail when an INBOX query should include archived or labelled messages, and supports `scope=folder` when callers intentionally want the selected folder only. The local index fallback can return indexed results when IMAP returns empty or fails.

## Runtime And Pollers

Scheduled email rows live in `data/scheduled_emails.db` and are owner-scoped. Scheduled send times are normalized before storage.

`routes.email.email_pollers` owns the scheduled-send poller and single-shot/task/CLI automation passes. Before SMTP work, each poller atomically claims a due row with a conditional `pending` to `sending` update; concurrent in-process/CLI pollers that lose the claim skip the row instead of sending a duplicate. Only the scheduled-send poller starts in-process by default when `ODYSSEUS_INPROCESS_POLLERS` allows it; Docker forwards that gate. Background email automation can also consult the foreground activity gate so auto actions do not compete with active browser/model work. Native cron/systemd can drive one-shot pollers through `scripts/odysseus-mail`.

Manual and scheduled summaries use the shared LLM adapter and owner-scoped cache instead of constructing provider calls locally. Scheduled summaries use background fallback policy and yield to foreground work; provider exception text is shaped before it can reach the browser.

Urgency delivery publishes through a serialized atomic checkpoint transaction. Generation and membership fences prevent stale scans from overwriting newer state; authoritative scans retire deleted/disabled accounts, partial failures preserve the prior checkpoint, concurrent account-scoped actions merge disjoint facts, and cancellation rolls back without publishing.

Transport degraded behavior:

- IMAP timeouts are clamped by configuration;
- providers can use implicit SSL, STARTTLS, or plain connections;
- poisoned IMAP sockets are reconnected around known provider failures;
- SMTP-capable account fallback is used where supported;
- route helpers, MCP, and CLI do not all share identical SMTP/IMAP parsing and security behavior today.

## Caching And Staleness

Email list/read behavior uses short route caches, longer read caches, capped warm prefetch, and owner/account-aware pool/cache keys. The frontend email library has bounded in-memory and local caches, cache-buster refreshes, scheduled/search cache exclusions and stale rows on refresh failure. A cached early return is not a universal stale-while-revalidate guarantee.

Opening an unread message is one authoritative backend IMAP operation. The read route fetches/parses the message and applies `\Seen` over the same connection; cached bodies still await one UID STORE, read-only mailboxes serve content without claiming a mark, and STORE failure returns the body with explicit failure state rather than caching a false read. Inbox/library clients deduplicate opens and carry mailbox context with selected request-generation guards. Those guards are not universal: some detached/inline completions still use mutable account/folder state, so late-response safety must be assessed per path.

Library prewarm runs only while genuinely idle, as one bounded single-flight request for the default or last-used enabled account and initial page. Visible foreground work, panel lifecycle, account changes, or explicit reads cancel or join it so delayed duplicate IMAP work cannot escape the idle gate.

List/read route caches are owner/account-aware. Helper-side summary, AI-reply, tag, calendar-extraction, urgency-alert, and learned sender-signature tables carry owner columns and owner clauses. Thread-boundary rows are still keyed by message shape rather than a full owner/account/mailbox key, so they remain cross-owner audit points when identical messages appear in multiple mailboxes.

## Attachments And Signed Replies

Compose uploads live under `ODYSSEUS_MAIL_ATTACHMENTS_DIR`; missing staged files are skipped with warnings. Attachment-to-document supports PDF, DOCX, TXT, and MD. DOCX attachment-to-document extraction uses MarkItDown first; optional LibreOffice conversion has a 60-second bound and can supply a PDF preview, otherwise extracted Markdown remains available; PDF form/open-in-doc flows can depend on optional PyMuPDF.

Email attachment-as-document flows stamp `Document.source_email_*` provenance. `GET /api/email/attachments-download/{uid}` builds an owner-scoped ZIP of visible non-signature attachments using safe names. `compose-from-odysseus` and `compose-from-odysseus-zip` can stage owner-visible documents and gallery images as compose uploads, preserving legacy session fallback only where the source object remains visible to the owner. `prepare-signed-reply` verifies document ownership, reconstructs reply headers, flattens/stages signed PDFs as compose uploads, and leaves final send/draft review to the compose flow.

Email bodies and attachments are untrusted model context.

## Threading And Rendering

`src.email_thread_parser` owns splitting plaintext/HTML email threads into quoted conversation parts. Frontend email library modules own reply-recipient logic, signature folding, local state, and rendering behavior. Bulk selections are cleared when folder/account loads, search text, search pills, or result scope changes so actions cannot carry stale UIDs into a different visible context. `static/js/emailShared.js` owns shared email UI helpers used across inbox/library surfaces.

Remote inbound email HTML is sanitized by frontend email-library utilities before `innerHTML` insertion. Server-side email routes sanitize composed/generated outbound HTML with an allowlist before draft/send, dropping scripts/styles and unsafe attributes. Both sides are part of the rendering invariant.

When the email reader is active, browser chat sends selected-message metadata. `src.tool_implementations` stores that request-local active email reference, `src.agent_loop` injects it as protected untrusted context, and `static/js/chatStream.js` handles `ui_control open_email_reply` so default reply/draft behavior opens the selected message's compose flow instead of a generic new document.

## MCP Email

`mcp_servers/email_server.py` exposes email tools for MCP/agent use. It has its own account discovery, IMAP/SMTP, attachment, cache, and send paths, but account visibility now mirrors the HTTP owner policy. The active owner comes from a hidden `_odysseus_owner` argument when the caller provides one, or from `ODYSSEUS_MCP_EMAIL_OWNER` / `ODYSSEUS_EMAIL_OWNER`. If any enabled account is owner-scoped and no current/configured owner exists, email MCP returns an owner-scope error instead of listing global accounts.

MCP email account filtering includes owner-owned rows and legacy ownerless rows whose mailbox/from-address matches the owner. Confirmation-first `send_email` resolves the selected account before stashing an `agent_draft`, so drafts cannot be staged against another owner's account. MCP-created draft documents use the resolved hidden/configured owner when available, with `ODYSSEUS_DOCUMENT_OWNER` and single-admin fallback only as document-visibility compatibility.

MCP email send behavior is confirmation-first by default: `send_email` and reply send paths stash a `scheduled_emails` row with `status='agent_draft'` when `agent_email_confirm` is true, and browser routes expose pending drafts for approval or cancellation. Separate MCP draft tools create Odysseus compose documents for user review without sending.

MCP email remains a separate local/admin trust boundary. Public and non-admin users must not see or execute email MCP tools. It still needs route-helper parity audits for attachment path containment, sanitization, transport behavior, and pending-draft result text, but global all-account behavior is no longer the current owner model.

## Contacts

`routes.contacts.contacts_routes` owns global/admin contacts and CardDAV behavior. The top-level `routes.contacts_routes` module is a compatibility shim. The canonical package supports local contacts, CardDAV config, list/search/add/update/delete, VCF/CSV import/export, and clear.

Contact runtime behavior:

- contacts routes are admin-gated;
- local `data/contacts.json` is used when CardDAV is unconfigured;
- import paths tolerate malformed or non-string contact bodies by skipping invalid rows instead of crashing the import;
- configured CardDAV uses REPORT with GET fallback and a short in-memory cache;
- configured-but-offline CardDAV can return cached reads but writes fail instead of falling back to local JSON;
- CardDAV config reads mask the password, settings-stored passwords are encrypted with `src.secret_storage`, omitted password updates preserve the existing secret, and an explicit empty password clears it;
- the native contacts CLI is CardDAV-oriented and does not fully match web JSON fallback behavior;
- agent contact tools reuse helper functions in-process because the HTTP routes require browser/admin auth.

HTTP contact administration remains admin-gated, but in-process helpers have owner-aware local rows and per-owner caches. Named owners see their own rows plus legacy ownerless contacts; synthetic `sft_` owners do not see ownerless rows and bypass CardDAV. This compatibility does not establish a general cross-user sharing API.

## Security Policy

Email HTTP access is owner-scoped, including account selection, scheduled email rows, and attachment routes. Null-owner/single-user compatibility paths are security-sensitive and must not allow cross-user mailbox access.

Codex email routes are the scoped bearer-token email API. They enforce `email:read`, `email:draft`, and `email:send` scopes and use token-owner attribution before borrowing email route handlers.

Known security policy details:

- decrypted email credentials stay process-local;
- account/config reads mask passwords and expose only OAuth status fields, not access or refresh token values;
- SMTP/IMAP security mode behavior is part of the credential contract;
- Google OAuth state and callback owner checks are part of the account-boundary contract;
- scheduled emails must remain owner-scoped;
- email pre-retrieval contacts context is allowed only for admin/single-user situations;
- HTTP and MCP attachment extraction share `src.mail_attachment_paths.attachment_scope_dir()` for hashed owner/account/folder/message directories; remaining attachment consumers still need end-to-end scope/path coverage.

CardDAV credentials and URLs are security-sensitive. CardDAV URL setup and derived href writes/deletes pass through outbound URL validation; absolute hrefs from a CardDAV server are constrained back to the configured origin before credentials are reused. CardDAV passwords in settings are encrypted and masked on read; environment-sourced legacy password values are used as supplied.

## Degraded Behavior

- IMAP/SMTP providers can be slow or inconsistent; folder resolution, pooled connections, and reconnect behavior should fail with clear errors.
- Google OAuth requires external Google endpoints plus configured `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`; missing client credentials or refresh failures degrade to reconnect-required or generic OAuth error paths.
- Scheduled email delivery depends on `scheduled_emails.db`, poller runtime, and configured SMTP.
- Attachment handling must tolerate missing staged files, unsupported formats, and inaccessible remote messages.
- CardDAV local fallback applies only when CardDAV is unconfigured; configured CardDAV outages are not treated as local-write mode.
- Multi-account list/search behavior can be sequential and cache-sensitive.

## Testing Coverage

Existing coverage includes header/envelope/IMAP/SMTP behavior, serialized default accounts, Google OAuth state/callback/token-refresh/XOAUTH2/redirect/settings behavior, shared-adapter summaries, authoritative read/mark-seen and frontend dedup, idle prewarm, UID-only mutations, scheduled-email claims and urgency checkpoint transactions, MCP full-message/owner behavior, owner scope/caches/signatures, thread/sanitizer behavior, CardDAV password encryption, mail CLI behavior, contacts basics, and selected frontend/security regressions.

Route-level and duplicate-path coverage is still thin for email list/read/search/mutations, account CRUD/security outside the OAuth path, send/draft security, attachments, scheduled-poller failures, contacts admin/CardDAV routes, MCP account/scope behavior, CardDAV degraded mode, and executable frontend behavior.

## Mail Safety And New Library Flows

Mail connection helpers resolve host addresses once and connect only to policy-approved addresses through `src.url_safety.connect_outbound_tcp()`, retaining hostname TLS verification. Link-local, non-loopback reserved, multicast and unspecified destinations are denied. IPv6 loopback and IPv4-mapped loopback follow the private-address policy rather than unconditional reserved-address denial. Exactly `64:ff9b::/96` is decoded to its IPv4 target and evaluated strictly, including all DNS answers; network-specific NAT64 prefixes are not inferred. Private/loopback/shared destinations are denied for ordinary multi-user owners, allowed for admins/auth-disabled operators, and configurable with `EMAIL_ALLOW_PRIVATE_IPS=true`; `EMAIL_BLOCK_PRIVATE_IPS=true` takes precedence and denies them for everyone.

The library adds owner-scoped unread-state, attachment metadata caches, bulk attachment ZIP, flagged-message actions, translation, and reviewed unsubscribe flows. `/unsubscribe/scan` reads newsletter/spam candidates; `/unsubscribe/execute` refetches the chosen message and supports mailto unsubscribe by sending through the selected account. HTTP unsubscribe links remain a manual-open path. `/unsubscribe/cleanup` moves reviewed candidates to Junk/Trash and can expand a sender's unsubscribe-header messages. Scanning alone does not send or move mail.

`src.mail_attachment_paths` hashes the full owner/account/folder/UID scope into an extraction directory so legacy ownerless paths cannot reuse another mailbox's staged attachment. `src.email_attachment_text` centralizes readable attachment extraction. Email calendar pollers apply ICS revisions through `src.email_calendar_import`; invitation state and reminder semantics are described in `calendar-tasks-notes.md`. `src.email_task_intent` supplies email-task intent helpers, and `src.email_reply_stream` handles reply-stream text behavior.

## Mailbox State And Delivery Boundaries

MCP email binds owner and active session in request-local contexts and resets them on exit. Cross-account list caching is short-lived (20 seconds) and listing can use four concurrent workers; it does not promise single-flight freshness. Nonempty local header-index results can suppress a live lookup. MCP sent-time ISO ranges normalize missing zones to UTC and use an inclusive start/exclusive end; browser date-only received-time filters include the end date. Cached/fixture paths do not uniformly apply every date filter. Search pills combine terms; index fallback and at-most-six attachment search terms do not guarantee complete provider search.

MCP bulk UID move fallback checks command statuses and verifies absence from the source; this is not proof of destination delivery. Browser and MCP single-message move paths instead rely on MOVE or COPY/STORE/expunge acknowledgements without a post-move source read.

Browser bulk delete requires a successful native MOVE or recoverable Trash COPY before removing originals. COPY failure retains source/index; fallback STORE batches only copied UIDs for selective RFC 4315 UID EXPUNGE, never mailbox-wide EXPUNGE. Index rows are removed only after confirmed deletion. Failed selective expunge retains the index and attempts to remove newly added `\Deleted` flags while preserving observed pre-existing flags; disconnects can prevent rollback, and a lost acknowledgement is not proof the source survived. Servers without selective expunge can therefore report failure despite a Trash copy. Responses distinguish confirmed deleted and failed UIDs, total failure, and partial success; source and Trash list caches invalidate in `finally` when any deletion was confirmed. Single-message paths retain their separate behavior.

Draft replacement appends the new mailbox draft before removing the old one. Sent draft cleanup follows successful Sent append; SMTP, IMAP and local database state are not one transaction. Scheduled send claims prevent concurrent claim winners, not exactly-once SMTP across a crash after send and before checkpoint.

Composer sends first save a recovery mailbox draft, then use `wait_for_delivery` and preserve exact draft UID/folder for delivery cleanup. Failures/cancellation retain recoverable draft state. AI reply application checks generation/document/body snapshots and intervening manual input before overwriting. Confirmation-first MCP pending drafts are a distinct workflow. Reply-by-Message-ID resolves owner/account/UID context rather than trusting a global id; recursive fallback work is bounded and gathered on cancellation. Reply generation disables thinking, uses bounded initial output and one larger retry; this does not guarantee provider delivery.

Unsubscribe scanning inspects at most the newest 500 candidates and deduplicates senders; bulk operations are bounded and can partially fail. A sent mailto attempt can move its source to Trash, while pending confirmation leaves it untouched; successful send-attempt bookkeeping is not confirmation that a remote subscription ended. Blocked-sender JSON is owner-keyed but not transactionally coupled to mailbox changes. Bulk attachment ZIP is capped at 250 messages in batches of twenty, skips failed messages, uses safe names and assembles in memory.

Reader HTML snapshots use inert parsing/sanitization and sandbox/CSP constraints; blocked network in browser tests is a test condition rather than universal runtime network isolation. Attachment-only/media-only bodies remain meaningful; a thumbnail `img.src` is not itself a URL sanitizer. Local reminder metadata can identify folder/UID without an account and is a narrower compatibility path than canonical mailbox identity. Contact cache success/failure windows and shared refresh locking do not turn a CardDAV outage into local-write success.

## Current Gaps

- Owner-keyed cache policy still needs an explicit decision for thread boundaries, plus continued migration/query audits for every email side table.
- CardDAV still needs redirect/proxy policy and broader route-level tests for URL validation, private-address blocking configuration, and same-origin href enforcement.
- MCP email needs continued route-helper parity for attachment path containment,
  sanitization, transport behavior, and pending-draft result text.
- Empty-owner route compatibility and ownerless email cache rows need
  end-to-end owner-boundary tests.
- CLI send/contact paths need parity decisions for SMTP security, recipient parsing, local fallback, and normalized contact shapes.
- Email HTTP route coverage is concentrated in scheduling/account-test helpers rather than full list/read/search/mutation/send/draft/account/attachment flows.
- Contacts coverage lacks admin-gate, config masking, import/export, CardDAV fallback, and CardDAV write-failure tests.
- Multi-account performance and cache staleness remain known audit areas.
