# Auth And Security

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers current security and trust-boundary behavior in:

- `core/auth.py`;
- `core/middleware.py`;
- `core/database.py`;
- `app.py` auth middleware and token cache;
- `src/auth_helpers.py`;
- `src/tool_security.py`;
- `src/prompt_security.py`;
- `src/url_safety.py` and `src/url_security.py`;
- `src/secret_storage.py`;
- `src/api_key_manager.py`;
- `src/integrations.py`;
- `src/webhook_manager.py`;
- `src/generated_images.py`;
- `scripts/diffusion_server.py`;
- `companion/routes.py` and `companion/pairing.py`;
- `routes/auth_routes.py`, `routes/api_token_routes.py`, `routes/vault_routes.py`;
- admin-gated call sites in route files;
- `THREAT_MODEL.md` and `SECURITY.md`.

## Trust Boundary

Odysseus is a trusted-user private-network app. Admins intentionally have powerful local capabilities: shell, files, email, calendar, MCP, model serving, vault, settings, and API token management. The security model prevents unauthenticated access, non-admin escalation, prompt-injection through untrusted content, and accidental exposure of internal services.

`THREAT_MODEL.md` owns high-level security framing, but implementation claims here should be verified against current code when the threat model is stale. This spec records the implementation map that contributors should check before changing auth or untrusted-context flows. Security-header runtime details live in `runtime.md`.

## Auth Ownership

- `core.auth.AuthManager` owns users, password hashing, TOTP/backup codes, reserved usernames, privilege defaults, and auth settings stored in `data/auth.json`. Auth config/setup mutations are lock-guarded, and session tokens are persisted separately in `data/sessions.json` behind their own lock.
- `app.py` owns request-time auth middleware, token-cache rebuild/invalidation, auth exemptions, API-token verification, and internal-tool identity stamping.
- `routes/auth_routes.py` owns HTTP endpoints for setup, signup/login/logout, 2FA, users, privileges, auth features, and integration settings.
- `core.middleware.require_admin()` owns the normal admin gate. Local wrappers must document and test any intentional divergence from that boundary.
- `src.auth_helpers.effective_user()` owns cookie/API-token owner attribution for selected route code. `require_user()` owns route-level degraded user resolution, `require_privilege()` owns privilege checks, and `owner_filter()` owns shared/null-owner query compatibility.

Reserved usernames include `internal-tool`, `api`, `demo`, and `system`. Do not create flows that can register or rename real users into those names.

## Auth Runtime Flow

`AuthMiddleware` is the outer request gate because FastAPI middleware executes in reverse add order. It can return API `401` JSON or browser `/login` redirects before timeout/security-header middleware reaches the route.

Public/auth-exempt surfaces are limited to setup, signup/login/logout/status, feature/settings/integration preset reads, health/version/login, `/static/*`, and task webhook trigger paths. `routes/task_routes.py` owns validation of `POST /api/tasks/{task_id}/webhook/{token}` path credentials.

Login issues an `HttpOnly`, `SameSite=Lax` cookie, with `SECURE_COOKIES` opt-in and a seven-day max age when "remember" is enabled. TOTP is checked before session issuance. Logout, password changes, user deletion, rename flows, expired sessions, and deleted-user sessions must keep revocation/migration behavior intact.

Deleting a user revokes that user's browser sessions and API-token rows, then the admin delete route invalidates the in-memory bearer-token cache so already-cached tokens stop authenticating.

## Owner Attribution

Cookie requests use the real username. Bearer-token requests are stamped as `request.state.current_user = "api"` plus `api_token_owner`, `api_token_scopes`, and token id. Routes that support API-token access must explicitly use `effective_user()` or route-local scope helpers instead of treating `"api"` as an owner.

Internal loopback calls may stamp `current_user = "internal-tool"` or a validated `X-Odysseus-Owner` username. Network/proxy validation for that bypass lives in `app.py`; `require_admin()` trusts the stamped sentinel or raw internal header and should be used behind equivalent middleware control.

## API Tokens And Scoped Integrations

`routes/api_token_routes.py` owns token CRUD and scope normalization. `app.py` caches active token prefix rows and verifies bearer tokens with bcrypt. API-token requests set `request.state.current_user = "api"` plus token owner/scopes.

Current call sites include Codex/Claude scoped APIs, `/api/v1/chat`, webhooks, selected session routes, companion pairing, and external integrations. `/api/codex/*` and `/api/v1/chat` enforce route-local scopes; companion and selected session routes use owner attribution. `companion/pairing.py` can mint chat-scoped tokens outside normal token CRUD.

Admin token CRUD is cookie/admin gated. Scoped route code must use the token owner and declared scopes instead of falling back to cookie-user assumptions.

## Internal Tool Loopback

Agent tools call admin-gated HTTP routes through an in-process loopback. `core.middleware.INTERNAL_TOOL_TOKEN` owns the random per-process secret. `app.py` only accepts this bypass from direct loopback clients without proxy-forwarding headers.

`src.tool_security` owns non-admin tool blocking. Non-admin users must not reach admin tools through agent mode, MCP tools, or loopback calls.

Current admin gates include `require_admin()` call sites across admin wipe, backup, contacts, Cookbook, diagnostics, embeddings, MCP, model, personal docs, presets, skills, uploads, vault, webhook, and companion routes. Local wrappers also exist in auth routes, shell routes, and task action policy; changes to those wrappers need the same trust-boundary review as `require_admin()`.

## Untrusted Context Policy

`src.prompt_security` owns the model-facing untrusted data contract:

- `UNTRUSTED_CONTEXT_POLICY` states the policy in system prompt text.
- `untrusted_context_message(label, content)` wraps external content as user-role data with `metadata.trusted = False`.

Current untrusted surfaces include fetched URLs, web results, emails, memories, skills, notes, documents, active editor content, and tool output sourced from outside the server. Injecting those as trusted system instructions is a security bug.

## URL, Path, And Secret Policy

- `src/url_security.py` owns public HTTP(S) validation for integration/API-token supplied URLs. It should fail closed for private IP, loopback, invalid scheme, and unsafe redirect targets.
- `src/url_safety.py` owns local-first outbound URL safety for model endpoints and similar local services. Loopback/LAN can be allowed by default, and private-IP blocking is an explicit caller policy.
- `src.webhook_manager` validates webhook URLs at create and delivery time. `src.integrations` owns admin-configured integration base URLs and secret masking.
- Path-based tools, upload/document/gallery/signature/generated-image routes, embedding cache paths, and research JSON helpers must stay confined to allowed roots and owner-scoped files.
- Secret-like DB columns use `EncryptedText` or `src.secret_storage`. `src.api_key_manager` keeps provider API keys encrypted in `data/api_keys.json` and writes by loading the raw encrypted dict so saving one provider does not rewrite other providers' keys as plaintext. Vault state in `data/vault.json` is a chmod-restricted JSON secret store, not Fernet-encrypted DB storage. Do not log or return decrypted secrets except for intentional admin vault retrieval flows with audit/reason checks.
- `.env` files are secrets-only inputs and should not be read or printed during agent work.

`scripts/diffusion_server.py` is a local model-serving helper with its own web surface. It defaults CORS to deny, installs a trusted-host allowlist for loopback/bind addresses, and only extends Host/CORS through explicit CLI flags.

## Degraded And Compatibility Behavior

- `AUTH_ENABLED=false` skips `AuthMiddleware` and `src.auth_helpers.require_user()` returns `""` from any host. Route code should still avoid assuming a non-empty owner.
- First-run setup mode redirects browser requests to `/login`, returns API `401 Setup required`, and keeps setup/status/login surfaces auth-exempt. Setup/signup/login are rate-limited; status is exempt but not rate-limited. Route helper fallbacks only tolerate unconfigured anonymous access from loopback.
- User privilege checks distinguish legacy empty `allowed_models=[]` from explicit no-model access through `allowed_models_restricted=True`.
- `LOCALHOST_BYPASS` in `app.py` only applies to direct loopback clients and excludes proxy/tunnel headers. Helper fallback code is weaker and should not be treated as the primary bypass boundary.
- Legacy migrations claim null-owner SQL/JSON data for the primary admin when possible, and startup repeats a null-owner sweep hourly. Remaining null-owner rows are surface-specific compatibility data that must be deliberately included, no-oped for single-user mode, or rejected for strict ownership gates.
- `.env` is loaded with `utf-8-sig`, so Windows BOM auth flags still parse.

## Current Gaps

- There is no shell/filesystem sandbox for admin tools.
- Token scopes remain coarse for some surfaces.
- `app.py` AuthMiddleware lacks direct regression coverage for bearer-token state/cache behavior, trusted-loopback proxy-header rejection, and internal-tool owner stamping.
- Codex/Claude scoped route enforcement and untrusted tool-result reinjection need stronger regression coverage.
- `THREAT_MODEL.md` still has stale token-scope and `/api/v1/chat` SSRF gap text that should be reconciled with current route validation.
