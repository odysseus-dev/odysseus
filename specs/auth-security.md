# Auth And Security

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers current security and trust-boundary behavior in:

- `core/auth.py`;
- `core/middleware.py`;
- `core/log_safety.py`;
- `core/database.py`;
- `app.py` auth middleware and token cache;
- `src/auth_helpers.py`;
- `src/owner_identity.py`;
- `src/tool_approval_scopes.py`, `src/tool_approvals.py`, and `src/tool_capabilities.py`;
- `src/tool_security.py`;
- `src/tool_execution.py`;
- `src/task_action_policy.py`;
- `src/prompt_security.py`;
- `src/url_safety.py` and `src/url_security.py`;
- `src/host_docker_access.py`;
- `src/attachment_refs.py` and upload lifecycle enforcement in
  `src/upload_handler.py` / `routes/upload_routes.py`;
- `src/secret_storage.py`;
- `src/api_key_manager.py`;
- `src/integrations.py`;
- `src/webhook_manager.py`;
- `src/generated_images.py`;
- `scripts/diffusion_server.py`;
- `scripts/mlx_image_server.py`;
- `companion/routes.py` and `companion/pairing.py`;
- `routes/auth_routes.py`, `routes/api_token_routes.py`, and canonical `routes/vault/vault_routes.py` plus its top-level compatibility shim;
- admin-gated call sites in route files;
- `THREAT_MODEL.md` and `SECURITY.md`.

## Trust Boundary

Odysseus is a trusted-user private-network app. Admins intentionally have powerful local capabilities: shell, files, email, calendar, MCP, model serving, vault, settings, and API token management. The security model prevents unauthenticated access, non-admin escalation, prompt-injection through untrusted content, and accidental exposure of internal services.

`THREAT_MODEL.md` owns high-level security framing, but implementation claims here should be verified against current code when the threat model is stale. This spec records the implementation map that contributors should check before changing auth or untrusted-context flows. Security-header runtime details live in `runtime.md`.

## Auth Ownership

- `core.auth.AuthManager` owns users, password hashing, TOTP/backup codes, reserved usernames, privilege defaults, admin promote/demote state, and auth settings stored in `data/auth.json`. Auth config/setup mutations are lock-guarded, and session tokens are persisted separately in `data/sessions.json` behind their own lock.
- `app.py` owns request-time auth middleware, token-cache rebuild/invalidation, auth exemptions, API-token verification, and internal-tool identity stamping.
- `routes/auth_routes.py` owns HTTP endpoints for setup, signup/login/logout, 2FA, users, privileges, auth features, and integration settings.
- `core.middleware.require_admin()` owns the normal admin gate. Local wrappers must document and test any intentional divergence from that boundary.
- `src.auth_helpers.effective_user()` owns cookie/API-token owner attribution for selected route code. `require_user()` owns route-level degraded user resolution, `require_privilege()` owns privilege checks, and `owner_filter()` owns shared/null-owner query compatibility.

Reserved usernames include request-only sentinels `internal-tool`, `api`, `demo`, and `system`, plus the storage-only Default/Local owner `__odysseus_local__`. Loaded auth data drops reserved user records, and create/rename flows must reject real users with those names. `src.owner_identity` is the canonical owner vocabulary and `auth_disabled()` parser.

## Auth Runtime Flow

`AuthMiddleware` is the outer request gate because FastAPI middleware executes in reverse add order. It can return API `401` JSON or browser `/login` redirects before timeout/security-header middleware reaches the route.

Public/auth-exempt surfaces are limited to setup, signup/login/logout/status, feature/settings/integration preset reads, health/version/login, `/static/*`, and task webhook trigger paths. `routes/task/task_routes.py` owns validation of `POST /api/tasks/{task_id}/webhook/{token}` path credentials.

Login issues an `HttpOnly`, `SameSite=Lax` cookie with a seven-day max age when "remember" is enabled. `_secure_cookie()` (`routes/auth_routes.py:89`) decides the `Secure` attribute: an explicit `SECURE_COOKIES` of `true` or `false` is authoritative, and any other value, including unset and the present-but-empty value docker-compose injects, derives it from the request, marking the cookie `Secure` when the connection scheme or the first `X-Forwarded-Proto` hop is https. TOTP is checked before session issuance. Logout, password changes, user deletion, rename flows, expired sessions, and deleted-user sessions must keep revocation/migration behavior intact.

Deleting a user revokes that user's browser sessions and API-token rows, then the admin delete route invalidates the in-memory bearer-token cache so already-cached tokens stop authenticating.

Rename first changes the auth username, then migrates owner-bearing DB rows and disk-backed stores. Current rename coverage includes user preferences, active/disk research state, `memory.json`, upload metadata and owner-qualified upload index keys, skills frontmatter/usage state, cached browser sessions, and API-token cache invalidation. If owner migration fails after the auth rename, the route attempts to roll auth back to the old username instead of leaving a split identity.

Admin promotion/demotion is a live auth flag change through `AuthManager.set_admin()` and `PUT /api/auth/users/{username}/admin`. Demotion refuses to remove the last admin, permits self-demotion when another admin remains, restores the pre-admin privilege map when available, and does not revoke sessions or API tokens because later admin checks read the current `is_admin` flag.

## Owner Attribution

Cookie requests use the real username. Bearer-token requests are stamped as `request.state.current_user = "api"` plus `api_token_owner`, `api_token_scopes`, and token id. Routes that support API-token access must explicitly use `effective_user()` or route-local scope helpers instead of treating `"api"` as an owner.

Internal loopback calls may stamp `current_user = "internal-tool"` or a validated `X-Odysseus-Owner` username. Network/proxy validation for that bypass lives in `app.py`; `require_admin()` trusts the stamped sentinel or raw internal header and should be used behind equivalent middleware control.

Missing-owner values remain state-dependent at legacy call sites, but new storage-facing code has one normalization contract:

- Auth-enabled, configured auth with no `current_user` is unauthenticated and should fail closed at route dependencies.
- `AUTH_ENABLED=false` is an explicit local single-user/no-login mode. Existing route dependencies can still return `""`, and admin gates allow the local operator. `effective_storage_owner()` and `storage_owner_for_request()` normalize an absent owner to `__odysseus_local__` only in this mode.
- Chat/agent code that reads `get_current_user(request)` directly gets `None` when auth middleware is disabled, because no middleware stamps request state.
- SQL `NULL`/JSON missing owners remain legacy/shared compatibility data, not the same thing as a logged-out authenticated caller.
- `"api"` and `"internal-tool"` are request sentinels. They must not be persisted as normal storage owners unless a route explicitly defines that behavior.
- `__odysseus_local__` is a valid storage owner but never a login or request sentinel. Adoption is incremental: callers that do not use the storage-owner helper can still expose older `None`/empty/null compatibility behavior.

Authenticated `manage_tasks` mutations require an exact stored task-owner
match and reject both cross-owner and legacy null-owner rows. The `owner=None`
agent path keeps deliberate auth-disabled single-user compatibility, including
unscoped list/create/mutation behavior.

Owner-scoped route code should use `require_user()` or equivalent policy before querying per-owner data. Current note CRUD/reorder/reminder routes do this so an auth-enabled request that reaches the route without identity returns `401` instead of falling into single-user/null-owner compatibility behavior.

Scheduled task actions attribute differently again. `_execute_action` (`src/task_scheduler.py:1231`) invokes the action with `owner=task.owner` read from the stored `ScheduledTask` row, so no request and no resolved principal are in flight. These trigger paths converge there: schedule, event bus, manual run (`routes/task/task_routes.py:865`), the `manage_tasks` agent tool (`src/tools/system.py:469`), webhook triggers (`routes/task/task_routes.py:1045`), which are unauthenticated by design with the token as the only credential and execute under the stored `task.owner`, and success-chained tasks (`src/task_scheduler.py:1063-1074`), which additionally require the chained target to share `task.owner` and reject cycles. Trigger-side ownership checks use the `if user and task.owner != user` shape, so a falsy caller skips them. Action bodies that reach owner-scoped storage must treat `task.owner` as the authority; route-level `require_user()` never runs on this path.

## API Tokens And Scoped Integrations

`routes/api_token_routes.py` owns token CRUD and scope normalization. Partial updates preserve existing scopes unless new scopes are supplied, write scopes imply the matching read scopes where applicable, and Cookbook scopes are part of the normalized scope set. `app.py` caches active token prefix rows and verifies bearer tokens with bcrypt. API-token requests set `request.state.current_user = "api"` plus token owner/scopes.

Current call sites include Codex/Claude scoped APIs, `/api/v1/chat`, webhooks, selected session routes, companion pairing, and external integrations. `/api/codex/*` and `/api/v1/chat` enforce route-local scopes; companion and selected session routes use owner attribution. `companion/pairing.py` can mint chat-scoped tokens outside normal token CRUD.

Admin token CRUD is cookie/admin gated. Update/delete operations check token ownership, and cache rebuild ignores active tokens whose owner no longer maps to a known auth user. Scoped route code must use the token owner and declared scopes instead of falling back to cookie-user assumptions.

## Internal Tool Loopback

Agent tools call admin-gated HTTP routes through an in-process loopback. `core.middleware.INTERNAL_TOOL_TOKEN` owns the random per-process secret. `app.py` only accepts this bypass from direct loopback clients without proxy-forwarding headers.

`src.tool_security` owns non-admin tool blocking. Non-admin users must not reach admin tools through agent mode, MCP tools, or loopback calls.

`src.tool_security.owner_is_admin_or_single_user()` treats explicit `AUTH_ENABLED=false` as intentional single-user mode even when an auth store already exists, while keeping pre-setup auth-enabled callers non-admin.

Current admin gates include `require_admin()` call sites across admin wipe, backup, contacts, Cookbook, diagnostics, embeddings, MCP, model, personal docs, presets, skills, uploads, vault, webhook, and companion routes. Local wrappers also exist in auth routes, shell routes, and task action policy; changes to those wrappers need the same trust-boundary review as `require_admin()`. Scheduled task action policy treats `run_local`, `run_script`, `ssh_command`, and `cookbook_serve` as admin-only action tasks across create/update/manual-run/webhook/scheduler execution.

`tidy_research` can remove only empty or unparseable research JSON. Because a broken file has no trustworthy owner stamp, the action checks `owner_is_admin_or_single_user()` before enumerating files; regular users and the pre-setup window cannot run that global unattributable-file sweep.

## Untrusted Context Policy

`src.prompt_security` owns the model-facing untrusted data contract:

- `UNTRUSTED_CONTEXT_POLICY` states the policy in system prompt text.
- `untrusted_context_message(label, content)` wraps external content as user-role data with `metadata.trusted = False`, provenance metadata, and a default `tool_gate_untrusted` marker. Guard-like labels/content are escaped so source text cannot counterfeit the wrapper boundary.

Current untrusted surfaces include fetched URLs, web results, emails, memories, skills, notes, documents, active editor content, and tool output sourced from outside the server. Injecting those as trusted system instructions is a security bug.

`src.tool_capabilities` classifies native and MCP tools by effects and result integrity. After external/workspace-untrusted context becomes model-visible, `ToolRunSecurityContext` keeps a server-owned taint for the session turn: only explicitly low-impact tools can run immediately, while write, execute, network-egress, UI/external-side-effect, admin, destructive, unknown, and arbitrary MCP actions require exact approval. Failed tools can still arm the gate when their result carries remote or stored payload; content-free failures and server-generated blocked/approval placeholders do not.

`src.tool_approvals` owns opaque approvals sealed to the owner, session, origin run, exact first tool name/content, workspace, capability effects/result integrity, selected continuation tool set/query, and expiry. Document actions additionally seal document id, version, content digest, and workspace. Chat cards offer task scope, chat-session scope, or deny: both allow choices consume and execute the exact sealed first action after current-policy/freshness checks, task scope bypasses the gate only for the resumed task, and chat-session scope persists a resolved session-bound grant for later turns in that same chat. The browser submits only the opaque decision and cannot replace the sealed action, selected tools, query, composer text, or attachments. Non-chat callers retain single-action scope. A new ordinary turn or superseding action retires an unresolved approval without clearing taint.

## URL, Path, And Secret Policy

- `src/url_security.py` owns public HTTP(S) validation for integration/API-token supplied URLs. It should fail closed for private IP, loopback, invalid scheme, and unsafe redirect targets.
- `src/url_safety.py` owns local-first outbound URL safety for model endpoints and similar local services. Loopback/LAN can be allowed by default, and private-IP blocking is an explicit caller policy. Strict `block_private=True` also rejects RFC 6598 shared/CGNAT space (`100.64.0.0/10`) explicitly because Python does not classify that range as private.
- `core.log_safety.redact_url()` strips URL userinfo, query strings, and fragments before endpoint URLs enter logs. Model, chat/research endpoint, contact/CardDAV, and similar diagnostics should use this helper instead of logging raw admin-configured URLs.
- `src.webhook_manager` validates webhook URLs at create and delivery time,
  rejects private/internal targets, disables redirects, and pins delivery to
  the public IP set that passed validation immediately before the request.
- `src.integrations` owns admin-configured integration base URLs and secret
  masking. `api_call` accepts only relative paths, rejects link-local/metadata destinations through `src.url_safety`, can additionally block RFC1918/loopback/private targets with `INTEGRATION_API_BLOCK_PRIVATE_IPS=true`, and pins requests to the IP set that passed SSRF validation while preserving the intended Host/TLS identity.
- `src.outbound_fetch` owns reusable public-URL classification, validates every redirect hop, rejects private/local resolved addresses, and pins the HTTP connection to the validated public IP while preserving original URL/SNI/Host semantics. `services.search.content` adapts that transport for extraction and caching.
- Path-based tools, upload/document/gallery/signature/generated-image routes, embedding cache paths, and research JSON helpers must stay confined to allowed roots and owner-scoped files. Native file/code-navigation tools also apply a case-insensitive sensitive-path denylist so `grep`, `glob`, `ls`, direct reads, and writes cannot reveal `.env`, SSH/GPG material, private-key filenames, or similar secret paths.
- Durable upload references are owner-reserved before chat/session, document,
  note, or calendar writes. Cleanup scans every current durable reference
  surface and fails closed on incomplete discovery or inconsistent upload-index
  state rather than deleting a possibly live upload.
- File-backed SQLite startup restricts `app.db` and existing rollback/WAL/SHM
  sidecars to `0600` on POSIX after resolving the real path from the parsed
  engine URL. Windows, in-memory, and non-SQLite databases are excluded, and
  failed POSIX restriction is logged as a secret-file warning.
- Secret-like DB columns use `EncryptedText` or `src.secret_storage`. Email passwords and Google OAuth mail tokens are encrypted manually in `EmailAccount` string columns; Google OAuth state is HMAC-signed and callback writes are owner-checked before token storage. `src.api_key_manager` keeps provider API keys encrypted in `data/api_keys.json`, writes by loading the raw encrypted dict so saving one provider does not rewrite other providers' keys as plaintext, and restricts local key-file permissions where the platform supports chmod. Vault state in `data/vault.json` is a chmod-restricted JSON secret store, not Fernet-encrypted DB storage. Do not log or return decrypted secrets except for intentional admin vault retrieval flows with audit/reason checks.
- `.env` files are secrets-only inputs and should not be read or printed during agent work.

`scripts/diffusion_server.py` is a local model-serving helper with its own web surface. It defaults CORS to deny, installs a trusted-host allowlist for loopback/bind addresses, and only extends Host/CORS through explicit CLI flags.

`scripts/mlx_image_server.py` serves exactly the model selected when the process starts. OpenAI-compatible request `model` fields are accepted but ignored for generation and edits, so an unauthenticated caller cannot select another local directory or Hugging Face repository and drive model-specific script/bridge execution.

Host Docker socket access is a high-trust admin/deployment choice, not a normal container capability. Default Docker Compose does not mount `/var/run/docker.sock`; `src.host_docker_access` only reports local Docker available inside a container when `ODYSSEUS_ENABLE_HOST_DOCKER=true` and the socket exists. Remote SSH Docker/Cookbook workflows remain the safer default.

## Degraded And Compatibility Behavior

- `AUTH_ENABLED=false` skips `AuthMiddleware` and `src.auth_helpers.require_user()` returns `""` from any host. This preserves local single-user/no-login operation; it is not permission for auth-enabled logged-out callers. Storage code that adopts `storage_owner_for_request()` receives the reserved Default/Local owner; direct `get_current_user()` readers still receive `None`. Owner-scoped routes that tolerate no-login mode should call the appropriate route or storage helper so auth-enabled anonymous requests fail closed.
- First-run setup mode redirects browser requests to `/login`, returns API `401 Setup required`, and keeps setup/status/login surfaces auth-exempt. Setup/signup/login are rate-limited; status is exempt but not rate-limited. Route helper fallbacks only tolerate unconfigured anonymous access from loopback.
- User privilege checks distinguish legacy empty `allowed_models=[]` from explicit no-model access through `allowed_models_restricted=True`.
- `LOCALHOST_BYPASS` in `app.py` only applies to direct loopback clients and excludes proxy/tunnel headers. Helper fallback code is weaker and should not be treated as the primary bypass boundary.
- Legacy migrations claim null-owner SQL/JSON data for the primary admin when possible, and startup repeats a null-owner sweep hourly. Remaining null-owner rows are surface-specific compatibility data that must be deliberately included, no-oped for single-user mode, or rejected for strict ownership gates.
- `.env` is loaded with `utf-8-sig`, so Windows BOM auth flags still parse.

## Current Gaps

- There is no shell/filesystem sandbox for admin tools.
- Token scopes remain coarse for some surfaces.
- `app.py` AuthMiddleware lacks direct regression coverage for bearer-token state/cache behavior, trusted-loopback proxy-header rejection, and internal-tool owner stamping.
- Codex/Claude scoped route enforcement still needs stronger regression coverage.
- `THREAT_MODEL.md` still has stale token-scope and `/api/v1/chat` SSRF gap text that should be reconciled with current route validation.
- The Default/Local owner contract is canonical but only incrementally adopted; route helper `""`, chat/agent `None`, SQL/JSON null-owner compatibility, and calendar fallback owner behavior still need domain-by-domain migration decisions.
