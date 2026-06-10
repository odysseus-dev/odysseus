# Settings And Admin Surfaces

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers settings and admin surfaces in:

- `app.py` auth-exempt and route-registration wiring;
- `routes/auth_routes.py` for setup, login/status, users, features, settings, and integration settings routes;
- `core/auth.py` and `core/middleware.py` admin/privilege behavior;
- `src/settings.py` and `src/settings_scrub.py`;
- `routes/prefs_routes.py`;
- `src/preset_manager.py` and `routes/preset_routes.py`;
- `routes/backup_routes.py` and `scripts/odysseus-backup`;
- `routes/diagnostics_routes.py`;
- `routes/admin_wipe_routes.py`;
- `routes/cleanup_routes.py` and `src/cleanup_service.py`;
- `routes/vault_routes.py` and vault-related tool implementations;
- `routes/font_routes.py`;
- `routes/model_routes.py` for `/api/tools` and settings-bound model endpoint references;
- `src/tool_implementations.py`, `src/tool_execution.py`, `src/tool_schemas.py`, and `src/tool_index.py` for `manage_settings`;
- `src/agent_loop.py` for stale agent prompt references to settings APIs;
- frontend modules `static/js/settings.js`, `static/js/admin.js`, `static/js/presets.js`, `static/js/theme.js`, and `static/js/storage.js`;
- CLI helpers `scripts/odysseus-preset` and `scripts/odysseus-theme`.

Generic API integrations are cross-referenced in `integrations.md`. Model endpoint CRUD and endpoint cleanup are covered in `llm-models.md`. Email/contact/calendar legacy setting fallbacks stay with their domain specs.

## Data Stores

`src.settings` owns `data/settings.json` and `data/features.json`. Settings and features are merged over defaults and cached briefly. Missing, corrupt, or non-object stores fall back to defaults; settings also handles unreadable-store fallback more defensively than features.

`routes.prefs_routes` owns `data/user_prefs.json`. It supports:

- `_users` multi-user storage;
- legacy flat prefs;
- auth-disabled first-user compatibility without clobbering the rest of `_users`.

`src.settings.get_user_setting()` overlays only a whitelist of per-user prefs over global settings. That whitelist is mostly model/media endpoint choices.

Other active stores include:

- `data/presets.json`;
- `data/vault.json`;
- `static/fonts/custom`;
- DB-backed domain tables used by admin wipe and cleanup;
- browser localStorage/sessionStorage for theme, preset, privacy, and transient UI state.

## Bootstrap, Auth, And Settings Routes

`routes.auth_routes` owns first-run setup, login/logout/status, password/TOTP flows, signup controls, user CRUD, privilege edits, feature flags, and app settings. `app.py` exposes setup/status/features/settings routes before cookie auth so first-run and frontend bootstrap can work.

Settings runtime:

- `GET /api/auth/features` is public feature visibility metadata;
- `POST /api/auth/features` is admin-only;
- `GET /api/auth/settings` returns full settings to admins;
- non-admin or unauthenticated `GET /api/auth/settings` returns `scrub_settings()` output;
- `POST /api/auth/settings` is admin-only and only writes keys present in `DEFAULT_SETTINGS`.

`src.settings_scrub` owns deep secret-key scrubbing for non-admin settings reads. It preserves structure while blanking secret-shaped string values.

Admin gates inherit the auth contracts in `auth-security.md`: normal deployments require an admin user, while `AUTH_ENABLED=false`, first-run/setup mode, validated internal-tool loopback, and direct localhost bypass have explicit behavior in auth middleware/helpers.

## Preferences And Frontend State

`routes.prefs_routes` owns per-user key/value preferences. Theme and custom-theme code uses localStorage first, syncs selected prefs through `/api/prefs/*`, and falls back from server prefs when local theme state is absent.

`static/js/theme.js` owns:

- theme and custom-theme persistence;
- old theme-name migrations;
- custom font selection and `/api/fonts/custom` discovery;
- CSS variable application.

`static/js/settings.js` owns the Settings modal shell, non-admin settings panels, admin visibility sync, provider/model/search/research/reminder/email/CalDAV/vault panels, scoped-token helpers, and unified integrations forms. `static/js/admin.js` owns user/admin panels, model endpoints, builtin tool toggles, MCP admin forms, feature toggles, token/webhook panels, backup/import, and danger-zone wipes.

Logout/user-switch flows clear local/session storage to avoid stale cross-account UI state.

## Presets

`src.preset_manager.PresetManager` owns preset persistence, default preset healing, corrupt-store fallback, and legacy custom-preset migration. `routes.preset_routes` owns HTTP behavior.

Runtime behavior:

- preset list/templates/groups/expand routes are read or utility surfaces;
- custom preset/template/group mutations are admin-gated;
- preset expansion can call the configured model;
- frontend activation combines persisted `custom.enabled` with local selected-preset UI state;
- presets, user templates, and group presets are currently shared stores, not owner-scoped stores.

`scripts/odysseus-preset` is a local CLI for preset store maintenance and backup of `presets.json`.

## Tools Settings

`routes.model_routes` owns `/api/tools`, which writes `settings.json:disabled_tools` for global builtin tool toggles.

`src.tool_implementations.do_manage_settings()` owns the model-facing settings tool. It is admin-only through tool execution/security policy, writes real global settings, refuses secret-shaped setting writes, refuses structured clobbers, resolves model aliases to endpoints, and can enable/disable tools.

The stale `app_api` prompt text that mentions `/api/settings` is not the canonical settings surface; the live HTTP route is `/api/auth/settings`, and `manage_settings` is the intended agent settings tool. The `manage_settings` schema also still describes free-form preferences even though implementation only accepts keys in `DEFAULT_SETTINGS`.

## Backup And Import

`routes.backup_routes` owns admin JSON export/import for selected app state:

- owner-filtered memories;
- shared presets;
- owner-filtered skills;
- raw global settings;
- feature flags;
- per-user preferences.

HTTP export is secret-bearing because it includes raw settings. Treat exported files as sensitive admin artifacts.

HTTP import is best-effort and section-based. It rejects invalid top-level JSON, ignores unrecognized or wrongly typed sections, merges recognized sections, and may partially write earlier sections before a later failure. Memory dedup is scoped to the importing user; imported memories/skills without owners are stamped to the caller, while explicit owner fields are preserved. Skill import writes through the disk-backed `SkillsManager.add_skill()` API, not the removed JSON-era `save()` shape.

`scripts/odysseus-backup` is a separate local `data/` snapshot/restore tool, with some large/runtime subtrees behind flags. It uses SQLite backup where applicable, rejects archives written inside `data/`, validates restore members, and refuses links/special files.

## Diagnostics, Cleanup, And Wipe

`routes.diagnostics_routes` owns admin diagnostics for DB, RAG, YouTube, and research status. Diagnostics are operational and must avoid growing into broad secret/environment dumps.

`routes.cleanup_routes` is owner-scoped, not admin-only. It previews and applies session cleanup for the current user through `src.cleanup_service`; when auth is disabled, cleanup can operate as a single-user unscoped flow.

`routes.admin_wipe_routes` owns global per-domain destructive wipe actions. Current kinds include chats, memory, skills, notes, tasks, documents, gallery, and calendar. Server enforcement is admin gate plus kind allowlist. Frontend double confirmation in `static/js/admin.js` is user-interface protection, not server authorization.

## Vault

`routes.vault_routes` owns Vaultwarden/Bitwarden CLI config, login, unlock, lock, logout, and `bw_installed` checks.

Runtime behavior:

- `GET /api/vault/config` returns no `session` value;
- `data/vault.json` stores config and `BW_SESSION`;
- POSIX saves attempt `0600` permissions;
- master passwords are passed to `bw` on stdin, not argv;
- missing `bw` degrades to route error/status responses;
- corrupt or non-object vault config loads as empty config;
- lock/logout clear the saved session.

Vault tool paths duplicate some route behavior and can return vault item secrets to an admin tool result after a reason check and audit log. They are admin/local trust-boundary surfaces.

## Fonts

`routes.font_routes` lists user-supplied font files under `static/fonts/custom`. It is a support/discovery route, not an admin operation. `static/js/theme.js` owns consuming this list for theme font selection.

## Security And Provenance

- Non-admin and unauthenticated settings reads are scrubbed.
- Admin settings reads, admin edit forms, vault flows, backup files, and local CLI artifacts can contain secrets and must remain admin-only or locally protected.
- Backup artifacts are sensitive because settings may include API keys, passwords, tokens, and endpoint credentials.
- Diagnostics and logs should avoid adding secret-bearing values.
- Admin wipe is global per kind and crosses owners.
- Cleanup is owner-scoped in normal auth mode.
- `manage_settings` blocks secret-shaped setting writes and structured setting clobbers.
- Vault master passwords must not appear in process argv.
- Client-side confirmations are not server authorization controls.

## Degraded And Compatibility Behavior

- Settings/features fall back to defaults on missing/corrupt/non-object stores.
- `is_setting_overridden()` has a narrower error contract than `load_settings()`.
- Prefs support legacy flat files and auth-disabled first-user writes.
- Presets heal missing built-ins and legacy custom state without clobbering user edits.
- `/api/import` is non-atomic section merge.
- Vault route and vault tool degraded behavior are not identical.
- Theme/preset frontend helpers tolerate malformed localStorage values.
- CLI helpers are local maintenance surfaces and may bypass HTTP route policy.

## Testing Notes

Current targeted coverage includes settings store fallback/error paths, settings scrub, prefs no-clobber behavior, preset store/migration/CLI/localStorage helpers, backup import cross-user dedup, backup CLI restore safety, cleanup owner scope, diagnostics admin-gate source checks, admin wipe gallery, font family derivation, theme helper behavior, vault password-not-in-argv checks, setup/auth regressions, reserved usernames, and a token-budget `manage_settings` path.

## Current Gaps

- Add route tests for `/api/auth/settings`: anonymous/non-admin scrubbed reads, admin full reads, non-admin POST rejection, and unknown-key ignore behavior.
- Add route tests for `/api/auth/features` admin writes.
- Add `/api/tools` and `manage_settings` tests for secret write refusal, enum/integer coercion failures, structured-setting refusal, reset/default behavior, endpoint/model resolution, and tool enable/disable aliases.
- Add backup tests for secret-bearing export policy, owner-scoped exported sections, invalid import handling, skills dedup, settings/features merge, and admin gates.
- Add diagnostics tests for error redaction and sensitive output limits.
- Add admin wipe tests for every wipe kind, unknown-kind 400, rollback behavior, and admin gating.
- Add vault route tests for session omission, permission setting, login/unlock failures, lock/logout clearing, corrupt config, and admin gates.
- Add frontend tests for Settings/Admin panel save/load flows, vault password clearing, diagnostics buttons, cleanup/wipe confirmations, custom font/theme wiring, and tab state.
- Decide whether `user_templates` and `group_presets` should remain shared despite user-facing names.
- Decide whether backup/import should preserve explicit owner fields or force imported owner ownership.
- Decide whether `is_setting_overridden()` should match `load_settings()` permission-error fallback.
- Decide whether a dedicated split is needed for the large `static/js/settings.js` and `static/js/admin.js` ownership boundary.
