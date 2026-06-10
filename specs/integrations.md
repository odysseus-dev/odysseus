# Integrations

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers external integration surfaces in:

- `routes/codex_routes.py`;
- `integrations/codex/*` and `integrations/claude/*`;
- `routes/api_token_routes.py` and bearer-token handling in `app.py`;
- `routes/auth_routes.py` integration CRUD/test routes;
- `src/integrations.py` and `data/integrations.json`;
- `routes/webhook_routes.py` and `src/webhook_manager.py`;
- task webhook generation/triggering in `routes/task_routes.py`, `app.py`, and `static/js/tasks.js`;
- companion/mobile pairing in `companion/routes.py` and `companion/pairing.py`;
- integration UI surfaces in `static/js/settings.js` and `static/js/admin.js`;
- database models `ApiToken` and `Webhook`.

The SQLAlchemy `Integration` model exists in `core/database.py`, but current Settings generic integration CRUD uses `src/integrations.py` and `data/integrations.json`.

## Scoped Agent Runtime

`/api/codex/*` is the canonical scoped HTTP surface for external coding agents. Claude Code uses the same runtime endpoints; `/api/claude/plugin.zip` only delivers the Claude skill bundle.

`routes.codex_routes` owns:

- `/api/codex/capabilities`;
- todos list/manage through `do_manage_notes()`;
- email list/read/draft/send;
- memory list/add/delete;
- calendar list/create/delete;
- document list/read/create/delete;
- Cookbook task/server/output/cached-model/preset/serve/adopt/stop controls.

`_scope_owner()` owns scope checks and token-owner resolution. `_as_owner()` temporarily runs borrowed route handlers as the scoped owner and restores request state afterward. Borrowed email, memory, calendar, and document route handlers own their domain behavior; Codex routes only adapt them behind scoped access.

Runtime behavior:

- missing scopes return 403;
- invalid payloads return 400;
- unavailable borrowed route surfaces return 503;
- capabilities expose scope-derived booleans and partial availability flags;
- email send and destructive actions remain described as confirmation-required behavior in bundled agent instructions.

The local integration skill/helper files require `ODYSSEUS_URL` and `ODYSSEUS_API_TOKEN`. They must use `/api/codex/*` and must not bypass Settings/token scopes through SSH, Docker, direct DB access, local files, MCP internals, or app imports. Helper scripts refuse non-`/api/codex/*` paths.

## Bundle Distribution

`/api/codex/plugin.zip` ships the Codex plugin tree from `integrations/codex/`. `/api/claude/plugin.zip` ships only the Claude `skills/` subtree from `integrations/claude/skills/`. These routes require an authenticated browser/user request and do not embed an API token.

Setup instructions are duplicated in integration READMEs and `static/js/settings.js`; they need to stay aligned with live route surfaces and `/api/codex/capabilities`.

## API Tokens

`routes.api_token_routes` owns token profiles, allowed scopes, scope normalization, token creation/update/revocation, and profile metadata shown in Settings. Write scopes auto-include their read scope where applicable.

`app.py` owns bearer-token validation. It accepts `Bearer ody_...`, checks a bcrypt hash through a prefix cache, updates `last_used_at` asynchronously, and stamps:

- `request.state.current_user = "api"`;
- `request.state.api_token = True`;
- `request.state.api_token_owner`;
- `request.state.api_token_scopes`.

The raw token is returned only on creation. Stored state is hash, prefix, owner, scopes, active flag, and timestamps. Token create/update/delete invalidates the auth middleware cache. Companion pairing also mints chat-scoped `ApiToken` rows and invalidates that cache.

Current API-token consumers include:

- `/api/codex/*` scoped agent routes;
- `/api/v1/chat` synchronous external chat;
- companion read endpoints;
- selected session and owner-attribution helpers described in `auth-security.md`.

The Cookbook scoped-agent surface currently exposes `cookbook:read` and `cookbook:launch` in Settings and checks them in Codex routes; those scope names must stay reconciled with `routes.api_token_routes.ALLOWED_SCOPES`.

## Generic API Integrations

`src.integrations` owns generic API integration presets, `data/integrations.json`, API-key encryption/decryption, secret masking, plaintext-key migration, enabled integration prompt text, and `execute_api_call()`.

`routes.auth_routes` owns admin-only HTTP CRUD/test routes for these integrations. Presets are public metadata. The ntfy test route is special: it publishes a real test notification to the configured reminder topic instead of only probing server health.

`api_call` is the agent/tool execution path for configured integrations. It is blocked for non-admin/public users by tool security, accepts only relative paths, uses the admin-configured base URL/auth settings, and returns truncated external responses to the model. Admin-authored integration descriptions are prompt context; external responses remain untrusted data.

Current call sites include:

- `src.agent_loop` injecting enabled integration descriptions;
- `src.tool_implementations.do_api_call()`;
- task scheduler discovery/check-ins;
- note reminder delivery through ntfy integrations and the generic webhook reminder channel.

## Webhooks And External Chat

Outgoing webhooks are admin-managed `Webhook` rows. `routes.webhook_routes` owns CRUD/test/toggle/delete and `/api/v1/chat`. `src.webhook_manager` owns allowed event validation, public URL validation, delivery-time URL revalidation, HMAC signing, fire-and-forget delivery, and delivery status/error persistence.

Allowed outgoing events are:

- `session.created`;
- `chat.message`;
- `chat.completed`;
- `webhook.test`.

Current webhook event emitters include session creation, chat message/completion paths, and `/api/v1/chat` completion.

`/api/v1/chat` is an inbound external chat endpoint. It requires a `chat` API token, checks session ownership before resume, can create a session from a direct API key, and otherwise falls back to the first owner-visible enabled model endpoint. Token-supplied direct `base_url` values use public-URL validation; configured endpoints remain admin-trusted.

## Task Webhooks And Event Triggers

Task webhook triggers are separate inbound webhooks. `app.py` exempts only `/api/tasks/{task_id}/webhook/{token}` from normal auth so external callers can trigger tasks without cookies. `routes.task_routes` owns token generation/regeneration and validates task id, token, and active status before queueing a run.

`static/js/tasks.js` displays the live task webhook URL. `scripts/odysseus-webhook` is stale against this route shape and is not authoritative.

Event-triggered tasks use `src.event_bus`; task execution and scheduling ownership lives in `calendar-tasks-notes.md`.

## Companion Pairing

`companion.routes` owns companion/mobile HTTP routes:

- `/api/companion/ping`;
- `/api/companion/info`;
- `/api/companion/models`;
- `/api/companion/pair`.

Read endpoints accept session or bearer-token callers and resolve the effective owner for visible rows. Model responses omit API keys. Pairing `GET` renders the admin form; pairing `POST` is admin-cookie only, mints a normal chat-scoped API token, invalidates the auth token cache, and returns a host/port/token payload as HTML or JSON.

`companion.pairing` owns LAN host detection, pairing payload shape, token minting, and optional QR generation. QR rendering depends on optional `qrcode`; if unavailable or failing, pairing still returns the text payload.

## Unified Settings Surface

The Settings Integrations view aggregates several subsystem surfaces:

- generic API integrations;
- Codex/Claude agent token setup;
- CalDAV, CardDAV, email accounts, MCP/OAuth links, and agent tokens.

Vault and companion/mobile setup are separate settings/route surfaces today, not entries in the unified add-integration list.

This spec owns the cross-integration framing and agent/token/webhook surfaces. Domain internals stay with their subsystem specs: calendar, email/contacts, shell-MCP, vault/auth, and settings-admin.

## Degraded And Compatibility Behavior

- 403 from scoped APIs means a settings/scope restriction.
- 503 from Codex borrowed routes means the domain route surface is unavailable.
- Missing or corrupt `data/integrations.json` loads as an empty list; non-object rows are ignored.
- Plaintext generic integration API keys migrate to encrypted storage on load.
- Webhook delivery has no retry/backoff queue; the persisted state is last status or sanitized last error.
- Webhook URLs are validated at create and delivery time and redirects are disabled.
- Companion LAN detection is best-effort and falls back to local host/port defaults.
- `ODYSSEUS_URL` must be reachable from the external coding agent; no Docker/native URL rewrite is performed.

## Security And Provenance

- API-token routes must either enforce a relevant scope or document an explicit exception.
- Codex/Claude plugin zips must not expose secrets beyond source instructions and helper files.
- Webhook list responses expose `has_secret`, not the secret value.
- Webhook secrets are encrypted when an API key manager is available; plaintext fallback is legacy/degraded behavior.
- Outgoing webhook signatures use `X-Odysseus-Signature`.
- Generic integration API keys are encrypted at rest and masked in API responses.
- Generic integration base URLs are admin-configured and not the same public-only policy as webhook URLs.
- `api_call` output and remote integration responses are untrusted model context.
- Pairing payloads expose the raw chat token once through HTML/JSON/QR; persisted token storage is hash/prefix only.

## Testing Notes

Current targeted coverage includes API-token CRUD basics, companion pairing/read-only owner scoping, webhook SSRF validation, webhook auth-exempt source checks, webhook CLI token masking, integration-store shape/encryption migration, and `/api/v1/chat` base-url/fallback owner scoping.

The integration audit also ran the targeted venv subset covering those areas with 52 passing tests and one warning.

## Current Gaps

- Codex/Claude scoped routes, owner restoration, degraded 503 behavior, plugin zip contents, and helper-script path refusal need focused regression tests.
- Token profile/update behavior and Settings agent-token scope toggles need direct coverage.
- Codex Cookbook scopes need reconciliation between Settings, route checks, and `ALLOWED_SCOPES`.
- Generic integration HTTP CRUD/test routes, `execute_api_call()` auth modes, response shaping, and frontend Settings/Admin flows need direct coverage.
- `do_manage_tokens()` does not match `/api/tokens` semantics for `ody_` prefix, owner, scopes, and cache invalidation.
- `do_manage_webhooks()` bypasses route behavior and does not cover signing-secret parity.
- Companion read endpoints should either require `chat` scope or be documented as an explicit scope-policy exception.
- Decide whether webhook secret plaintext fallback should remain accepted when the API key manager is unavailable.
- Decide whether generic integration base URLs are intentionally admin-trusted/local-capable or should gain SSRF validation.
- Decide whether admin-authored integration descriptions and `api_call` results need a shared untrusted-context wrapper.
- The dormant SQLAlchemy `Integration` model should be removed, migrated into use, or documented as legacy.
- `scripts/odysseus-webhook` still emits the removed `/api/webhook/{token}` path.
